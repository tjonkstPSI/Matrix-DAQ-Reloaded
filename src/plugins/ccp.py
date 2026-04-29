# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import os
import sys
import time
import math
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .base import BasePlugin, PluginStatus
from ._ccp_a2l import (
    A2LChannel,
    A2LDaqList,
    parse_a2l,
    parse_a2l_daq_lists,
    dtype_size,
    decode_value,
    normalize_dto_can_id,
    _canonical_poll_tier,
    is_daq_tier,
)
from ._ccp_protocol import (
    nixnet,
    compute_key_from_seed_algo,
    CanFrame,
    CcpProto,
    NixnetSession,
)


_PRIORITY_HIGH = "high"
_PRIORITY_LOW = "low"
_DEFAULT_PRIORITY = "low"
_DEFAULT_HIGH_LOW_RATIO = 3
_DAQ_DTO_PAYLOAD_BYTES = 7


def _build_priority_sequence(high_low_ratio: int = _DEFAULT_HIGH_LOW_RATIO) -> list:
    """Build a priority sequence from a HIGH:LOW ratio (e.g. 3 means 3 HIGH per 1 LOW)."""
    n = max(1, int(high_low_ratio))
    return [_PRIORITY_HIGH] * n + [_PRIORITY_LOW]


class DAQConfigError(RuntimeError):
    """Raised for DAQ configuration problems that should never be caught by fallback."""
    pass


