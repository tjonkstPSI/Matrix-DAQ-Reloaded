# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from typing import Dict, Any, Set, List

from .base import BasePlugin, PluginStatus


class CCPPlugin(BasePlugin):
    id = "CCP"

    def _final_aliases(self) -> List[str]:
        meas = (self.config.get("measurements") or {})
        prefix = str(meas.get("naming_prefix") or "")
        items = meas.get("list", []) or []
        result: List[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not name:
                continue
            alias = f"{prefix}{name}" if prefix else str(name)
            result.append(alias)
        return result

    def configure(self) -> None:
        pass

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
        return PluginStatus(ok=True)

    def aliases(self) -> Set[str]:
        return set(self._final_aliases())

    def units(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        meas = (self.config.get("measurements") or {})
        prefix = str(meas.get("naming_prefix") or "")
        for item in meas.get("list", []) or []:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not name:
                continue
            alias = f"{prefix}{name}" if prefix else str(name)
            unit = item.get("unit_override") or ""
            mapping[alias] = str(unit)
        return mapping

    _theta: float = 0.0

    def start(self) -> None:
        self._theta = 0.0

    def simulate_step(self) -> Dict[str, Any]:
        import math
        vals: Dict[str, Any] = {}
        meas = (self.config.get("measurements") or {})
        prefix = str(meas.get("naming_prefix") or "")
        items = meas.get("list", []) or []
        self._theta += math.pi / 28.0
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not name:
                continue
            alias = f"{prefix}{name}" if prefix else str(name)
            lname = str(name).lower()
            phase = idx * math.pi / 5.0
            if "rpm" in lname:
                vals[alias] = 1300.0 + 150.0 * math.sin(self._theta + phase)
            elif ("temp" in lname) or ("temperature" in lname):
                vals[alias] = 85.0 + 1.5 * math.sin(self._theta + phase)
            elif ("press" in lname) or ("pressure" in lname):
                vals[alias] = 320.0 + 10.0 * math.cos(self._theta + phase)
            else:
                vals[alias] = 1.0 * math.sin(self._theta + phase)
        return vals


