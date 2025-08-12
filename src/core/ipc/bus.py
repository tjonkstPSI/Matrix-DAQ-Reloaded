# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from dataclasses import dataclass

try:
    import zmq  # type: ignore
except Exception:  # pragma: no cover
    zmq = None


TELEMETRY_PUB_ENDPOINT = "tcp://127.0.0.1:5556"


@dataclass
class PubSockets:
    context: any
    telemetry_pub: any


@dataclass
class SubSockets:
    context: any
    telemetry_sub: any


class IPCBus:
    def __init__(self) -> None:
        self.started = False
        self._ctx = None
        self._pub = None

    def start(self) -> None:
        if zmq is None:
            return
        self._ctx = zmq.Context.instance()
        self._pub = self._ctx.socket(zmq.PUB)
        self._pub.bind(TELEMETRY_PUB_ENDPOINT)
        self.started = True

    def stop(self) -> None:
        if self._pub is not None:
            self._pub.close(0)
        self.started = False

    def publish_telemetry(self, payload: bytes) -> None:
        if not self.started or self._pub is None:
            return
        # Topic 'telemetry'
        self._pub.send_multipart([b"telemetry", payload])


def create_ui_subscriber() -> SubSockets | None:
    if zmq is None:
        return None
    ctx = zmq.Context.instance()
    sub = ctx.socket(zmq.SUB)
    sub.connect(TELEMETRY_PUB_ENDPOINT)
    sub.setsockopt(zmq.SUBSCRIBE, b"telemetry")
    return SubSockets(context=ctx, telemetry_sub=sub)


