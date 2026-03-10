# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from typing import Dict, Any, Set
import threading

from .base import BasePlugin, PluginStatus
from ..config.loader import validate_with_schema
from pathlib import Path


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

    def configure(self) -> None:
        # Nothing heavy yet; configuration already loaded.
        try:
            hz = float(self.config.get("recording_rate_hz", 10.0))
        except Exception:
            hz = 10.0
        self._snapshot_period_s = max(0.01, 1.0 / max(1.0, hz))

    def validate(self) -> PluginStatus:
        # Minimal validation: ensure reads/writes blocks are lists if present
        reads = self._resolved_reads()
        writes = self.config.get("writes", [])
        if not isinstance(reads, list) or not isinstance(writes, list):
            return PluginStatus(ok=False, message="reads/writes must be lists")
        # Alias uniqueness within this plugin
        aliases = [str(item.get("alias")) for item in (reads or []) if isinstance(item, dict) and item.get("alias")]
        if len(aliases) != len(set(aliases)):
            return PluginStatus(ok=False, message="Duplicate aliases within Modbus plugin configuration")
        # Schema validation (optional if jsonschema not present)
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
        return mapping

    def start(self) -> None:
        # Reset simulator phase
        self._theta = 0.0
        self._snapshot_stop.clear()
        with self._snapshot_lock:
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

    def simulate_step(self) -> Dict[str, Any]:
        with self._snapshot_lock:
            return dict(self._snapshot_values)

    def _snapshot_loop(self) -> None:
        while not self._snapshot_stop.is_set():
            vals = self._compute_step_values()
            with self._snapshot_lock:
                self._snapshot_values = vals
            self._snapshot_stop.wait(self._snapshot_period_s)

    def _compute_step_values(self) -> Dict[str, Any]:
        """Return one simulated sample for all read aliases.
        Room Temp: 25 + 2*sin(theta); Humidity: 40 + 5*cos(theta)
        """
        import math

        vals: Dict[str, Any] = {}
        self._theta += math.pi / 20.0  # advance phase
        for item in self._resolved_reads():
            alias = item.get("alias")
            if alias == "Room Temp":
                vals[alias] = 25.0 + 2.0 * math.sin(self._theta)
            elif alias == "Humidity":
                vals[alias] = 40.0 + 5.0 * math.cos(self._theta)
            else:
                vals[alias] = 0.0
        return vals

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
                    # Carry device association for future real runtime routing.
                    if dev_name and not item.get("server"):
                        item["server"] = dev_name
                    out.append(item)
            if out:
                return out
        # Legacy fallback
        reads = self.config.get("reads", [])
        if isinstance(reads, list):
            for read in reads:
                if isinstance(read, dict):
                    out.append(dict(read))
        return out


