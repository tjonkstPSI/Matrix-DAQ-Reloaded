# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple


@dataclass
class LimitConfig:
    high_warning: Optional[float] = None
    low_warning: Optional[float] = None
    high_shutdown: Optional[float] = None
    low_shutdown: Optional[float] = None
    # Debounce semantics
    enter_delay_s: float = 0.0  # time to sustain non-OK before entering WARN/SHUT
    clear_delay_s: float = 0.0  # time to sustain OK before clearing back to OK


@dataclass
class ChannelAlarmState:
    state: str = "OK"  # OK | WARN | SHUT
    last_change_ts: float = 0.0
    # Debounce bookkeeping
    pending_target: Optional[str] = None
    pending_since_ts: float = 0.0
    clear_since_ts: float = 0.0


class AlarmEngine:
    """
    Evaluate per-channel alarms with optional latching.
    Minimal implementation: time-based latching using provided tick timestamp.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        # Config structure example:
        # channels:
        #   - alias: "Room Temp"
        #     high_warning: 28
        #     high_shutdown: 30
        #     latch_on_s: 0.0
        #     unlatch_after_s: 0.0
        self._limits: Dict[str, LimitConfig] = {}
        self._states: Dict[str, ChannelAlarmState] = {}
        self._load_config(config)

    def _load_config(self, cfg: Dict[str, Any]) -> None:
        for item in cfg.get("channels", []) or []:
            if not isinstance(item, dict):
                continue
            alias = item.get("alias")
            if not alias:
                continue
            lc = LimitConfig(
                high_warning=self._opt_float(item.get("high_warning")),
                low_warning=self._opt_float(item.get("low_warning")),
                high_shutdown=self._opt_float(item.get("high_shutdown")),
                low_shutdown=self._opt_float(item.get("low_shutdown")),
                # Support new explicit debounce keys, fallback to legacy names
                enter_delay_s=float(item.get("enter_delay_s", item.get("latch_on_s", 0.0))),
                clear_delay_s=float(item.get("clear_delay_s", item.get("unlatch_after_s", 0.0))),
            )
            self._limits[str(alias)] = lc
            if str(alias) not in self._states:
                self._states[str(alias)] = ChannelAlarmState()

    @staticmethod
    def _opt_float(v: Any) -> Optional[float]:
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    def evaluate(self, values: Dict[str, Any], now_ts: float) -> Tuple[Dict[str, str], Dict[str, bool], list[Dict[str, Any]]]:
        """
        Evaluate alarms for this tick using explicit debounce semantics.

        Returns:
          - per_alias_state: {alias: "OK"|"WARN"|"SHUT"}
          - summary: {any_warning: bool, any_shutdown: bool}
          - events: list of event dicts for transitions
        """
        per_state: Dict[str, str] = {}
        any_warn = False
        any_shut = False
        events: list[Dict[str, Any]] = []

        for alias, limits in self._limits.items():
            val = values.get(alias)
            current = self._states.get(alias) or ChannelAlarmState()
            classified = self._classify(val, limits)
            new_state = current.state

            if classified == "OK":
                # Start or continue clear debounce if we are not already OK
                current.pending_target = None
                current.pending_since_ts = 0.0
                if current.state != "OK":
                    if current.clear_since_ts == 0.0:
                        current.clear_since_ts = now_ts
                    if (now_ts - current.clear_since_ts) >= max(0.0, limits.clear_delay_s):
                        new_state = "OK"
                        events.append({
                            "alias": alias,
                            "from": current.state,
                            "to": new_state,
                            "ts": now_ts,
                            "value": val,
                        })
                        current.clear_since_ts = 0.0
                        current.last_change_ts = now_ts
                else:
                    # Already OK, keep clear timer reset
                    current.clear_since_ts = 0.0
            else:
                # Non-OK classification (WARN or SHUT) with enter debounce
                current.clear_since_ts = 0.0
                if current.state == classified:
                    # Stable in same non-OK state
                    current.pending_target = None
                    current.pending_since_ts = 0.0
                else:
                    # Begin or continue timing towards classified state
                    if current.pending_target != classified:
                        current.pending_target = classified
                        current.pending_since_ts = now_ts
                    # Transition immediately if delay is 0 or elapsed exceeds delay
                    if (now_ts - current.pending_since_ts) >= max(0.0, limits.enter_delay_s):
                        new_state = classified
                        events.append({
                            "alias": alias,
                            "from": current.state,
                            "to": new_state,
                            "ts": now_ts,
                            "value": val,
                        })
                        current.pending_target = None
                        current.pending_since_ts = 0.0
                        current.last_change_ts = now_ts

            # Apply state
            current.state = new_state
            self._states[alias] = current
            per_state[alias] = current.state
            any_warn = any_warn or (current.state == "WARN")
            any_shut = any_shut or (current.state == "SHUT")

        summary = {"any_warning": any_warn, "any_shutdown": any_shut}
        return per_state, summary, events

    @staticmethod
    def _classify(val: Any, limits: LimitConfig) -> str:
        try:
            fval = float(val)
        except Exception:
            return "OK"
        # Shutdown has priority over warning
        if limits.high_shutdown is not None and fval >= limits.high_shutdown:
            return "SHUT"
        if limits.low_shutdown is not None and fval <= limits.low_shutdown:
            return "SHUT"
        if limits.high_warning is not None and fval >= limits.high_warning:
            return "WARN"
        if limits.low_warning is not None and fval <= limits.low_warning:
            return "WARN"
        return "OK"


