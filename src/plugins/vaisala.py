# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from typing import Dict, Any, Set

from .base import BasePlugin, PluginStatus


class VaisalaPlugin(BasePlugin):
    id = "Vaisala"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._theta: float = 0.0
        self._exposed: Dict[str, str] = {}

    def configure(self) -> None:
        # Derive exposed channels based on model (sim path only)
        # For now, standard env channels with configurable aliases via 'channels' list
        # If channels empty, default to Humidity/Temperature/Ambient Pressure
        chs = self.config.get("channels", []) or []
        if not chs:
            chs = [
                {"alias": "Ambient Temp", "unit": "C"},
                {"alias": "Ambient RH", "unit": "%RH"},
                {"alias": "Ambient Pressure", "unit": "kPa"},
            ]
        self._exposed = {}
        for item in chs:
            if not isinstance(item, dict):
                continue
            alias = item.get("alias")
            unit = item.get("unit", "")
            if alias:
                self._exposed[str(alias)] = str(unit)

    def validate(self) -> PluginStatus:
        # Minimal: connection and model blocks present
        if not isinstance(self.config.get("connection", {}), dict):
            return PluginStatus(ok=False, message="connection block required")
        if not isinstance(self.config.get("model", {}), dict):
            return PluginStatus(ok=False, message="model block required")
        # Alias uniqueness within channel list
        chs = self.config.get("channels", []) or []
        aliases = [str(x.get("alias")) for x in chs if isinstance(x, dict) and x.get("alias")]
        if len(aliases) != len(set(aliases)):
            return PluginStatus(ok=False, message="Duplicate aliases in Vaisala channels")
        return PluginStatus(ok=True)

    def start(self) -> None:
        self._theta = 0.0

    def aliases(self) -> Set[str]:
        return set(self._exposed.keys())

    def units(self) -> Dict[str, str]:
        return dict(self._exposed)

    def simulate_step(self) -> Dict[str, Any]:
        # Simple ambient environment simulation
        import math
        self._theta += math.pi / 60.0
        vals: Dict[str, Any] = {}
        for alias, unit in self._exposed.items():
            low = alias.lower()
            if ("temp" in low) or ("temperature" in low):
                vals[alias] = 23.0 + 0.5 * math.sin(self._theta)
            elif ("rh" in low) or ("humid" in low):
                vals[alias] = 45.0 + 3.0 * math.cos(self._theta)
            elif ("press" in low) or ("pressure" in low):
                vals[alias] = 101.3 + 0.2 * math.sin(self._theta / 2.0)
            else:
                vals[alias] = 0.0
        # Apply optional calibration offsets
        offsets = self.config.get("calibration_offsets", {}) or {}
        for alias, off in offsets.items():
            try:
                vals[alias] = float(vals.get(alias, 0.0)) + float(off)
            except Exception:
                continue
        return vals


