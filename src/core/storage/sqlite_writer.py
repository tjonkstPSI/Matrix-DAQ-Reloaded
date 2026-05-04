# Author: T. Onkst | Date: 05042026

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class SqliteWriterSettings:
    commit_interval_s: float = 2.0
    segment_time_limit_s: float = 14400  # 4 hours
    segment_size_limit_mb: float = 100.0  # 100 MB


class SqliteWriter:
    """
    Append-only, crash-safe writer that inserts rows into a SQLite database
    in WAL mode.  Rows are buffered in memory and committed periodically
    (every ``commit_interval_s`` seconds) so that a crash loses at most one
    commit window of data.

    Segmentation rolls the database file when elapsed time or file size
    limits are exceeded, producing ``seg_1.db``, ``seg_2.db``, etc.

    Layout:
      runs/<base>/
        data/
          seg_1.db
          seg_2.db   (only if segmentation triggers)
        config_snapshot/

    Each row contains:
      - Time_Relative_s  (REAL)
      - Time_Absolute_iso8601  (TEXT)
      - One column per channel alias (REAL)
    """

    def __init__(self, run_dir: Path, settings: Optional[SqliteWriterSettings] = None) -> None:
        self.run_dir = run_dir
        self.settings = settings or SqliteWriterSettings()
        self.data_dir = (self.run_dir / "data").resolve()
        self.cfg_snapshot_dir = (self.run_dir / "config_snapshot").resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cfg_snapshot_dir.mkdir(parents=True, exist_ok=True)

        self._segment_index: int = 1
        self._segment_start_ts: float = 0.0
        self._observed_units: Dict[str, str] = {}

        self._conn: Optional[sqlite3.Connection] = None
        self._columns: List[str] = []
        self._table_created: bool = False
        self._buf_rows: List[tuple] = []
        self._last_commit_ts: float = 0.0

        self._open_segment(self._segment_index)

    # ------------------------------------------------------------------
    # Segment lifecycle
    # ------------------------------------------------------------------

    def _db_path(self, idx: int) -> Path:
        return self.data_dir / f"seg_{int(idx)}.db"

    def _open_segment(self, idx: int) -> None:
        path = self._db_path(idx)
        self._conn = sqlite3.connect(str(path), isolation_level="DEFERRED")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._table_created = False
        self._columns = []
        self._buf_rows = []
        self._last_commit_ts = time.monotonic()

    def _close_segment(self) -> None:
        if self._conn is None:
            return
        self._flush()
        self._write_metadata()
        try:
            self._conn.close()
        except Exception:
            pass
        self._conn = None

    def _roll_segment_if_needed(self, now_ts: float) -> None:
        if self._segment_start_ts == 0.0:
            self._segment_start_ts = now_ts
            return

        time_limit = max(0.0, float(self.settings.segment_time_limit_s))
        size_limit_bytes = max(0.0, float(self.settings.segment_size_limit_mb)) * 1024.0 * 1024.0

        time_exceeded = (now_ts - self._segment_start_ts) >= time_limit if time_limit > 0 else False
        size_exceeded = False
        if size_limit_bytes > 0:
            try:
                size_exceeded = self._db_path(self._segment_index).stat().st_size >= size_limit_bytes
            except Exception:
                pass

        if time_exceeded or size_exceeded:
            self._close_segment()
            self._segment_index += 1
            self._segment_start_ts = now_ts
            self._open_segment(self._segment_index)

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def _ensure_table(self, row_keys: List[str]) -> None:
        """Create the data table on first row, or add columns for new keys."""
        if self._conn is None:
            return

        if not self._table_created:
            self._columns = list(row_keys)
            col_defs = ", ".join(f'"{c}" REAL' if c != "Time_Absolute_iso8601" else f'"{c}" TEXT'
                                 for c in self._columns)
            self._conn.execute(f"CREATE TABLE IF NOT EXISTS data ({col_defs})")
            self._conn.commit()
            self._table_created = True
            return

        new_cols = [k for k in row_keys if k not in self._columns]
        if new_cols:
            for c in new_cols:
                col_type = "TEXT" if c == "Time_Absolute_iso8601" else "REAL"
                try:
                    self._conn.execute(f'ALTER TABLE data ADD COLUMN "{c}" {col_type}')
                except Exception:
                    pass
                self._columns.append(c)
            self._conn.commit()

    # ------------------------------------------------------------------
    # Metadata table (units, etc.)
    # ------------------------------------------------------------------

    def _write_metadata(self) -> None:
        if self._conn is None or not self._observed_units:
            return
        try:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS _metadata (key TEXT PRIMARY KEY, value TEXT)"
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO _metadata (key, value) VALUES (?, ?)",
                ("units_json", json.dumps(self._observed_units)),
            )
            self._conn.commit()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def _iso8601(ts: float) -> str:
        try:
            import datetime as _dt
            return _dt.datetime.fromtimestamp(ts).isoformat(timespec="milliseconds")
        except Exception:
            return "1970-01-01T00:00:00.000"

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

        row_dict: Dict[str, Any] = {
            "Time_Relative_s": float(values.get("Time_Relative_s", 0.0)),
            "Time_Absolute_iso8601": self._iso8601(now_ts),
        }
        for alias, raw in values.items():
            if alias in ("Time_Relative_s",):
                continue
            try:
                if isinstance(raw, bool):
                    row_dict[str(alias)] = int(raw)
                else:
                    row_dict[str(alias)] = float(raw)
            except Exception:
                continue

        row_keys = list(row_dict.keys())
        self._ensure_table(row_keys)

        row_tuple = tuple(row_dict.get(c) for c in self._columns)
        self._buf_rows.append(row_tuple)

        now_mono = time.monotonic()
        if (now_mono - self._last_commit_ts) >= self.settings.commit_interval_s:
            self._flush()

    def _flush(self) -> None:
        if not self._buf_rows or self._conn is None or not self._table_created:
            return
        try:
            placeholders = ", ".join("?" for _ in self._columns)
            col_names = ", ".join(f'"{c}"' for c in self._columns)
            self._conn.executemany(
                f"INSERT INTO data ({col_names}) VALUES ({placeholders})",
                self._buf_rows,
            )
            self._conn.commit()
        except Exception as e:
            try:
                print(f"[WARN] SqliteWriter flush failed: {e}")
            except Exception:
                pass
        finally:
            self._buf_rows.clear()
            self._last_commit_ts = time.monotonic()

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
        """Flush remaining buffer and close the database."""
        self._close_segment()
