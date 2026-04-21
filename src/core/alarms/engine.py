# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple


@dataclass
class TierConfig:
    low: Optional[float] = None
    high: Optional[float] = None
    low_enter_delay_s: float = 0.0
    low_clear_delay_s: float = 0.0
    high_enter_delay_s: float = 0.0
    high_clear_delay_s: float = 0.0
    action: str = "visible_alert"


@dataclass
class ChannelConfig:
    warning: TierConfig
    alarm: TierConfig
    enabling_condition: str = "always_enabled"  # always_enabled | engine_running | engine_run_time | test_time
    enable_threshold: float = 0.0
    shutdown_type: str = "hard"  # hard | soft


@dataclass
class ChannelAlarmState:
    state: str = "OK"  # OK | WARN | SHUT
    active_trigger: Optional[str] = None  # warn_low|warn_high|shut_low|shut_high
    last_change_ts: float = 0.0
    pending_target: Optional[str] = None
    pending_trigger: Optional[str] = None
    pending_since_ts: float = 0.0
    clear_since_ts: float = 0.0


class AlarmEngine:
    """
    Evaluate per-channel alarms with optional latching.
    Minimal implementation: time-based latching using provided tick timestamp.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self._cfg = dict(config or {})
        self._limits: Dict[str, ChannelConfig] = {}
        self._states: Dict[str, ChannelAlarmState] = {}
        self._test_start_ts: float = 0.0
        self._engine_running_since_ts: float = 0.0
        self._load_config(config)

    def _load_config(self, cfg: Dict[str, Any]) -> None:
        er = cfg.get("engine_running") or {}
        self._engine_speed_alias = str(er.get("source_alias", "")).strip()
        self._engine_rpm_threshold = float(er.get("rpm_threshold", 0.0) or 0.0)
        for item in cfg.get("channels", []) or []:
            if not isinstance(item, dict):
                continue
            alias = item.get("alias")
            if not alias:
                continue
            legacy_enter = float(item.get("enter_delay_s", item.get("latch_on_s", 0.0)) or 0.0)
            legacy_clear = float(item.get("clear_delay_s", item.get("unlatch_after_s", 0.0)) or 0.0)
            warn = item.get("warning") or {}
            alarm = item.get("alarm") or item.get("shutdown") or {}
            warn_cfg = TierConfig(
                low=self._opt_float(warn.get("low", item.get("low_warning"))),
                high=self._opt_float(warn.get("high", item.get("high_warning"))),
                low_enter_delay_s=float(warn.get("low_enter_delay_s", legacy_enter) or 0.0),
                low_clear_delay_s=float(warn.get("low_clear_delay_s", legacy_clear) or 0.0),
                high_enter_delay_s=float(warn.get("high_enter_delay_s", legacy_enter) or 0.0),
                high_clear_delay_s=float(warn.get("high_clear_delay_s", legacy_clear) or 0.0),
                action=str(warn.get("action", "visible_alert") or "visible_alert").strip().lower(),
            )
            alarm_cfg = TierConfig(
                low=self._opt_float(alarm.get("low", item.get("low_shutdown"))),
                high=self._opt_float(alarm.get("high", item.get("high_shutdown"))),
                low_enter_delay_s=float(alarm.get("low_enter_delay_s", legacy_enter) or 0.0),
                low_clear_delay_s=float(alarm.get("low_clear_delay_s", legacy_clear) or 0.0),
                high_enter_delay_s=float(alarm.get("high_enter_delay_s", legacy_enter) or 0.0),
                high_clear_delay_s=float(alarm.get("high_clear_delay_s", legacy_clear) or 0.0),
                action=str(alarm.get("action", "visible_alert_shutdown") or "visible_alert_shutdown").strip().lower(),
            )
            raw_stype = str(alarm.get("shutdown_type", item.get("shutdown_type", "hard")) or "hard").strip().lower()
            stype = raw_stype if raw_stype in {"hard", "soft"} else "hard"
            self._limits[str(alias)] = ChannelConfig(
                warning=warn_cfg,
                alarm=alarm_cfg,
                enabling_condition=str(item.get("enabling_condition", "always_enabled") or "always_enabled").strip().lower(),
                enable_threshold=float(item.get("enable_threshold", 0.0) or 0.0),
                shutdown_type=stype,
            )
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
        any_shutdown_request = False
        any_soft_shutdown = False
        any_hard_shutdown = False
        events: list[Dict[str, Any]] = []
        if self._test_start_ts <= 0.0:
            self._test_start_ts = now_ts

        engine_running = self._is_engine_running(values)
        if engine_running:
            if self._engine_running_since_ts <= 0.0:
                self._engine_running_since_ts = now_ts
        else:
            self._engine_running_since_ts = 0.0

        for alias, limits in self._limits.items():
            val = values.get(alias)
            current = self._states.get(alias) or ChannelAlarmState()
            is_enabled = self._is_row_enabled(limits, now_ts, engine_running)
            if not is_enabled:
                classified, trig_key = ("OK", None)
            else:
                classified, trig_key = self._classify(val, limits)
            new_state = current.state

            if classified == "OK":
                # Start or continue clear debounce if we are not already OK
                current.pending_target = None
                current.pending_trigger = None
                current.pending_since_ts = 0.0
                if current.state != "OK":
                    if current.clear_since_ts == 0.0:
                        current.clear_since_ts = now_ts
                    clear_delay = self._clear_delay_for_trigger(limits, current.active_trigger)
                    if (now_ts - current.clear_since_ts) >= clear_delay:
                        new_state = "OK"
                        events.append({
                            "alias": alias,
                            "from": current.state,
                            "to": new_state,
                            "ts": now_ts,
                            "value": val,
                        })
                        current.clear_since_ts = 0.0
                        current.active_trigger = None
                        current.last_change_ts = now_ts
                else:
                    # Already OK, keep clear timer reset
                    current.clear_since_ts = 0.0
            else:
                # Non-OK classification (WARN or SHUT) with enter debounce
                current.clear_since_ts = 0.0
                if current.state == classified and current.active_trigger == trig_key:
                    # Stable in same non-OK state
                    current.pending_target = None
                    current.pending_trigger = None
                    current.pending_since_ts = 0.0
                else:
                    # Begin or continue timing towards classified state
                    if current.pending_target != classified or current.pending_trigger != trig_key:
                        current.pending_target = classified
                        current.pending_trigger = trig_key
                        current.pending_since_ts = now_ts
                    # Transition immediately if delay is 0 or elapsed exceeds delay
                    enter_delay = self._enter_delay_for_trigger(limits, trig_key)
                    if (now_ts - current.pending_since_ts) >= enter_delay:
                        new_state = classified
                        events.append({
                            "alias": alias,
                            "from": current.state,
                            "to": new_state,
                            "ts": now_ts,
                            "value": val,
                        })
                        current.pending_target = None
                        current.pending_trigger = None
                        current.pending_since_ts = 0.0
                        current.active_trigger = trig_key
                        current.last_change_ts = now_ts

            # Apply state
            current.state = new_state
            self._states[alias] = current
            per_state[alias] = current.state
            any_warn = any_warn or (current.state == "WARN")
            any_shut = any_shut or (current.state == "SHUT")
            if current.state in {"WARN", "SHUT"}:
                action = self._action_for_state(limits, current.state)
                if action == "visible_alert_shutdown":
                    any_shutdown_request = True
            if current.state == "SHUT":
                if limits.shutdown_type == "soft":
                    any_soft_shutdown = True
                else:
                    any_hard_shutdown = True

        summary = {
            "any_warning": any_warn,
            "any_shutdown": any_shut,
            "any_shutdown_request": any_shutdown_request,
            "any_soft_shutdown": any_soft_shutdown,
            "any_hard_shutdown": any_hard_shutdown,
            "engine_running": engine_running,
        }
        return per_state, summary, events

    def _is_engine_running(self, values: Dict[str, Any]) -> bool:
        if not self._engine_speed_alias:
            return False
        try:
            rpm = float(values.get(self._engine_speed_alias, 0.0))
        except Exception:
            rpm = 0.0
        return rpm > float(self._engine_rpm_threshold)

    def _is_row_enabled(self, cfg: ChannelConfig, now_ts: float, engine_running: bool) -> bool:
        cond = str(cfg.enabling_condition or "always_enabled").strip().lower()
        if cond in {"always_enabled", "always enabled"}:
            return True
        if cond in {"engine_running", "engine running"}:
            return engine_running
        if cond in {"engine_run_time", "engine run time"}:
            if self._engine_running_since_ts <= 0.0:
                return False
            return (now_ts - self._engine_running_since_ts) >= max(0.0, float(cfg.enable_threshold))
        if cond in {"test_time", "test time"}:
            if self._test_start_ts <= 0.0:
                return False
            return (now_ts - self._test_start_ts) >= max(0.0, float(cfg.enable_threshold))
        return True

    def _classify(self, val: Any, limits: ChannelConfig) -> tuple[str, Optional[str]]:
        try:
            fval = float(val)
        except Exception:
            return ("OK", None)
        # Tier 2 alarm has priority over tier 1 warning.
        if limits.alarm.high is not None and fval >= limits.alarm.high:
            return ("SHUT", "shut_high")
        if limits.alarm.low is not None and fval <= limits.alarm.low:
            return ("SHUT", "shut_low")
        if limits.warning.high is not None and fval >= limits.warning.high:
            return ("WARN", "warn_high")
        if limits.warning.low is not None and fval <= limits.warning.low:
            return ("WARN", "warn_low")
        return ("OK", None)

    def _enter_delay_for_trigger(self, limits: ChannelConfig, trig: Optional[str]) -> float:
        if trig == "warn_high":
            return max(0.0, float(limits.warning.high_enter_delay_s))
        if trig == "warn_low":
            return max(0.0, float(limits.warning.low_enter_delay_s))
        if trig == "shut_high":
            return max(0.0, float(limits.alarm.high_enter_delay_s))
        if trig == "shut_low":
            return max(0.0, float(limits.alarm.low_enter_delay_s))
        return 0.0

    def _clear_delay_for_trigger(self, limits: ChannelConfig, trig: Optional[str]) -> float:
        if trig == "warn_high":
            return max(0.0, float(limits.warning.high_clear_delay_s))
        if trig == "warn_low":
            return max(0.0, float(limits.warning.low_clear_delay_s))
        if trig == "shut_high":
            return max(0.0, float(limits.alarm.high_clear_delay_s))
        if trig == "shut_low":
            return max(0.0, float(limits.alarm.low_clear_delay_s))
        return 0.0

    def _action_for_state(self, limits: ChannelConfig, state: str) -> str:
        if state == "SHUT":
            return str(limits.alarm.action or "visible_alert_shutdown").strip().lower()
        if state == "WARN":
            return str(limits.warning.action or "visible_alert").strip().lower()
        return "visible_alert"


