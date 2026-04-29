# Author: T. Onkst | Date: 08122025

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

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
        self._console_msgs: List[str] = []
        self._console_msgs_lock = threading.Lock()

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

    def _console_msg(self, text: str) -> None:
        """Queue a message for display in the console Messages box.

        Thread-safe -- can be called from worker threads.
        Messages are drained by the orchestrator after each simulate_step().
        """
        with self._console_msgs_lock:
            self._console_msgs.append(str(text))

    def _drain_console_msgs(self) -> List[str]:
        """Return and clear all queued console messages."""
        with self._console_msgs_lock:
            msgs = list(self._console_msgs)
            self._console_msgs.clear()
        return msgs


