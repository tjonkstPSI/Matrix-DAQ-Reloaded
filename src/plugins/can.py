# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from typing import Dict, Any, Set

from .base import BasePlugin, PluginStatus


class CANPlugin(BasePlugin):
    id = "CAN"

    def configure(self) -> None:
        pass

    def validate(self) -> PluginStatus:
        signals = self.config.get("signals", [])
        if not isinstance(signals, list):
            return PluginStatus(ok=False, message="signals must be a list")
        aliases = [str(item.get("alias")) for item in (signals or []) if isinstance(item, dict) and item.get("alias")]
        if len(aliases) != len(set(aliases)):
            return PluginStatus(ok=False, message="Duplicate aliases within CAN plugin configuration")
        return PluginStatus(ok=True)

    def aliases(self) -> Set[str]:
        aliases: Set[str] = set()
        for item in self.config.get("signals", []) or []:
            alias = item.get("alias") if isinstance(item, dict) else None
            if alias:
                aliases.add(str(alias))
        return aliases

    def units(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for item in self.config.get("signals", []) or []:
            if not isinstance(item, dict):
                continue
            alias = item.get("alias")
            unit = item.get("unit", "")
            if alias:
                mapping[str(alias)] = str(unit)
        return mapping

    _theta: float = 0.0

    def start(self) -> None:
        self._theta = 0.0

    def simulate_step(self) -> Dict[str, Any]:
        import math

        vals: Dict[str, Any] = {}
        self._theta += math.pi / 30.0  # slower phase than Modbus
        for idx, item in enumerate(self.config.get("signals", []) or []):
            alias = item.get("alias")
            if not alias:
                continue
            phase = idx * math.pi / 6.0
            # Simple canned behaviors for common signals
            name = str(alias).lower()
            if "rpm" in name:
                vals[alias] = 1200.0 + 200.0 * math.sin(self._theta + phase)
            elif "oil" in name and "pressure" in name:
                vals[alias] = 300.0 + 20.0 * math.cos(self._theta + phase)
            else:
                vals[alias] = 1.0 * math.sin(self._theta + phase)
        return vals


