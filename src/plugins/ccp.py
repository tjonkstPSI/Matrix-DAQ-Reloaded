# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import os
import time
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .base import BasePlugin, PluginStatus

try:
    import nixnet  # type: ignore
except Exception:
    nixnet = None


def _rotr32(value: int, shift: int) -> int:
    shift &= 31
    return ((value >> shift) | (value << (32 - shift))) & 0xFFFFFFFF


def _rotl32(value: int, shift: int) -> int:
    shift &= 31
    return ((value << shift) | (value >> (32 - shift))) & 0xFFFFFFFF


def _compute_key_from_seed_algo(seed: bytes, access_key: int, seed_endian: str, sec_type: str) -> bytes:
    if len(seed) != 4:
        raise ValueError("Seed must be 4 bytes")
    if seed_endian not in {"big", "little", "reverse"}:
        raise ValueError("seed_endian must be big|little|reverse")
    if sec_type not in {"CAL", "DAQ"}:
        raise ValueError("sec_type must be CAL|DAQ")
    seed_bytes = seed[::-1] if seed_endian == "reverse" else seed
    byteorder = "little" if seed_endian == "little" else "big"
    seed_value = int.from_bytes(seed_bytes, byteorder=byteorder, signed=False)
    key = seed_value ^ (access_key & 0xFFFFFFFF)
    if sec_type == "CAL":
        key = _rotr32(key, 7)
        key ^= seed_value
    else:
        key = _rotr32(key, 3)
        key ^= seed_value
        key = _rotl32(key, 5)
        key ^= seed_value
    out = key.to_bytes(4, byteorder=byteorder, signed=False)
    return out[::-1] if seed_endian == "reverse" else out


@dataclass
class _CanFrame:
    arbitration_id: int
    data: bytes
    is_extended: bool = False


class _CcpProto:
    def __init__(self, tx_id: int, is_extended: bool) -> None:
        self.tx_id = tx_id
        self.is_extended = is_extended
        self._ctr = 0

    def _next_ctr(self) -> int:
        self._ctr = (self._ctr + 1) & 0xFF
        return self._ctr

    def _frame(self, payload: bytes) -> _CanFrame:
        return _CanFrame(arbitration_id=self.tx_id, data=payload, is_extended=self.is_extended)

    def build_connect(self, station_address: int, ctr_override: int | None = None) -> _CanFrame:
        ctr = ctr_override if ctr_override is not None else self._next_ctr()
        payload = bytes([
            0x01,
            ctr & 0xFF,
            station_address & 0xFF,
            (station_address >> 8) & 0xFF,
            0x00,
            0x00,
            0x00,
        ])
        return self._frame(payload)

    def build_get_seed(self, resource: int, ctr_override: int | None = None) -> _CanFrame:
        ctr = ctr_override if ctr_override is not None else self._next_ctr()
        payload = bytes([0x12, ctr & 0xFF, resource & 0xFF, 0, 0, 0, 0, 0])
        return self._frame(payload)

    def build_unlock(self, key: bytes, ctr_override: int | None = None, pad: int = 0x55) -> _CanFrame:
        ctr = ctr_override if ctr_override is not None else self._next_ctr()
        key6 = key[:6].ljust(6, bytes([pad & 0xFF]))
        payload = bytes([0x13, ctr & 0xFF]) + key6
        return self._frame(payload)

    def build_set_s_status(self, status: int) -> _CanFrame:
        ctr = self._next_ctr()
        payload = bytes([0x0C, ctr & 0xFF, status & 0xFF]).ljust(8, b"\x00")
        return self._frame(payload)

    def build_short_up(self, size: int, address: int, extension: int = 0, byteorder: str = "big") -> _CanFrame:
        ctr = self._next_ctr()
        addr_bytes = int(address).to_bytes(4, byteorder=byteorder, signed=False)
        payload = bytes([0x0F, ctr & 0xFF, size & 0xFF, extension & 0xFF]) + addr_bytes
        return self._frame(payload)


