# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import os
import time
import math
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .base import BasePlugin, PluginStatus
from ._ccp_a2l import A2LChannel, parse_a2l, dtype_size, decode_value
from ._ccp_protocol import (
    nixnet,
    compute_key_from_seed_algo,
    CanFrame,
    CcpProto,
    NixnetSession,
)


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
        }

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
        top_io = self.config.get("io_timeout_s", 0.05)
        top_reconn = self.config.get("reconnect_interval_s", 2.0)
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
                out.append(
                    {
                        "name": name,
                        "role": role,
                        "session": session,
                        "security": security,
                        "a2l": a2l,
                        "measurements": meas,
                        "poll_interval_ms": dev.get("poll_interval_ms", top_poll_ms),
                        "poll_channels_per_tick": dev.get("poll_channels_per_tick", top_cpt),
                        "io_timeout_s": dev.get("io_timeout_s", top_io),
                        "reconnect_interval_s": dev.get("reconnect_interval_s", top_reconn),
                    }
                )
            if out:
                return out
        role = "primary"
        top_session.setdefault("station_address", self._role_to_station_address(role))
        out.append(
            {
                "name": "CCP Primary",
                "role": role,
                "session": top_session,
                "security": top_security,
                "a2l": top_a2l,
                "measurements": top_meas,
                "poll_interval_ms": top_poll_ms,
                "poll_channels_per_tick": top_cpt,
                "io_timeout_s": top_io,
                "reconnect_interval_s": top_reconn,
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
                poll_s = max(0.02, float(dcfg.get("poll_interval_ms", 100)) / 1000.0)
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
                    "role": str(dcfg.get("role") or "primary"),
                    "session_cfg": dict(dcfg.get("session") or {}),
                    "security_cfg": dict(dcfg.get("security") or {}),
                    "a2l_cfg": dict(dcfg.get("a2l") or {}),
                    "meas_cfg": dict(dcfg.get("measurements") or {}),
                    "poll_interval_s": poll_s,
                    "poll_channels_per_tick": cpt,
                    "io_timeout_s": io_to,
                    "reconnect_interval_s": reconn,
                    "entries": [],
                    "poll_index": 0,
                    "last_poll_ts": 0.0,
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
            access_key = str(security.get("access_key") or "").strip() or str(os.getenv("CCP_ACCESS_KEY", "")).strip()
            if not access_key:
                return PluginStatus(ok=False, message="security.access_key (or CCP_ACCESS_KEY env var) is required")
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
            return dict(self._snapshot_values)

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

    def _resolve_access_key_text(self, sec_cfg: Dict[str, Any]) -> str:
        raw = str(sec_cfg.get("access_key") or "").strip()
        if raw:
            return raw
        env_candidates = [
            "CCP_ACCESS_KEY",
            "ccp_access_key",
            "CCP_ACCESSKEY",
            "CCP_KEY",
        ]
        for key in env_candidates:
            v = str(os.getenv(key, "")).strip()
            if v:
                return v
        top_security = self.config.get("security") or {}
        return str(top_security.get("access_key") or "").strip()

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
            access_key_text = self._resolve_access_key_text(sec_cfg)
            a2l_path = Path(str(a2l_cfg.get("path") or "").strip())
            parsed = parse_a2l(a2l_path)
            poll_endian = str(self.config.get("poll_endian") or "big").lower()
            mta_addr_endian = str(self.config.get("mta_addr_endian") or "big").lower()
            addr_ext_high = bool(self.config.get("addr_ext_high", True))
            prefix = str(meas_cfg.get("naming_prefix") or "")
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
                size = max(1, min(5, size))
                item_limits = item.get("limits", None)
                limits = None
                if isinstance(item_limits, (list, tuple)) and len(item_limits) == 2:
                    try:
                        limits = (float(item_limits[0]), float(item_limits[1]))
                    except Exception:
                        limits = None
                if limits is None and ch is not None:
                    limits = ch.limits
                entries.append(
                    {
                        "name": name,
                        "alias": alias,
                        "address": address,
                        "extension": extension,
                        "size": size,
                        "dtype": dtype,
                        "limits": limits,
                        "poll_endian": poll_endian,
                        "mta_addr_endian": mta_addr_endian,
                    }
                )
            ctx["entries"] = entries
            try:
                n_ch = len(entries)
                if n_ch > 0:
                    rec = self._recommended_poll_channels_per_tick(n_ch, float(ctx.get("poll_interval_s", 0.1)))
                    if rec > int(ctx.get("poll_channels_per_tick", 1)):
                        ctx["poll_channels_per_tick"] = int(rec)
                        print(f"[CCP:{ctx.get('name','?')}] Auto-tuned poll_channels_per_tick={int(rec)} for {n_ch} channels")
            except Exception:
                pass
            session = NixnetSession(interface=interface, baudrate=baud)
            session.open(rx_id=rx_id)
            proto = CcpProto(tx_id=tx_id, is_extended=is_ext)
            ctx["session"] = session
            ctx["proto"] = proto
            conn = proto.build_connect(station_address=station, ctr_override=connect_ctr)
            session.send(conn)
            session.recv(timeout_s=float(ctx.get("io_timeout_s", 0.05)), only_id=rx_id)
            self._set_state("connected", 20)
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
                    raise RuntimeError("missing_access_key (security.access_key or CCP_ACCESS_KEY)")
                access_key = int(access_key_text.replace(" ", "").replace("0x", "").replace("0X", ""), 16)
                key = compute_key_from_seed_algo(seed=seed, access_key=access_key, seed_endian=seed_endian, sec_type=sec_type)
                unlock = proto.build_unlock(key=key, ctr_override=unlock_ctr, pad=unlock_pad)
                session.send(unlock)
                session.recv(timeout_s=float(ctx.get("io_timeout_s", 0.05)), only_id=rx_id)
                self._diag["unlock_ok"] = int(self._diag.get("unlock_ok", 0)) + 1
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

    def _run_real_worker(self) -> None:
        while not self._worker_stop.is_set():
            now = time.time()
            any_connected = False
            for ctx in self._contexts:
                if not bool(ctx.get("connected", False)):
                    if now - float(ctx.get("last_connect_attempt_ts", 0.0)) >= float(ctx.get("reconnect_interval_s", 2.0)):
                        self._connect_real_ctx(ctx)
                else:
                    any_connected = True
                    if now - float(ctx.get("last_poll_ts", 0.0)) >= float(ctx.get("poll_interval_s", 0.1)):
                        ctx["last_poll_ts"] = now
                        self._poll_real_ctx(ctx)
            self._connected = any_connected or any(bool(c.get("connected", False)) for c in self._contexts)
            self._refresh_freshness(now)
            self._append_diag_values()
            with self._state_lock:
                self._snapshot_values = dict(self._values)
            self._worker_stop.wait(0.005)

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
            count = min(len(entries), max(1, int(ctx.get("poll_channels_per_tick", 1))))
            for _ in range(count):
                idx = int(ctx.get("poll_index", 0)) % len(entries)
                entry = entries[idx]
                ctx["poll_index"] = (idx + 1) % len(entries)
                val = self._poll_short_up_ctx(ctx, entry)
                if val is not None:
                    alias = str(entry["alias"])
                    self._values[alias] = float(val)
                    self._value_ts[alias] = time.time()
                    self._diag["poll_success"] = int(self._diag.get("poll_success", 0)) + 1
                    self._diag["last_error"] = ""
                else:
                    self._diag["poll_fail"] = int(self._diag.get("poll_fail", 0)) + 1
                    try:
                        pf = int(self._diag.get("poll_fail", 0))
                        if pf % 50 == 0:
                            print(f"[CCP:{ctx.get('name','?')}] Poll fails={pf} last_error={self._diag.get('last_error','')}")
                    except Exception:
                        pass
        except Exception:
            ctx["connected"] = False
            self._diag["last_error"] = "poll_exception"
            self._set_state("error_poll", 92)

    def _poll_short_up_ctx(self, ctx: Dict[str, Any], entry: Dict[str, Any]) -> Optional[float]:
        session = ctx.get("session")
        proto = ctx.get("proto")
        rx_id = int(ctx.get("rx_id", 0))
        if session is None or proto is None:
            self._diag["last_error"] = "poll_no_session"
            return None
        session.recv(timeout_s=0.001, only_id=rx_id)
        req = proto.build_short_up(
            size=int(entry["size"]),
            address=int(entry["address"]),
            extension=int(entry["extension"]),
            byteorder=str(entry["mta_addr_endian"]),
        )
        req_ctr = req.data[1] if req.data else None
        session.send(req)
        per_req_timeout_s = max(0.005, float(ctx.get("io_timeout_s", 0.05)))
        deadline = time.time() + per_req_timeout_s
        while time.time() < deadline:
            rx = session.recv(timeout_s=min(0.005, per_req_timeout_s), only_id=rx_id)
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
                )
        self._diag["last_error"] = f"short_up_timeout:{entry.get('name','?')}"
        return None

    def _set_state(self, state: str, code: int) -> None:
        self._diag["state"] = str(state)
        self._diag["state_code"] = int(code)

    def _append_diag_values(self) -> None:
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

    def _refresh_freshness(self, now_s: float) -> None:
        aliases = self._final_aliases()
        age_values: List[float] = []
        for a in aliases:
            ts = float(self._value_ts.get(a, 0.0))
            if ts > 0.0:
                age_values.append(max(0.0, now_s - ts))
        if age_values:
            plugin_age = min(age_values)
            max_age = max(age_values)
        else:
            plugin_age = -1.0
            max_age = -1.0
        self._diag["fresh_age_s"] = plugin_age
        self._diag["fresh_max_channel_age_s"] = max_age
        prev_state = int(self._diag.get("freshness_state_code", -1))
        if not self._connected or max_age < 0.0:
            new_state = -1
        else:
            sample_period_s = max(0.001, float(self._freshness_sample_period_s))
            warn_th = sample_period_s * 0.25
            stale_th = sample_period_s * 1.00
            if max_age > stale_th:
                new_state = 2
            elif max_age > warn_th:
                new_state = 1
            else:
                new_state = 0
        self._diag["freshness_state_code"] = int(new_state)
        if new_state != prev_state:
            if new_state == 1:
                self._diag["freshness_warn_count"] = int(self._diag.get("freshness_warn_count", 0)) + 1
                print(
                    "[CCP] Freshness WARN: max_age=%.3fs threshold=%.3fs"
                    % (max_age, max(0.001, float(self._freshness_sample_period_s)) * 0.25)
                )
            elif new_state == 2:
                self._diag["freshness_stale_count"] = int(self._diag.get("freshness_stale_count", 0)) + 1
                print(
                    "[CCP] Freshness STALE: max_age=%.3fs threshold=%.3fs"
                    % (max_age, max(0.001, float(self._freshness_sample_period_s)))
                )

    def _recommended_poll_channels_per_tick(self, channel_count: int, poll_interval_s: float | None = None) -> int:
        if channel_count <= 12:
            return channel_count
        poll_ms = max(1.0, float(poll_interval_s if poll_interval_s is not None else self._poll_interval_s) * 1000.0)
        target_sweep_s = 0.25
        rec = int(math.ceil((channel_count * (poll_ms / 1000.0)) / target_sweep_s))
        if channel_count > 1 and poll_ms >= 100.0:
            rec = max(rec, 2)
        rec = max(1, min(channel_count, rec, 6))
        return rec

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
            _emit("poll_one", True, f"{entry.get('name','?')}={val:.3f}", True)
        except Exception as e:
            _emit("poll_one", False, f"Polling exception: {e}", True)
