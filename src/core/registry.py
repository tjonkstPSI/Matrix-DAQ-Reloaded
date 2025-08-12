# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Type, List, Set

from ..plugins.base import BasePlugin


@dataclass
class PluginSpec:
    id: str
    cls: Type[BasePlugin]
    config_name: str


class PluginRegistry:
    def __init__(self, configs_dir: Path) -> None:
        self.configs_dir = configs_dir
        self._specs: Dict[str, PluginSpec] = {}
        self._instances: Dict[str, BasePlugin] = {}

    def register(self, spec: PluginSpec) -> None:
        if spec.id in self._specs:
            raise ValueError(f"Plugin id already registered: {spec.id}")
        self._specs[spec.id] = spec

    def create_all(self) -> Dict[str, BasePlugin]:
        for pid, spec in self._specs.items():
            self._instances[pid] = spec.cls(self.configs_dir, spec.config_name)
        return dict(self._instances)

    def validate_global_aliases(self, alias_lists: List[Set[str]]) -> None:
        # Flatten and ensure uniqueness
        seen: Set[str] = set()
        for group in alias_lists:
            for alias in group:
                if alias in seen:
                    raise ValueError(f"Duplicate alias detected: {alias}")
                seen.add(alias)


