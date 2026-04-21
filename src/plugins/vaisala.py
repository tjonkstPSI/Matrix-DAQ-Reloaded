# Author: T. Onkst | Date: 03092026
# Updated: 03092026 — pressure writes, filtering flags, dynamic pressure telemetry feed

from __future__ import annotations

import math
import struct
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Set

from .base import BasePlugin, PluginStatus

try:
    from pymodbus.client import ModbusTcpClient  # type: ignore
except Exception:
    try:
        from pymodbus.client.tcp import ModbusTcpClient  # type: ignore
    except Exception:
        ModbusTcpClient = None  # type: ignore

from ._modbus_compat import uid_kwargs

REGISTER_MAP: List[Dict[str, Any]] = [
    {"id": "RH",   "description": "Relative Humidity",       "address": 0,  "unit": "%",    "sim_center": 45.0,  "sim_amp": 3.0},
    {"id": "T",    "description": "Temperature",             "address": 2,  "unit": "C",    "sim_center": 23.0,  "sim_amp": 0.5},
    {"id": "Td",   "description": "Dewpoint",                "address": 6,  "unit": "C",    "sim_center": 10.0,  "sim_amp": 1.0},
    {"id": "Tdf",  "description": "Dewpoint/Frostpoint",     "address": 8,  "unit": "C",    "sim_center": 9.5,   "sim_amp": 1.0},
    {"id": "a",    "description": "Absolute Humidity",        "address": 14, "unit": "g/m3", "sim_center": 10.0,  "sim_amp": 1.5},
    {"id": "x",    "description": "Mixing Ratio",            "address": 16, "unit": "g/kg", "sim_center": 8.0,   "sim_amp": 0.8},
    {"id": "Tw",   "description": "Wet Bulb Temperature",    "address": 18, "unit": "C",    "sim_center": 15.0,  "sim_amp": 0.7},
    {"id": "H2Ov", "description": "H2O by Volume",           "address": 20, "unit": "ppmv", "sim_center": 14000, "sim_amp": 500},
    {"id": "pw",   "description": "Water Vapor Pressure",    "address": 22, "unit": "hPa",  "sim_center": 12.0,  "sim_amp": 1.0},
    {"id": "pws",  "description": "Saturation Pressure",     "address": 24, "unit": "hPa",  "sim_center": 28.0,  "sim_amp": 0.5},
    {"id": "H",    "description": "Enthalpy",                "address": 26, "unit": "kJ/kg","sim_center": 50.0,  "sim_amp": 2.0},
    {"id": "dT",   "description": "T minus Td/f",            "address": 30, "unit": "C",    "sim_center": 13.0,  "sim_amp": 1.2},
    {"id": "H2Ow", "description": "H2O by Weight",           "address": 64, "unit": "ppmw", "sim_center": 8700,  "sim_amp": 300},
]

_REG_BY_ID: Dict[str, Dict[str, Any]] = {r["id"]: r for r in REGISTER_MAP}


_PRESSURE_TEMP_REG = 770   # manual 0771 → PDU 770
_FILTER_STD_REG = 1280     # manual 1281 → PDU 1280
_FILTER_EXT_REG = 1281     # manual 1282 → PDU 1281


def _decode_float32(regs: list, offset: int, word_order: str = "little") -> float:
    """Decode an IEEE 754 float32 from two consecutive 16-bit registers.

    word_order='little' (Vaisala default): LSW at lower address → swap words.
    word_order='big': MSW at lower address → no swap.
    """
    if offset + 1 >= len(regs):
        return float("nan")
    w0 = int(regs[offset]) & 0xFFFF
    w1 = int(regs[offset + 1]) & 0xFFFF
    if word_order == "little":
        raw = struct.pack(">HH", w1, w0)
    else:
        raw = struct.pack(">HH", w0, w1)
    return float(struct.unpack(">f", raw)[0])


def _encode_float32(value: float, word_order: str = "little") -> tuple:
    """Encode a float into two 16-bit register values.

    word_order='little' (Vaisala default): returns (LSW, MSW) so LSW lands at
    the lower register address.
    word_order='big': returns (MSW, LSW).
    """
    raw = struct.pack(">f", float(value))
    msw, lsw = struct.unpack(">HH", raw)
    if word_order == "little":
        return (lsw, msw)
    return (msw, lsw)


