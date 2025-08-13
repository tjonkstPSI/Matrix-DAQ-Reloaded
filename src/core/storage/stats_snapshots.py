# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any


class StatsSnapshotsSink:
    """
    Append-only JSON Lines writer for statistics snapshots.
    Each snapshot is a single JSON object (one line) with fields:
      - ts_hms: local time HH:MM:SS.fff
      - <metric columns>: flattened keys like "Room Temp_mean": value
    """

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.run_dir / "stats_snapshots.jsonl"
        self._opened = False

    def _ensure(self) -> None:
        if not self._opened:
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            self.jsonl_path.touch(exist_ok=True)
            self._opened = True

    def append_snapshot(self, record: Dict[str, Any]) -> None:
        self._ensure()
        try:
            import json as _json
            with self.jsonl_path.open("a", encoding="utf-8") as f:
                f.write(_json.dumps(record) + "\n")
        except Exception:
            # best-effort
            pass


