# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from dataclasses import dataclass

try:
    import zmq  # type: ignore
except Exception:  # pragma: no cover
    zmq = None


TELEMETRY_PUB_ENDPOINT = "tcp://127.0.0.1:5556"
CONTROL_PULL_ENDPOINT = "tcp://127.0.0.1:5557"


@dataclass
class PubSockets:
    context: any
    telemetry_pub: any


@dataclass
class SubSockets:
    context: any
    telemetry_sub: any
    status_sub: any


class IPCBus:
    def __init__(self) -> None:
        self.started = False
        self._ctx = None
        self._pub = None
        self._pull = None

    def start(self) -> None:
        if zmq is None:
            return
        self._ctx = zmq.Context.instance()
        self._pub = self._ctx.socket(zmq.PUB)
        self._pub.setsockopt(zmq.SNDHWM, 10)
        self._pub.bind(TELEMETRY_PUB_ENDPOINT)
        self._pull = self._ctx.socket(zmq.PULL)
        self._pull.bind(CONTROL_PULL_ENDPOINT)
        self.started = True

    def stop(self) -> None:
        if self._pub is not None:
            self._pub.close(0)
        if self._pull is not None:
            self._pull.close(0)
        self.started = False

    def publish_telemetry(self, payload: bytes) -> None:
        if not self.started or self._pub is None:
            return
        # Topic 'telemetry'
        self._pub.send_multipart([b"telemetry", payload])

    def publish_status(self, payload: bytes) -> None:
        if not self.started or self._pub is None:
            return
        # Topic 'status'
        self._pub.send_multipart([b"status", payload])

    def recv_controls_nonblocking(self) -> list[bytes]:
        if not self.started or self._pull is None:
            return []
        msgs: list[bytes] = []
        try:
            import zmq as _zmq
            while True:
                msg = self._pull.recv(flags=_zmq.NOBLOCK)
                msgs.append(msg)
        except Exception:
            pass
        return msgs


def create_ui_subscriber() -> SubSockets | None:
    if zmq is None:
        return None
    ctx = zmq.Context.instance()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.RCVHWM, 10)
    sub.connect(TELEMETRY_PUB_ENDPOINT)
    sub.setsockopt(zmq.SUBSCRIBE, b"telemetry")
    sub.setsockopt(zmq.SUBSCRIBE, b"status")
    return SubSockets(context=ctx, telemetry_sub=sub, status_sub=sub)


def create_ui_control_push():
    if zmq is None:
        return None
    ctx = zmq.Context.instance()
    push = ctx.socket(zmq.PUSH)
    push.connect(CONTROL_PULL_ENDPOINT)
    return {"context": ctx, "control_push": push}