class VaisalaPlugin(BasePlugin):
    id = "Vaisala"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._enabled_channels: List[Dict[str, Any]] = []
        self._alias_map: Dict[str, str] = {}
        self._unit_map: Dict[str, str] = {}
        self._theta: float = 0.0
        self._client: Any = None
        self._poll_stop = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._snapshot_lock = threading.Lock()
        self._snapshot_values: Dict[str, Any] = {}
        self._poll_period_s: float = 1.0
        # Pressure / filtering configuration
        self._pressure_mode: str = "fixed"
        self._pressure_fixed_hpa: float = 1013.25
        self._pressure_dyn_channel: str = ""
        self._pressure_dyn_gain: float = 1.0
        self._pressure_dyn_offset: float = 0.0
        self._filtering_mode: str = "none"
        self._conn_ok: bool = False
        self._word_order: str = "little"
        # Telemetry feed from orchestrator (for dynamic pressure)
        self._telemetry_lock = threading.Lock()
        self._latest_telemetry: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def configure(self) -> None:
        chs_cfg = self.config.get("channels", []) or []
        self._enabled_channels = []
        self._alias_map = {}
        self._unit_map = {}

        for item in chs_cfg:
            if not isinstance(item, dict):
                continue
            if not bool(item.get("enabled", True)):
                continue
            ch_id = str(item.get("id", "")).strip()
            reg = _REG_BY_ID.get(ch_id)
            if reg is None:
                continue
            alias = str(item.get("alias") or reg["description"]).strip()
            if not alias:
                continue
            entry = {
                "id": ch_id,
                "alias": alias,
                "address": reg["address"],
                "unit": reg["unit"],
                "sim_center": reg["sim_center"],
                "sim_amp": reg["sim_amp"],
            }
            self._enabled_channels.append(entry)
            self._alias_map[ch_id] = alias
            self._unit_map[alias] = reg["unit"]

        conn = self.config.get("connection") or {}
        wo = str(conn.get("word_order", "little")).strip().lower()
        self._word_order = wo if wo in ("little", "big") else "little"
        try:
            hz = float(conn.get("poll_rate_hz", 1.0))
        except Exception:
            hz = 1.0
        self._poll_period_s = max(0.05, 1.0 / max(0.1, hz))

        prs = self.config.get("pressure") or {}
        self._pressure_mode = str(prs.get("mode", "fixed")).strip().lower()
        try:
            self._pressure_fixed_hpa = float(prs.get("fixed_value_hpa", 1013.25))
        except Exception:
            self._pressure_fixed_hpa = 1013.25
        dyn = prs.get("dynamic") or {}
        self._pressure_dyn_channel = str(dyn.get("source_channel", "")).strip()
        try:
            self._pressure_dyn_gain = float(dyn.get("gain", 1.0))
        except Exception:
            self._pressure_dyn_gain = 1.0
        try:
            self._pressure_dyn_offset = float(dyn.get("offset", 0.0))
        except Exception:
            self._pressure_dyn_offset = 0.0

        filt = str(self.config.get("filtering", "none")).strip().lower()
        self._filtering_mode = filt if filt in ("none", "std", "ext") else "none"

    def validate(self) -> PluginStatus:
        if not isinstance(self.config.get("connection", {}), dict):
            return PluginStatus(ok=False, message="connection block required")
        aliases = [ch["alias"] for ch in self._enabled_channels]
        if len(aliases) != len(set(aliases)):
            return PluginStatus(ok=False, message="Duplicate aliases in Vaisala channels")
        if self.mode == "real":
            if ModbusTcpClient is None:
                return PluginStatus(ok=False, message="pymodbus package is required for Vaisala real mode")
        return PluginStatus(ok=True)

    def start(self) -> None:
        self._poll_stop.clear()
        self._theta = 0.0
        with self._snapshot_lock:
            self._snapshot_values = {}
        if self.mode == "real":
            if not self._connect():
                print("[Vaisala] WARNING: initial Modbus connect failed; will retry in poll loop")
            self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._poll_thread.start()

    def stop(self) -> None:
        self._poll_stop.set()
        t = self._poll_thread
        if t is not None and t.is_alive():
            try:
                t.join(timeout=2.0)
            except Exception:
                pass
        self._poll_thread = None
        self._disconnect()

    # ------------------------------------------------------------------
    # Data interface
    # ------------------------------------------------------------------

    def aliases(self) -> Set[str]:
        out = {ch["alias"] for ch in self._enabled_channels}
        out.add("Vaisala/conn_ok")
        return out

    def units(self) -> Dict[str, str]:
        m = dict(self._unit_map)
        m["Vaisala/conn_ok"] = ""
        return m

    def update_telemetry(self, vals: Dict[str, Any]) -> None:
        """Receive latest merged telemetry from the orchestrator (all plugins).

        Used by the poll thread to resolve the dynamic pressure source channel.
        """
        with self._telemetry_lock:
            self._latest_telemetry = dict(vals)

    def simulate_step(self) -> Dict[str, Any]:
        if self.mode == "real":
            with self._snapshot_lock:
                vals = dict(self._snapshot_values)
            vals["Vaisala/conn_ok"] = 1.0 if self._conn_ok else 0.0
            return vals
        vals = self._compute_sim_values()
        vals["Vaisala/conn_ok"] = 1.0
        return vals

    # ------------------------------------------------------------------
    # Modbus TCP (real mode)
    # ------------------------------------------------------------------

    def _connect(self) -> bool:
        if ModbusTcpClient is None:
            self._conn_ok = False
            return False
        conn = self.config.get("connection") or {}
        host = str(conn.get("host", "127.0.0.1")).strip()
        port = int(conn.get("port", 502))
        try:
            timeout_s = max(0.1, float(conn.get("timeout_ms", 1000)) / 1000.0)
        except Exception:
            timeout_s = 1.0
        try:
            self._client = ModbusTcpClient(host=host, port=port, timeout=timeout_s)
            ok = bool(self._client.connect())
            self._conn_ok = ok
            return ok
        except Exception as exc:
            print(f"[Vaisala] Modbus connect error: {exc}")
            self._client = None
            self._conn_ok = False
            return False

    def _disconnect(self) -> None:
        c = self._client
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
        self._client = None

    def _poll_loop(self) -> None:
        conn = self.config.get("connection") or {}
        unit_id = int(conn.get("unit_id", 1))
        logged_first = False

        while not self._poll_stop.is_set():
            c = self._client
            if c is None or not getattr(c, "is_socket_open", lambda: False)():
                self._disconnect()
                if not self._connect():
                    self._poll_stop.wait(min(5.0, self._poll_period_s * 10))
                    continue
                c = self._client

            self._write_parameters(c, unit_id)

            try:
                vals = self._read_all_channels(c, unit_id)
                with self._snapshot_lock:
                    self._snapshot_values = vals
                self._conn_ok = True
                if not logged_first:
                    print(f"[Vaisala] First poll OK: {len(vals)} channel(s)")
                    logged_first = True
            except Exception as exc:
                self._conn_ok = False
                print(f"[Vaisala] Poll error: {exc}")

            self._poll_stop.wait(self._poll_period_s)

    def _write_parameters(self, client: Any, unit_id: int) -> None:
        """Write pressure and filtering configuration to the device each cycle."""
        # --- Pressure ---
        pressure_hpa: float | None = None
        if self._pressure_mode == "fixed":
            pressure_hpa = self._pressure_fixed_hpa
        elif self._pressure_mode == "dynamic" and self._pressure_dyn_channel:
            with self._telemetry_lock:
                raw = self._latest_telemetry.get(self._pressure_dyn_channel)
            if raw is not None:
                try:
                    pressure_hpa = float(raw) * self._pressure_dyn_gain + self._pressure_dyn_offset
                except (TypeError, ValueError):
                    pressure_hpa = None

        if pressure_hpa is not None:
            pressure_hpa = max(0.0, min(9999.0, pressure_hpa))
            w0, w1 = _encode_float32(pressure_hpa, self._word_order)
            try:
                client.write_registers(address=_PRESSURE_TEMP_REG, values=[w0, w1], **uid_kwargs(unit_id))
            except Exception as exc:
                print(f"[Vaisala] Pressure write error: {exc}")

        # --- Filtering ---
        std_val = 1 if self._filtering_mode == "std" else 0
        ext_val = 1 if self._filtering_mode == "ext" else 0
        try:
            client.write_register(address=_FILTER_STD_REG, value=std_val, **uid_kwargs(unit_id))
            client.write_register(address=_FILTER_EXT_REG, value=ext_val, **uid_kwargs(unit_id))
        except Exception as exc:
            print(f"[Vaisala] Filtering write error: {exc}")

    def _read_all_channels(self, client: Any, unit_id: int) -> Dict[str, Any]:
        vals: Dict[str, Any] = {}

        need_block_a = any(ch["address"] < 32 for ch in self._enabled_channels)
        need_block_b = any(ch["address"] >= 64 for ch in self._enabled_channels)

        regs_a: list = []
        regs_b: list = []

        if need_block_a:
            rr = client.read_holding_registers(address=0, count=32, **uid_kwargs(unit_id))
            if not hasattr(rr, "registers") or rr.isError():
                print("[Vaisala] Block A read error")
            else:
                regs_a = list(rr.registers)

        if need_block_b:
            rr = client.read_holding_registers(address=64, count=2, **uid_kwargs(unit_id))
            if not hasattr(rr, "registers") or rr.isError():
                print("[Vaisala] Block B read error")
            else:
                regs_b = list(rr.registers)

        for ch in self._enabled_channels:
            addr = ch["address"]
            alias = ch["alias"]
            if addr < 32 and regs_a:
                value = _decode_float32(regs_a, addr, self._word_order)
            elif addr >= 64 and regs_b:
                value = _decode_float32(regs_b, addr - 64, self._word_order)
            else:
                value = float("nan")
            vals[alias] = value

        return vals

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------

    def _compute_sim_values(self) -> Dict[str, Any]:
        self._theta += math.pi / 60.0
        vals: Dict[str, Any] = {}
        for i, ch in enumerate(self._enabled_channels):
            phase = self._theta + i * math.pi / 7.0
            vals[ch["alias"]] = ch["sim_center"] + ch["sim_amp"] * math.sin(phase)
        return vals
