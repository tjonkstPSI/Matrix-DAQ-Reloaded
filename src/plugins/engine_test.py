# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Set

from .base import BasePlugin, PluginStatus

# Ordered lists match `lock_dialog.py` combo boxes for stable numeric codes.
_ENGINE_TYPES_ORDERED = (
    "",
    "0.97L",
    "0.998L",
    "2L",
    "2.4L",
    "3L",
    "4X",
    "5.7L",
    "6L",
    "8.8L",
    "11L",
    "14L",
    "17L",
    "20L",
    "22L",
    "32L",
    "40L",
    "53L",
    "65L",
    "88L",
    "110L",
)

_TEST_TYPES_ORDERED = (
    "",
    "Air-To-Boil Testing",
    "BSFC Mapping",
    "Camshaft Testing",
    "Engine Health Check",
    "Engine Map",
    "Engine Start Testing",
    "Heat Rejection",
    "Load Step Testing",
    "Other Testing",
    "Spark Sweep",
    "Standard Break-In",
    "Steady State Full Load",
    "Torque Curve",
    "Vibration Testing",
)

ENGINE_TYPE_CODE: Dict[str, float] = {v: float(i) for i, v in enumerate(_ENGINE_TYPES_ORDERED)}
TEST_TYPE_CODE: Dict[str, float] = {v: float(i) for i, v in enumerate(_TEST_TYPES_ORDERED)}


class EngineTestPlugin(BasePlugin):
    """Engine test session metadata and Lock / Start / Stop lifecycle.

    State machine: unlocked -> locked -> running -> stopped -> unlocked (reset).

    - ``lock_session`` (UI lock, YAML saved): unlocked -> locked
    - ``start`` (recording begins): locked -> running
    - ``stop`` (recording ends): running -> stopped
    - ``unlock_session`` (UI after stop): locked or stopped -> unlocked
    """

    id = "EngineTest"

    MANDATORY_FIELDS = ("engine_type", "engine_serial_number", "test_type")

    def __init__(self, configs_dir, config_name: str) -> None:
        super().__init__(configs_dir, config_name)
        self._lock = threading.Lock()
        self._phase: str = "unlocked"
        self._t_start: float | None = None
        self._t_end: float | None = None
        self._frozen_elapsed_s: float = 0.0
        self._required_snapshot: Dict[str, str] = {}

    def configure(self) -> None:
        req = self.config.get("required_fields") or {}
        snap: Dict[str, str] = {}
        if isinstance(req, dict):
            for k in self.MANDATORY_FIELDS:
                try:
                    snap[k] = str(req.get(k, "")).strip()
                except Exception:
                    snap[k] = ""
        self._required_snapshot = snap

    def _field_invalid(self, raw: str) -> bool:
        s = str(raw).strip()
        if not s:
            return True
        return s.lower() == "unknown"

    def validate(self) -> PluginStatus:
        self.configure()
        req = self.config.get("required_fields") or {}
        if not isinstance(req, dict):
            return PluginStatus(ok=False, message="EngineTest: required_fields missing or invalid in config")
        for key in self.MANDATORY_FIELDS:
            try:
                val = req.get(key, "")
            except Exception:
                val = ""
            if self._field_invalid(val):
                return PluginStatus(ok=False, message=f"EngineTest: '{key}' is empty or unknown")
        return PluginStatus(ok=True)

    def phase(self) -> str:
        with self._lock:
            return self._phase

    def lock_session(self) -> PluginStatus:
        """Reload YAML, validate, then transition unlocked -> locked (or refresh locked session)."""
        self.load_config()
        self.configure()
        st = self.validate()
        if not st.ok:
            with self._lock:
                self._phase = "unlocked"
                self._clear_timers_unlocked()
            return st
        with self._lock:
            if self._phase == "unlocked":
                self._phase = "locked"
                self._clear_timers_unlocked()
            elif self._phase == "stopped":
                self._phase = "locked"
                self._clear_timers_unlocked()
        return PluginStatus(ok=True)

    def unlock_session(self) -> None:
        """Transition stopped or locked -> unlocked (reset)."""
        with self._lock:
            if self._phase in ("locked", "stopped"):
                self._phase = "unlocked"
            self._clear_timers_unlocked()

    def _clear_timers_unlocked(self) -> None:
        self._t_start = None
        self._t_end = None
        self._frozen_elapsed_s = 0.0

    def start(self) -> None:
        """Recording started: locked -> running."""
        with self._lock:
            if self._phase != "locked":
                return
            self._phase = "running"
            self._t_start = time.time()
            self._t_end = None
            self._frozen_elapsed_s = 0.0

    def stop(self) -> None:
        """Recording stopped: running -> stopped."""
        with self._lock:
            if self._phase != "running":
                return
            now = time.time()
            self._t_end = now
            self._phase = "stopped"
            if self._t_start is not None:
                self._frozen_elapsed_s = max(0.0, now - self._t_start)
            else:
                self._frozen_elapsed_s = 0.0

    def aliases(self) -> Set[str]:
        return {
            "EngineTest/locked",
            "EngineTest/test_active",
            "EngineTest/test_time_s",
            "EngineTest/engine_type",
            "EngineTest/test_type",
        }

    def units(self) -> Dict[str, str]:
        return {
            "EngineTest/locked": "",
            "EngineTest/test_active": "",
            "EngineTest/test_time_s": "s",
            "EngineTest/engine_type": "code",
            "EngineTest/test_type": "code",
        }

    def _code_for_engine_type(self) -> float:
        key = self._required_snapshot.get("engine_type", "")
        return ENGINE_TYPE_CODE.get(key, 0.0)

    def _code_for_test_type(self) -> float:
        key = self._required_snapshot.get("test_type", "")
        return TEST_TYPE_CODE.get(key, 0.0)

    def simulate_step(self) -> Dict[str, Any]:
        with self._lock:
            phase = self._phase
            t_start = self._t_start
            frozen = self._frozen_elapsed_s
            eng_code = self._code_for_engine_type()
            tst_code = self._code_for_test_type()

        session_on = phase in ("locked", "running", "stopped")
        active = phase == "running"

        if phase == "running" and t_start is not None:
            test_time = max(0.0, time.time() - t_start)
        elif phase == "stopped":
            test_time = frozen
        else:
            test_time = 0.0

        return {
            "EngineTest/locked": 1.0 if session_on else 0.0,
            "EngineTest/test_active": 1.0 if active else 0.0,
            "EngineTest/test_time_s": float(test_time),
            "EngineTest/engine_type": float(eng_code),
            "EngineTest/test_type": float(tst_code),
        }