class _NixnetSession:
    def __init__(self, interface: str, baudrate: int) -> None:
        self.interface = interface
        self.baudrate = int(baudrate)
        self._tx = None
        self._rx = None

    def open(self, rx_id: int) -> None:
        if nixnet is None:
            raise RuntimeError("nixnet package is not available")
        self._tx = nixnet.FrameOutStreamSession(self.interface)
        try:
            self._rx = nixnet.FrameInQueuedSession(self.interface, ":memory:", "", hex(int(rx_id)))
        except Exception:
            self._rx = nixnet.FrameInStreamSession(self.interface)
        try:
            self._tx.intf.baud_rate = self.baudrate
            self._rx.intf.baud_rate = self.baudrate
            self._tx.intf.can_tx_io_mode = nixnet.constants.CanIoMode.CAN
        except Exception:
            pass
        try:
            self._rx.start()
            self._tx.start()
        except Exception:
            pass

    def close(self) -> None:
        for s in (self._rx, self._tx):
            if s is None:
                continue
            try:
                s.close()
            except Exception:
                pass
        self._tx = None
        self._rx = None

    def send(self, frame: _CanFrame) -> None:
        if self._tx is None:
            raise RuntimeError("TX session is not open")
        can_id = nixnet.types.CanIdentifier(frame.arbitration_id, extended=bool(frame.is_extended))  # type: ignore[attr-defined]
        can_frame = nixnet.types.CanFrame(can_id, payload=frame.data)  # type: ignore[attr-defined]
        self._tx.frames.write([can_frame])

    def recv(self, timeout_s: float = 0.2, only_id: Optional[int] = None) -> List[_CanFrame]:
        if self._rx is None:
            raise RuntimeError("RX session is not open")
        deadline = time.time() + max(0.001, float(timeout_s))
        out: List[_CanFrame] = []
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            # Use small reads and return promptly after draining currently available frames.
            step_timeout = min(max(remaining, 0.0), 0.01)
            if step_timeout <= 0.0:
                break
            try:
                frames = list(
                    self._rx.frames.read(
                        num_frames=1,
                        timeout=step_timeout,
                        frame_type=nixnet.types.CanFrame,
                    )
                )  # type: ignore[call-arg]
            except Exception:
                frames = []
            if not frames:
                # If we already captured data, stop waiting for the full timeout window.
                if out:
                    break
                continue
            for fr in frames:
                cid = int(fr.identifier.identifier)
                if only_id is not None and cid != int(only_id):
                    continue
                out.append(
                    _CanFrame(
                        arbitration_id=cid,
                        data=bytes(fr.payload),
                        is_extended=bool(fr.identifier.extended),
                    )
                )
            # Continue briefly to drain any immediate backlog, but don't sit for full timeout.
            if out and (deadline - time.time()) > 0.02:
                deadline = time.time() + 0.02
        return out


@dataclass(frozen=True)
class _A2LChannel:
    name: str
    address: Optional[int]
    data_type: Optional[str]
    limits: Optional[tuple[float, float]]
    unit: str = ""


def _parse_address(token: str) -> Optional[int]:
    try:
        if token.startswith(("0x", "0X")):
            return int(token, 16)
        return int(token, 10)
    except Exception:
        return None


