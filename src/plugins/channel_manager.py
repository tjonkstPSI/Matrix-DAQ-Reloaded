# Author: T. Onkst | Date: 03092026

from __future__ import annotations

from .base import BasePlugin


class ChannelManagerPlugin(BasePlugin):
    """Placeholder plugin for Channel Manager.

    The Channel Manager currently operates through the orchestrator's
    alarm engine and tick cadence logic rather than the standard plugin
    lifecycle.  This class exists so it can be registered in the plugin
    registry like every other plugin, keeping the _Stub out of the
    orchestrator.
    """

    id = "Channel_Manager"
