# Author: T. Onkst | Date: 04202026

from __future__ import annotations

import struct
import threading
import time
from typing import Any, Dict, List, Optional, Set

from .base import BasePlugin, PluginStatus
from ..config.loader import validate_with_schema
from pathlib import Path

try:
    from pymodbus.client import ModbusTcpClient  # type: ignore
except ImportError:
    try:
        from pymodbus.client.tcp import ModbusTcpClient  # type: ignore
    except ImportError:
        ModbusTcpClient = None  # type: ignore


def _decode_registers(
    regs: List[int],
    dtype: str,
    signed: bool,
    word_order: str,
) -> float:
    """Decode a list of 16-bit register values into a Python float.

    Supports int16, uint16, int32, uint32, float32 with AB or BA word order.
    """
    if not regs:
        return float("nan")

    if word_order.upper() == "BA" and len(regs) >= 2:
        regs = list(reversed(regs))

    raw = b""
    for r in regs:
        raw += struct.pack(">H", r & 0xFFFF)

    if dtype in ("float32", "float"):
        if len(raw) < 4:
            return float("nan")
        return struct.unpack(">f", raw[:4])[0]
    elif dtype in ("int32",):
        if len(raw) < 4:
            return float("nan")
        return float(struct.unpack(">i", raw[:4])[0])
    elif dtype in ("uint32",):
        if len(raw) < 4:
            return float("nan")
        return float(struct.unpack(">I", raw[:4])[0])
    elif dtype in ("int16",):
        return float(struct.unpack(">h", raw[:2])[0])
    elif dtype in ("uint16",):
        return float(struct.unpack(">H", raw[:2])[0])
    else:
        if signed and len(raw) >= 4:
            return float(struct.unpack(">i", raw[:4])[0])
        elif len(raw) >= 4:
            return float(struct.unpack(">I", raw[:4])[0])
        elif signed:
            return float(struct.unpack(">h", raw[:2])[0])
        else:
            return float(struct.unpack(">H", raw[:2])[0])


class _ServerConnection:
    """Manages a single Modbus TCP connection to one server."""
    __slots__ = ("name", "host", "port", "unit_id", "timeout_s", "max_retries", "client")

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.name: str = str(cfg.get("name", "")).strip()
        self.host: str = str(cfg.get("host", "127.0.0.1")).strip()
        self.port: int = int(cfg.get("port", 502))
        self.unit_id: int = int(cfg.get("unit_id", 1))
        try:
            self.timeout_s: float = max(0.1, float(cfg.get("timeout_ms", 1000)) / 1000.0)
        except Exception:
            self.timeout_s = 1.0
        self.max_retries: int = int(cfg.get("max_retries", 3))
        self.client: Any = None

    def connect(self) -> bool:
        if ModbusTcpClient is None:
            return False
        try:
            self.client = ModbusTcpClient(
                host=self.host, port=self.port, timeout=self.timeout_s
            )
            return bool(self.client.connect())
        except Exception as exc:
            print(f"[Modbus] Connect error for '{self.name}' ({self.host}:{self.port}): {exc}")
            self.client = None
            return False

    def disconnect(self) -> None:
        c = self.client
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
        self.client = None

    def is_open(self) -> bool:
        c = self.client
        if c is None:
            return False
        return bool(getattr(c, "is_socket_open", lambda: False)())


