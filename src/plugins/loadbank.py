# Author: T. Onkst | Date: 04212026

from __future__ import annotations

from typing import Dict, Any, Set, Optional
from pathlib import Path
import math
import struct
import threading
import time

from .base import BasePlugin, PluginStatus
from ..config.loader import load_yaml_config

try:
    # pymodbus 3.x
    from pymodbus.client import ModbusTcpClient  # type: ignore
except Exception:
    try:
        # Older fallback
        from pymodbus.client.tcp import ModbusTcpClient  # type: ignore
    except Exception:  # pragma: no cover - handled by validate()
        ModbusTcpClient = None  # type: ignore

from ._modbus_compat import uid_kwargs


class LoadBankPlugin(BasePlugin):
    id = "LoadBank"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._map: Dict[str, Any] = {}
        self._mode: str = "sim"
        self._setpoint_val: float = 0.0
        self._measured_val: float = 0.0
        self._pending_setpoint: Optional[float] = None
        self._last_setpoint_write_ts: float = 0.0
        self._setpoint_rate_hz: float = 1.0
        self._snapshot_lock = threading.Lock()
        self._snapshot_values: Dict[str, Any] = {}
        self._worker_thread = None
        self._worker_stop = threading.Event()
        self._client = None
        self._connected = False
        self._next_poll_ts: Dict[str, float] = {}
        self._control_enable_ok = False
        self._last_control_enable_try_ts: float = 0.0
        self._control_values_a: list[bool] = [False, False, False]
        self._control_dirty_a: bool = True
        self._heartbeat_enabled: bool = False
        self._heartbeat_interval_s: float = 1.0
        self._heartbeat_state: bool = False
        self._next_heartbeat_ts: float = 0.0
        self._last_step_vector: list[bool] = []
        self._last_step_remainder_kw: float = 0.0

    def configure(self) -> None:
        self._mode = str(self.config.get("mode", "sim")).strip().lower()
        self._setpoint_rate_hz = float((self.config.get("safety", {}) or {}).get("rate_limit_setpoint_hz", 1.0) or 1.0)
        model_cfg = self._resolved_model_cfg()
        map_file = model_cfg.get("map_file")
        self._map = {}
        if map_file:
            mf = Path(map_file)
            candidates = []
            if mf.is_absolute():
                candidates.append(mf)
            candidates.append((self.configs_dir / mf).resolve())
            # If user provided a path like "configs/...", also try relative to project root
            candidates.append((self.configs_dir.parent / mf).resolve())
            for p in candidates:
                if p.exists():
                    self._map = load_yaml_config(p)
                    break
        hb = (self._map.get("commands", {}) or {}).get("heartbeat", {}) or {}
        self._heartbeat_enabled = bool(hb.get("enabled", False))
        try:
            self._heartbeat_interval_s = max(0.2, float(hb.get("interval_s", 1.0)))
        except Exception:
            self._heartbeat_interval_s = 1.0
        self._seed_snapshot()

    def _resolved_primary(self) -> Dict[str, Any]:
        lbs = self.config.get("load_banks") or {}
        if isinstance(lbs, dict):
            primary = lbs.get("primary") or {}
            if isinstance(primary, dict) and primary:
                return dict(primary)
        devices = self.config.get("devices") or []
        if isinstance(devices, list):
            for dev in devices:
                if not isinstance(dev, dict):
                    continue
                role = str(dev.get("role", "")).strip().lower()
                if role == "primary":
                    conn = dev.get("connection") or {}
                    return {
                        "model": dev.get("model"),
                        "map_file": dev.get("map_file"),
                        "ip_address": (conn.get("host") if isinstance(conn, dict) else None),
                        "port": (conn.get("port") if isinstance(conn, dict) else 502),
                        "unit_id": (conn.get("unit_id") if isinstance(conn, dict) else 1),
                        "enabled": dev.get("enabled", True),
                    }
        return {}

    def _resolved_model_cfg(self) -> Dict[str, Any]:
        primary = self._resolved_primary()
        if primary:
            return {
                "selected": primary.get("model"),
                "map_file": primary.get("map_file"),
            }
        return self.config.get("model", {}) or {}

    def _resolved_connection(self) -> Dict[str, Any]:
        primary = self._resolved_primary()
        if primary:
            return {
                "host": primary.get("ip_address") or "127.0.0.1",
                "port": int(primary.get("port", 502)),
                "unit_id": int(primary.get("unit_id", 1)),
                "timeout_ms": int(primary.get("timeout_ms", 250)),
            }
        conn = self.config.get("connection", {}) or {}
        return {
            "host": conn.get("host", "127.0.0.1"),
            "port": int(conn.get("port", 502)),
            "unit_id": int(conn.get("unit_id", 1)),
            "timeout_ms": int(conn.get("timeout_ms", 250)),
        }

    def validate(self) -> PluginStatus:
        if self._mode == "real" and ModbusTcpClient is None:
            return PluginStatus(ok=False, message="pymodbus is required for LoadBank real mode")
        model_cfg = self._resolved_model_cfg()
        if not isinstance(model_cfg, dict) or not model_cfg:
            return PluginStatus(ok=False, message="model block must be provided")
        if not self._map:
            return PluginStatus(ok=False, message="loadbank map_file is missing or unreadable")
        return PluginStatus(ok=True)

    def aliases(self) -> Set[str]:
        aliases: Set[str] = set()
        exposes = self.config.get("expose_channels", {}) or {}
        for k, v in exposes.items():
            if not str(k).endswith("_alias"):
                continue
            if v:
                aliases.add(str(v))
        return aliases

    def units(self) -> Dict[str, str]:
        exposes = self.config.get("expose_channels", {}) or {}
        # derive from model map if present
        measured_unit = "%"
        setpoint_unit = "%"
        try:
            measured_unit = str(((self._map.get("status", {}) or {}).get("measured_load", {}) or {}).get("scaling", {}).get("unit", measured_unit))
        except Exception:
            pass
        try:
            setpoint_unit = str(((self._map.get("commands", {}) or {}).get("setpoint", {}) or {}).get("ui_unit", setpoint_unit))
        except Exception:
            pass
        unit_map = {
            exposes.get("measured_load_alias", ""): measured_unit,
            exposes.get("setpoint_alias", ""): setpoint_unit,
            exposes.get("step_count_alias", ""): "",
            exposes.get("step_remainder_alias", ""): "kW",
            exposes.get("voltage_ab_alias", ""): "Vrms",
            exposes.get("voltage_bc_alias", ""): "Vrms",
            exposes.get("voltage_ca_alias", ""): "Vrms",
            exposes.get("current_l1_alias", ""): "A",
            exposes.get("current_l2_alias", ""): "A",
            exposes.get("current_l3_alias", ""): "A",
            exposes.get("frequency_alias", ""): "Hz",
            exposes.get("power_alias", ""): "",
            exposes.get("error_alias", ""): "",
            exposes.get("fan_alias", ""): "",
            exposes.get("control_available_alias", ""): "",
            exposes.get("normal_operation_alias", ""): "",
            exposes.get("load_available_alias", ""): "",
            exposes.get("loadbank_failure_alias", ""): "",
        }
        return {k: v for k, v in unit_map.items() if k}

    def start(self) -> None:
        self._setpoint_val = 0.0
        self._measured_val = 0.0
        self._pending_setpoint = None
        self._last_setpoint_write_ts = 0.0
        self._next_poll_ts = {}
        self._connected = False
        self._control_enable_ok = False
        self._last_control_enable_try_ts = 0.0
        self._control_values_a = [False, False, False]
        self._control_dirty_a = True
        self._snapshot_values = {}
        self._heartbeat_state = False
        self._next_heartbeat_ts = 0.0
        self._last_step_vector = []
        self._last_step_remainder_kw = 0.0
        if self._mode == "real":
            self._worker_stop.clear()
            self._worker_thread = threading.Thread(target=self._real_worker_loop, daemon=True)
            self._worker_thread.start()

    def stop(self) -> None:
        self._worker_stop.set()
        t = self._worker_thread
        if t is not None and t.is_alive():
            try:
                t.join(timeout=0.6)
            except Exception:
                pass
        self._worker_thread = None
        self._disconnect_client()

    def command_setpoint_pct(self, pct: float) -> None:
        limits = (self.config.get("safety", {}) or {}).get("setpoint_limits_percent", {})
        lo = float(limits.get("min", 0.0))
        hi = float(limits.get("max", 100.0))
        self._setpoint_val = max(lo, min(hi, float(pct or 0.0)))
        if self._mode == "real":
            self._pending_setpoint = float(self._setpoint_val)

    def command_setpoint_kw(self, kw: float) -> None:
        # Explicit kW-oriented alias for future UI control wiring.
        self.command_setpoint_pct(kw)

    def set_control_enable_a(
        self,
        take_control: Optional[bool] = None,
        fan_power: Optional[bool] = None,
        master_load: Optional[bool] = None,
    ) -> None:
        cur = list(self._control_values_a)
        if take_control is not None:
            cur[0] = bool(take_control)
        if fan_power is not None:
            cur[1] = bool(fan_power)
        if master_load is not None:
            cur[2] = bool(master_load)
        self._control_values_a = cur
        self._control_dirty_a = True

    def command_take_control(self, enabled: bool) -> None:
        self.set_control_enable_a(take_control=enabled)

    def command_fan_power(self, enabled: bool) -> None:
        self.set_control_enable_a(fan_power=enabled)

    def command_master_load(self, enabled: bool) -> None:
        self.set_control_enable_a(master_load=enabled)

    def simulate_step(self) -> Dict[str, Any]:
        """Return latest snapshot for real mode or simulated values for sim mode."""
        if self._mode == "real":
            with self._snapshot_lock:
                return dict(self._snapshot_values)
        out = self._compute_sim_step()
        with self._snapshot_lock:
            self._snapshot_values = dict(out)
        return out

    def _compute_sim_step(self) -> Dict[str, Any]:
        exposes = self.config.get("expose_channels", {}) or {}
        out: Dict[str, Any] = {}
        self._measured_val += 0.2 * (self._setpoint_val - self._measured_val)
        out[exposes.get("measured_load_alias", "lPO_LdbAct")] = self._measured_val
        out[exposes.get("setpoint_alias", "lPO_LdbStp")] = self._setpoint_val
        out[exposes.get("ready_alias", "LB Ready")] = 1
        out[exposes.get("faults_alias", "LB Faults")] = 0
        out[exposes.get("power_alias", "Power")] = 1 if self._setpoint_val > 0.0 else 0
        out[exposes.get("error_alias", "Error")] = 0
        out[exposes.get("fan_alias", "lDG_Fan")] = 1 if self._setpoint_val > 0.0 else 0
        out[exposes.get("voltage_ab_alias", "lVO_Ldb1")] = 480.0
        out[exposes.get("voltage_bc_alias", "lVO_Ldb2")] = 480.0
        out[exposes.get("voltage_ca_alias", "lVO_Ldb3")] = 480.0
        cur = max(0.0, self._measured_val * 1.2)
        out[exposes.get("current_l1_alias", "lCT_Ldb1")] = cur
        out[exposes.get("current_l2_alias", "lCT_Ldb2")] = cur
        out[exposes.get("current_l3_alias", "lCT_Ldb3")] = cur
        out[exposes.get("frequency_alias", "LB Frequency")] = 60.0
        return out

    def _seed_snapshot(self) -> None:
        try:
            with self._snapshot_lock:
                self._snapshot_values = self._compute_sim_step()
        except Exception:
            self._snapshot_values = {}

    def _real_worker_loop(self) -> None:
        conn = self._resolved_connection()
        cmd = (self._map.get("commands", {}) or {}).get("control_enable_a", {}) or {}
        print(f"[LB] Worker starting: host={conn.get('host')} port={conn.get('port')} "
              f"ctrl_addr={cmd.get('address')} init_vals={self._control_values_a}")
        while not self._worker_stop.is_set():
            if not self._connected:
                self._connected = self._connect_client()
                if not self._connected:
                    self._worker_stop.wait(0.75)
                    continue
            try:
                now = time.time()
                out = self._poll_real_once(now)
                if out:
                    with self._snapshot_lock:
                        self._snapshot_values.update(out)
            except Exception:
                self._connected = False
                self._disconnect_client()
            self._worker_stop.wait(0.02)

    def _connect_client(self) -> bool:
        if ModbusTcpClient is None:
            return False
        conn = self._resolved_connection()
        host = str(conn.get("host", "127.0.0.1")).strip()
        port = int(conn.get("port", 502))
        timeout_s = max(0.05, float(conn.get("timeout_ms", 250)) / 1000.0)
        if not host:
            return False
        try:
            self._client = ModbusTcpClient(host=host, port=port, timeout=timeout_s)  # type: ignore
            return bool(self._client.connect())
        except Exception:
            self._client = None
            return False

    def _disconnect_client(self) -> None:
        c = self._client
        self._client = None
        if c is not None:
            try:
                c.close()
            except Exception:
                pass

    def _poll_real_once(self, now_ts: float) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        exposes = self.config.get("expose_channels", {}) or {}
        conn = self._resolved_connection()
        unit_id = int(conn.get("unit_id", 1))

        # First VI write block: keep A-bank control chain enabled (3456..3458).
        if (not self._control_enable_ok or self._control_dirty_a) and (now_ts - self._last_control_enable_try_ts) >= 0.2:
            self._last_control_enable_try_ts = now_ts
            self._control_enable_ok = self._write_control_enable_a(unit_id)

        # Heartbeat coil required by some load banks (e.g., Simplex 700kW).
        if self._heartbeat_enabled and self._control_enable_ok and now_ts >= self._next_heartbeat_ts:
            self._heartbeat_state = not self._heartbeat_state
            if self._write_heartbeat(unit_id, self._heartbeat_state):
                self._next_heartbeat_ts = now_ts + self._heartbeat_interval_s

        # Rate-limited setpoint write
        if self._pending_setpoint is not None:
            min_dt = 1.0 / max(0.1, float(self._setpoint_rate_hz))
            if (now_ts - self._last_setpoint_write_ts) >= min_dt:
                if self._write_setpoint(unit_id, float(self._pending_setpoint)):
                    self._last_setpoint_write_ts = now_ts
                    self._pending_setpoint = None

        # Status polling from map
        status = self._map.get("status", {}) or {}
        for key, cfg in status.items():
            if not isinstance(cfg, dict):
                continue
            poll_hz = float(cfg.get("poll_hz", (self._map.get("polling", {}) or {}).get("default_status_poll_hz", 2.0)) or 2.0)
            next_ts = float(self._next_poll_ts.get(key, 0.0))
            if now_ts < next_ts:
                continue
            self._next_poll_ts[key] = now_ts + (1.0 / max(0.1, poll_hz))
            val = self._read_point(cfg, unit_id)
            if val is None:
                continue
            alias = self._status_alias(key, cfg, exposes)
            if self._ctrl_diag_count < 5 and key == "fan_on":
                print(f"[LB] READ fan_on: addr={cfg.get('address')} val={val} -> alias={alias}")
            out[alias] = val

        # Always expose current command echo.
        if exposes.get("setpoint_alias"):
            out[str(exposes.get("setpoint_alias"))] = float(self._setpoint_val)
        if exposes.get("step_count_alias"):
            out[str(exposes.get("step_count_alias"))] = float(sum(1 for b in self._last_step_vector if b))
        if exposes.get("step_remainder_alias"):
            out[str(exposes.get("step_remainder_alias"))] = float(self._last_step_remainder_kw)
        if exposes.get("ready_alias") and str(exposes.get("ready_alias")) not in out:
            out[str(exposes.get("ready_alias"))] = 1 if self._connected else 0
        return out

    def _status_alias(self, key: str, cfg: Dict[str, Any], exposes: Dict[str, Any]) -> str:
        explicit = cfg.get("alias")
        if explicit:
            return str(explicit)
        alias_key = str(cfg.get("alias_key", "")).strip()
        if alias_key and exposes.get(alias_key):
            return str(exposes.get(alias_key))
        defaults = {
            "measured_load": "measured_load_alias",
            "faults_word": "faults_alias",
            "ready_bit": "ready_alias",
            "power_bool": "power_alias",
            "error_bool": "error_alias",
            "power_kw": "measured_load_alias",
            "fan_on": "fan_alias",
            "voltage_ab_vrms": "voltage_ab_alias",
            "voltage_bc_vrms": "voltage_bc_alias",
            "voltage_ca_vrms": "voltage_ca_alias",
            "current_l1_a": "current_l1_alias",
            "current_l2_a": "current_l2_alias",
            "current_l3_a": "current_l3_alias",
            "frequency_hz": "frequency_alias",
            "control_available": "control_available_alias",
            "normal_operation": "normal_operation_alias",
            "load_available": "load_available_alias",
            "load_bank_failure": "loadbank_failure_alias",
        }
        k = defaults.get(str(key), "")
        if k and exposes.get(k):
            return str(exposes.get(k))
        return str(key)

    def _address_zero_based(self, addr: Any) -> int:
        a = int(addr)
        # Default to one-based map addresses common in vendor docs.
        base = int((self._map.get("address_base") if isinstance(self._map, dict) else None) or 1)
        return a - base

    def _is_error_response(self, resp: Any) -> bool:
        if resp is None:
            return True
        try:
            fn = getattr(resp, "isError", None)
            if callable(fn):
                return bool(fn())
        except Exception:
            pass
        return False

    _meter_diag_ts: float = 0.0

    def _read_point(self, cfg: Dict[str, Any], unit_id: int) -> Optional[float]:
        c = self._client
        if c is None:
            return None
        fc = int(cfg.get("fc", 3))
        address = self._address_zero_based(cfg.get("address", 0))
        dtype = str(cfg.get("type", "uint16")).lower()
        count = int(cfg.get("count", 1))
        is_meter = fc in (3, 4) and dtype == "float32"
        log_meter = is_meter and (time.time() - self._meter_diag_ts) >= 30.0
        try:
            if fc == 1:
                rr = self._client_read(c.read_coils, address, max(1, count), unit_id)
                if self._is_error_response(rr):
                    return None
                bits = list(getattr(rr, "bits", []) or [])
                raw = 1.0 if (bits and bool(bits[0])) else 0.0
            elif fc == 2:
                rr = self._client_read(c.read_discrete_inputs, address, max(1, count), unit_id)
                if self._is_error_response(rr):
                    return None
                bits = list(getattr(rr, "bits", []) or [])
                raw = 1.0 if (bits and bool(bits[0])) else 0.0
            elif fc == 4:
                regs_needed = 2 if dtype in {"uint32", "int32", "float32", "bcd_double", "bcd32"} else 1
                rr = self._client_read(c.read_input_registers, address, max(regs_needed, count), unit_id)
                if self._is_error_response(rr):
                    if log_meter:
                        print(f"[LB] METER FC4 ERROR addr={address} cfg_addr={cfg.get('address')} resp={rr}")
                        self._meter_diag_ts = time.time()
                    return None
                regs = list(getattr(rr, "registers", []) or [])
                raw = self._decode_registers(regs, dtype, cfg)
            else:
                regs_needed = 2 if dtype in {"uint32", "int32", "float32", "bcd_double", "bcd32"} else 1
                rr = self._client_read(c.read_holding_registers, address, max(regs_needed, count), unit_id)
                if self._is_error_response(rr):
                    if log_meter:
                        print(f"[LB] METER FC3 ERROR addr={address} cfg_addr={cfg.get('address')} resp={rr}")
                        self._meter_diag_ts = time.time()
                    return None
                regs = list(getattr(rr, "registers", []) or [])
                raw = self._decode_registers(regs, dtype, cfg)
            if log_meter:
                print(f"[LB] METER fc={fc} addr={address} cfg_addr={cfg.get('address')} "
                      f"regs={regs if fc in (3,4) else 'n/a'} raw={raw}")
                self._meter_diag_ts = time.time()
        except Exception as exc:
            if log_meter:
                print(f"[LB] METER EXCEPTION fc={fc} addr={address}: {type(exc).__name__}: {exc}")
                self._meter_diag_ts = time.time()
            self._connected = False
            self._disconnect_client()
            return None

        # Optional bit extraction for status words.
        if "bit" in cfg:
            bit = int(cfg.get("bit", 0))
            try:
                raw = 1.0 if (int(raw) & (1 << bit)) else 0.0
            except Exception:
                raw = 0.0

        sc = cfg.get("scaling", {}) or {}
        m = float(sc.get("m", 1.0))
        b = float(sc.get("b", 0.0))
        return float(raw) * m + b

    def _decode_registers(self, regs: list[int], dtype: str, cfg: Dict[str, Any]) -> float:
        if not regs:
            return float("nan")
        word_order = str(cfg.get("word_order", "AB")).upper()
        if dtype == "int16":
            v = regs[0]
            return float(v - 65536 if v > 32767 else v)
        if dtype == "uint16":
            return float(int(regs[0]) & 0xFFFF)
        if dtype in {"uint32", "int32", "float32"}:
            if len(regs) < 2:
                return float("nan")
            w0, w1 = int(regs[0]) & 0xFFFF, int(regs[1]) & 0xFFFF
            if word_order == "BA":
                w0, w1 = w1, w0
            b = struct.pack(">HH", w0, w1)
            if dtype == "float32":
                return float(struct.unpack(">f", b)[0])
            if dtype == "int32":
                return float(struct.unpack(">i", b)[0])
            return float(struct.unpack(">I", b)[0])
        if dtype in {"bcd_double", "bcd32"}:
            if len(regs) < 2:
                return float("nan")
            w0, w1 = int(regs[0]) & 0xFFFF, int(regs[1]) & 0xFFFF
            if word_order == "BA":
                w0, w1 = w1, w0
            digits = f"{(w0 >> 12) & 0xF}{(w0 >> 8) & 0xF}{(w0 >> 4) & 0xF}{w0 & 0xF}{(w1 >> 12) & 0xF}{(w1 >> 8) & 0xF}{(w1 >> 4) & 0xF}{w1 & 0xF}"
            # Filter invalid BCD nibbles defensively.
            digits = "".join(ch if ch in "0123456789" else "0" for ch in digits)
            return float(int(digits))
        return float(regs[0])

    def _client_read(self, fn, address: int, count: int, unit_id: int):
        return fn(address, count=count, **uid_kwargs(unit_id))

    def _write_setpoint(self, unit_id: int, value: float) -> bool:
        c = self._client
        if c is None:
            return False
        cmd = (self._map.get("commands", {}) or {}).get("setpoint", {}) or {}
        fc = int(cmd.get("fc", 6))
        ctype = str(cmd.get("type", "")).lower()
        # VI-aligned step-array write path for Simplex.
        if fc == 15 or ctype == "coil_array":
            return self._write_setpoint_steps(unit_id, value, cmd)
        if ctype in {"bcd_double", "bcd32"} or fc == 16:
            return self._write_setpoint_bcd(unit_id, value, cmd)
        address = self._address_zero_based(cmd.get("address", 0))
        # Inverse of read scaling: ui -> register raw.
        sc = cmd.get("scaling", {}) or {}
        m = float(sc.get("m", 1.0))
        b = float(sc.get("b", 0.0))
        lo = float(cmd.get("min", (self.config.get("safety", {}) or {}).get("setpoint_limits_percent", {}).get("min", 0.0)))
        hi = float(cmd.get("max", (self.config.get("safety", {}) or {}).get("setpoint_limits_percent", {}).get("max", 100.0)))
        clamped = max(lo, min(hi, float(value)))
        raw = int(round(clamped * m + b))
        try:
            wr = c.write_register(address=address, value=raw, **uid_kwargs(unit_id))
            if self._is_error_response(wr):
                return False
            self._setpoint_val = clamped
            return True
        except Exception:
            self._connected = False
            self._disconnect_client()
            return False

    def _write_setpoint_bcd(self, unit_id: int, value: float, cmd: Dict[str, Any]) -> bool:
        c = self._client
        if c is None:
            return False
        address = self._address_zero_based(cmd.get("address", 0))
        lo = float(cmd.get("min", (self.config.get("safety", {}) or {}).get("setpoint_limits_percent", {}).get("min", 0.0)))
        hi = float(cmd.get("max", (self.config.get("safety", {}) or {}).get("setpoint_limits_percent", {}).get("max", 100.0)))
        target = int(round(max(lo, min(hi, float(value)))))
        digits = f"{max(0, min(99_999_999, target)):08d}"
        b = bytes(
            [
                (int(digits[0]) << 4) | int(digits[1]),
                (int(digits[2]) << 4) | int(digits[3]),
                (int(digits[4]) << 4) | int(digits[5]),
                (int(digits[6]) << 4) | int(digits[7]),
            ]
        )
        regs = [((b[0] << 8) | b[1]), ((b[2] << 8) | b[3])]
        word_order = str(cmd.get("word_order", "AB")).upper()
        if word_order == "BA":
            regs = [regs[1], regs[0]]
        try:
            wr = c.write_registers(address=address, values=regs, **uid_kwargs(unit_id))
            if self._is_error_response(wr):
                return False
            self._setpoint_val = float(target)
            return True
        except Exception:
            self._connected = False
            self._disconnect_client()
            return False

    def _write_setpoint_steps(self, unit_id: int, value: float, cmd: Dict[str, Any]) -> bool:
        c = self._client
        if c is None:
            return False
        address = self._address_zero_based(cmd.get("address", 3459))
        steps_raw = cmd.get("steps_kw", []) or []
        steps: list[int] = []
        for s in steps_raw:
            try:
                steps.append(int(round(float(s))))
            except Exception:
                continue
        if not steps:
            return False

        lo = float(cmd.get("min", (self.config.get("safety", {}) or {}).get("setpoint_limits_percent", {}).get("min", 0.0)))
        hi = float(cmd.get("max", (self.config.get("safety", {}) or {}).get("setpoint_limits_percent", {}).get("max", 100.0)))
        req_kw = max(lo, min(hi, float(value)))
        target = int(round(req_kw))

        # Greedy descending over available step bank; write order stays in address order.
        vec = [False] * len(steps)
        rem = int(target)
        for i in range(len(steps) - 1, -1, -1):
            step = int(steps[i])
            if step <= 0:
                continue
            if rem >= step:
                vec[i] = True
                rem -= step
            if rem <= 0:
                break
        self._last_step_vector = list(vec)
        self._last_step_remainder_kw = float(rem)

        try:
            wr = c.write_coils(address=address, values=vec, **uid_kwargs(unit_id))
            if self._is_error_response(wr):
                return False
            self._setpoint_val = float(target - rem)
            return True
        except Exception:
            self._connected = False
            self._disconnect_client()
            return False

    _ctrl_diag_count: int = 0

    def _write_control_enable_a(self, unit_id: int) -> bool:
        c = self._client
        if c is None:
            return False
        cmd = (self._map.get("commands", {}) or {}).get("control_enable_a", {}) or {}
        fc = int(cmd.get("fc", 15))
        if fc != 15:
            return False
        address = self._address_zero_based(cmd.get("address", 3456))
        raw_vals = self._control_values_a
        if not raw_vals:
            raw_vals = cmd.get("values", [True, True, True]) or [True, True, True]
        values = [bool(v) for v in list(raw_vals)]
        if not values:
            values = [True]
        try:
            if self._ctrl_diag_count < 5:
                print(f"[LB] write_coils addr={address} (1-based={address+1}) values={values} unit={unit_id}")
            wr = c.write_coils(address=address, values=values, **uid_kwargs(unit_id))
            if self._is_error_response(wr):
                if self._ctrl_diag_count < 5:
                    print(f"[LB] write_coils FAILED (error response): {wr}")
                    self._ctrl_diag_count += 1
                return False
            if self._ctrl_diag_count < 5:
                print(f"[LB] write_coils OK")
                self._ctrl_diag_count += 1
            self._control_dirty_a = False
            return True
        except Exception as exc:
            if self._ctrl_diag_count < 5:
                print(f"[LB] write_coils EXCEPTION: {type(exc).__name__}: {exc}")
                self._ctrl_diag_count += 1
            self._connected = False
            self._disconnect_client()
            return False

    def _write_heartbeat(self, unit_id: int, state: bool) -> bool:
        c = self._client
        if c is None:
            return False
        hb = (self._map.get("commands", {}) or {}).get("heartbeat", {}) or {}
        address = self._address_zero_based(hb.get("address", 0))
        try:
            wr = c.write_coil(address=address, value=bool(state), **uid_kwargs(unit_id))
            return not self._is_error_response(wr)
        except Exception:
            self._connected = False
            self._disconnect_client()
            return False


