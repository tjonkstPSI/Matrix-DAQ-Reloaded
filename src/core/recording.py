# Author: T. Onkst | Date: 03092026

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, TYPE_CHECKING

from .storage.parquet_writer import ParquetWriter, ParquetWriterSettings
from .storage.alarm_events import AlarmEventsSink
from .storage.stats_snapshots import StatsSnapshotsSink

if TYPE_CHECKING:
    from .orchestrator import Orchestrator


def begin_recording(orch: Orchestrator) -> None:
    if orch._recording:
        print("[INFO] Recording already active")
        return
    try:
        import time as _t
        import yaml as _yaml  # type: ignore

        mmddyy = _t.strftime("%m%d%y")
        hhmmss = _t.strftime("%H%M%S")
        test_cell = "unknown"
        try:
            plug_cfg_path = (orch.configs_dir / "plugins.yaml").resolve()
            plug_cfg = _yaml.safe_load(plug_cfg_path.read_text(encoding="utf-8")) or {}
            test_cell = str(plug_cfg.get("test_cell", "unknown")).strip() or "unknown"
        except Exception:
            pass
        engine_type = "unknown"; engine_sn = "unknown"; test_type = "unknown"
        try:
            et_path = (orch.configs_dir / "engine_test.yaml").resolve()
            et_cfg = _yaml.safe_load(et_path.read_text(encoding="utf-8")) or {}
            req = et_cfg.get("required_fields") or {}
            engine_type = str(req.get("engine_type", "unknown")).strip() or "unknown"
            engine_sn = str(req.get("engine_serial_number", "unknown")).strip() or "unknown"
            test_type = str(req.get("test_type", "unknown")).strip() or "unknown"
        except Exception:
            pass

        def _sanitize(part: str) -> str:
            try:
                ok = []
                for ch in str(part):
                    if ch.isalnum() or ch in (" ", "_", "-", "."):
                        ok.append(ch)
                s = ("".join(ok)).strip()
                return s if s else "unknown"
            except Exception:
                return "unknown"

        run_name = f"{_sanitize(test_cell)}_{mmddyy}_{hhmmss}_{_sanitize(engine_type)}_{_sanitize(engine_sn)}_{_sanitize(test_type)}"
        run_dir = (orch.configs_dir.parent / f"runs/{run_name}").resolve()
        orch._run_dir = run_dir
        orch._last_run_dir = run_dir
        orch._events_sink = AlarmEventsSink(run_dir)
        orch._stats_sink = StatsSnapshotsSink(run_dir)
        settings = build_parquet_settings(orch.channel_cfg)
        orch._parquet = ParquetWriter(run_dir, settings)
        try:
            orch._parquet.snapshot_configs(orch.configs_dir)
        except Exception:
            pass
        meta = {
            "run_id": run_name,
            "run_start_iso8601": _t.strftime("%Y-%m-%dT%H:%M:%S", _t.localtime()),
            "recording_rate_hz": float(orch.channel_cfg.get("recording_rate_hz", orch.settings.recording_rate_hz)),
            "plugins": sorted(list(orch.plugins.keys())),
            "test_cell": test_cell,
            "engine_type": engine_type,
            "engine_serial_number": engine_sn,
            "test_type": test_type,
        }
        try:
            (run_dir / "metadata.yaml").write_text(_yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
        except Exception:
            pass
        orch._recording = True
        print(f"[INFO] Started recording: {str(run_dir)}")
    except Exception as e:
        print(f"[WARN] Failed to start recording: {e}")


def end_recording(orch: Orchestrator) -> None:
    if not orch._recording:
        print("[INFO] Recording already stopped")
        return
    try:
        try:
            if orch._parquet is not None:
                orch._parquet._flush_chunk(orch._parquet._buf_second_key)  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            if orch._events_sink is not None:
                orch._events_sink.finalize()
        except Exception:
            pass
        print("[INFO] Recording stopped and files finalized")
    finally:
        orch._recording = False
        pw = orch._parquet
        orch._parquet = None
        orch._events_sink = None
        orch._stats_sink = None
        orch._run_dir = None
        if pw is not None:
            run_dir = pw.run_dir

            def _on_progress(pct: float, detail: str) -> None:
                try:
                    import json
                    msg = json.dumps({"type": "merge_progress", "run": str(run_dir), "percent": float(pct), "detail": detail}).encode("utf-8")
                    orch.bus.publish_status(msg)
                except Exception:
                    pass

            def _on_done(ok: bool, error: str | None) -> None:
                try:
                    import json
                    msg = json.dumps({"type": "merge_done", "run": str(run_dir), "ok": bool(ok), "error": error}).encode("utf-8")
                    orch.bus.publish_status(msg)
                except Exception:
                    pass
                # Auto-kickoff Excel export once the Parquet merge finished cleanly.
                # Any failure is surfaced via the export_done status message; we do
                # not block the orchestrator or bubble exceptions back to merge.
                if ok:
                    try:
                        kickoff_export(orch)
                    except Exception as ex:
                        try:
                            print(f"[WARN] Auto Excel export failed to start: {ex}")
                        except Exception:
                            pass

            try:
                pw.merge_async(_on_progress, _on_done)
                print("[INFO] Started parquet merge in background")
            except Exception as e:
                print(f"[WARN] Failed to start background merge: {e}")


def kickoff_export(orch: Orchestrator) -> None:
    if orch._export_in_progress:
        try:
            print("[INFO] Export already in progress; ignoring duplicate request")
        except Exception:
            pass
        return
    if orch._recording:
        try:
            print("[WARN] Cannot export while recording is active. Stop recording first.")
        except Exception:
            pass
        return
    run_dir = orch._run_dir or orch._last_run_dir
    if run_dir is None:
        try:
            print("[WARN] Cannot export: run directory not initialized")
        except Exception:
            pass
        return
    orch._export_in_progress = True

    def _publish(payload: Dict[str, Any]) -> None:
        try:
            import json
            orch.bus.publish_status(json.dumps(payload).encode("utf-8"))
        except Exception:
            pass

    def _worker() -> None:
        outputs: list = []
        ok = False
        err: str | None = None
        try:
            _publish({"type": "export_progress", "run": str(run_dir), "stage": "started"})
            import importlib
            mod = importlib.import_module("src.tools.export_excel")
            outputs = mod.export_excel(run_dir, output_dir=(run_dir / "data"))
            ok = True
            try:
                print("[INFO] Excel export completed:")
                for p in outputs:
                    print(f"  - {p}")
            except Exception:
                pass
        except Exception as e:
            err = str(e)
            try:
                print(f"[WARN] Excel export failed: {e}")
            except Exception:
                pass
        finally:
            orch._export_in_progress = False
            _publish({
                "type": "export_done",
                "run": str(run_dir),
                "ok": bool(ok),
                "error": err,
                "files": [str(p) for p in outputs] if outputs else [],
            })

    try:
        import threading
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        print("[INFO] Started Excel export in background")
    except Exception as e:
        orch._export_in_progress = False
        try:
            print(f"[WARN] Failed to start export thread: {e}")
        except Exception:
            pass


def build_parquet_settings(channel_cfg: Dict[str, Any]) -> ParquetWriterSettings:
    cfg = channel_cfg or {}
    storage_cfg = cfg.get("storage", {}) or {}
    defaults = ParquetWriterSettings()

    def _get_float(key: str, default: float) -> float:
        try:
            v = storage_cfg.get(key)
            return float(v) if v is not None else default
        except Exception:
            return default

    def _get_bool(key: str, default: bool) -> bool:
        try:
            v = storage_cfg.get(key)
            return bool(v) if v is not None else default
        except Exception:
            return default

    return ParquetWriterSettings(
        chunk_duration_s=_get_float("chunk_duration_s", defaults.chunk_duration_s),
        segment_time_limit_s=_get_float("segment_time_limit_s", defaults.segment_time_limit_s),
        segment_size_limit_mb=_get_float("segment_size_limit_mb", defaults.segment_size_limit_mb),
        coalesce_on_finalize=_get_bool("coalesce_on_finalize", defaults.coalesce_on_finalize),
        keep_chunk_files=_get_bool("keep_chunk_files", defaults.keep_chunk_files),
    )
