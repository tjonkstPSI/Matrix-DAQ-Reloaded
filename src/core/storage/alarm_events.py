# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, List


class AlarmEventsSink:
    """
    Persist alarm events during a run as JSON Lines (one JSON object per line).
    The log is operator-friendly and includes only: ts_hms, alias, from, to, value.
    """

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.run_dir / "alarm_events.jsonl"
        self._jsonl_opened = False

    def _ensure_jsonl(self) -> None:
        if not self._jsonl_opened:
            # Touch file by opening and closing once to ensure existence
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            self.jsonl_path.touch(exist_ok=True)
            self._jsonl_opened = True

    @staticmethod
    def _enrich(ev: Dict[str, Any]) -> Dict[str, Any]:
        # Ensure human-friendly local time is present; omit epoch/iso fields
        def _format_local_hms(epoch_seconds: float) -> str:
            try:
                import time as _t
                whole = int(epoch_seconds)
                frac_ms = int(round((epoch_seconds - whole) * 1000.0))
                if frac_ms >= 1000:
                    whole += 1
                    frac_ms = 0
                hhmmss = _t.strftime("%H:%M:%S", _t.localtime(whole))
                return f"{hhmmss}.{frac_ms:03d}"
            except Exception:
                return "00:00:00.000"

        ts_hms = ev.get("ts_hms")
        if not ts_hms:
            try:
                ts_val = float(ev.get("ts", 0.0))
            except Exception:
                ts_val = 0.0
            ts_hms = _format_local_hms(ts_val)

        ordered = {
            "ts_hms": ts_hms,
            "alias": ev.get("alias", ""),
            "from": ev.get("from", ""),
            "to": ev.get("to", ""),
            "value": ev.get("value", ""),
        }
        return ordered

    def append_many(self, events: List[Dict[str, Any]]) -> None:
        if not events:
            return
        self._ensure_jsonl()
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            for ev in events:
                enriched = self._enrich(ev)
                # JSON Lines for append-only durability
                try:
                    import json as _json
                    f.write(_json.dumps(enriched) + "\n")
                except Exception:
                    # Best-effort; skip malformed event
                    continue

    def finalize(self) -> None:
        # No-op for now; exports (CSV/XLSX) are deferred until recording/export pipeline is ready
        return


