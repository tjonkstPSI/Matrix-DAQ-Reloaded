# Author: T. Onkst | Date: 04212026

from __future__ import annotations

import time
import csv
from typing import Any, Dict, List, Set
from pathlib import Path

from .base import BasePlugin, PluginStatus

_STATE_IDLE = "idle"
_STATE_RUNNING = "running"
_STATE_PAUSED = "paused"
_STATE_COMPLETE = "complete"

_STATE_INT = {_STATE_IDLE: 0, _STATE_RUNNING: 1, _STATE_PAUSED: 2, _STATE_COMPLETE: 3}


class CyclePlugin(BasePlugin):
    id = "Cycle"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._schedule: List[tuple[float, float]] = []
        self._t0: float = 0.0
        self._loop_len: float = 0.0
        self._loops_total: int = 1
        self._state: str = _STATE_IDLE
        self._paused: bool = False
        self._pause_elapsed: float = 0.0
        self._last_setpoint: float = 0.0
        self._start_with_test: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def configure(self) -> None:
        src = self.config.get("source") or {}
        csv_path = src.get("csv_path")
        if csv_path:
            p = Path(csv_path)
            candidates = [p, (self.configs_dir / p).resolve(), (self.configs_dir.parent / p).resolve()]
            for c in candidates:
                if c.exists():
                    self._schedule = self._read_csv(c)
                    break
        if self._schedule:
            self._loop_len = max(t for t, _ in self._schedule)
        exec_cfg = self.config.get("execution") or {}
        self._loops_total = max(1, int(exec_cfg.get("loops_total", 1)))
        self._start_with_test = bool(exec_cfg.get("start_with_test", False))

    def validate(self) -> PluginStatus:
        if not isinstance(self.config.get("source", {}), dict):
            return PluginStatus(ok=False, message="cycle source block required")
        return PluginStatus(ok=True)

    def start(self) -> None:
        self._state = _STATE_IDLE
        self._paused = False
        self._pause_elapsed = 0.0
        self._last_setpoint = 0.0
        self._t0 = 0.0

    def stop(self) -> None:
        self._state = _STATE_IDLE
        self._paused = False

    def aliases(self) -> Set[str]:
        return {
            "Cycle/state", "Cycle/position_s", "Cycle/setpoint_kw",
            "Cycle/loop_current", "Cycle/loop_total", "Cycle/progress_pct",
            "Cycle/schedule_len_s",
        }

    def units(self) -> Dict[str, str]:
        return {
            "Cycle/state": "",
            "Cycle/position_s": "s",
            "Cycle/setpoint_kw": "kW",
            "Cycle/loop_current": "",
            "Cycle/loop_total": "",
            "Cycle/progress_pct": "%",
            "Cycle/schedule_len_s": "s",
        }

    # ------------------------------------------------------------------
    # Play / Pause / Seek / Loops
    # ------------------------------------------------------------------

    def play(self) -> None:
        """Start or resume the cycle. If complete, restart from the beginning."""
        if self._state == _STATE_COMPLETE:
            self._t0 = time.time()
            self._paused = False
            self._pause_elapsed = 0.0
            self._last_setpoint = 0.0
            self._state = _STATE_RUNNING
            return
        if self._state == _STATE_PAUSED:
            self._t0 = time.time() - self._pause_elapsed
            self._paused = False
            self._state = _STATE_RUNNING
        elif self._state == _STATE_IDLE:
            self._t0 = time.time()
            self._paused = False
            self._pause_elapsed = 0.0
            self._last_setpoint = 0.0
            self._state = _STATE_RUNNING

    def pause(self) -> None:
        """Freeze the cycle at its current position."""
        if self._state != _STATE_RUNNING:
            return
        self._pause_elapsed = time.time() - self._t0
        self._paused = True
        self._state = _STATE_PAUSED

    def seek(self, time_s: float) -> None:
        """Jump to a specific time in the schedule (only when paused)."""
        if self._state != _STATE_PAUSED:
            return
        total_dur = self._loop_len * max(self._loops_total, 1)
        self._pause_elapsed = max(0.0, min(float(time_s), total_dur))

    def set_loops(self, n: int) -> None:
        """Update the total loop count at runtime."""
        self._loops_total = max(1, int(n))
        if self._state == _STATE_COMPLETE:
            elapsed = self._elapsed_s()
            total_dur = self._loop_len * self._loops_total
            if elapsed < total_dur:
                self._state = _STATE_PAUSED
                self._paused = True
                self._pause_elapsed = elapsed

    def set_start_with_test(self, enabled: bool) -> None:
        self._start_with_test = bool(enabled)

    def is_ready(self) -> bool:
        return bool(self._schedule) and self._state != _STATE_COMPLETE

    def is_complete(self) -> bool:
        return self._state == _STATE_COMPLETE

    @property
    def start_with_test(self) -> bool:
        return self._start_with_test

    @property
    def schedule(self) -> List[tuple[float, float]]:
        return list(self._schedule)

    # ------------------------------------------------------------------
    # Setpoint evaluation
    # ------------------------------------------------------------------

    def current_setpoint_kw(self) -> float:
        if self._state == _STATE_PAUSED:
            return self._last_setpoint
        if self._state != _STATE_RUNNING or not self._schedule:
            return self._last_setpoint
        pos = self._current_loop_pos()
        val = self._interp_schedule(pos)
        self._last_setpoint = val
        return val

    def _elapsed_s(self) -> float:
        if self._paused:
            return self._pause_elapsed
        if self._t0 <= 0.0:
            return 0.0
        return time.time() - self._t0

    def _current_loop_pos(self) -> float:
        """Position within the current loop (seconds)."""
        elapsed = self._elapsed_s()
        total_dur = self._loop_len * max(self._loops_total, 1)
        if self._loop_len <= 0:
            return 0.0
        if elapsed >= total_dur and self._loops_total >= 1:
            self._state = _STATE_COMPLETE
            return self._loop_len
        if self._loops_total > 1:
            return elapsed % self._loop_len
        return min(elapsed, self._loop_len)

    def _current_loop_number(self) -> int:
        """1-based loop index."""
        elapsed = self._elapsed_s()
        if self._loop_len <= 0:
            return 1
        return min(int(elapsed // self._loop_len) + 1, self._loops_total)

    def _interp_schedule(self, pos: float) -> float:
        last_val = 0.0
        for t, v in self._schedule:
            if pos >= t:
                last_val = v
            else:
                break
        return last_val

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------

    def simulate_step(self, _vals: Dict[str, Any] | None = None) -> Dict[str, Any]:
        elapsed = self._elapsed_s()
        pos = self._current_loop_pos() if self._state in (_STATE_RUNNING, _STATE_PAUSED) else 0.0
        sp = self.current_setpoint_kw()
        loop_cur = self._current_loop_number() if self._state in (_STATE_RUNNING, _STATE_PAUSED, _STATE_COMPLETE) else 0
        total_dur = self._loop_len * max(self._loops_total, 1)
        progress = min(100.0, (elapsed / total_dur * 100.0) if total_dur > 0 else 0.0)
        return {
            "Cycle/state": float(_STATE_INT.get(self._state, 0)),
            "Cycle/position_s": round(pos, 2),
            "Cycle/setpoint_kw": round(sp, 2),
            "Cycle/loop_current": float(loop_cur),
            "Cycle/loop_total": float(self._loops_total),
            "Cycle/progress_pct": round(progress, 1),
            "Cycle/schedule_len_s": round(self._loop_len, 2),
        }

    # ------------------------------------------------------------------
    # CSV loader
    # ------------------------------------------------------------------

    @staticmethod
    def _read_csv(path: Path) -> List[tuple[float, float]]:
        rows: List[tuple[float, float]] = []
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