def _parse_a2l(path: Path) -> Dict[str, _A2LChannel]:
    channels: Dict[str, _A2LChannel] = {}
    if not path.exists():
        return channels
    data_types = {"UBYTE", "SBYTE", "UWORD", "SWORD", "ULONG", "SLONG", "FLOAT32_IEEE", "FLOAT64_IEEE"}
    compu_units: Dict[str, str] = {}
    in_compu = False
    compu_name: Optional[str] = None
    rat_mode = False
    rat_q_count = 0

    def _extract_quoted(text: str) -> List[str]:
        vals: List[str] = []
        s = text
        while '"' in s:
            try:
                _, rest = s.split('"', 1)
                q, s = rest.split('"', 1)
                vals.append(q)
            except Exception:
                break
        return vals

    # Pass 1: COMPU_METHOD -> unit
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if line.startswith("/begin COMPU_METHOD"):
            parts = line.split()
            compu_name = parts[2] if len(parts) > 2 else None
            in_compu = True
            rat_mode = False
            rat_q_count = 0
            continue
        if line.startswith("/end COMPU_METHOD"):
            in_compu = False
            compu_name = None
            rat_mode = False
            rat_q_count = 0
            continue
        if not in_compu or not compu_name:
            continue
        if line.startswith("RAT_FUNC"):
            rat_mode = True
            rat_q_count = 0
            continue
        if not rat_mode:
            continue
        quoted = _extract_quoted(line)
        if not quoted:
            continue
        for q in quoted:
            rat_q_count += 1
            if rat_q_count == 2:
                compu_units[str(compu_name)] = str(q).strip()
                rat_mode = False
                break

    in_block = False
    cur_name: Optional[str] = None
    cur_addr: Optional[int] = None
    cur_type: Optional[str] = None
    cur_compu_ref: Optional[str] = None
    cur_limits: Optional[tuple[float, float]] = None
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if line.startswith("/begin MEASUREMENT") or line.startswith("/begin CHARACTERISTIC"):
            parts = line.split()
            cur_name = parts[2] if len(parts) > 2 else None
            cur_addr = None
            cur_type = None
            cur_compu_ref = None
            cur_limits = None
            in_block = True
            continue
        if line.startswith("/end MEASUREMENT") or line.startswith("/end CHARACTERISTIC"):
            if in_block and cur_name:
                unit = str(compu_units.get(str(cur_compu_ref or ""), "")).strip()
                channels[cur_name] = _A2LChannel(
                    name=cur_name,
                    address=cur_addr,
                    data_type=cur_type,
                    limits=cur_limits,
                    unit=unit,
                )
            in_block = False
            cur_name = None
            continue
        if not in_block or cur_name is None:
            continue
        token = line.split()[0] if line else ""
        if cur_type is None and token in data_types:
            cur_type = token
            continue
        if cur_compu_ref is None and "/* Conversion */" in line and token:
            cur_compu_ref = token
            continue
        if cur_compu_ref is None and cur_type is not None and token.startswith("Compu_"):
            cur_compu_ref = token
            continue
        if line.startswith("ECU_ADDRESS") or line.startswith("ADDRESS"):
            parts = line.split()
            if len(parts) >= 2:
                cur_addr = _parse_address(parts[1])
            continue
        if line and line[0].isdigit():
            parts = line.split()
            if len(parts) >= 2:
                try:
                    candidate = (float(parts[0]), float(parts[1]))
                    if cur_limits is None or cur_limits == (0.0, 0.0):
                        cur_limits = candidate
                except Exception:
                    pass
    return channels


def _dtype_size(dtype: Optional[str]) -> int:
    sizes = {
        "UBYTE": 1,
        "SBYTE": 1,
        "UWORD": 2,
        "SWORD": 2,
        "ULONG": 4,
        "SLONG": 4,
        "FLOAT32_IEEE": 4,
    }
    return int(sizes.get(str(dtype or "").upper(), 4))


def _decode_value(dtype: Optional[str], raw: bytes, byteorder: str, limits: Optional[tuple[float, float]]) -> float:
    dt = str(dtype or "").upper()
    if dt == "SWORD":
        v = int.from_bytes(raw, byteorder=byteorder, signed=True)
        if limits:
            return float(v) * (float(limits[1]) / 0x7FFF)
        return float(v)
    v = int.from_bytes(raw, byteorder=byteorder, signed=False)
    if dt == "UWORD" and limits:
        return float(v) * (float(limits[1]) / 0xFFFF)
    return float(v)