class ModbusPlugin(BasePlugin):
    id = "Modbus"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._theta = 0.0
        self._snapshot_values: Dict[str, Any] = {}
        self._snapshot_lock = threading.Lock()
        self._snapshot_thread = None
        self._snapshot_stop = threading.Event()
        self._snapshot_period_s: float = 0.1
        self._conn_ok: bool = False
        self._servers: Dict[str, _ServerConnection] = {}

    def configure(self) -> None:
        try:
            hz = float(self.config.get("recording_rate_hz", 10.0))
        except Exception:
            hz = 10.0
        self._snapshot_period_s = max(0.01, 1.0 / max(1.0, hz))

        self._servers = {}
        for srv_cfg in (self.config.get("servers") or []):
            if not isinstance(srv_cfg, dict):
                continue
            sc = _ServerConnection(srv_cfg)
            if sc.name:
                self._servers[sc.name] = sc

        n = len(self._resolved_reads())
        print(f"[INFO] Modbus: {n} read channel(s) resolved, {len(self._servers)} server(s)")

    def validate(self) -> PluginStatus:
        reads = self._resolved_reads()
        writes = self.config.get("writes", [])
        if not isinstance(reads, list) or not isinstance(writes, list):
            return PluginStatus(ok=False, message="reads/writes must be lists")
        aliases = [str(item.get("alias")) for item in (reads or []) if isinstance(item, dict) and item.get("alias")]
        if len(aliases) != len(set(aliases)):
            return PluginStatus(ok=False, message="Duplicate aliases within Modbus plugin configuration")
        if self.mode == "real" and ModbusTcpClient is None:
            return PluginStatus(ok=False, message="pymodbus is required for Modbus real mode")
        try:
            schema_path = (self.configs_dir / "schemas" / "modbus.schema.json")
            validate_with_schema(self.config, schema_path)
        except ValueError as e:
            return PluginStatus(ok=False, message=str(e))
        return PluginStatus(ok=True)

    def aliases(self) -> Set[str]:
        aliases: Set[str] = set()
        for item in self._resolved_reads():
            alias = item.get("alias") if isinstance(item, dict) else None
            if alias:
                aliases.add(str(alias))
        for item in self.config.get("writes", []) or []:
            alias = item.get("alias") if isinstance(item, dict) else None
            if alias:
                aliases.add(str(alias))
        aliases.add("Modbus/conn_ok")
        return aliases

    def units(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for item in self._resolved_reads():
            if not isinstance(item, dict):
                continue
            alias = item.get("alias")
            scaling = item.get("scaling") or {}
            unit = scaling.get("unit") or ""
            if alias:
                mapping[str(alias)] = str(unit)
        mapping["Modbus/conn_ok"] = ""
        return mapping

    def start(self) -> None:
        self._theta = 0.0
        self._snapshot_stop.clear()

        if self.mode == "real":
            any_ok = False
            for sc in self._servers.values():
                if sc.connect():
                    print(f"[Modbus] Connected to '{sc.name}' at {sc.host}:{sc.port}")
                    any_ok = True
                else:
                    print(f"[Modbus] WARNING: could not connect to '{sc.name}'; will retry in poll loop")
            self._conn_ok = any_ok
        else:
            self._conn_ok = True

        with self._snapshot_lock:
            if self.mode == "real":
                self._snapshot_values = self._read_all_servers()
            else:
                self._snapshot_values = self._compute_step_values()

        self._snapshot_thread = threading.Thread(target=self._snapshot_loop, daemon=True)
        self._snapshot_thread.start()

    def stop(self) -> None:
        self._snapshot_stop.set()
        t = self._snapshot_thread
        if t is not None and t.is_alive():
            try:
                t.join(timeout=0.5)
            except Exception:
                pass
        self._snapshot_thread = None
        for sc in self._servers.values():
            sc.disconnect()
        self._conn_ok = False

    def simulate_step(self) -> Dict[str, Any]:
        with self._snapshot_lock:
            vals = dict(self._snapshot_values)
        vals["Modbus/conn_ok"] = 1.0 if self._conn_ok else 0.0
        return vals

    # ------------------------------------------------------------------
    # Snapshot loop
    # ------------------------------------------------------------------

    def _snapshot_loop(self) -> None:
        while not self._snapshot_stop.is_set():
            if self.mode == "real":
                vals = self._read_all_servers()
            else:
                vals = self._compute_step_values()
            with self._snapshot_lock:
                self._snapshot_values = vals
            self._snapshot_stop.wait(self._snapshot_period_s)

    # ------------------------------------------------------------------
    # Real-mode Modbus TCP reads
    # ------------------------------------------------------------------

    def _read_all_servers(self) -> Dict[str, Any]:
        """Read all configured channels from their assigned servers."""
        vals: Dict[str, Any] = {}
        any_ok = False

        reads_by_server: Dict[str, List[Dict[str, Any]]] = {}
        for rd in self._resolved_reads():
            if not isinstance(rd, dict):
                continue
            if not rd.get("enabled", True):
                continue
            srv_name = str(rd.get("server", "")).strip()
            reads_by_server.setdefault(srv_name, []).append(rd)

        for srv_name, reads in reads_by_server.items():
            sc = self._servers.get(srv_name)
            if sc is None:
                for alias_rd in reads:
                    a = alias_rd.get("alias")
                    if a:
                        vals[str(a)] = float("nan")
                continue

            if not sc.is_open():
                sc.disconnect()
                if not sc.connect():
                    for alias_rd in reads:
                        a = alias_rd.get("alias")
                        if a:
                            vals[str(a)] = float("nan")
                    continue

            any_ok = True
            for rd in reads:
                alias = rd.get("alias")
                if not alias:
                    continue
                try:
                    val = self._read_single(sc, rd)
                    vals[str(alias)] = val
                except Exception as exc:
                    print(f"[Modbus] Read error for '{alias}': {exc}")
                    vals[str(alias)] = float("nan")

        self._conn_ok = any_ok
        return vals

    def _read_single(self, sc: _ServerConnection, rd: Dict[str, Any]) -> float:
        """Read a single register group and decode/scale the result."""
        fc = int(rd.get("fc", 3))
        address = int(rd.get("address", 0))
        length = int(rd.get("length", 1))
        dtype = str(rd.get("type", "uint16")).lower()
        signed = str(rd.get("data_type_input", "unsigned")).lower() == "signed"
        word_order = str(rd.get("word_order", "AB")).upper()

        client = sc.client
        if client is None:
            return float("nan")

        if fc == 4:
            resp = client.read_input_registers(address=address, count=length, slave=sc.unit_id)
        else:
            resp = client.read_holding_registers(address=address, count=length, slave=sc.unit_id)

        if resp is None or getattr(resp, "isError", lambda: True)():
            raise RuntimeError(f"Modbus read error at address {address} (fc={fc})")

        regs = resp.registers
        raw_value = _decode_registers(regs, dtype, signed, word_order)

        scaling = rd.get("scaling") or {}
        try:
            m = float(scaling.get("m", 1.0))
        except Exception:
            m = 1.0
        try:
            b = float(scaling.get("b", 0.0))
        except Exception:
            b = 0.0

        return round(raw_value * m + b, 6)

    # ------------------------------------------------------------------
    # Simulation mode
    # ------------------------------------------------------------------

    def _compute_step_values(self) -> Dict[str, Any]:
        """Return one simulated sample for all read aliases."""
        import math

        vals: Dict[str, Any] = {}
        self._theta += math.pi / 20.0
        for idx, item in enumerate(self._resolved_reads()):
            alias = item.get("alias")
            if not alias:
                continue
            phase_offset = idx * 0.5
            vals[alias] = round(50.0 + 10.0 * math.sin(self._theta + phase_offset), 3)
        return vals

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _resolved_reads(self) -> list[dict[str, Any]]:
        """Resolve read channel config from new multi-device shape first, then legacy shape.

        Priority:
        1) `devices[*].reads[*]` (current UI model)
        2) top-level `reads[*]` (legacy model)
        """
        out: list[dict[str, Any]] = []
        devices = self.config.get("devices", [])
        if isinstance(devices, list) and devices:
            for dev in devices:
                if not isinstance(dev, dict):
                    continue
                dev_name = str(dev.get("name", "")).strip()
                for read in (dev.get("reads") or []):
                    if not isinstance(read, dict):
                        continue
                    item = dict(read)
                    if dev_name and not item.get("server"):
                        item["server"] = dev_name
                    out.append(item)
            if out:
                return out
        reads = self.config.get("reads", [])
        if isinstance(reads, list):
            for read in reads:
                if isinstance(read, dict):
                    out.append(dict(read))
        return out
