# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from typing import Dict, Any, Set
from pathlib import Path
import math

from .base import BasePlugin, PluginStatus
from ..config.loader import load_yaml_config


class LoadBankPlugin(BasePlugin):
    id = "LoadBank"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._map: Dict[str, Any] = {}
        self._setpoint_val: float = 0.0
        self._measured_val: float = 0.0

    def configure(self) -> None:
        model_cfg = self.config.get("model", {}) or {}
        map_file = model_cfg.get("map_file")
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

    def validate(self) -> PluginStatus:
        # Minimal presence checks
        if not isinstance(self.config.get("model", {}), dict):
            return PluginStatus(ok=False, message="model block must be provided")
        return PluginStatus(ok=True)

    def aliases(self) -> Set[str]:
        aliases: Set[str] = set()
        exposes = self.config.get("expose_channels", {}) or {}
        for k in ("measured_load_alias", "ready_alias", "faults_alias", "setpoint_alias", "accept_alias"):
            v = exposes.get(k)
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
        }
        return {k: v for k, v in unit_map.items() if k}

    def start(self) -> None:
        self._setpoint_val = 0.0
        self._measured_val = 0.0

    def command_setpoint_pct(self, pct: float) -> None:
        limits = (self.config.get("safety", {}) or {}).get("setpoint_limits_percent", {})
        lo = float(limits.get("min", 0.0))
        hi = float(limits.get("max", 100.0))
        self._setpoint_val = max(lo, min(hi, float(pct)))

    def simulate_step(self) -> Dict[str, Any]:
        """Simulate measured load following setpoint with first-order lag."""
        exposes = self.config.get("expose_channels", {}) or {}
        out: Dict[str, Any] = {}
        # simple lag towards setpoint
        self._measured_val += 0.2 * (self._setpoint_val - self._measured_val)
        out[exposes.get("measured_load_alias", "LB Measured Load")] = self._measured_val
        out[exposes.get("setpoint_alias", "LB Setpoint")] = self._setpoint_val
        out[exposes.get("ready_alias", "LB Ready")] = 1
        out[exposes.get("faults_alias", "LB Faults")] = 0
        return out


