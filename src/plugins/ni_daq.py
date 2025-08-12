# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from typing import Dict, Any, Set, List

from .base import BasePlugin, PluginStatus


class NiDAQPlugin(BasePlugin):
    id = "NI_DAQ"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._inventory: Dict[str, Any] = {}

    def _nidaq_available(self) -> bool:
        try:
            import nidaqmx  # type: ignore
            return True
        except Exception:
            return False

    def configure(self) -> None:
        # Enumerate devices/channels if NI-DAQmx is available and mode is real
        if self.mode == "real" and self._nidaq_available():
            self._inventory = self._enumerate_system()

    def validate(self) -> PluginStatus:
        if self.mode == "real" and not self._nidaq_available():
            return PluginStatus(ok=False, message="NI-DAQmx Python package not available")
        # Basic channel alias uniqueness within config
        chans = self.config.get("channels", []) or []
        aliases = [str(ch.get("alias")) for ch in chans if isinstance(ch, dict) and ch.get("alias")]
        if len(aliases) != len(set(aliases)):
            return PluginStatus(ok=False, message="Duplicate aliases within NI DAQ configuration")
        return PluginStatus(ok=True)

    def aliases(self) -> Set[str]:
        aliases: Set[str] = set()
        for ch in self.config.get("channels", []) or []:
            alias = ch.get("alias") if isinstance(ch, dict) else None
            if alias:
                aliases.add(str(alias))
        return aliases

    def units(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for ch in self.config.get("channels", []) or []:
            if not isinstance(ch, dict):
                continue
            alias = ch.get("alias")
            unit = (ch.get("scaling") or {}).get("unit") or ""
            if alias:
                mapping[str(alias)] = str(unit)
        return mapping

    def inventory(self) -> Dict[str, Any]:
        return dict(self._inventory)

    def _enumerate_system(self) -> Dict[str, Any]:
        """Return a simple inventory of devices/modules and AI/DI/DO/AO channels."""
        inv: Dict[str, Any] = {"devices": []}
        try:
            from nidaqmx.system import System  # type: ignore
        except Exception:
            return inv
        sys = System.local()
        for dev in sys.devices:
            dev_info: Dict[str, Any] = {
                "name": dev.name,
                "product_type": getattr(dev, "product_type", ""),
                "ai": [],
                "di": [],
                "do": [],
                "ao": [],
            }
            try:
                for ch in getattr(dev, "ai_physical_chans", []):
                    dev_info["ai"].append(ch.name)
            except Exception:
                pass
            try:
                for ch in getattr(dev, "di_lines", []):
                    dev_info["di"].append(ch.name)
            except Exception:
                pass
            try:
                for ch in getattr(dev, "do_lines", []):
                    dev_info["do"].append(ch.name)
            except Exception:
                pass
            try:
                for ch in getattr(dev, "ao_physical_chans", []):
                    dev_info["ao"].append(ch.name)
            except Exception:
                pass
            inv["devices"].append(dev_info)
        return inv


