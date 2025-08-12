# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Set

from ..config.loader import load_yaml_config

@dataclass
class PluginStatus:
    ok: bool
    message: str = ""


class BasePlugin:
    id: str = "base"

    def __init__(self, configs_dir: Path, config_name: str) -> None:
        self.configs_dir = configs_dir
        self.config_name = config_name
        self.config: Dict[str, Any] = {}
        self.mode: str = "real"  # or "sim"

    def load_config(self) -> None:
        path = self.configs_dir / self.config_name
        self.config = load_yaml_config(path)
        self.mode = self.config.get("mode", "real")

    def configure(self) -> None:
        pass

    def validate(self) -> PluginStatus:
        return PluginStatus(ok=True)

    def arm(self) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def teardown(self) -> None:
        pass

    def status(self) -> PluginStatus:
        return PluginStatus(ok=True)

    def aliases(self) -> Set[str]:
        """Return a set of aliases this plugin will produce for recording/UI.
        Default is empty; plugins should override when applicable.
        """
        return set()

    def units(self) -> Dict[str, str]:
        """Return a mapping of alias -> unit label for display/export.
        Default empty; plugins should override when applicable.
        """
        return {}


