# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from typing import Dict, Any, List
from pathlib import Path

from .base import BasePlugin, PluginStatus


class CyclePlugin(BasePlugin):
    id = "Cycle"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._schedule: List[tuple[float, float]] = []  # (time_s, load_kw)
        self._t0: float = 0.0
        self._loop_len: float = 0.0
        self._running: bool = False
        self._loops_total: int = 1
        self._completed: bool = False

    def configure(self) -> None:
        src = (self.config.get("source") or {})
        csv_path = src.get("csv_path")
        if csv_path:
            p = Path(csv_path)
            candidates = [p, (self.configs_dir / p).resolve(), (self.configs_dir.parent / p).resolve()]
            for c in candidates:
                if c.exists():
                    self._schedule = self._read_csv(c)
                    break
        # compute loop length
        if self._schedule:
            self._loop_len = max(t for t, _ in self._schedule)
        exec_cfg = (self.config.get("execution") or {})
        self._loops_total = int(exec_cfg.get("loops_total", 1))
        if self._loops_total < 1:
            self._loops_total = 1

    def validate(self) -> PluginStatus:
        if not isinstance(self.config.get("source", {}), dict):
            return PluginStatus(ok=False, message="cycle source block required")
        return PluginStatus(ok=True)

    def start(self) -> None:
        import time
        self._t0 = time.time()
        self._running = True
        self._completed = False

    def stop(self) -> None:
        self._running = False

    def current_setpoint_kw(self) -> float:
        if not self._running or not self._schedule:
            return 0.0
        import time
        elapsed = time.time() - self._t0
        # Total duration across loops
        total_duration = self._loop_len * max(self._loops_total, 1)
        if self._loop_len <= 0:
            pos = 0.0
        else:
            if elapsed > total_duration and self._loops_total >= 1:
                # Completed all loops; hold last step value and mark complete
                self._completed = True
                pos = self._loop_len
            else:
                # Position within current loop
                pos = (elapsed % self._loop_len) if self._loops_total > 1 else min(elapsed, self._loop_len)
        last_val = 0.0
        for t, v in self._schedule:
            if pos >= t:
                last_val = v
            else:
                break
        return last_val

    def is_complete(self) -> bool:
        return self._completed

    @staticmethod
    def _read_csv(path: Path) -> List[tuple[float, float]]:
        rows: List[tuple[float, float]] = []
        import csv
        with path.open("r", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if not row or row[0].startswith("#"):
                    continue
                try:
                    t = float(row[0])
                    v = float(row[1])
                    rows.append((t, v))
                except Exception:
                    continue
        rows.sort(key=lambda x: x[0])
        return rows