class CCPPlugin(BasePlugin):
    id = "CCP"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._theta = 0.0
        self._session: _NixnetSession | None = None
        self._proto: _CcpProto | None = None
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

    def _final_aliases(self) -> List[str]:
        meas = (self.config.get("measurements") or {})
        prefix = str(meas.get("naming_prefix") or "")
        items = meas.get("list", []) or []
        result: List[str] = []
        for item in items:
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
        self._values = {}
        self._snapshot_values = {}
        self._units = self._build_units_map()
        self._units_cache_valid = True
        self._value_ts = {a: 0.0 for a in self._final_aliases()}
        try:
            self._poll_interval_s = max(0.02, float(self.config.get("poll_interval_ms", 100)) / 1000.0)
        except Exception:
            self._poll_interval_s = 0.1
        try:
            self._poll_channels_per_tick = max(1, int(self.config.get("poll_channels_per_tick", 1)))
        except Exception:
            self._poll_channels_per_tick = 1
        try:
            self._io_timeout_s = max(0.01, float(self.config.get("io_timeout_s", 0.05)))
        except Exception:
            self._io_timeout_s = 0.05
        try:
            self._reconnect_interval_s = max(0.5, float(self.config.get("reconnect_interval_s", 2.0)))
        except Exception:
            self._reconnect_interval_s = 2.0
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
        meas = self.config.get("measurements")
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

        session = self.config.get("session") or {}
        security = self.config.get("security") or {}
        a2l_cfg = self.config.get("a2l") or {}
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
        parsed = _parse_a2l(Path(a2l_path))
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
        meas = (self.config.get("measurements") or {})
        prefix = str(meas.get("naming_prefix") or "")
        # Runtime fallback: if units are not stored in config, resolve from A2L.
        a2l_units: Dict[str, str] = {}
        try:
            a2l_path = Path(str((self.config.get("a2l") or {}).get("path") or "").strip())
            if a2l_path.exists():
                parsed = _parse_a2l(a2l_path)
                a2l_units = {str(k): str(v.unit or "") for k, v in parsed.items()}
        except Exception:
            a2l_units = {}
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
        # Keep this method O(1) on the core tick path.
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
        try:
            if self._session is not None:
                self._session.close()
        except Exception:
            pass
        self._session = None
        self._proto = None
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
        import math
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

    def _connect_real(self) -> None:
        self._last_connect_attempt_ts = time.time()
        self._diag["connect_attempts"] = int(self._diag.get("connect_attempts", 0)) + 1
        self._set_state("connecting", 10)
        try:
            session_cfg = self.config.get("session") or {}
            sec_cfg = self.config.get("security") or {}
            a2l_cfg = self.config.get("a2l") or {}
            meas_cfg = self.config.get("measurements") or {}

            interface = str(session_cfg.get("interface") or "").strip()
            baud = self._parse_int(session_cfg.get("baudrate"), 250000)
            tx_id = self._parse_int(session_cfg.get("tx_id"), 0)
            self._rx_id = self._parse_int(session_cfg.get("rx_id"), 0)
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
            access_key_text = str(sec_cfg.get("access_key") or "").strip() or str(os.getenv("CCP_ACCESS_KEY", "")).strip()
            access_key = int(access_key_text.replace(" ", ""), 16)

            a2l_path = Path(str(a2l_cfg.get("path") or "").strip())
            parsed = _parse_a2l(a2l_path)
            poll_endian = str(self.config.get("poll_endian") or "big").lower()
            mta_addr_endian = str(self.config.get("mta_addr_endian") or "big").lower()
            addr_ext_high = bool(self.config.get("addr_ext_high", True))
            prefix = str(meas_cfg.get("naming_prefix") or "")
            items = meas_cfg.get("list", []) or []

            self._entries = []
            self._units = self._build_units_map()
            self._units_cache_valid = True
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
                size = int(item.get("size") or _dtype_size(dtype))
                if size < 1:
                    size = 1
                if size > 5:
                    size = 5
                item_limits = item.get("limits", None)
                limits = None
                if isinstance(item_limits, (list, tuple)) and len(item_limits) == 2:
                    try:
                        limits = (float(item_limits[0]), float(item_limits[1]))
                    except Exception:
                        limits = None
                if limits is None and ch is not None:
                    limits = ch.limits
                self._entries.append({
                    "name": name,
                    "alias": alias,
                    "address": address,
                    "extension": extension,
                    "size": size,
                    "dtype": dtype,
                    "limits": limits,
                    "poll_endian": poll_endian,
                    "mta_addr_endian": mta_addr_endian,
                })

            # Auto-tune per-tick poll fanout so visible channel latency stays low.
            try:
                n_ch = len(self._entries)
                if n_ch > 0:
                    rec = self._recommended_poll_channels_per_tick(n_ch)
                    if rec > int(self._poll_channels_per_tick):
                        self._poll_channels_per_tick = int(rec)
                        print(f"[CCP] Auto-tuned poll_channels_per_tick={self._poll_channels_per_tick} for {n_ch} channels")
            except Exception:
                pass

            self._session = _NixnetSession(interface=interface, baudrate=baud)
            self._session.open(rx_id=self._rx_id)
            self._proto = _CcpProto(tx_id=tx_id, is_extended=is_ext)
            print("[CCP] Session opened")

            # CONNECT
            conn = self._proto.build_connect(station_address=station, ctr_override=connect_ctr)
            self._session.send(conn)
            self._session.recv(timeout_s=self._io_timeout_s, only_id=self._rx_id)
            self._set_state("connected", 20)
            print("[CCP] CONNECT sent")

            # GET_SEED
            get_seed = self._proto.build_get_seed(resource=seed_resource, ctr_override=seed_ctr)
            self._session.send(get_seed)
            seed_frames = self._session.recv(timeout_s=self._io_timeout_s, only_id=self._rx_id)
            if not seed_frames:
                raise RuntimeError("No GET_SEED response")
            seed_data = seed_frames[-1].data.ljust(8, b"\x00")
            protection_status = int(seed_data[3])
            self._diag["last_seed_status"] = protection_status
            seed = bytes(seed_data[4:8])
            self._set_state("seed_received", 30)
            print(f"[CCP] GET_SEED ok (protection={protection_status})")

            if protection_status or force_unlock:
                key = _compute_key_from_seed_algo(
                    seed=seed,
                    access_key=access_key,
                    seed_endian=seed_endian,
                    sec_type=sec_type,
                )
                unlock = self._proto.build_unlock(key=key, ctr_override=unlock_ctr, pad=unlock_pad)
                self._session.send(unlock)
                self._session.recv(timeout_s=self._io_timeout_s, only_id=self._rx_id)
                self._diag["unlock_ok"] = int(self._diag.get("unlock_ok", 0)) + 1
                self._set_state("unlocked", 40)
                print("[CCP] UNLOCK sent")
            else:
                self._set_state("unlock_skipped", 41)
                print("[CCP] UNLOCK skipped (not protected)")

            if set_s_status:
                status_frame = self._proto.build_set_s_status(s_status)
                self._session.send(status_frame)
                self._session.recv(timeout_s=self._io_timeout_s, only_id=self._rx_id)
                self._set_state("s_status_set", 50)
                print(f"[CCP] SET_S_STATUS sent ({hex(int(s_status))})")

            self._connected = True
            self._diag["connect_ok"] = int(self._diag.get("connect_ok", 0)) + 1
            self._set_state("ready_polling", 60)
            self._diag["last_error"] = ""
            print("[CCP] Ready for polling")
        except Exception as e:
            self._connected = False
            self._diag["last_error"] = f"connect_or_unlock_failed:{e}"
            self._set_state("error_connect", 90)
            print(f"[CCP] Connect/unlock failed: {e}")
            try:
                if self._session is not None:
                    self._session.close()
            except Exception:
                pass
            self._session = None
            self._proto = None

    def _run_real_worker(self) -> None:
        while not self._worker_stop.is_set():
            now = time.time()
            connected = bool(self._connected)
            last_attempt = float(self._last_connect_attempt_ts)
            reconnect_s = float(self._reconnect_interval_s)
            last_poll = float(self._last_poll_ts)
            poll_s = float(self._poll_interval_s)
            if not connected:
                if now - last_attempt >= reconnect_s:
                    self._connect_real()
                self._refresh_freshness(now)
                self._append_diag_values()
                with self._state_lock:
                    self._snapshot_values = dict(self._values)
                self._worker_stop.wait(0.02)
                continue
            if now - last_poll >= poll_s:
                self._last_poll_ts = now
                self._poll_real()
                self._refresh_freshness(now)
                self._append_diag_values()
                with self._state_lock:
                    self._snapshot_values = dict(self._values)
            self._worker_stop.wait(0.005)

    def _poll_real(self) -> None:
        if self._session is None or self._proto is None:
            self._connected = False
            self._diag["last_error"] = "session_not_ready"
            self._set_state("error_session", 91)
            return
        try:
            if not self._entries:
                self._diag["last_error"] = "no_measurements"
                self._set_state("no_measurements", 61)
                return
            self._set_state("polling", 70)
            count = min(len(self._entries), max(1, self._poll_channels_per_tick))
            for _ in range(count):
                entry = self._entries[self._poll_index % len(self._entries)]
                self._poll_index = (self._poll_index + 1) % len(self._entries)
                val = self._poll_short_up(entry)
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
                            print(f"[CCP] Poll fails={pf} last_error={self._diag.get('last_error','')}")
                    except Exception:
                        pass
        except Exception:
            self._connected = False
            self._diag["last_error"] = "poll_exception"
            self._set_state("error_poll", 92)

    def _poll_short_up(self, entry: Dict[str, Any]) -> Optional[float]:
        if self._session is None or self._proto is None:
            self._diag["last_error"] = "poll_no_session"
            return None
        # Drain stale RX before request to reduce cross-talk.
        self._session.recv(timeout_s=0.001, only_id=self._rx_id)
        req = self._proto.build_short_up(
            size=int(entry["size"]),
            address=int(entry["address"]),
            extension=int(entry["extension"]),
            byteorder=str(entry["mta_addr_endian"]),
        )
        req_ctr = req.data[1] if req.data else None
        self._session.send(req)
        per_req_timeout_s = max(0.005, min(float(self._io_timeout_s), 0.015))
        deadline = time.time() + per_req_timeout_s
        while time.time() < deadline:
            rx = self._session.recv(timeout_s=min(0.005, per_req_timeout_s), only_id=self._rx_id)
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
                return _decode_value(
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
            sample_period_s = max(0.001, float(self._poll_interval_s))
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
                    % (max_age, max(0.001, float(self._poll_interval_s)) * 0.25)
                )
            elif new_state == 2:
                self._diag["freshness_stale_count"] = int(self._diag.get("freshness_stale_count", 0)) + 1
                print(
                    "[CCP] Freshness STALE: max_age=%.3fs threshold=%.3fs"
                    % (max_age, max(0.001, float(self._poll_interval_s)))
                )

    def _recommended_poll_channels_per_tick(self, channel_count: int) -> int:
        # For small/medium channel sets, poll everything every tick for snappy UX.
        if channel_count <= 12:
            return channel_count
        # For larger sets, target a full sweep in ~250 ms when possible.
        poll_ms = max(1.0, float(self._poll_interval_s) * 1000.0)
        target_sweep_s = 0.25
        rec = int(math.ceil((channel_count * (poll_ms / 1000.0)) / target_sweep_s))
        # For slower poll intervals, never leave at 1 channel/tick unless only one channel selected.
        if channel_count > 1 and poll_ms >= 100.0:
            rec = max(rec, 2)
        # Keep bounded to avoid long blocking bursts.
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
            self._connect_real()
            if not self._connected:
                _emit("connect_unlock", False, str(self._diag.get("last_error", "connect failed")), True)
                return
            _emit("connect_unlock", True, "Connected, seed/unlock path completed")
        except Exception as e:
            _emit("connect_unlock", False, f"Connect exception: {e}", True)
            return

        try:
            if not self._entries:
                _emit("poll_prepare", False, "No A2L measurements configured", True)
                return
            entry = self._entries[0]
            val = self._poll_short_up(entry)
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