class CCPPlugin(BasePlugin):
    id = "CCP"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._theta = 0.0
        self._session: NixnetSession | None = None
        self._proto: CcpProto | None = None
        self._entries: List[Dict[str, Any]] = []
        self._values: Dict[str, float] = {}
        self._units: Dict[str, str] = {}
        self._units_cache_valid: bool = False
        self._value_ts: Dict[str, float] = {}
        self._last_poll_ts: float = 0.0
        self._poll_interval_s: float = 0.1
        self._poll_index: int = 0
        self._poll_channels_per_tick: int = 1
        self._io_timeout_s: float = 0.05
        self._rx_id: int = 0
        self._connected: bool = False
        self._last_connect_attempt_ts: float = 0.0
        self._reconnect_interval_s: float = 2.0
        self._worker_thread: threading.Thread | None = None
        self._worker_stop = threading.Event()
        self._state_lock = threading.Lock()
        self._snapshot_values: Dict[str, float] = {}
        self._contexts: List[Dict[str, Any]] = []
        self._freshness_sample_period_s: float = 0.1
        self._diag: Dict[str, Any] = {
            "state": "idle",
            "state_code": 0,
            "last_error": "",
            "connect_attempts": 0,
            "connect_ok": 0,
            "unlock_ok": 0,
            "poll_success": 0,
            "poll_fail": 0,
            "last_seed_status": -1,
            "last_rc": -1,
            "ctr_mismatch": 0,
            "fresh_age_s": -1.0,
            "fresh_max_channel_age_s": -1.0,
            "freshness_state_code": -1,
            "freshness_warn_count": 0,
            "freshness_stale_count": 0,
            "bus_load_pct": 0.0,
            "poll_rtt_avg_ms": 0.0,
            "high_priority_budget_pct": 0.0,
            "high_priority_over_budget": 0,
            "short_up_rtt_last_ms": 0.0,
            "short_up_rtt_min_ms": 0.0,
            "short_up_rtt_max_ms": 0.0,
            "short_up_timeout_count": 0,
            "crm_error_count": 0,
            "poll_selected_count": 0,
            "poll_loop_ms": 0.0,
            "attempted_reads_per_sec": 0.0,
            "successful_reads_per_sec": 0.0,
            "estimated_sweep_s": 0.0,
            "rx_read_calls": 0.0,
            "rx_empty_reads": 0.0,
            "rx_read_calls_per_response": 0.0,
            "rx_predrain_ms": 0.0,
            "rx_mode_code": 0,
            "daq_enabled": 0,
            "daq_running": 0,
            "daq_setup_ok": 0,
            "daq_dto_count": 0,
            "daq_dto_rate_hz": 0.0,
            "daq_odt_count": 0,
            "daq_decode_errors": 0,
            "daq_fallback_active": 0,
            "daq_last_pid": -1,
            "daq_last_dto_id": 0,
        }

    def _core_sample_rate_hz(self) -> float:
        try:
            hz = float(getattr(self, "_core_tick_rate_hz", 0.0))
            if hz > 0.0:
                return hz
        except Exception:
            pass
        try:
            hz = float(self.config.get("recording_rate_hz", 10.0))
            if hz > 0.0:
                return hz
        except Exception:
            pass
        return 10.0

    def _canonical_priority(self, value: Any = None, poll_tier: Any = None) -> str:
        raw = str(value or "").strip().lower().replace(" ", "")
        if not raw:
            raw = str(poll_tier or "").strip().lower().replace(" ", "")
        return _canonical_poll_tier(raw) if raw else _DEFAULT_PRIORITY

    @staticmethod
    def _poll_bucket(tier: str) -> str:
        canon = _canonical_poll_tier(tier)
        if canon == "low":
            return _PRIORITY_LOW
        return _PRIORITY_HIGH

    def _role_to_station_address(self, role: str) -> str:
        r = str(role or "").strip().lower()
        if r == "secondary":
            return "0x1"
        return "0x0"

    def _resolved_device_cfgs(self) -> List[Dict[str, Any]]:
        top_session = dict(self.config.get("session") or {})
        top_security = dict(self.config.get("security") or {})
        top_a2l = dict(self.config.get("a2l") or {})
        top_meas = dict(self.config.get("measurements") or {})
        top_poll_ms = self.config.get("poll_interval_ms", 100)
        top_cpt = self.config.get("poll_channels_per_tick", 1)
        top_target_hz = self.config.get("target_poll_hz", 10)
        top_hl_ratio = self.config.get("high_low_ratio", _DEFAULT_HIGH_LOW_RATIO)
        top_io = self.config.get("io_timeout_s", 0.05)
        top_reconn = self.config.get("reconnect_interval_s", 2.0)
        top_priority = self._canonical_priority(self.config.get("poll_default_priority") or self.config.get("poll_default_tier"))
        top_acq = dict(self.config.get("acquisition") or {})
        top_acq_mode = str(self.config.get("acquisition_mode") or top_acq.get("mode") or "short_up").strip().lower()
        top_fallback = bool(self.config.get("fallback_short_up", top_acq.get("fallback_short_up", False)))
        devices = self.config.get("devices")
        out: List[Dict[str, Any]] = []
        if isinstance(devices, list) and devices:
            for i, dev in enumerate(devices):
                if not isinstance(dev, dict):
                    continue
                role = str(dev.get("role") or ("secondary" if i == 1 else "primary")).strip().lower()
                name = str(dev.get("name") or f"CCP {role.title()}").strip()
                session = dict(top_session)
                session.update(dev.get("session") or {})
                if not str(session.get("station_address") or "").strip():
                    session["station_address"] = self._role_to_station_address(role)
                security = dict(top_security)
                security.update(dev.get("security") or {})
                a2l = dict(top_a2l)
                a2l.update(dev.get("a2l") or {})
                meas = dict(top_meas)
                meas.update(dev.get("measurements") or {})
                acq = dict(top_acq)
                acq.update(dev.get("acquisition") or {})
                acq_mode = str(dev.get("acquisition_mode") or acq.get("mode") or top_acq_mode or "short_up").strip().lower()
                out.append(
                    {
                        "device_index": i,
                        "name": name,
                        "role": role,
                        "session": session,
                        "security": security,
                        "a2l": a2l,
                        "measurements": meas,
                        "poll_interval_ms": dev.get("poll_interval_ms", top_poll_ms),
                        "poll_channels_per_tick": dev.get("poll_channels_per_tick", top_cpt),
                        "target_poll_hz": dev.get("target_poll_hz", top_target_hz),
                        "high_low_ratio": dev.get("high_low_ratio", top_hl_ratio),
                        "io_timeout_s": dev.get("io_timeout_s", top_io),
                        "reconnect_interval_s": dev.get("reconnect_interval_s", top_reconn),
                        "poll_default_priority": self._canonical_priority(
                            dev.get("poll_default_priority") or dev.get("poll_default_tier") or top_priority
                        ),
                        "acquisition_mode": "daq" if acq_mode in {"daq", "daq_stream", "stream"} else "short_up",
                        "fallback_short_up": bool(dev.get("fallback_short_up", acq.get("fallback_short_up", top_fallback))),
                        "acquisition": acq,
                    }
                )
            if out:
                return out
        role = "primary"
        top_session.setdefault("station_address", self._role_to_station_address(role))
        out.append(
            {
                "device_index": 0,
                "name": "CCP Primary",
                "role": role,
                "session": top_session,
                "security": top_security,
                "a2l": top_a2l,
                "measurements": top_meas,
                "poll_interval_ms": top_poll_ms,
                "poll_channels_per_tick": top_cpt,
                "target_poll_hz": top_target_hz,
                "high_low_ratio": top_hl_ratio,
                "io_timeout_s": top_io,
                "reconnect_interval_s": top_reconn,
                "poll_default_priority": top_priority,
                "acquisition_mode": "daq" if top_acq_mode in {"daq", "daq_stream", "stream"} else "short_up",
                "fallback_short_up": top_fallback,
                "acquisition": top_acq,
            }
        )
        return out

    def _final_aliases(self) -> List[str]:
        result: List[str] = []
        for dcfg in self._resolved_device_cfgs():
            meas = (dcfg.get("measurements") or {})
            prefix = str(meas.get("naming_prefix") or "")
            for item in meas.get("list", []) or []:
                if not isinstance(item, dict):
                    continue
                if not bool(item.get("enabled", True)):
                    continue
                name = item.get("name")
                if not name:
                    continue
                result.append(f"{prefix}{name}" if prefix else str(name))
        return result

    def configure(self) -> None:
        self._theta = 0.0
        self._entries = []
        self._contexts = []
        self._values = {}
        self._snapshot_values = {}
        self._units = self._build_units_map()
        self._units_cache_valid = True
        self._value_ts = {a: 0.0 for a in self._final_aliases()}
        min_poll_s = 1.0
        for dcfg in self._resolved_device_cfgs():
            try:
                poll_s = max(0.001, float(dcfg.get("poll_interval_ms", 100)) / 1000.0)
            except Exception:
                poll_s = 0.1
            min_poll_s = min(min_poll_s, poll_s)
            try:
                cpt = max(1, int(dcfg.get("poll_channels_per_tick", 1)))
            except Exception:
                cpt = 1
            try:
                io_to = max(0.01, float(dcfg.get("io_timeout_s", 0.05)))
            except Exception:
                io_to = 0.05
            try:
                reconn = max(0.5, float(dcfg.get("reconnect_interval_s", 2.0)))
            except Exception:
                reconn = 2.0
            self._contexts.append(
                {
                    "name": str(dcfg.get("name") or "CCP"),
                    "device_index": int(dcfg.get("device_index", len(self._contexts))),
                    "role": str(dcfg.get("role") or "primary"),
                    "session_cfg": dict(dcfg.get("session") or {}),
                    "security_cfg": dict(dcfg.get("security") or {}),
                    "a2l_cfg": dict(dcfg.get("a2l") or {}),
                    "meas_cfg": dict(dcfg.get("measurements") or {}),
                    "acquisition_cfg": dict(dcfg.get("acquisition") or {}),
                    "target_poll_hz": max(1, min(50, int(dcfg.get("target_poll_hz", 10)))),
                    "high_low_ratio": max(1, min(20, int(dcfg.get("high_low_ratio", _DEFAULT_HIGH_LOW_RATIO)))),
                    "priority_sequence": _build_priority_sequence(int(dcfg.get("high_low_ratio", _DEFAULT_HIGH_LOW_RATIO))),
                    "acquisition_mode": str(dcfg.get("acquisition_mode") or "short_up"),
                    "fallback_short_up": bool(dcfg.get("fallback_short_up", False)),
                    "poll_interval_s": poll_s,
                    "poll_channels_per_tick": cpt,
                    "io_timeout_s": io_to,
                    "short_up_timeout_s": min(io_to, max(0.005, float(dcfg.get("short_up_timeout_s", 0.015)))),
                    "reconnect_interval_s": reconn,
                    "poll_default_priority": self._canonical_priority(dcfg.get("poll_default_priority") or dcfg.get("poll_default_tier")),
                    "entries": [],
                    "priority_index": 0,
                    "priority_rr": {_PRIORITY_HIGH: 0, _PRIORITY_LOW: 0},
                    "last_rtt_ms": 0.0,
                    "rtt_avg_ms": 0.0,
                    "rtt_min_ms": 0.0,
                    "rtt_max_ms": 0.0,
                    "bus_load_pct": 0.0,
                    "high_priority_budget_pct": 0.0,
                    "high_priority_over_budget": 0,
                    "priority_counts": {_PRIORITY_HIGH: 0, _PRIORITY_LOW: 0},
                    "last_poll_ts": 0.0,
                    "poll_selected_count": 0,
                    "poll_loop_ms": 0.0,
                    "throughput_window_ts": 0.0,
                    "throughput_window_attempts": 0,
                    "throughput_window_success": 0,
                    "attempted_reads_per_sec": 0.0,
                    "successful_reads_per_sec": 0.0,
                    "estimated_sweep_s": 0.0,
                    "short_up_timeout_count": 0,
                    "crm_error_count": 0,
                    "rx_read_calls": 0.0,
                    "rx_empty_reads": 0.0,
                    "rx_read_calls_per_response": 0.0,
                    "rx_predrain_ms": 0.0,
                    "rx_mode_code": 0,
                    "daq_plan": [],
                    "daq_plans": {},
                    "daq_pid_map": {},
                    "daq_active_lists": [],
                    "daq_meta": {},
                    "daq_running": False,
                    "daq_setup_ok": 0,
                    "daq_dto_count": 0,
                    "daq_dto_rate_hz": 0.0,
                    "daq_dto_window_ts": 0.0,
                    "daq_dto_window_count": 0,
                    "daq_odt_count": 0,
                    "daq_decode_errors": 0,
                    "daq_fallback_active": 0,
                    "daq_last_pid": -1,
                    "daq_last_dto_id": 0,
                    "last_connect_attempt_ts": 0.0,
                    "rx_id": 0,
                    "connected": False,
                    "session": None,
                    "proto": None,
                }
            )
        self._freshness_sample_period_s = min_poll_s if min_poll_s < 1.0 else 0.1
        self._poll_interval_s = self._freshness_sample_period_s
        self._last_poll_ts = 0.0
        self._poll_index = 0
        self._connected = False
        self._last_connect_attempt_ts = 0.0
        self._diag.update(
            {
                "state": "configured",
                "state_code": 1,
                "last_error": "",
                "connect_attempts": 0,
                "connect_ok": 0,
                "unlock_ok": 0,
                "poll_success": 0,
                "poll_fail": 0,
                "last_seed_status": -1,
                "last_rc": -1,
                "ctr_mismatch": 0,
                "fresh_age_s": -1.0,
                "fresh_max_channel_age_s": -1.0,
                "freshness_state_code": -1,
                "freshness_warn_count": 0,
                "freshness_stale_count": 0,
                "bus_load_pct": 0.0,
                "poll_rtt_avg_ms": 0.0,
                "high_priority_budget_pct": 0.0,
                "high_priority_over_budget": 0,
                "short_up_rtt_last_ms": 0.0,
                "short_up_rtt_min_ms": 0.0,
                "short_up_rtt_max_ms": 0.0,
                "short_up_timeout_count": 0,
                "crm_error_count": 0,
                "poll_selected_count": 0,
                "poll_loop_ms": 0.0,
                "attempted_reads_per_sec": 0.0,
                "successful_reads_per_sec": 0.0,
                "estimated_sweep_s": 0.0,
                "rx_read_calls": 0.0,
                "rx_empty_reads": 0.0,
                "rx_read_calls_per_response": 0.0,
                "rx_predrain_ms": 0.0,
                "rx_mode_code": 0,
                "daq_enabled": 0,
                "daq_running": 0,
                "daq_setup_ok": 0,
                "daq_dto_count": 0,
                "daq_dto_rate_hz": 0.0,
                "daq_odt_count": 0,
                "daq_decode_errors": 0,
                "daq_fallback_active": 0,
                "daq_last_pid": -1,
                "daq_last_dto_id": 0,
            }
        )

    def validate(self) -> PluginStatus:
        device_cfgs = self._resolved_device_cfgs()
        if not device_cfgs:
            return PluginStatus(ok=False, message="At least one CCP device config is required")
        for dcfg in device_cfgs:
            meas = dcfg.get("measurements")
            if not isinstance(meas, dict):
                return PluginStatus(ok=False, message="measurements must be a mapping with naming_prefix and list")
            items = meas.get("list")
            if items is None or not isinstance(items, list):
                return PluginStatus(ok=False, message="measurements.list must be a list")
        aliases = self._final_aliases()
        if len(aliases) != len(set(aliases)):
            return PluginStatus(ok=False, message="Duplicate final aliases within CCP configuration")

        if self.mode != "real":
            return PluginStatus(ok=True)
        if nixnet is None:
            return PluginStatus(ok=False, message="nixnet package is not available for real CCP mode")

        a2l_cache: Dict[str, Dict[str, A2LChannel]] = {}
        for dcfg in device_cfgs:
            session = dcfg.get("session") or {}
            security = dcfg.get("security") or {}
            a2l_cfg = dcfg.get("a2l") or {}
            meas = dcfg.get("measurements") or {}
            items = meas.get("list", []) or []
            if not str(session.get("interface") or "").strip():
                return PluginStatus(ok=False, message="session.interface is required for real CCP mode")
            if session.get("tx_id") is None or session.get("rx_id") is None:
                return PluginStatus(ok=False, message="session.tx_id and session.rx_id are required for real CCP mode")
            access_key = self._resolve_access_key_text(security, dcfg)
            if not access_key:
                return PluginStatus(ok=False, message="No access key found -- enter in CCP config dialog (session-only) or configure API server")
            a2l_path = str(a2l_cfg.get("path") or "").strip()
            if not a2l_path:
                return PluginStatus(ok=False, message="a2l.path is required for real CCP mode")
            if not Path(a2l_path).exists():
                return PluginStatus(ok=False, message=f"a2l.path not found: {a2l_path}")
            if a2l_path not in a2l_cache:
                a2l_cache[a2l_path] = parse_a2l(Path(a2l_path))
            parsed = a2l_cache[a2l_path]
            for item in items:
                if not isinstance(item, dict) or not bool(item.get("enabled", True)):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                ch = parsed.get(name)
                if ch is None or ch.address is None:
                    return PluginStatus(ok=False, message=f"Measurement '{name}' is missing in A2L or has no address")
        return PluginStatus(ok=True)

    def aliases(self) -> Set[str]:
        out = set(self._final_aliases())
        out.update(
            {
                "CCP/connected",
                "CCP/state_code",
                "CCP/connect_attempts",
                "CCP/connect_ok",
                "CCP/unlock_ok",
                "CCP/poll_success",
                "CCP/poll_fail",
                "CCP/last_seed_status",
                "CCP/last_rc",
                "CCP/ctr_mismatch",
                "CCP/fresh_age_s",
                "CCP/fresh_max_channel_age_s",
                "CCP/freshness_state_code",
                "CCP/freshness_warn_count",
                "CCP/freshness_stale_count",
                "CCP/bus_load_pct",
                "CCP/poll_rtt_avg_ms",
                "CCP/high_priority_budget_pct",
                "CCP/high_priority_over_budget",
                "CCP/short_up_rtt_last_ms",
                "CCP/short_up_rtt_min_ms",
                "CCP/short_up_rtt_max_ms",
                "CCP/short_up_timeout_count",
                "CCP/crm_error_count",
                "CCP/poll_selected_count",
                "CCP/poll_loop_ms",
                "CCP/attempted_reads_per_sec",
                "CCP/successful_reads_per_sec",
                "CCP/estimated_sweep_s",
                "CCP/rx_read_calls",
                "CCP/rx_empty_reads",
                "CCP/rx_read_calls_per_response",
                "CCP/rx_predrain_ms",
                "CCP/rx_mode_code",
                "CCP/daq_enabled",
                "CCP/daq_running",
                "CCP/daq_setup_ok",
                "CCP/daq_dto_count",
                "CCP/daq_dto_rate_hz",
                "CCP/daq_odt_count",
                "CCP/daq_active_list_count",
                "CCP/daq_decode_errors",
                "CCP/daq_fallback_active",
                "CCP/daq_last_pid",
                "CCP/daq_last_dto_id",
            }
        )
        return out

    def _build_units_map(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        a2l_units_cache: Dict[str, Dict[str, str]] = {}
        for dcfg in self._resolved_device_cfgs():
            meas = (dcfg.get("measurements") or {})
            prefix = str(meas.get("naming_prefix") or "")
            a2l_path_text = str((dcfg.get("a2l") or {}).get("path") or "").strip()
            if a2l_path_text not in a2l_units_cache:
                a2l_units: Dict[str, str] = {}
                try:
                    a2l_path = Path(a2l_path_text)
                    if a2l_path.exists():
                        parsed = parse_a2l(a2l_path)
                        a2l_units = {str(k): str(v.unit or "") for k, v in parsed.items()}
                except Exception:
                    a2l_units = {}
                a2l_units_cache[a2l_path_text] = a2l_units
            a2l_units = a2l_units_cache.get(a2l_path_text, {})
            for item in meas.get("list", []) or []:
                if not isinstance(item, dict) or not bool(item.get("enabled", True)):
                    continue
                name = item.get("name")
                if not name:
                    continue
                alias = f"{prefix}{name}" if prefix else str(name)
                unit = str(item.get("unit_override") or item.get("unit") or "").strip()
                if not unit:
                    unit = str(a2l_units.get(str(name), "")).strip()
                mapping[alias] = unit
        mapping["CCP/connected"] = ""
        mapping["CCP/state_code"] = ""
        mapping["CCP/connect_attempts"] = "count"
        mapping["CCP/connect_ok"] = "count"
        mapping["CCP/unlock_ok"] = "count"
        mapping["CCP/poll_success"] = "count"
        mapping["CCP/poll_fail"] = "count"
        mapping["CCP/last_seed_status"] = ""
        mapping["CCP/last_rc"] = ""
        mapping["CCP/ctr_mismatch"] = "count"
        mapping["CCP/fresh_age_s"] = "s"
        mapping["CCP/fresh_max_channel_age_s"] = "s"
        mapping["CCP/freshness_state_code"] = ""
        mapping["CCP/freshness_warn_count"] = "count"
        mapping["CCP/freshness_stale_count"] = "count"
        mapping["CCP/bus_load_pct"] = "%"
        mapping["CCP/poll_rtt_avg_ms"] = "ms"
        mapping["CCP/high_priority_budget_pct"] = "%"
        mapping["CCP/high_priority_over_budget"] = ""
        mapping["CCP/short_up_rtt_last_ms"] = "ms"
        mapping["CCP/short_up_rtt_min_ms"] = "ms"
        mapping["CCP/short_up_rtt_max_ms"] = "ms"
        mapping["CCP/short_up_timeout_count"] = "count"
        mapping["CCP/crm_error_count"] = "count"
        mapping["CCP/poll_selected_count"] = "count"
        mapping["CCP/poll_loop_ms"] = "ms"
        mapping["CCP/attempted_reads_per_sec"] = "reads/s"
        mapping["CCP/successful_reads_per_sec"] = "reads/s"
        mapping["CCP/estimated_sweep_s"] = "s"
        mapping["CCP/rx_read_calls"] = "count"
        mapping["CCP/rx_empty_reads"] = "count"
        mapping["CCP/rx_read_calls_per_response"] = "calls"
        mapping["CCP/rx_predrain_ms"] = "ms"
        mapping["CCP/rx_mode_code"] = ""
        mapping["CCP/daq_enabled"] = ""
        mapping["CCP/daq_running"] = ""
        mapping["CCP/daq_setup_ok"] = ""
        mapping["CCP/daq_dto_count"] = "count"
        mapping["CCP/daq_dto_rate_hz"] = "Hz"
        mapping["CCP/daq_odt_count"] = "count"
        mapping["CCP/daq_active_list_count"] = "count"
        mapping["CCP/daq_decode_errors"] = "count"
        mapping["CCP/daq_fallback_active"] = ""
        mapping["CCP/daq_last_pid"] = ""
        mapping["CCP/daq_last_dto_id"] = ""
        return mapping

    def units(self) -> Dict[str, str]:
        if self._units_cache_valid and self._units:
            return dict(self._units)
        self._units = self._build_units_map()
        self._units_cache_valid = True
        return dict(self._units)

    def start(self) -> None:
        self._theta = 0.0
        self._values = {a: float("nan") for a in self._final_aliases()}
        self._value_ts = {a: 0.0 for a in self._final_aliases()}
        self._connected = False
        self._last_connect_attempt_ts = 0.0
        for ctx in self._contexts:
            ctx["connected"] = False
            ctx["last_connect_attempt_ts"] = 0.0
            ctx["last_poll_ts"] = 0.0
            ctx["poll_index"] = 0
        self._set_state("starting", 2)
        if self.mode == "real":
            self._worker_stop.clear()
            self._worker_thread = threading.Thread(target=self._run_real_worker, daemon=True)
            self._worker_thread.start()
        self._refresh_freshness(time.time())
        self._append_diag_values()
        with self._state_lock:
            self._snapshot_values = dict(self._values)

    def stop(self) -> None:
        self._worker_stop.set()
        wt = self._worker_thread
        if wt is not None and wt.is_alive():
            try:
                wt.join(timeout=1.0)
            except Exception:
                pass
        self._worker_thread = None
        for ctx in self._contexts:
            try:
                self._stop_daq_ctx(ctx)
            except Exception:
                pass
            try:
                self._stop_daq_ctx(ctx)
            except Exception:
                pass
            try:
                session = ctx.get("session")
                if session is not None:
                    session.close()
            except Exception:
                pass
            ctx["session"] = None
            ctx["proto"] = None
            ctx["connected"] = False
        self._connected = False
        self._set_state("stopped", 0)
        self._refresh_freshness(time.time())
        self._append_diag_values()
        with self._state_lock:
            self._snapshot_values = dict(self._values)

    def simulate_step(self) -> Dict[str, Any]:
        if self.mode != "real":
            return self._simulate_step_values()
        with self._state_lock:
            vals = dict(self._snapshot_values)
        msgs = self._drain_console_msgs()
        if msgs:
            vals["__console_msgs__"] = msgs
        return vals

    def _simulate_step_values(self) -> Dict[str, Any]:
        vals: Dict[str, Any] = {}
        meas = (self.config.get("measurements") or {})
        prefix = str(meas.get("naming_prefix") or "")
        items = [x for x in (meas.get("list", []) or []) if isinstance(x, dict) and bool(x.get("enabled", True))]
        self._theta += math.pi / 28.0
        for idx, item in enumerate(items):
            name = str(item.get("name") or "")
            if not name:
                continue
            alias = f"{prefix}{name}" if prefix else name
            lname = name.lower()
            phase = idx * math.pi / 5.0
            if "rpm" in lname:
                vals[alias] = 1300.0 + 150.0 * math.sin(self._theta + phase)
            elif ("temp" in lname) or ("temperature" in lname):
                vals[alias] = 85.0 + 1.5 * math.sin(self._theta + phase)
            elif ("press" in lname) or ("pressure" in lname):
                vals[alias] = 320.0 + 10.0 * math.cos(self._theta + phase)
            else:
                vals[alias] = math.sin(self._theta + phase)
        return vals

    def _parse_int(self, val: Any, default: int = 0) -> int:
        if val is None:
            return default
        if isinstance(val, int):
            return int(val)
        s = str(val).strip()
        try:
            if s.lower().startswith("0x"):
                return int(s, 16)
            return int(s)
        except Exception:
            return default

    def _resolve_access_key_text(self, sec_cfg: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> str:
        if ctx:
            store = getattr(sys, "_matrix_ccp_session_keys", {})
            session_key = store.get(ctx.get("name", ""), "")
            if session_key:
                return session_key
        raw = str(sec_cfg.get("access_key") or "").strip()
        if raw:
            return raw
        top_security = self.config.get("security") or {}
        return str(top_security.get("access_key") or "").strip()

    _CCP_NOTIFICATION_CODES = {0x30, 0x31, 0x32, 0x33}
    _CCP_NOTIFICATION_NAMES = {
        0x30: "cold_start_request",
        0x31: "cal_init_request",
        0x32: "daq_init_request",
        0x33: "code_update_request",
    }

    def _crm_match(self, data: bytes, ctr: int) -> tuple[bool, bool, int]:
        """Returns (counter_matched, success, error_code).

        CCP notification codes 0x30-0x33 are treated as ACK + warning,
        not as errors.
        """
        d = data.ljust(8, b"\x00")
        if d[0] != 0xFF:
            return False, False, -1
        if d[1] == 0x00 and d[2] == ctr:
            return True, True, 0
        if d[2] == 0x00 and d[1] == ctr:
            return True, True, 0
        if d[2] == ctr:
            rc = int(d[1])
            return True, rc in self._CCP_NOTIFICATION_CODES, rc
        if d[1] == ctr:
            rc = int(d[2])
            return True, rc in self._CCP_NOTIFICATION_CODES, rc
        return False, False, -1

    def _send_wait_crm(self, ctx: Dict[str, Any], frame: CanFrame, label: str, timeout_s: float | None = None) -> bytes:
        session = ctx.get("session")
        if session is None:
            raise RuntimeError(f"{label}: no session")
        rx_id = int(ctx.get("rx_id", 0))
        ctr = int(frame.data[1]) if frame.data else -1
        _pc = time.perf_counter
        session.send(frame)
        deadline = _pc() + max(0.01, float(timeout_s if timeout_s is not None else ctx.get("io_timeout_s", 0.05)))
        last_rc = -1
        while _pc() < deadline:
            for fr in session.recv(timeout_s=0.01, only_id=rx_id):
                data = fr.data.ljust(8, b"\x00")
                matched, ok, rc = self._crm_match(data, ctr)
                if matched:
                    if ok:
                        if rc in self._CCP_NOTIFICATION_CODES:
                            name = ctx.get("name", "?")
                            note = self._CCP_NOTIFICATION_NAMES.get(rc, "notification")
                            print(f"[CCP:{name}] {label}: ACK with notification rc=0x{rc:02X} ({note})")
                        return data
                    raise RuntimeError(f"{label} rejected (rc={rc})")
                if rc >= 0:
                    last_rc = rc
        raise RuntimeError(f"{label} timed out (last_rc={last_rc})")

    def _daq_cfg_value(self, ctx: Dict[str, Any], key: str, default: Any = None) -> Any:
        acq = ctx.get("acquisition_cfg") or {}
        if isinstance(acq, dict) and key in acq:
            return acq.get(key)
        return self.config.get(key, default)

    def _build_multi_daq_plan(
        self,
        ctx: Dict[str, Any],
        parsed: Dict[str, A2LChannel],
        daq_lists: Dict[str, A2LDaqList],
    ) -> List[Dict[str, Any]]:
        """Build per-tier DAQ plans, packing channels into their assigned lists.

        Returns a list of plan dicts, one per active tier:
          { "tier", "list_num", "event_ch", "first_pid", "cmd_dto", "entries",
            "last_odt", "n_channels", "meta" }
        """
        tier_order = ["1ms", "10ms", "50ms", "100ms"]
        config_tier = _canonical_poll_tier(
            self._daq_cfg_value(ctx, "tier", "100ms") or "100ms"
        )
        available_tiers = list(daq_lists.keys())
        name = ctx.get("name", "?")

        tier_buckets: Dict[str, List[Dict[str, Any]]] = {}
        for entry in ctx.get("entries", []) or []:
            if not isinstance(entry, dict):
                continue
            size = int(entry.get("size") or 0)
            if size not in {1, 2, 4}:
                continue
            raw_tier = entry.get("priority") or entry.get("poll_tier") or config_tier
            tier = _canonical_poll_tier(raw_tier)
            if tier not in daq_lists:
                raise DAQConfigError(
                    f"Channel '{entry.get('name', '?')}' assigned to {tier} "
                    f"but ECU has no {tier} DAQ list "
                    f"(available: {', '.join(available_tiers)})"
                )
            tier_buckets.setdefault(tier, []).append(entry)

        plans: List[Dict[str, Any]] = []
        for tier in tier_order:
            entries = tier_buckets.get(tier)
            if not entries:
                continue
            meta = daq_lists[tier]
            max_odts = int(meta.odt_count) if meta.odt_count else 10

            max_odt_pct = float(self._daq_cfg_value(ctx, "max_odt_utilization_pct", 90))
            usable_odts = max(1, int(max_odts * max_odt_pct / 100.0))

            packed: List[Dict[str, Any]] = []
            odt = 0
            offset = 0
            for entry in entries:
                size = int(entry.get("size") or 0)
                if offset + size > _DAQ_DTO_PAYLOAD_BYTES:
                    odt += 1
                    offset = 0
                if odt >= max_odts:
                    remaining = len(entries) - len(packed)
                    raise DAQConfigError(
                        f"DAQ {tier} needs more than {max_odts} ODTs "
                        f"(ECU allows {max_odts}) -- "
                        f"reduce channels in {tier} tier or redistribute "
                        f"{remaining} channels to other tiers"
                    )
                item = dict(entry)
                item["odt"] = odt
                item["offset"] = offset
                item["tier"] = tier
                packed.append(item)
                offset += size

            last_odt = max((int(x.get("odt", 0)) for x in packed), default=0)
            used_odts = last_odt + 1
            utilization_pct = (used_odts / max_odts) * 100.0 if max_odts > 0 else 100.0

            if used_odts > usable_odts:
                overflow_entries = [
                    e for e in packed if int(e.get("odt", 0)) >= usable_odts
                ]
                overflow_names = ", ".join(
                    str(e.get("name", "?")) for e in overflow_entries[:5]
                )
                raise DAQConfigError(
                    f"DAQ {tier} is at {utilization_pct:.0f}% ODT capacity "
                    f"({used_odts}/{max_odts} ODTs) -- max allowed is "
                    f"{max_odt_pct:.0f}% ({usable_odts}/{max_odts}). "
                    f"Move these channels to a different tier: {overflow_names}"
                )

            raw_dto = getattr(meta, "raw_can_id", None)
            runtime_dto = normalize_dto_can_id(meta.can_id)
            cmd_dto = int(raw_dto if raw_dto is not None else (runtime_dto if runtime_dto is not None else ctx.get("rx_id", 0)))

            plans.append({
                "tier": tier,
                "list_num": int(meta.list_number if meta.list_number is not None else 0),
                "event_ch": int(meta.raster if meta.raster is not None else 0),
                "first_pid": int(meta.first_pid if meta.first_pid is not None else 0),
                "cmd_dto": cmd_dto,
                "dto_id": int(runtime_dto if runtime_dto is not None else normalize_dto_can_id(cmd_dto) or ctx.get("rx_id", 0)),
                "entries": packed,
                "last_odt": last_odt,
                "n_channels": len(packed),
                "meta": meta,
            })
            print(
                f"[CCP:{name}] DAQ plan: {tier} = "
                f"{len(packed)} channels in {used_odts}/{max_odts} ODTs "
                f"({utilization_pct:.0f}%, cap={max_odt_pct:.0f}%) "
                f"(list_num={meta.list_number})"
            )

        if not plans:
            raise DAQConfigError("DAQ plan has no entries -- no channels enabled or no matching A2L DAQ lists")

        ctx["daq_multi_plans"] = plans
        return plans

    def _connect_daq_ctx(self, ctx: Dict[str, Any], parsed: Dict[str, A2LChannel]) -> None:
        session_cfg = ctx.get("session_cfg") or {}
        sec_cfg = ctx.get("security_cfg") or {}
        a2l_cfg = ctx.get("a2l_cfg") or {}
        interface = str(session_cfg.get("interface") or "").strip()
        baud = self._parse_int(session_cfg.get("baudrate"), 250000)
        tx_id = self._parse_int(session_cfg.get("tx_id"), 0)
        rx_id = self._parse_int(session_cfg.get("rx_id"), 0)
        ctx["rx_id"] = rx_id
        station = self._parse_int(session_cfg.get("station_address"), 0)
        is_ext = bool(session_cfg.get("is_extended", True))
        a2l_path = Path(str(a2l_cfg.get("path") or "").strip())
        daq_lists = parse_a2l_daq_lists(a2l_path)
        prescaler = max(1, self._parse_int(self._daq_cfg_value(ctx, "prescaler", 1), 1))
        seed_resource = self._parse_int(self._daq_cfg_value(ctx, "seed_resource", None), -1)
        if seed_resource < 0:
            seed_resource = 0x02
        sec_type = str(self._daq_cfg_value(ctx, "sec_type", "DAQ") or "DAQ").upper()
        seed_ctr = self._parse_int(sec_cfg.get("seed_ctr"), 0x07)
        connect_ctr = self._parse_int(sec_cfg.get("connect_ctr"), 0x19)
        unlock_ctr = self._parse_int(sec_cfg.get("unlock_ctr"), 0x08)
        unlock_pad = self._parse_int(sec_cfg.get("unlock_pad"), 0x55)
        seed_endian = str(sec_cfg.get("seed_endian") or "big").lower()
        access_key_text = self._resolve_access_key_text(sec_cfg, ctx)
        timeout_s = float(ctx.get("io_timeout_s", 0.05))

        plans = self._build_multi_daq_plan(ctx, parsed, daq_lists)

        session = NixnetSession(interface=interface, baudrate=baud, force_stream_rx=True)
        proto = CcpProto(tx_id=tx_id, is_extended=is_ext)
        ctx["session"] = session
        ctx["proto"] = proto
        ctx["daq_running"] = False
        session.open(rx_id=rx_id, is_extended=is_ext)

        name = ctx.get("name", "?")
        conn = proto.build_connect(station_address=station, ctr_override=connect_ctr)
        session.send(conn)
        session.recv(timeout_s=timeout_s, only_id=rx_id)
        self._console_msg(f"[CCP] Connected to {name}")

        if not access_key_text:
            raise RuntimeError(
                "DAQ unlock failed -- enter access key in CCP config dialog "
                "or verify the key matches this ECU"
            )
        access_key = int(access_key_text.replace(" ", "").replace("0x", "").replace("0X", ""), 16)

        set_s_status = bool(sec_cfg.get("set_s_status", True))
        s_status = self._parse_int(sec_cfg.get("s_status"), 0x83)
        s_status_sent = False

        daq_ena_addr = self._parse_int(self._daq_cfg_value(ctx, "daq_ena_address", None), -1)
        daq_ena_val = self._parse_int(self._daq_cfg_value(ctx, "daq_ena_value", None), -1)
        need_cal_unlock = daq_ena_addr >= 0 and daq_ena_val >= 0

        if need_cal_unlock:
            cal_resource = self._parse_int(sec_cfg.get("seed_resource"), 0x01)
            cal_sec_type = str(sec_cfg.get("sec_type") or "CAL").upper()
            session.send(proto.build_get_seed(resource=cal_resource, ctr_override=seed_ctr))
            cal_seed_frames = session.recv(timeout_s=timeout_s, only_id=rx_id)
            if not cal_seed_frames:
                raise RuntimeError(
                    "No CAL GET_SEED response -- check CAN wiring, interface config, or ECU power"
                )
            cal_seed_data = cal_seed_frames[-1].data.ljust(8, b"\x00")
            cal_key = compute_key_from_seed_algo(
                seed=bytes(cal_seed_data[4:8]),
                access_key=access_key,
                seed_endian=seed_endian,
                sec_type=cal_sec_type,
            )
            session.send(proto.build_unlock(key=cal_key, ctr_override=unlock_ctr, pad=unlock_pad))
            cal_unlock_frames = session.recv(timeout_s=timeout_s, only_id=rx_id)
            cal_status = "no_response"
            if cal_unlock_frames:
                cu = cal_unlock_frames[-1].data.ljust(8, b"\x00")
                cal_rc = int(cu[1]) if cu[0] == 0xFF else -1
                cal_status = f"rc={cal_rc}" if cal_rc != 0 else "ok"
            print(f"[CCP:{name}] CAL unlock: resource=0x{cal_resource:02X}, sec_type={cal_sec_type}, status={cal_status}")

            if set_s_status:
                self._send_wait_crm(ctx, proto.build_set_s_status(s_status), "SET_S_STATUS", timeout_s)
                s_status_sent = True
                print(f"[CCP:{name}] SET_S_STATUS: 0x{s_status:02X}")

            addr_endian = str(ctx.get("mta_addr_endian") or self.config.get("mta_addr_endian") or "big")
            try:
                self._send_wait_crm(ctx, proto.build_set_mta(daq_ena_addr, extension=0, byteorder=addr_endian), "SET_MTA (daq_ena)", timeout_s)
                print(f"[CCP:{name}] SET_MTA: 0x{daq_ena_addr:08X}")
                self._send_wait_crm(ctx, proto.build_dnload(1, bytes([daq_ena_val & 0xFF])), "DNLOAD (daq_ena)", timeout_s)
                print(f"[CCP:{name}] DAQ enable: wrote 0x{daq_ena_val:02X} to 0x{daq_ena_addr:08X}")
            except RuntimeError as e:
                raise RuntimeError(
                    f"CCP_DAQ_ena write rejected -- verify daq_ena_address "
                    f"(0x{daq_ena_addr:08X}) and daq_ena_value ({daq_ena_val}) "
                    f"in config: {e}"
                ) from e

        session.send(proto.build_get_seed(resource=seed_resource, ctr_override=seed_ctr))
        seed_frames = session.recv(timeout_s=timeout_s, only_id=rx_id)
        if not seed_frames:
            raise RuntimeError(
                "No DAQ GET_SEED response -- check CAN wiring, interface config, or ECU power"
            )
        seed_data = seed_frames[-1].data.ljust(8, b"\x00")
        self._diag["last_seed_status"] = int(seed_data[3])
        key = compute_key_from_seed_algo(
            seed=bytes(seed_data[4:8]),
            access_key=access_key,
            seed_endian=seed_endian,
            sec_type=sec_type,
        )
        unlock = proto.build_unlock(key=key, ctr_override=unlock_ctr, pad=unlock_pad)
        session.send(unlock)
        unlock_frames = session.recv(timeout_s=timeout_s, only_id=rx_id)
        unlock_status = "no_response"
        unlock_rc = -1
        if unlock_frames:
            ud = unlock_frames[-1].data.ljust(8, b"\x00")
            unlock_rc = int(ud[1]) if ud[0] == 0xFF else -1
            unlock_status = f"rc={unlock_rc}" if unlock_rc != 0 else "ok"
        print(f"[CCP:{name}] DAQ unlock: resource=0x{seed_resource:02X}, sec_type={sec_type}, status={unlock_status}")
        if unlock_rc > 0:
            raise RuntimeError(
                f"DAQ unlock rejected (rc={unlock_rc}) -- verify security.access_key for this ECU"
            )
        self._diag["unlock_ok"] = int(self._diag.get("unlock_ok", 0)) + 1
        self._console_msg(f"[CCP] {name}: Unlock OK")

        if set_s_status and not s_status_sent:
            self._send_wait_crm(ctx, proto.build_set_s_status(s_status), "SET_S_STATUS", timeout_s)
            print(f"[CCP:{name}] SET_S_STATUS: 0x{s_status:02X}")

        # --- Phase 2: Configure all DAQ lists ---
        pid_map: Dict[int, List[Dict[str, Any]]] = {}
        active_lists: List[Dict[str, Any]] = []
        started_lists: List[int] = []
        total_channels = 0

        for p in plans:
            tier = p["tier"]
            list_num = p["list_num"]
            cmd_dto = p["cmd_dto"]
            event_ch = p["event_ch"]
            last_odt = p["last_odt"]
            entries = p["entries"]
            first_pid_hint = p["first_pid"]

            print(f"[CCP:{name}] Configuring list {list_num} ({tier}): {len(entries)} channels, {last_odt + 1} ODTs")

            try:
                self._send_wait_crm(ctx, proto.build_start_stop(0, list_num, 0, 0, 0), f"DAQ {tier} STOP", timeout_s)
            except Exception:
                pass

            try:
                size_resp = self._send_wait_crm(
                    ctx, proto.build_get_daq_size(list_num, cmd_dto),
                    f"DAQ {tier} GET_DAQ_SIZE", timeout_s,
                )
            except RuntimeError as e:
                raise RuntimeError(
                    f"GET_DAQ_SIZE failed for {tier} list {list_num} -- "
                    f"ECU may not support this DAQ list: {e}"
                ) from e

            ecu_size = int(size_resp[3])
            first_pid = first_pid_hint if first_pid_hint > 0 else int(size_resp[4])
            resp_hex = " ".join(f"{b:02X}" for b in size_resp[:8])
            print(f"[CCP:{name}] GET_DAQ_SIZE: list={list_num} ({tier}), ecu_odts={ecu_size}, first_pid={first_pid}, full_resp=[{resp_hex}]")

            if ecu_size and last_odt + 1 > ecu_size:
                raise RuntimeError(
                    f"DAQ {tier} needs {last_odt + 1} ODTs, ECU allows {ecu_size} -- "
                    f"reduce channels in {tier} tier or redistribute to other tiers"
                )

            element_counts: Dict[int, int] = {}
            for entry in entries:
                odt = int(entry.get("odt", 0))
                element = int(element_counts.get(odt, 0))
                element_counts[odt] = element + 1
                entry_addr = int(entry.get("address", 0))
                entry_ext = int(entry.get("extension", 0))
                entry_size = int(entry.get("size", 1))
                entry_endian = str(entry.get("mta_addr_endian") or "big")
                self._send_wait_crm(
                    ctx, proto.build_set_daq_ptr(list_num, odt, element),
                    f"DAQ {tier} SET_DAQ_PTR", timeout_s,
                )
                self._send_wait_crm(
                    ctx,
                    proto.build_write_daq(
                        size=entry_size,
                        address=entry_addr,
                        extension=entry_ext,
                        byteorder=entry_endian,
                    ),
                    f"DAQ {tier} WRITE_DAQ",
                    timeout_s,
                )

            self._send_wait_crm(
                ctx, proto.build_start_stop(1, list_num, last_odt, event_ch, prescaler),
                f"DAQ {tier} START", timeout_s,
            )
            started_lists.append(list_num)
            print(f"[CCP:{name}] START: list={list_num} ({tier}), last_odt={last_odt}, event_ch={event_ch}, prescaler={prescaler}")

            for entry in entries:
                pid = first_pid + int(entry.get("odt", 0))
                pid_map.setdefault(pid, []).append(entry)

            active_lists.append({
                "tier": tier,
                "list_number": list_num,
                "dto_id": p["dto_id"],
                "cmd_dto_id": cmd_dto,
                "first_pid": first_pid,
                "event_channel": event_ch,
                "last_odt": last_odt,
                "n_channels": len(entries),
                "n_odts": last_odt + 1,
            })
            total_channels += len(entries)

        # --- Phase 3: START_STOP_ALL ---
        try:
            self._send_wait_crm(ctx, proto.build_start_stop_all(1), "DAQ START_STOP_ALL", timeout_s)
            print(f"[CCP:{name}] START_STOP_ALL: ok")
        except Exception as e:
            print(f"[CCP:{name}] START_STOP_ALL: skipped ({e})")

        dto_can_ids: set[int] = {0}
        for al in active_lists:
            dto_can_ids.add(int(al.get("dto_id", rx_id)))
        dto_can_ids.add(int(rx_id))

        ctx["daq_meta"] = {
            "dto_id": rx_id,
            "dto_can_ids": dto_can_ids,
            "active_lists": active_lists,
            "prescaler": prescaler,
        }
        ctx["daq_pid_map"] = pid_map
        ctx["daq_active_lists"] = active_lists
        ctx["daq_dto_can_ids"] = dto_can_ids
        ctx["daq_running"] = True
        ctx["daq_setup_ok"] = 1
        ctx["daq_fallback_active"] = 0
        ctx["connected"] = True
        ctx["last_poll_ts"] = time.time()
        ctx["daq_poll_log_ts"] = time.time()
        ctx["daq_poll_raw_count"] = 0
        ctx["daq_poll_filtered_count"] = 0
        ids_str = " ".join(f"0x{x:08X}" for x in sorted(dto_can_ids) if x != 0)
        print(
            f"[CCP:{name}] DAQ active: {total_channels} channels across "
            f"{len(active_lists)} list(s), "
            f"DTO CAN IDs=[{ids_str}] (+0x0 fallback), "
            f"PIDs in map: {sorted(pid_map.keys())}"
        )
        self._diag["connect_ok"] = int(self._diag.get("connect_ok", 0)) + 1
        self._diag["last_error"] = ""
        self._set_state("daq_streaming", 75)
        self._console_msg(f"[CCP] {name}: DAQ streaming {total_channels} channels")
        try:
            parts = ", ".join(
                f"{al['tier']}={al['n_odts']} ODTs ({al['n_channels']}ch)"
                for al in active_lists
            )
            print(
                f"[CCP:{ctx.get('name','?')}] DAQ multi-list OK: "
                f"{parts}, total={total_channels}ch, "
                f"lists={len(active_lists)}"
            )
        except Exception:
            pass

    def _stop_daq_ctx(self, ctx: Dict[str, Any]) -> None:
        if not bool(ctx.get("daq_running", False)):
            return
        session = ctx.get("session")
        proto = ctx.get("proto")
        if session is None or proto is None:
            ctx["daq_running"] = False
            return
        timeout_s = float(ctx.get("io_timeout_s", 0.05))
        active = ctx.get("daq_active_lists") or []
        try:
            for al in active:
                list_num = int(al.get("list_number", 0))
                try:
                    self._send_wait_crm(ctx, proto.build_start_stop(0, list_num, 0, 0, 0), f"DAQ stop list {list_num}", timeout_s)
                except Exception:
                    pass
            try:
                self._send_wait_crm(ctx, proto.build_start_stop_all(0), "DAQ START_STOP_ALL stop", timeout_s)
            except Exception:
                pass
        except Exception as exc:
            self._diag["last_error"] = f"daq_stop_failed:{exc}"
        finally:
            ctx["daq_running"] = False

    def _connect_real_ctx(self, ctx: Dict[str, Any]) -> None:
        ctx["last_connect_attempt_ts"] = time.time()
        self._diag["connect_attempts"] = int(self._diag.get("connect_attempts", 0)) + 1
        self._set_state("connecting", 10)
        try:
            session_cfg = ctx.get("session_cfg") or {}
            sec_cfg = ctx.get("security_cfg") or {}
            a2l_cfg = ctx.get("a2l_cfg") or {}
            meas_cfg = ctx.get("meas_cfg") or {}
            interface = str(session_cfg.get("interface") or "").strip()
            baud = self._parse_int(session_cfg.get("baudrate"), 250000)
            tx_id = self._parse_int(session_cfg.get("tx_id"), 0)
            rx_id = self._parse_int(session_cfg.get("rx_id"), 0)
            ctx["rx_id"] = rx_id
            station = self._parse_int(session_cfg.get("station_address"), 0)
            is_ext = bool(session_cfg.get("is_extended", True))
            seed_resource = self._parse_int(sec_cfg.get("seed_resource"), 0x01)
            seed_ctr = self._parse_int(sec_cfg.get("seed_ctr"), 0x07)
            connect_ctr = self._parse_int(sec_cfg.get("connect_ctr"), 0x19)
            unlock_ctr = self._parse_int(sec_cfg.get("unlock_ctr"), 0x08)
            unlock_pad = self._parse_int(sec_cfg.get("unlock_pad"), 0x55)
            force_unlock = bool(sec_cfg.get("force_unlock", True))
            set_s_status = bool(sec_cfg.get("set_s_status", True))
            s_status = self._parse_int(sec_cfg.get("s_status"), 0x83)
            seed_endian = str(sec_cfg.get("seed_endian") or "big").lower()
            sec_type = str(sec_cfg.get("sec_type") or "CAL").upper()
            access_key_text = self._resolve_access_key_text(sec_cfg, ctx)
            a2l_path = Path(str(a2l_cfg.get("path") or "").strip())
            parsed = parse_a2l(a2l_path)
            poll_endian = str(self.config.get("poll_endian") or "big").lower()
            mta_addr_endian = str(self.config.get("mta_addr_endian") or "big").lower()
            addr_ext_high = bool(self.config.get("addr_ext_high", True))
            prefix = str(meas_cfg.get("naming_prefix") or "")
            default_priority = self._canonical_priority(ctx.get("poll_default_priority") or _DEFAULT_PRIORITY)
            items = meas_cfg.get("list", []) or []
            entries: List[Dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict) or not bool(item.get("enabled", True)):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                ch = parsed.get(name)
                item_addr = item.get("address", None)
                if item_addr is None and (ch is None or ch.address is None):
                    continue
                alias = f"{prefix}{name}" if prefix else name
                address = int(item_addr) if item_addr is not None else int(ch.address)
                extension = int(item.get("address_extension", 0))
                if addr_ext_high:
                    extension = (address >> 24) & 0xFF
                    address = address & 0x00FFFFFF
                item_dtype = str(item.get("data_type") or "").strip().upper() or None
                dtype = item_dtype or (ch.data_type if ch is not None else None)
                size = int(item.get("size") or dtype_size(dtype))
                size = max(1, min(8, size))
                item_limits = item.get("limits", None)
                limits = None
                if isinstance(item_limits, (list, tuple)) and len(item_limits) == 2:
                    try:
                        limits = (float(item_limits[0]), float(item_limits[1]))
                    except Exception:
                        limits = None
                if limits is None and ch is not None:
                    limits = ch.limits
                coeffs = ch.coeffs if ch is not None else None
                priority = self._canonical_priority(
                    item.get("priority"),
                    item.get("poll_tier") or item.get("daq_list") or default_priority,
                )
                entries.append(
                    {
                        "name": name,
                        "alias": alias,
                        "address": address,
                        "extension": extension,
                        "size": size,
                        "dtype": dtype,
                        "limits": limits,
                        "coeffs": coeffs,
                        "poll_endian": poll_endian,
                        "mta_addr_endian": mta_addr_endian,
                        "priority": priority,
                        "expected_period_s": 1.0 / max(0.001, self._core_sample_rate_hz()),
                        "last_attempt_ts": 0.0,
                        "last_success_ts": 0.0,
                        "achieved_hz": 0.0,
                    }
                )
            ctx["entries"] = entries
            ctx["_alias_prefix"] = prefix
            self._update_ctx_load_estimates(ctx)
            try:
                n_ch = len(entries)
                target_hz = int(ctx.get("target_poll_hz", 10))
                if n_ch > 0:
                    rec = self._recommended_poll_channels_per_tick(n_ch, target_hz)
                    ctx["poll_channels_per_tick"] = int(rec)
                    hl_ratio = int(ctx.get("high_low_ratio", _DEFAULT_HIGH_LOW_RATIO))
                    print(f"[CCP:{ctx.get('name','?')}] Poll config: {n_ch} channels, target {target_hz} Hz, {rec} ch/tick, HIGH:LOW={hl_ratio}:1")
            except Exception:
                pass
            old_session = ctx.get("session")
            if old_session is not None:
                try:
                    self._stop_daq_ctx(ctx)
                except Exception:
                    pass
                try:
                    old_session.close()
                except Exception:
                    pass
            ctx["session"] = None
            ctx["proto"] = None
            if str(ctx.get("acquisition_mode") or "short_up").lower() == "daq":
                try:
                    self._connect_daq_ctx(ctx, parsed)
                    return
                except DAQConfigError as cfg_exc:
                    ctx["daq_setup_ok"] = 0
                    ctx["daq_running"] = False
                    self._diag["last_error"] = f"daq_config_error:{ctx.get('name','?')}:{cfg_exc}"
                    self._set_state("daq_config_error", 96)
                    self._console_msg(f"[CCP] {ctx.get('name','?')}: DAQ setup failed - {cfg_exc}")
                    try:
                        session = ctx.get("session")
                        if session is not None:
                            session.close()
                    except Exception:
                        pass
                    ctx["session"] = None
                    ctx["proto"] = None
                    print(f"[CCP:{ctx.get('name','?')}] ERROR: {cfg_exc}")
                    raise
                except Exception as daq_exc:
                    ctx["daq_setup_ok"] = 0
                    ctx["daq_running"] = False
                    self._diag["last_error"] = f"daq_setup_failed:{ctx.get('name','?')}:{daq_exc}"
                    self._set_state("daq_setup_failed", 93)
                    try:
                        session = ctx.get("session")
                        if session is not None:
                            session.close()
                    except Exception:
                        pass
                    ctx["session"] = None
                    ctx["proto"] = None
                    if not bool(ctx.get("fallback_short_up", False)):
                        self._console_msg(f"[CCP] {ctx.get('name','?')}: DAQ setup failed - {daq_exc}")
                        print(f"[CCP:{ctx.get('name','?')}] ERROR: DAQ setup failed: {daq_exc}")
                        raise
                    print(
                        f"[CCP:{ctx.get('name','?')}] WARNING: DAQ mode failed "
                        f"({daq_exc}), running SHORT_UP fallback"
                    )
                    ctx["daq_fallback_active"] = 1
                    ctx["daq_fallback_reason"] = str(daq_exc)
            session = NixnetSession(interface=interface, baudrate=baud)
            session.open(rx_id=rx_id, is_extended=is_ext)
            proto = CcpProto(tx_id=tx_id, is_extended=is_ext)
            ctx["session"] = session
            ctx["proto"] = proto
            conn = proto.build_connect(station_address=station, ctr_override=connect_ctr)
            session.send(conn)
            session.recv(timeout_s=float(ctx.get("io_timeout_s", 0.05)), only_id=rx_id)
            self._set_state("connected", 20)
            self._console_msg(f"[CCP] Connected to {ctx.get('name','?')}")
            get_seed = proto.build_get_seed(resource=seed_resource, ctr_override=seed_ctr)
            session.send(get_seed)
            seed_frames = session.recv(timeout_s=float(ctx.get("io_timeout_s", 0.05)), only_id=rx_id)
            if not seed_frames:
                raise RuntimeError("No GET_SEED response")
            seed_data = seed_frames[-1].data.ljust(8, b"\x00")
            protection_status = int(seed_data[3])
            self._diag["last_seed_status"] = protection_status
            seed = bytes(seed_data[4:8])
            if protection_status or force_unlock:
                if not access_key_text:
                    raise RuntimeError("missing_access_key -- enter in CCP config dialog")
                access_key = int(access_key_text.replace(" ", "").replace("0x", "").replace("0X", ""), 16)
                key = compute_key_from_seed_algo(seed=seed, access_key=access_key, seed_endian=seed_endian, sec_type=sec_type)
                unlock = proto.build_unlock(key=key, ctr_override=unlock_ctr, pad=unlock_pad)
                session.send(unlock)
                session.recv(timeout_s=float(ctx.get("io_timeout_s", 0.05)), only_id=rx_id)
                self._diag["unlock_ok"] = int(self._diag.get("unlock_ok", 0)) + 1
                self._console_msg(f"[CCP] {ctx.get('name','?')}: Unlock OK")
            if set_s_status:
                status_frame = proto.build_set_s_status(s_status)
                session.send(status_frame)
                session.recv(timeout_s=float(ctx.get("io_timeout_s", 0.05)), only_id=rx_id)
            ctx["connected"] = True
            self._diag["connect_ok"] = int(self._diag.get("connect_ok", 0)) + 1
            self._diag["last_error"] = ""
            self._set_state("ready_polling", 60)
        except Exception as e:
            ctx["connected"] = False
            self._diag["last_error"] = f"connect_or_unlock_failed:{ctx.get('name','?')}:{e}"
            self._set_state("error_connect", 90)
            self._console_msg(f"[CCP] {ctx.get('name','?')}: Unlock failed - {e}")
            try:
                print(f"[CCP:{ctx.get('name','?')}] Connect/unlock failed: {e}")
            except Exception:
                pass
            try:
                session = ctx.get("session")
                if session is not None:
                    session.close()
            except Exception:
                pass
            ctx["session"] = None
            ctx["proto"] = None

    def _next_priority_entry(self, ctx: Dict[str, Any], preferred: str) -> Optional[Dict[str, Any]]:
        entries = [x for x in (ctx.get("entries") or []) if isinstance(x, dict)]
        if not entries:
            return None
        bucket = preferred
        queue = [x for x in entries if self._poll_bucket(x.get("priority", "")) == bucket]
        if not queue and bucket == _PRIORITY_LOW:
            bucket = _PRIORITY_HIGH
            queue = [x for x in entries if self._poll_bucket(x.get("priority", "")) == bucket]
        elif not queue:
            bucket = _PRIORITY_LOW
            queue = [x for x in entries if self._poll_bucket(x.get("priority", "")) == bucket]
        if not queue:
            return None
        rr = ctx.get("priority_rr")
        if not isinstance(rr, dict):
            rr = {_PRIORITY_HIGH: 0, _PRIORITY_LOW: 0}
            ctx["priority_rr"] = rr
        idx = int(rr.get(bucket, 0)) % len(queue)
        rr[bucket] = (idx + 1) % len(queue)
        return queue[idx]

    def _next_priority_entries(self, ctx: Dict[str, Any], count: int) -> List[Dict[str, Any]]:
        selected: List[Dict[str, Any]] = []
        seen: Set[int] = set()
        seq = ctx.get("priority_sequence") or _build_priority_sequence()
        sequence_len = max(1, len(seq))
        for _ in range(max(1, int(count))):
            seq_idx = int(ctx.get("priority_index", 0)) % sequence_len
            preferred = seq[seq_idx]
            ctx["priority_index"] = (seq_idx + 1) % sequence_len
            fallback = _PRIORITY_LOW if preferred == _PRIORITY_HIGH else _PRIORITY_HIGH
            for priority in (preferred, fallback):
                entry = self._next_priority_entry(ctx, priority)
                if entry is None:
                    continue
                ident = id(entry)
                if ident in seen:
                    continue
                seen.add(ident)
                selected.append(entry)
                break
        return selected

    def _record_poll_rtt(self, ctx: Dict[str, Any], elapsed_ms: float) -> None:
        prev = float(ctx.get("rtt_avg_ms", 0.0))
        if prev <= 0.0:
            avg = float(elapsed_ms)
        else:
            avg = (prev * 0.90) + (float(elapsed_ms) * 0.10)
        ctx["last_rtt_ms"] = float(elapsed_ms)
        ctx["rtt_avg_ms"] = avg
        prev_min = float(ctx.get("rtt_min_ms", 0.0))
        prev_max = float(ctx.get("rtt_max_ms", 0.0))
        ctx["rtt_min_ms"] = float(elapsed_ms) if prev_min <= 0.0 else min(prev_min, float(elapsed_ms))
        ctx["rtt_max_ms"] = max(prev_max, float(elapsed_ms))
        self._update_ctx_load_estimates(ctx)

    def _update_ctx_load_estimates(self, ctx: Dict[str, Any]) -> None:
        entries = [x for x in (ctx.get("entries") or []) if isinstance(x, dict)]
        rtt_ms = float(ctx.get("rtt_avg_ms", 0.0))
        if rtt_ms <= 0.0:
            rtt_ms = min(max(float(ctx.get("io_timeout_s", 0.05)) * 1000.0 * 0.25, 1.0), 5.0)
        priority_counts: Dict[str, int] = {_PRIORITY_HIGH: 0, _PRIORITY_LOW: 0}
        for entry in entries:
            bucket = self._poll_bucket(entry.get("priority", ""))
            priority_counts[bucket] = priority_counts.get(bucket, 0) + 1
        target_hz = max(1, int(ctx.get("target_poll_hz", 10)))
        measured_rps = float(ctx.get("successful_reads_per_sec", 0.0))
        if measured_rps <= 0.0:
            measured_rps = max(1.0, 1000.0 / max(1.0, rtt_ms))
        high_count = int(priority_counts.get(_PRIORITY_HIGH, 0))
        low_count = int(priority_counts.get(_PRIORITY_LOW, 0))
        total_ch = max(1, high_count + low_count)
        target_rps = float(target_hz * total_ch)
        read_load_pct = (target_rps / max(0.001, measured_rps)) * 100.0
        high_budget_pct = read_load_pct
        high_over_budget = int(target_rps > measured_rps)
        seq = ctx.get("priority_sequence") or _build_priority_sequence()
        sequence_high = max(1, sum(1 for p in seq if p == _PRIORITY_HIGH))
        sequence_low = max(1, sum(1 for p in seq if p == _PRIORITY_LOW))
        total_slots = max(1, len(seq))
        high_share = (sequence_high / total_slots) if low_count > 0 else 1.0
        low_share = (sequence_low / total_slots) if high_count > 0 else 1.0
        high_period = (float(high_count) / max(0.001, measured_rps * high_share)) if high_count > 0 else 1.0
        low_period = (float(low_count) / max(0.001, measured_rps * low_share)) if low_count > 0 else 1.0
        for entry in entries:
            bucket = self._poll_bucket(entry.get("priority", ""))
            entry["expected_period_s"] = high_period if bucket == _PRIORITY_HIGH else low_period
        ctx["priority_counts"] = priority_counts
        ctx["bus_load_pct"] = read_load_pct
        ctx["high_priority_budget_pct"] = high_budget_pct
        ctx["high_priority_over_budget"] = high_over_budget

    def _run_real_worker(self) -> None:
        while not self._worker_stop.is_set():
            now = time.time()
            any_connected = False
            did_work = False
            for ctx in self._contexts:
                if not bool(ctx.get("connected", False)):
                    if now - float(ctx.get("last_connect_attempt_ts", 0.0)) >= float(ctx.get("reconnect_interval_s", 2.0)):
                        self._connect_real_ctx(ctx)
                else:
                    any_connected = True
                    if bool(ctx.get("daq_running", False)):
                        self._poll_daq_ctx(ctx)
                        did_work = True
                    elif self._rate_governor_allows(ctx, now):
                        self._poll_real_ctx(ctx)
                        did_work = True
            self._connected = any_connected or any(bool(c.get("connected", False)) for c in self._contexts)
            self._refresh_freshness(now)
            self._append_diag_values()
            with self._state_lock:
                self._snapshot_values = dict(self._values)
            self._worker_stop.wait(0.001 if did_work else 0.005)

    @staticmethod
    def _rate_governor_allows(ctx: Dict[str, Any], now: float) -> bool:
        target_hz = int(ctx.get("target_poll_hz", 10))
        n_entries = len(ctx.get("entries") or [])
        if n_entries == 0:
            return False
        target_reads_per_sec = float(target_hz * n_entries)
        window_start = float(ctx.get("_rg_window_ts", 0.0))
        window_reads = int(ctx.get("_rg_window_reads", 0))
        if window_start <= 0.0 or (now - window_start) >= 1.0:
            ctx["_rg_window_ts"] = now
            ctx["_rg_window_reads"] = 0
            return True
        elapsed = max(0.001, now - window_start)
        current_rate = float(window_reads) / elapsed
        return current_rate < target_reads_per_sec

    def _poll_daq_ctx(self, ctx: Dict[str, Any]) -> None:
        session = ctx.get("session")
        meta = ctx.get("daq_meta") if isinstance(ctx.get("daq_meta"), dict) else {}
        if session is None:
            ctx["connected"] = False
            self._diag["last_error"] = "daq_no_session"
            self._set_state("error_daq_session", 94)
            return
        try:
            dto_id = int(meta.get("dto_id", ctx.get("rx_id", 0)))
            dto_can_ids: set[int] = ctx.get("daq_dto_can_ids") or {int(dto_id), 0}
            pid_map = ctx.get("daq_pid_map") or {}
            frames = session.recv(timeout_s=0.010, only_id=None)
            now = time.time()
            ctx["daq_poll_raw_count"] = int(ctx.get("daq_poll_raw_count", 0)) + len(frames)
            log_elapsed = now - float(ctx.get("daq_poll_log_ts", now))
            if log_elapsed >= 5.0:
                dto_total = int(ctx.get("daq_dto_count", 0))
                decode_errs = int(ctx.get("daq_decode_errors", 0))
                raw_count = int(ctx.get("daq_poll_raw_count", 0))
                filtered_count = int(ctx.get("daq_poll_filtered_count", 0))
                rate_hz = float(ctx.get("daq_dto_rate_hz", 0.0))
                n_lists = len(ctx.get("daq_active_lists") or [])
                if dto_total == 0 and not ctx.get("_bus_sniff_done"):
                    ctx["_bus_sniff_done"] = True
                    try:
                        sniff = session.recv(timeout_s=0.1, only_id=None)
                        id_counts: Dict[int, int] = {}
                        for sf in sniff:
                            aid = int(sf.arbitration_id)
                            id_counts[aid] = id_counts.get(aid, 0) + 1
                        top = sorted(id_counts.items(), key=lambda x: -x[1])[:6]
                        id_str = ", ".join(f"0x{aid:08X}={cnt}" for aid, cnt in top)
                        print(f"[CCP:{ctx.get('name','?')}] Bus sniff ({len(sniff)} frames): {id_str or 'none'}")
                    except Exception as se:
                        print(f"[CCP:{ctx.get('name','?')}] Bus sniff failed: {se}")
                try:
                    pid_hits = ctx.get("_pid_hits", {})
                    expected_pids = sorted(pid_map.keys())
                    active_pids = sorted(p for p in expected_pids if pid_hits.get(p, 0) > 0)
                    dead_pids = sorted(p for p in expected_pids if pid_hits.get(p, 0) == 0)
                    unexpected = sorted(p for p in pid_hits if p not in pid_map)
                    print(
                        f"[CCP:{ctx.get('name','?')}] DAQ poll: "
                        f"raw={raw_count} filtered={filtered_count} "
                        f"decoded={dto_total} ({rate_hz:.1f} Hz)"
                    )
                    if dead_pids:
                        print(f"[CCP:{ctx.get('name','?')}] WARNING: PIDs with 0 hits: {dead_pids}")
                    if unexpected:
                        print(f"[CCP:{ctx.get('name','?')}] Unexpected PIDs (not in map): {unexpected} hits={[pid_hits[p] for p in unexpected]}")
                except Exception:
                    pass
                ctx["_pid_hits"] = {}
                if not ctx.get("_nan_report_done") and dto_total > 0:
                    ctx["_nan_report_done"] = True
                    nan_channels = [
                        a for a in self._values
                        if a.startswith(str(ctx.get("_alias_prefix", "")))
                        and not a.startswith("CCP/")
                        and math.isnan(self._values[a])
                    ]
                    if nan_channels:
                        pid_map_local = ctx.get("daq_pid_map") or {}
                        print(f"[CCP:{ctx.get('name','?')}] WARNING: {len(nan_channels)} channel(s) still NaN after streaming:")
                        for a in nan_channels[:15]:
                            found_pid = None
                            for pid_val, ents in pid_map_local.items():
                                for e in ents:
                                    if e.get("alias") == a:
                                        found_pid = pid_val
                                        break
                            print(f"[CCP:{ctx.get('name','?')}]   {a} (pid={found_pid}, dtype={next((e.get('dtype') for ents in pid_map_local.values() for e in ents if e.get('alias')==a), '?')})")
                ctx["daq_poll_log_ts"] = now
                ctx["daq_poll_raw_count"] = 0
                ctx["daq_poll_filtered_count"] = 0
            if not frames:
                return
            decode_err_reported: set = ctx.setdefault("_decode_err_reported", set())
            for fr in frames:
                frame_id = int(fr.arbitration_id)
                if frame_id not in dto_can_ids:
                    ctx["daq_poll_filtered_count"] = int(ctx.get("daq_poll_filtered_count", 0)) + 1
                    continue
                data = fr.data.ljust(8, b"\x00")
                pid = int(data[0])
                pid_hits = ctx.setdefault("_pid_hits", {})
                pid_hits[pid] = pid_hits.get(pid, 0) + 1
                entries = pid_map.get(pid)
                if not entries:
                    ctx["daq_decode_errors"] = int(ctx.get("daq_decode_errors", 0)) + 1
                    continue
                for entry in entries:
                    ch_name = entry.get("name", "?")
                    try:
                        offset = int(entry.get("offset", 0))
                        size = int(entry.get("size", 1))
                        raw = data[1 + offset:1 + offset + size]
                        if len(raw) < size:
                            ctx["daq_decode_errors"] = int(ctx.get("daq_decode_errors", 0)) + 1
                            if ch_name not in decode_err_reported:
                                decode_err_reported.add(ch_name)
                                print(f"[CCP:{ctx.get('name','?')}] DECODE ERR: {ch_name} -- raw too short (need {size}B, got {len(raw)}B at off={offset})")
                            continue
                        value = decode_value(
                            dtype=entry.get("dtype"),
                            raw=raw,
                            byteorder=str(entry.get("poll_endian") or "big"),
                            limits=entry.get("limits"),
                            coeffs=entry.get("coeffs"),
                        )
                        if math.isnan(value) and ch_name not in decode_err_reported:
                            decode_err_reported.add(ch_name)
                            hex_raw = " ".join(f"{b:02X}" for b in raw)
                            print(
                                f"[CCP:{ctx.get('name','?')}] NaN DECODE: {ch_name} "
                                f"dtype={entry.get('dtype')} raw=[{hex_raw}] "
                                f"coeffs={entry.get('coeffs')} limits={entry.get('limits')}"
                            )
                        alias = str(entry.get("alias") or "")
                        if alias:
                            self._values[alias] = float(value)
                            self._value_ts[alias] = now
                            entry["last_success_ts"] = now
                    except Exception as exc:
                        ctx["daq_decode_errors"] = int(ctx.get("daq_decode_errors", 0)) + 1
                        if ch_name not in decode_err_reported:
                            decode_err_reported.add(ch_name)
                            hex_raw = " ".join(f"{b:02X}" for b in data[:8])
                            print(f"[CCP:{ctx.get('name','?')}] DECODE ERR: {ch_name} -- {exc} raw=[{hex_raw}]")
                ctx["daq_dto_count"] = int(ctx.get("daq_dto_count", 0)) + 1
                ctx["daq_last_pid"] = pid
                ctx["daq_last_dto_id"] = int(fr.arbitration_id)
                start = float(ctx.get("daq_dto_window_ts", 0.0))
                if start <= 0.0:
                    ctx["daq_dto_window_ts"] = now
                    start = now
                ctx["daq_dto_window_count"] = int(ctx.get("daq_dto_window_count", 0)) + 1
                elapsed = max(0.001, now - start)
                if elapsed >= 1.0:
                    ctx["daq_dto_rate_hz"] = float(ctx.get("daq_dto_window_count", 0)) / elapsed
                    ctx["daq_dto_window_ts"] = now
                    ctx["daq_dto_window_count"] = 0
            self._set_state("daq_streaming", 75)
        except Exception as exc:
            ctx["connected"] = False
            self._diag["last_error"] = f"daq_poll_exception:{exc}"
            self._set_state("error_daq_poll", 95)
            self._console_msg(f"[CCP] {ctx.get('name','?')}: Connection lost")

    def _poll_real_ctx(self, ctx: Dict[str, Any]) -> None:
        session = ctx.get("session")
        proto = ctx.get("proto")
        if session is None or proto is None:
            ctx["connected"] = False
            self._diag["last_error"] = "session_not_ready"
            self._set_state("error_session", 91)
            return
        try:
            entries = ctx.get("entries") or []
            if not entries:
                self._diag["last_error"] = "no_measurements"
                self._set_state("no_measurements", 61)
                return
            self._set_state("polling", 70)
            if not ctx.get("_console_poll_started"):
                ctx["_console_poll_started"] = True
                self._console_msg(f"[CCP] {ctx.get('name','?')}: Polling {len(entries)} channels")
            target_hz = int(ctx.get("target_poll_hz", 10))
            auto_cpt = max(1, min(len(entries), int(math.ceil(target_hz * len(entries) / 200.0))))
            count = min(len(entries), max(1, auto_cpt))
            selected = self._next_priority_entries(ctx, count)
            _pc = time.perf_counter
            sweep_start = _pc()
            sweep_success = 0
            ctx["poll_selected_count"] = len(selected)
            ctx["_rg_window_reads"] = int(ctx.get("_rg_window_reads", 0)) + len(selected)
            for entry in selected:
                attempt_ts = _pc()
                entry["last_attempt_ts"] = time.time()
                val = self._poll_short_up_ctx(ctx, entry)
                elapsed_ms = (_pc() - attempt_ts) * 1000.0
                self._record_poll_rtt(ctx, elapsed_ms)
                rtt_diag_count = int(ctx.get("_rtt_diag_count", 0))
                if rtt_diag_count < 10:
                    ctx["_rtt_diag_count"] = rtt_diag_count + 1
                    ok = "OK" if val is not None else "FAIL"
                    print(
                        f"[CCP:{ctx.get('name','?')}] RTT #{rtt_diag_count}: "
                        f"{elapsed_ms:.1f}ms {ok} ch={entry.get('name','?')} "
                        f"cpt={count} rx_mode={ctx.get('session').rx_mode if ctx.get('session') else '?'}"
                    )
                if val is not None:
                    alias = str(entry["alias"])
                    prev_success_ts = float(entry.get("last_success_ts", 0.0))
                    success_ts = time.time()
                    self._values[alias] = float(val)
                    self._value_ts[alias] = success_ts
                    entry["last_success_ts"] = success_ts
                    if prev_success_ts > 0.0 and success_ts > prev_success_ts:
                        entry["achieved_hz"] = 1.0 / max(0.001, success_ts - prev_success_ts)
                    self._diag["poll_success"] = int(self._diag.get("poll_success", 0)) + 1
                    self._diag["last_error"] = ""
                    sweep_success += 1
                else:
                    self._diag["poll_fail"] = int(self._diag.get("poll_fail", 0)) + 1
                    try:
                        pf = int(self._diag.get("poll_fail", 0))
                        if pf % 50 == 0:
                            print(f"[CCP:{ctx.get('name','?')}] Poll fails={pf} last_error={self._diag.get('last_error','')}")
                    except Exception:
                        pass
            sweep_ms = (_pc() - sweep_start) * 1000.0
            ctx["poll_loop_ms"] = sweep_ms
            self._record_throughput_window(ctx, attempts=len(selected), successes=sweep_success)
        except Exception:
            ctx["connected"] = False
            ctx["_console_poll_started"] = False
            self._diag["last_error"] = "poll_exception"
            self._set_state("error_poll", 92)
            self._console_msg(f"[CCP] {ctx.get('name','?')}: Connection lost")

    def _record_throughput_window(self, ctx: Dict[str, Any], attempts: int, successes: int) -> None:
        now = time.time()
        start = float(ctx.get("throughput_window_ts", 0.0))
        if start <= 0.0:
            ctx["throughput_window_ts"] = now
            start = now
        ctx["throughput_window_attempts"] = int(ctx.get("throughput_window_attempts", 0)) + int(attempts)
        ctx["throughput_window_success"] = int(ctx.get("throughput_window_success", 0)) + int(successes)
        elapsed = max(0.001, now - start)
        if elapsed >= 1.0:
            attempts_window = int(ctx.get("throughput_window_attempts", 0))
            success_window = int(ctx.get("throughput_window_success", 0))
            attempted_rate = float(attempts_window) / elapsed
            success_rate = float(success_window) / elapsed
            ctx["attempted_reads_per_sec"] = attempted_rate
            ctx["successful_reads_per_sec"] = success_rate
            entries = [x for x in (ctx.get("entries") or []) if isinstance(x, dict)]
            sweep_s = (float(len(entries)) / success_rate) if success_rate > 0.0 else 0.0
            ctx["estimated_sweep_s"] = sweep_s
            ctx["throughput_window_ts"] = now
            ctx["throughput_window_attempts"] = 0
            ctx["throughput_window_success"] = 0
            target_hz = int(ctx.get("target_poll_hz", 10))
            high_count = sum(1 for e in entries if self._poll_bucket(e.get("priority", "")) == _PRIORITY_HIGH)
            low_count = len(entries) - high_count
            if low_count == 0:
                high_hz = (success_rate / max(1, high_count)) if success_rate > 0 else 0.0
                low_hz = 0.0
            elif high_count == 0:
                high_hz = 0.0
                low_hz = (success_rate / max(1, low_count)) if success_rate > 0 else 0.0
            else:
                high_hz = (success_rate * 0.75 / max(1, high_count)) if success_rate > 0 else 0.0
                low_hz = (success_rate * 0.25 / max(1, low_count)) if success_rate > 0 else 0.0
            target_rps = float(target_hz * len(entries))
            budget_pct = (success_rate / target_rps * 100.0) if target_rps > 0 else 0.0
            ctx["high_achieved_hz"] = high_hz
            ctx["low_achieved_hz"] = low_hz
            ctx["budget_pct"] = budget_pct
            log_interval = int(ctx.get("_throughput_log_count", 0))
            ctx["_throughput_log_count"] = log_interval + 1
            if log_interval % 5 == 0:
                parts = [f"SHORT_UP: {success_rate:.0f} reads/sec"]
                if high_count:
                    parts.append(f"HIGH: {high_count}ch @ ~{high_hz:.1f} Hz")
                if low_count:
                    parts.append(f"LOW: {low_count}ch @ ~{low_hz:.1f} Hz")
                parts.append(f"Budget: {budget_pct:.0f}%")
                print(f"[CCP:{ctx.get('name','?')}] {' | '.join(parts)}")
            if int(ctx.get("daq_fallback_active", 0)):
                est_hz = (1.0 / sweep_s) if sweep_s > 0.0 else 0.0
                print(
                    f"[CCP:{ctx.get('name','?')}] WARNING: SHORT_UP fallback active "
                    f"-- estimated sample rate: ~{est_hz:.1f} Hz ({len(entries)} channels)"
                )

    def _record_recv_stats(self, ctx: Dict[str, Any], session: Any, pred_ms: float | None = None) -> None:
        stats = getattr(session, "last_recv_stats", None)
        if not isinstance(stats, dict):
            return
        read_calls = float(stats.get("read_calls", 0.0))
        empty_reads = float(stats.get("empty_reads", 0.0))
        prev_calls = float(ctx.get("rx_read_calls", 0.0))
        prev_empty = float(ctx.get("rx_empty_reads", 0.0))
        ctx["rx_read_calls"] = prev_calls + read_calls
        ctx["rx_empty_reads"] = prev_empty + empty_reads
        if pred_ms is None:
            prev_avg = float(ctx.get("rx_read_calls_per_response", 0.0))
            ctx["rx_read_calls_per_response"] = read_calls if prev_avg <= 0.0 else (prev_avg * 0.90) + (read_calls * 0.10)
        ctx["rx_mode_code"] = int(float(stats.get("rx_mode_code", 0.0)))
        if pred_ms is not None:
            prev_pred = float(ctx.get("rx_predrain_ms", 0.0))
            ctx["rx_predrain_ms"] = float(pred_ms) if prev_pred <= 0.0 else (prev_pred * 0.90) + (float(pred_ms) * 0.10)

    def _poll_short_up_ctx(self, ctx: Dict[str, Any], entry: Dict[str, Any]) -> Optional[float]:
        _pc = time.perf_counter
        session = ctx.get("session")
        proto = ctx.get("proto")
        rx_id = int(ctx.get("rx_id", 0))
        if session is None or proto is None:
            self._diag["last_error"] = "poll_no_session"
            return None
        pred_start = _pc()
        session.recv(timeout_s=0.001, only_id=rx_id)
        self._record_recv_stats(ctx, session, pred_ms=(_pc() - pred_start) * 1000.0)
        req = proto.build_short_up(
            size=int(entry["size"]),
            address=int(entry["address"]),
            extension=int(entry["extension"]),
            byteorder=str(entry["mta_addr_endian"]),
        )
        req_ctr = req.data[1] if req.data else None
        session.send(req)
        sup_timeout_s = float(ctx.get("short_up_timeout_s", 0.015))
        deadline = _pc() + sup_timeout_s
        while _pc() < deadline:
            rx = session.recv(timeout_s=min(0.003, sup_timeout_s), only_id=rx_id)
            self._record_recv_stats(ctx, session)
            for fr in rx:
                data = fr.data.ljust(8, b"\x00")
                if data[0] != 0xFF:
                    continue
                if req_ctr is not None:
                    ctr_match = (data[1] == 0x00 and data[2] == req_ctr) or (data[2] == 0x00 and data[1] == req_ctr)
                else:
                    ctr_match = (data[1] == 0x00) or (data[2] == 0x00)
                if not ctr_match:
                    self._diag["ctr_mismatch"] = int(self._diag.get("ctr_mismatch", 0)) + 1
                    continue
                rc = int(data[1]) if data[1] != req_ctr else int(data[2])
                self._diag["last_rc"] = rc
                if rc != 0:
                    self._diag["last_error"] = f"crm_rc:{rc}"
                    ctx["crm_error_count"] = int(ctx.get("crm_error_count", 0)) + 1
                    return None
                size = int(entry["size"])
                payload = data[3:3 + size]
                if len(payload) < size:
                    self._diag["last_error"] = "payload_short"
                    continue
                return decode_value(
                    dtype=entry.get("dtype"),
                    raw=payload,
                    byteorder=str(entry.get("poll_endian") or "big"),
                    limits=entry.get("limits"),
                    coeffs=entry.get("coeffs"),
                )
        self._diag["last_error"] = f"short_up_timeout:{entry.get('name','?')}"
        ctx["short_up_timeout_count"] = int(ctx.get("short_up_timeout_count", 0)) + 1
        return None

    def _set_state(self, state: str, code: int) -> None:
        self._diag["state"] = str(state)
        self._diag["state_code"] = int(code)

    def _append_diag_values(self) -> None:
        self._refresh_load_diag()
        self._values["CCP/connected"] = 1.0 if self._connected else 0.0
        self._values["CCP/state_code"] = float(int(self._diag.get("state_code", 0)))
        self._values["CCP/connect_attempts"] = float(int(self._diag.get("connect_attempts", 0)))
        self._values["CCP/connect_ok"] = float(int(self._diag.get("connect_ok", 0)))
        self._values["CCP/unlock_ok"] = float(int(self._diag.get("unlock_ok", 0)))
        self._values["CCP/poll_success"] = float(int(self._diag.get("poll_success", 0)))
        self._values["CCP/poll_fail"] = float(int(self._diag.get("poll_fail", 0)))
        self._values["CCP/last_seed_status"] = float(int(self._diag.get("last_seed_status", -1)))
        self._values["CCP/last_rc"] = float(int(self._diag.get("last_rc", -1)))
        self._values["CCP/ctr_mismatch"] = float(int(self._diag.get("ctr_mismatch", 0)))
        self._values["CCP/fresh_age_s"] = float(self._diag.get("fresh_age_s", -1.0))
        self._values["CCP/fresh_max_channel_age_s"] = float(self._diag.get("fresh_max_channel_age_s", -1.0))
        self._values["CCP/freshness_state_code"] = float(int(self._diag.get("freshness_state_code", -1)))
        self._values["CCP/freshness_warn_count"] = float(int(self._diag.get("freshness_warn_count", 0)))
        self._values["CCP/freshness_stale_count"] = float(int(self._diag.get("freshness_stale_count", 0)))
        self._values["CCP/bus_load_pct"] = float(self._diag.get("bus_load_pct", 0.0))
        self._values["CCP/poll_rtt_avg_ms"] = float(self._diag.get("poll_rtt_avg_ms", 0.0))
        self._values["CCP/high_priority_budget_pct"] = float(self._diag.get("high_priority_budget_pct", 0.0))
        self._values["CCP/high_priority_over_budget"] = float(int(self._diag.get("high_priority_over_budget", 0)))
        self._values["CCP/short_up_rtt_last_ms"] = float(self._diag.get("short_up_rtt_last_ms", 0.0))
        self._values["CCP/short_up_rtt_min_ms"] = float(self._diag.get("short_up_rtt_min_ms", 0.0))
        self._values["CCP/short_up_rtt_max_ms"] = float(self._diag.get("short_up_rtt_max_ms", 0.0))
        self._values["CCP/short_up_timeout_count"] = float(int(self._diag.get("short_up_timeout_count", 0)))
        self._values["CCP/crm_error_count"] = float(int(self._diag.get("crm_error_count", 0)))
        self._values["CCP/poll_selected_count"] = float(int(self._diag.get("poll_selected_count", 0)))
        self._values["CCP/poll_loop_ms"] = float(self._diag.get("poll_loop_ms", 0.0))
        self._values["CCP/attempted_reads_per_sec"] = float(self._diag.get("attempted_reads_per_sec", 0.0))
        self._values["CCP/successful_reads_per_sec"] = float(self._diag.get("successful_reads_per_sec", 0.0))
        self._values["CCP/estimated_sweep_s"] = float(self._diag.get("estimated_sweep_s", 0.0))
        self._values["CCP/rx_read_calls"] = float(self._diag.get("rx_read_calls", 0.0))
        self._values["CCP/rx_empty_reads"] = float(self._diag.get("rx_empty_reads", 0.0))
        self._values["CCP/rx_read_calls_per_response"] = float(self._diag.get("rx_read_calls_per_response", 0.0))
        self._values["CCP/rx_predrain_ms"] = float(self._diag.get("rx_predrain_ms", 0.0))
        self._values["CCP/rx_mode_code"] = float(int(self._diag.get("rx_mode_code", 0)))
        self._values["CCP/daq_enabled"] = float(int(self._diag.get("daq_enabled", 0)))
        self._values["CCP/daq_running"] = float(int(self._diag.get("daq_running", 0)))
        self._values["CCP/daq_setup_ok"] = float(int(self._diag.get("daq_setup_ok", 0)))
        self._values["CCP/daq_dto_count"] = float(int(self._diag.get("daq_dto_count", 0)))
        self._values["CCP/daq_dto_rate_hz"] = float(self._diag.get("daq_dto_rate_hz", 0.0))
        self._values["CCP/daq_odt_count"] = float(int(self._diag.get("daq_odt_count", 0)))
        self._values["CCP/daq_active_list_count"] = float(int(self._diag.get("daq_active_list_count", 0)))
        self._values["CCP/daq_decode_errors"] = float(int(self._diag.get("daq_decode_errors", 0)))
        self._values["CCP/daq_fallback_active"] = float(int(self._diag.get("daq_fallback_active", 0)))
        self._values["CCP/daq_last_pid"] = float(int(self._diag.get("daq_last_pid", -1)))
        self._values["CCP/daq_last_dto_id"] = float(int(self._diag.get("daq_last_dto_id", 0)))

        self._values["CCP/conn_ok"] = 1.0 if self._connected else 0.0
        data_flowing = (
            float(self._diag.get("successful_reads_per_sec", 0.0)) > 0.0
            or bool(self._diag.get("daq_running", 0))
        )
        self._values["CCP/health_ok"] = 1.0 if (self._connected and data_flowing) else 0.0

    def _refresh_load_diag(self) -> None:
        loads = [float(c.get("bus_load_pct", 0.0)) for c in self._contexts]
        rtts = [float(c.get("rtt_avg_ms", 0.0)) for c in self._contexts if float(c.get("rtt_avg_ms", 0.0)) > 0.0]
        self._diag["bus_load_pct"] = max(loads) if loads else 0.0
        self._diag["poll_rtt_avg_ms"] = (sum(rtts) / len(rtts)) if rtts else 0.0
        budgets = [float(c.get("high_priority_budget_pct", 0.0)) for c in self._contexts]
        over = [int(c.get("high_priority_over_budget", 0)) for c in self._contexts]
        self._diag["high_priority_budget_pct"] = max(budgets) if budgets else 0.0
        self._diag["high_priority_over_budget"] = 1 if any(over) else 0
        last_rtts = [float(c.get("last_rtt_ms", 0.0)) for c in self._contexts]
        min_rtts = [float(c.get("rtt_min_ms", 0.0)) for c in self._contexts if float(c.get("rtt_min_ms", 0.0)) > 0.0]
        max_rtts = [float(c.get("rtt_max_ms", 0.0)) for c in self._contexts]
        self._diag["short_up_rtt_last_ms"] = max(last_rtts) if last_rtts else 0.0
        self._diag["short_up_rtt_min_ms"] = min(min_rtts) if min_rtts else 0.0
        self._diag["short_up_rtt_max_ms"] = max(max_rtts) if max_rtts else 0.0
        self._diag["short_up_timeout_count"] = sum(int(c.get("short_up_timeout_count", 0)) for c in self._contexts)
        self._diag["crm_error_count"] = sum(int(c.get("crm_error_count", 0)) for c in self._contexts)
        self._diag["poll_selected_count"] = sum(int(c.get("poll_selected_count", 0)) for c in self._contexts)
        self._diag["poll_loop_ms"] = max(float(c.get("poll_loop_ms", 0.0)) for c in self._contexts) if self._contexts else 0.0
        self._diag["attempted_reads_per_sec"] = sum(float(c.get("attempted_reads_per_sec", 0.0)) for c in self._contexts)
        self._diag["successful_reads_per_sec"] = sum(float(c.get("successful_reads_per_sec", 0.0)) for c in self._contexts)
        success_rate = float(self._diag.get("successful_reads_per_sec", 0.0))
        channel_count = sum(len([x for x in (c.get("entries") or []) if isinstance(x, dict)]) for c in self._contexts)
        self._diag["estimated_sweep_s"] = (float(channel_count) / success_rate) if success_rate > 0.0 else 0.0
        self._diag["rx_read_calls"] = sum(float(c.get("rx_read_calls", 0.0)) for c in self._contexts)
        self._diag["rx_empty_reads"] = sum(float(c.get("rx_empty_reads", 0.0)) for c in self._contexts)
        rx_avgs = [float(c.get("rx_read_calls_per_response", 0.0)) for c in self._contexts if float(c.get("rx_read_calls_per_response", 0.0)) > 0.0]
        self._diag["rx_read_calls_per_response"] = (sum(rx_avgs) / len(rx_avgs)) if rx_avgs else 0.0
        pred_avgs = [float(c.get("rx_predrain_ms", 0.0)) for c in self._contexts if float(c.get("rx_predrain_ms", 0.0)) > 0.0]
        self._diag["rx_predrain_ms"] = (sum(pred_avgs) / len(pred_avgs)) if pred_avgs else 0.0
        self._diag["rx_mode_code"] = max(int(c.get("rx_mode_code", 0)) for c in self._contexts) if self._contexts else 0
        self._diag["daq_enabled"] = 1 if any(str(c.get("acquisition_mode", "")).lower() == "daq" for c in self._contexts) else 0
        self._diag["daq_running"] = 1 if any(bool(c.get("daq_running", False)) for c in self._contexts) else 0
        self._diag["daq_setup_ok"] = 1 if any(int(c.get("daq_setup_ok", 0)) for c in self._contexts) else 0
        self._diag["daq_dto_count"] = sum(int(c.get("daq_dto_count", 0)) for c in self._contexts)
        self._diag["daq_dto_rate_hz"] = sum(float(c.get("daq_dto_rate_hz", 0.0)) for c in self._contexts)
        self._diag["daq_odt_count"] = sum(int(c.get("daq_odt_count", 0)) for c in self._contexts)
        self._diag["daq_active_list_count"] = sum(len(c.get("daq_active_lists") or []) for c in self._contexts)
        self._diag["daq_decode_errors"] = sum(int(c.get("daq_decode_errors", 0)) for c in self._contexts)
        self._diag["daq_fallback_active"] = 1 if any(int(c.get("daq_fallback_active", 0)) for c in self._contexts) else 0
        self._diag["daq_last_pid"] = max(int(c.get("daq_last_pid", -1)) for c in self._contexts) if self._contexts else -1
        self._diag["daq_last_dto_id"] = max(int(c.get("daq_last_dto_id", 0)) for c in self._contexts) if self._contexts else 0

    def _refresh_freshness(self, now_s: float) -> None:
        age_values: List[float] = []
        age_ratios: List[float] = []
        for ctx in self._contexts:
            for entry in ctx.get("entries", []) or []:
                if not isinstance(entry, dict):
                    continue
                alias = str(entry.get("alias") or "")
                if not alias:
                    continue
                ts = float(self._value_ts.get(alias, 0.0))
                if ts <= 0.0:
                    continue
                age = max(0.0, now_s - ts)
                period_s = max(0.001, float(entry.get("expected_period_s", 1.0 / max(0.001, self._core_sample_rate_hz()))))
                age_values.append(age)
                age_ratios.append(age / period_s)
        if age_values:
            plugin_age = min(age_values)
            max_age = max(age_values)
            max_ratio = max(age_ratios) if age_ratios else -1.0
        else:
            plugin_age = -1.0
            max_age = -1.0
            max_ratio = -1.0
        self._diag["fresh_age_s"] = plugin_age
        self._diag["fresh_max_channel_age_s"] = max_age
        prev_state = int(self._diag.get("freshness_state_code", -1))
        if not self._connected or max_age < 0.0:
            new_state = -1
        else:
            if max_ratio > 1.00:
                new_state = 2
            elif max_ratio > 0.25:
                new_state = 1
            else:
                new_state = 0
        self._diag["freshness_state_code"] = int(new_state)
        if new_state == 1:
            self._diag["freshness_warn_count"] = int(self._diag.get("freshness_warn_count", 0)) + 1
        elif new_state == 2:
            self._diag["freshness_stale_count"] = int(self._diag.get("freshness_stale_count", 0)) + 1
        last_fresh_print = float(self._diag.get("_fresh_print_ts", 0.0))
        if new_state > 0 and new_state != prev_state and (now_s - last_fresh_print) > 5.0:
            self._diag["_fresh_print_ts"] = now_s
            label = "WARN" if new_state == 1 else "STALE"
            print(
                "[CCP] Freshness %s: max_age=%.3fs max_ratio=%.2f"
                % (label, max_age, max_ratio)
            )

    def _recommended_poll_channels_per_tick(self, channel_count: int, target_poll_hz: int = 10) -> int:
        if channel_count <= 0:
            return 1
        target_rps = float(target_poll_hz) * float(channel_count)
        assumed_capacity_rps = 200.0
        rec = max(1, int(math.ceil(target_rps / assumed_capacity_rps)))
        return min(channel_count, rec)

    def _run_throughput_probe(self, ctx: Dict[str, Any], duration_s: float = 5.0) -> Dict[str, float]:
        entries = [x for x in (ctx.get("entries") or []) if isinstance(x, dict)]
        duration_s = max(1.0, float(duration_s))
        count = max(1, min(len(entries), 4))
        deadline = time.time() + duration_s
        attempts = 0
        successes = 0
        timeouts_start = int(ctx.get("short_up_timeout_count", 0))
        crm_start = int(ctx.get("crm_error_count", 0))
        rtts: List[float] = []
        while time.time() < deadline and entries:
            selected = self._next_priority_entries(ctx, count)
            for entry in selected:
                if time.time() >= deadline:
                    break
                attempts += 1
                req_start = time.time()
                val = self._poll_short_up_ctx(ctx, entry)
                elapsed_ms = (time.time() - req_start) * 1000.0
                rtts.append(elapsed_ms)
                self._record_poll_rtt(ctx, elapsed_ms)
                if val is not None:
                    successes += 1
        elapsed = max(0.001, duration_s - max(0.0, deadline - time.time()))
        rtts_sorted = sorted(rtts)
        p95 = rtts_sorted[min(len(rtts_sorted) - 1, int(math.ceil(len(rtts_sorted) * 0.95)) - 1)] if rtts_sorted else 0.0
        avg = (sum(rtts) / len(rtts)) if rtts else 0.0
        success_rate = float(successes) / elapsed
        return {
            "duration_s": elapsed,
            "attempts": float(attempts),
            "successes": float(successes),
            "attempted_reads_per_sec": float(attempts) / elapsed,
            "successful_reads_per_sec": success_rate,
            "timeout_count": float(max(0, int(ctx.get("short_up_timeout_count", 0)) - timeouts_start)),
            "crm_error_count": float(max(0, int(ctx.get("crm_error_count", 0)) - crm_start)),
            "rtt_avg_ms": avg,
            "rtt_p95_ms": p95,
            "estimated_sweep_s": (float(len(entries)) / success_rate) if success_rate > 0.0 else 0.0,
        }

    def run_connection_test(self, emit) -> None:
        """Run a step-by-step CCP test and emit status lines.

        `emit` should be a callable accepting (step: str, ok: bool, detail: str, done: bool).
        """
        def _emit(step: str, ok: bool, detail: str, done: bool = False) -> None:
            try:
                emit(step, ok, detail, done)
            except Exception:
                pass

        if self.mode != "real":
            _emit("validate", False, "CCP mode is not real", True)
            return

        try:
            st = self.validate()
            if not st.ok:
                _emit("validate", False, st.message, True)
                return
            _emit("validate", True, "Configuration is valid")
        except Exception as e:
            _emit("validate", False, f"Validation exception: {e}", True)
            return

        try:
            if not self._contexts:
                self.configure()
            if not self._contexts:
                _emit("connect_unlock", False, "No CCP devices configured", True)
                return
            ctx = self._contexts[0]
            self._connect_real_ctx(ctx)
            if not bool(ctx.get("connected", False)):
                _emit("connect_unlock", False, str(self._diag.get("last_error", "connect failed")), True)
                return
            _emit("connect_unlock", True, "Connected, seed/unlock path completed")
        except Exception as e:
            _emit("connect_unlock", False, f"Connect exception: {e}", True)
            return

        try:
            entries = ctx.get("entries") or []
            if not entries:
                _emit("poll_prepare", False, "No A2L measurements configured", True)
                return
            entry = entries[0]
            val = self._poll_short_up_ctx(ctx, entry)
            if val is None:
                _emit(
                    "poll_one",
                    False,
                    f"Failed reading {entry.get('name','?')} ({self._diag.get('last_error','unknown')})",
                    True,
                )
                return
            _emit("poll_one", True, f"{entry.get('name','?')}={val:.3f}", False)
            probe = self._run_throughput_probe(ctx, duration_s=5.0)
            timeout_rate = (probe["timeout_count"] / max(1.0, probe["attempts"])) * 100.0
            _emit(
                "throughput_probe",
                True,
                "attempted={attempted:.1f}/s success={success:.1f}/s "
                "timeouts={timeouts:.0f} ({timeout_rate:.1f}%) avg={avg:.2f}ms "
                "p95={p95:.2f}ms est_sweep={sweep:.2f}s".format(
                    attempted=probe["attempted_reads_per_sec"],
                    success=probe["successful_reads_per_sec"],
                    timeouts=probe["timeout_count"],
                    timeout_rate=timeout_rate,
                    avg=probe["rtt_avg_ms"],
                    p95=probe["rtt_p95_ms"],
                    sweep=probe["estimated_sweep_s"],
                ),
                True,
            )
        except Exception as e:
            _emit("poll_one", False, f"Polling exception: {e}", True)
