# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import math
import struct
import threading
import time
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

CHANNEL_MAP: List[Dict[str, Any]] = [
    {"id": "temp",     "alias": "xTP_Amb",  "address": 8,  "unit": "C",   "sim_center": 22.0, "sim_amp": 2.0},
    {"id": "baro",     "alias": "xPR_Amb",  "address": 10, "unit": "kPa", "sim_center": 101.0, "sim_amp": 0.5},
    {"id": "humidity", "alias": "xHM_Amb",  "address": 12, "unit": "Pct", "sim_center": 45.0, "sim_amp": 5.0},
]

_NAN_CODES = frozenset((0x7F800000, 0x7F800001, 0x7F800002, 0x7F800003))

_BASE_ADDR = CHANNEL_MAP[0]["address"]
_REG_COUNT = (CHANNEL_MAP[-1]["address"] - _BASE_ADDR) + 2


def _decode_float32(regs: list, offset: int) -> tuple:
    """Decode big-endian float32 from two 16-bit registers.

    Returns (value, is_error).  Error/NaN sentinel codes produce (nan, True).
    """
    if offset + 1 >= len(regs):
        return (float("nan"), True)
    w0 = int(regs[offset]) & 0xFFFF
    w1 = int(regs[offset + 1]) & 0xFFFF
    raw_int = (w0 << 16) | w1
    if raw_int in _NAN_CODES:
        return (float("nan"), True)
    raw_bytes = struct.pack(">HH", w0, w1)
    return (float(struct.unpack(">f", raw_bytes)[0]), False)


_DEFAULT_ALIASES: Dict[str, str] = {ch["id"]: ch["alias"] for ch in CHANNEL_MAP}


class OmegaPlugin(BasePlugin):
    id = "Omega"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._active_channels: List[Dict[str, Any]] = []
        self._unit_map: Dict[str, str] = {}
        self._theta: float = 0.0
        self._client: Any = None
        self._poll_stop = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._snapshot_lock = threading.Lock()
        self._snapshot_values: Dict[str, Any] = {}
        self._poll_period_s: float = 1.0
        self._conn_ok: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def configure(self) -> None:
        channels_cfg = self.config.get("channels") or []
        alias_overrides: Dict[str, str] = {}
        for item in channels_cfg:
            if isinstance(item, dict):
                cid = str(item.get("id", "")).strip()
                alias = str(item.get("alias", "")).strip()
                if cid and alias:
                    alias_overrides[cid] = alias

        self._active_channels = []
        self._unit_map = {}
        for ch in CHANNEL_MAP:
            alias = alias_overrides.get(ch["id"], ch["alias"])
            if not alias:
                continue
            entry = dict(ch, alias=alias)
            self._active_channels.append(entry)
            self._unit_map[alias] = ch["unit"]

    def validate(self) -> PluginStatus:
        if not isinstance(self.config.get("connection", {}), dict):
            return PluginStatus(ok=False, message="connection block required in omega.yaml")
        if self.mode == "real" and ModbusTcpClient is None:
            return PluginStatus(ok=False, message="pymodbus is required for Omega real mode")
        aliases = [ch["alias"] for ch in self._active_channels]
        if len(aliases) != len(set(aliases)):
            return PluginStatus(ok=False, message="Duplicate aliases in Omega channels")
        return PluginStatus(ok=True)

    def start(self) -> None:
        self._poll_stop.clear()
        self._theta = 0.0
        with self._snapshot_lock:
            self._snapshot_values = {}
        if self.mode == "real":
            if not self._connect():
                print("[Omega] WARNING: initial Modbus connect failed; will retry in poll loop")
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
        out = {ch["alias"] for ch in self._active_channels}
        out.add("Omega/conn_ok")
        return out

    def units(self) -> Dict[str, str]:
        m = dict(self._unit_map)
        m["Omega/conn_ok"] = ""
        return m

    def simulate_step(self) -> Dict[str, Any]:
        if self.mode == "real":
            with self._snapshot_lock:
                vals = dict(self._snapshot_values)
            vals["Omega/conn_ok"] = 1.0 if self._conn_ok else 0.0
            return vals
        vals = self._compute_sim_values()
        vals["Omega/conn_ok"] = 1.0
        return vals

    # ------------------------------------------------------------------
    # Modbus TCP (real mode)
    # ------------------------------------------------------------------

    def _connect(self) -> bool:
        if ModbusTcpClient is None:
            self._conn_ok = False
            return False
        conn = self.config.get("connection") or {}
        host = str(conn.get("host", "192.168.76.45")).strip()
        port = int(conn.get("port", 502))
        try:
            timeout_s = max(0.1, float(conn.get("timeout_ms", 2000)) / 1000.0)
        except Exception:
            timeout_s = 2.0
        try:
            self._client = ModbusTcpClient(host=host, port=port, timeout=timeout_s)
            ok = bool(self._client.connect())
            self._conn_ok = ok
            return ok
        except Exception as exc:
            print(f"[Omega] Modbus connect error: {exc}")
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
        logged_first = False

        while not self._poll_stop.is_set():
            c = self._client
            if c is None or not getattr(c, "is_socket_open", lambda: False)():
                self._disconnect()
                if not self._connect():
                    self._poll_stop.wait(min(5.0, self._poll_period_s * 10))
                    continue
                c = self._client

            try:
                vals = self._read_channels(c)
                with self._snapshot_lock:
                    self._snapshot_values = vals
                self._conn_ok = True
                if not logged_first:
                    print(f"[Omega] First poll OK: {len(vals)} channel(s)")
                    logged_first = True
            except Exception as exc:
                self._conn_ok = False
                print(f"[Omega] Poll error: {exc}")

            self._poll_stop.wait(self._poll_period_s)

    def _read_channels(self, client: Any) -> Dict[str, Any]:
        """Bulk read all 3 channels in a single Modbus transaction."""
        rr = client.read_holding_registers(address=_BASE_ADDR, count=_REG_COUNT, **uid_kwargs(1))
        if not hasattr(rr, "registers") or rr.isError():
            return {}

        regs = list(rr.registers)
        vals: Dict[str, Any] = {}
        for ch in self._active_channels:
            offset = ch["address"] - _BASE_ADDR
            value, _is_err = _decode_float32(regs, offset)
            vals[ch["alias"]] = value
        return vals

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------

    def _compute_sim_values(self) -> Dict[str, Any]:
        self._theta += math.pi / 60.0
        vals: Dict[str, Any] = {}
        for i, ch in enumerate(self._active_channels):
            phase = self._theta + i * math.pi / 7.0
            vals[ch["alias"]] = ch["sim_center"] + ch["sim_amp"] * math.sin(phase)
        return vals
