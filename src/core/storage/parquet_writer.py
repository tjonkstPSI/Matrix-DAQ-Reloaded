# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List, Optional


@dataclass
class ParquetWriterSettings:
    chunk_duration_s: float = 1.0 # 1 second
    segment_time_limit_s: float = 14400 # 4 hours
    segment_size_limit_mb: float = 100.0 # 100 MB
    coalesce_on_finalize: bool = True # True: coalesce all segments into a single file on finalize
    keep_chunk_files: bool = False # True: keep chunk files after coalesce  


class ParquetWriter:
    """
    Append-only, crash-safe writer that buffers rows into 1-second chunks
    and writes each chunk as a standalone Parquet file under a per-segment
    directory. Segmentation rolls over by elapsed time or total bytes.

    Layout:
      runs/<base>/
        data/
          seg_1/ data_YYYYMMDD_HHMMSS.parquet
          seg_2/ ... (created only if segmentation triggers)
        config_snapshot/

    Each written row contains:
      - Time_Relative_s (float, seconds)
      - Time_Absolute_iso8601 (string)
      - One column per channel alias with numeric/boolean values

    If pyarrow is not available, the writer becomes a no-op to avoid
    interrupting demo/continuous modes.
    """

    def __init__(self, run_dir: Path, settings: Optional[ParquetWriterSettings] = None) -> None:
        self.run_dir = run_dir
        self.settings = settings or ParquetWriterSettings()
        self.data_dir = (self.run_dir / "data").resolve()
        self.cfg_snapshot_dir = (self.run_dir / "config_snapshot").resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cfg_snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._segment_index: int = 1
        self._segment_dir: Path = self._ensure_segment_dir(self._segment_index)
        self._segment_start_ts: float = 0.0
        self._segment_bytes: int = 0
        self._buf_rows: List[Dict[str, Any]] = []
        self._buf_second_key: Optional[int] = None
        self._observed_units: Dict[str, str] = {}
        self._arrow_ok = self._check_pyarrow()

    def _check_pyarrow(self) -> bool:
        try:
            import pyarrow as _pa  # type: ignore
            import pyarrow.parquet as _pq  # type: ignore
            return True
        except Exception:
            print("[WARN] pyarrow not available; ParquetWriter disabled (no-op)")
            return False

    def _ensure_segment_dir(self, idx: int) -> Path:
        d = self.data_dir / f"seg_{int(idx)}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _roll_segment_if_needed(self, now_ts: float) -> None:
        if self._segment_start_ts == 0.0:
            self._segment_start_ts = now_ts
            return
        time_limit = max(0.0, float(self.settings.segment_time_limit_s))
        size_limit_bytes = max(0.0, float(self.settings.segment_size_limit_mb)) * 1024.0 * 1024.0
        time_exceeded = (now_ts - self._segment_start_ts) >= time_limit if time_limit > 0 else False
        size_exceeded = self._segment_bytes >= size_limit_bytes if size_limit_bytes > 0 else False
        if time_exceeded or size_exceeded:
            self._segment_index += 1
            self._segment_dir = self._ensure_segment_dir(self._segment_index)
            self._segment_start_ts = now_ts
            self._segment_bytes = 0

    @staticmethod
    def _iso8601(ts: float) -> str:
        try:
            import datetime as _dt
            return _dt.datetime.fromtimestamp(ts).isoformat(timespec="milliseconds")
        except Exception:
            return "1970-01-01T00:00:00.000"

    @staticmethod
    def _chunk_filename(ts: float) -> str:
        try:
            import time as _t
            return _t.strftime("data_%Y%m%d_%H%M%S.parquet", _t.localtime(ts))
        except Exception:
            return "data_unknown.parquet"

    def append(self, now_ts: float, values: Dict[str, Any], units: Dict[str, str]) -> None:
        if not isinstance(values, dict):
            return
        self._roll_segment_if_needed(now_ts)
        for k, v in (units or {}).items():
            if k not in self._observed_units and isinstance(k, str):
                try:
                    self._observed_units[k] = str(v)
                except Exception:
                    self._observed_units[k] = ""
        row: Dict[str, Any] = {
            "Time_Relative_s": float(values.get("Time_Relative_s", 0.0)),
            "Time_Absolute_iso8601": self._iso8601(now_ts),
        }
        for alias, raw in values.items():
            if alias in ("Time_Relative_s",):
                continue
            try:
                if isinstance(raw, bool):
                    row[str(alias)] = int(raw)
                else:
                    row[str(alias)] = float(raw)
            except Exception:
                continue
        sec_key = int(now_ts)
        if self._buf_second_key is None:
            self._buf_second_key = sec_key
        if sec_key != self._buf_second_key:
            self._flush_chunk(self._buf_second_key)
            self._buf_rows = []
            self._buf_second_key = sec_key
        self._buf_rows.append(row)

    def _flush_chunk(self, second_key: Optional[int]) -> None:
        if not self._buf_rows:
            return
        if not self._arrow_ok:
            self._buf_rows.clear()
            return
        try:
            import json as _json
            import pandas as _pd  # type: ignore
            import pyarrow as _pa  # type: ignore
            import pyarrow.parquet as _pq  # type: ignore
            ts_for_name = float(second_key or 0)
            fname = self._chunk_filename(ts_for_name)
            out_path = self._segment_dir / fname
            df = _pd.DataFrame(self._buf_rows)
            table = _pa.Table.from_pandas(df, preserve_index=False)
            md = dict(table.schema.metadata or {})
            try:
                md[b"units_json"] = _json.dumps(self._observed_units).encode("utf-8")
            except Exception:
                pass
            table = table.replace_schema_metadata(md)
            _pq.write_table(table, out_path)
            try:
                self._segment_bytes += out_path.stat().st_size
            except Exception:
                pass
        except Exception as e:
            try:
                print(f"[WARN] ParquetWriter flush failed: {e}")
            except Exception:
                pass
        finally:
            self._buf_rows.clear()

    def snapshot_configs(self, configs_dir: Path) -> None:
        try:
            import shutil
            self.cfg_snapshot_dir.mkdir(parents=True, exist_ok=True)
            for p in configs_dir.glob("*.yaml"):
                try:
                    shutil.copy2(p, self.cfg_snapshot_dir / p.name)
                except Exception:
                    continue
        except Exception:
            pass

    def finalize(self) -> None:
        # Flush any buffered rows first
        self._flush_chunk(self._buf_second_key)
        if not self._arrow_ok:
            return
        if not self.settings.coalesce_on_finalize:
            return
        try:
            import re as _re
            import pandas as _pd  # type: ignore
            import pyarrow as _pa  # type: ignore
            import pyarrow.parquet as _pq  # type: ignore
            import json as _json
            if not self.data_dir.exists():
                return
            # Discover segment folders
            seg_dirs = []
            for p in sorted(self.data_dir.iterdir()):
                if p.is_dir() and _re.match(r"^seg_\d+$", p.name):
                    seg_dirs.append(p)
            if not seg_dirs:
                return
            multi = len(seg_dirs) > 1
            # Load run metadata to customize output file names
            run_meta = {}
            try:
                import yaml as _yaml  # type: ignore
                meta_p = self.run_dir / "metadata.yaml"
                if meta_p.exists():
                    run_meta = _yaml.safe_load(meta_p.read_text(encoding="utf-8")) or {}
            except Exception:
                run_meta = {}
            run_stem = f"Data_{self.run_dir.name}"
            try:
                # Prefer explicit fields to reconstruct name if needed
                tc = str(run_meta.get("test_cell", "")).strip()
                et = str(run_meta.get("engine_type", "")).strip()
                es = str(run_meta.get("engine_serial_number", "")).strip()
                tt = str(run_meta.get("test_type", "")).strip()
                if tc and et and es and tt:
                    run_stem = f"Data_{tc}_{self.run_dir.name.split('_',1)[1]}" if '_' in self.run_dir.name else f"Data_{self.run_dir.name}"
            except Exception:
                pass
            for seg_dir in seg_dirs:
                # Collect chunk files (sorted)
                chunk_files = sorted([f for f in seg_dir.iterdir() if f.suffix.lower() == ".parquet" and f.is_file()])
                if not chunk_files:
                    continue
                # Build union column order across chunks
                cols_set = set(["Time_Relative_s", "Time_Absolute_iso8601"])
                for cf in chunk_files:
                    try:
                        cdf = _pd.read_parquet(cf)
                        for c in list(cdf.columns):
                            cols_set.add(str(c))
                    except Exception:
                        continue
                # Preferred column order: Time_Relative_s, Time_Absolute_iso8601, then others sorted
                other_cols = sorted([c for c in cols_set if c not in ("Time_Relative_s", "Time_Absolute_iso8601")])
                final_cols = ["Time_Relative_s", "Time_Absolute_iso8601"] + other_cols
                # Prepare output path
                try:
                    idx = int(seg_dir.name.split("_")[1])
                except Exception:
                    idx = 1
                out_name = f"{run_stem}.parquet" if not multi else f"{run_stem}_{idx}.parquet"
                out_path = self.data_dir / out_name
                writer = None
                try:
                    # Stream-write each chunk as a row group with consistent schema
                    for i, cf in enumerate(chunk_files):
                        try:
                            df = _pd.read_parquet(cf)
                        except Exception:
                            continue
                        # Reindex to final_cols
                        df = df.reindex(columns=final_cols)
                        tbl = _pa.Table.from_pandas(df, preserve_index=False)
                        if writer is None:
                            # Attach merged units metadata to the file schema
                            md = {}
                            try:
                                md = _json.loads(_json.dumps(self._observed_units))
                            except Exception:
                                md = {}
                            meta = {b"units_json": _json.dumps(md).encode("utf-8")}
                            writer = _pq.ParquetWriter(out_path, tbl.schema.with_metadata(meta))
                        # Cast to writer schema to enforce column order/types
                        try:
                            tbl = tbl.cast(writer.schema, safe=False)
                        except Exception:
                            pass
                        writer.write_table(tbl)
                finally:
                    try:
                        if writer is not None:
                            writer.close()
                    except Exception:
                        pass
                # Cleanup chunk files and segment dir if configured
                if not self.settings.keep_chunk_files:
                    for cf in chunk_files:
                        try:
                            cf.unlink(missing_ok=True)  # type: ignore[arg-type]
                        except Exception:
                            pass
                    try:
                        seg_dir.rmdir()
                    except Exception:
                        pass
        except Exception as e:
            try:
                print(f"[WARN] ParquetWriter finalize coalesce failed: {e}")
            except Exception:
                pass



