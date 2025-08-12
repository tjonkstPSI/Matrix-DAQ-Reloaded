# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from typing import Dict, Any, Set

from .base import BasePlugin, PluginStatus
from ..config.loader import validate_with_schema
from pathlib import Path


class ModbusPlugin(BasePlugin):
    id = "Modbus"

    def configure(self) -> None:
        # Nothing heavy yet; configuration already loaded
        pass

    def validate(self) -> PluginStatus:
        # Minimal validation: ensure reads/writes blocks are lists if present
        reads = self.config.get("reads", [])
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
        for item in self.config.get("reads", []) or []:
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
        for item in self.config.get("reads", []) or []:
            if not isinstance(item, dict):
                continue
            alias = item.get("alias")
            scaling = item.get("scaling") or {}
            unit = scaling.get("unit") or ""
            if alias:
                mapping[str(alias)] = str(unit)
        return mapping

    # Simple simulator state
    _theta: float = 0.0

    def start(self) -> None:
        # Reset simulator phase
        self._theta = 0.0

    def stop(self) -> None:
        pass

    def simulate_step(self) -> Dict[str, Any]:
        """Return one simulated sample for all read aliases.
        Room Temp: 25 + 2*sin(theta); Humidity: 40 + 5*cos(theta)
        """
        import math

        vals: Dict[str, Any] = {}
        self._theta += math.pi / 20.0  # advance phase
        for item in self.config.get("reads", []) or []:
            alias = item.get("alias")
            if alias == "Room Temp":
                vals[alias] = 25.0 + 2.0 * math.sin(self._theta)
            elif alias == "Humidity":
                vals[alias] = 40.0 + 5.0 * math.cos(self._theta)
            else:
                vals[alias] = 0.0
        return vals


