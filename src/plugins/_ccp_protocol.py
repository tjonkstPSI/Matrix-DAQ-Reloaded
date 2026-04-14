# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, List, Optional

try:
    import nixnet  # type: ignore
except Exception:
    nixnet = None


def _rotr32(value: int, shift: int) -> int:
    shift &= 31
    return ((value >> shift) | (value << (32 - shift))) & 0xFFFFFFFF


def _rotl32(value: int, shift: int) -> int:
    shift &= 31
    return ((value << shift) | (value >> (32 - shift))) & 0xFFFFFFFF


def compute_key_from_seed_algo(seed: bytes, access_key: int, seed_endian: str, sec_type: str) -> bytes:
    if len(seed) != 4:
        raise ValueError("Seed must be 4 bytes")
    if seed_endian not in {"big", "little", "reverse"}:
        raise ValueError("seed_endian must be big|little|reverse")
    if sec_type not in {"CAL", "DAQ"}:
        raise ValueError("sec_type must be CAL|DAQ")
    seed_bytes = seed[::-1] if seed_endian == "reverse" else seed
    byteorder = "little" if seed_endian == "little" else "big"
    seed_value = int.from_bytes(seed_bytes, byteorder=byteorder, signed=False)
    key = seed_value ^ (access_key & 0xFFFFFFFF)
    if sec_type == "CAL":
        key = _rotr32(key, 7)
        key ^= seed_value
    else:
        key = _rotr32(key, 3)
        key ^= seed_value
        key = _rotl32(key, 5)
        key ^= seed_value
    out = key.to_bytes(4, byteorder=byteorder, signed=False)
    return out[::-1] if seed_endian == "reverse" else out


@dataclass
class CanFrame:
    arbitration_id: int
    data: bytes
    is_extended: bool = False


class CcpProto:
    def __init__(self, tx_id: int, is_extended: bool) -> None:
        self.tx_id = tx_id
        self.is_extended = is_extended
        self._ctr = 0

    def _next_ctr(self) -> int:
        self._ctr = (self._ctr + 1) & 0xFF
        return self._ctr

    def _frame(self, payload: bytes) -> CanFrame:
        return CanFrame(arbitration_id=self.tx_id, data=payload, is_extended=self.is_extended)

    def build_connect(self, station_address: int, ctr_override: int | None = None) -> CanFrame:
        ctr = ctr_override if ctr_override is not None else self._next_ctr()
        payload = bytes([
            0x01,
            ctr & 0xFF,
            station_address & 0xFF,
            (station_address >> 8) & 0xFF,
            0x00,
            0x00,
            0x00,
        ])
        return self._frame(payload)

    def build_get_seed(self, resource: int, ctr_override: int | None = None) -> CanFrame:
        ctr = ctr_override if ctr_override is not None else self._next_ctr()
        payload = bytes([0x12, ctr & 0xFF, resource & 0xFF, 0, 0, 0, 0, 0])
        return self._frame(payload)

    def build_unlock(self, key: bytes, ctr_override: int | None = None, pad: int = 0x55) -> CanFrame:
        ctr = ctr_override if ctr_override is not None else self._next_ctr()
        key6 = key[:6].ljust(6, bytes([pad & 0xFF]))
        payload = bytes([0x13, ctr & 0xFF]) + key6
        return self._frame(payload)

    def build_set_s_status(self, status: int) -> CanFrame:
        ctr = self._next_ctr()
        payload = bytes([0x0C, ctr & 0xFF, status & 0xFF]).ljust(8, b"\x00")
        return self._frame(payload)

    def build_short_up(self, size: int, address: int, extension: int = 0, byteorder: str = "big") -> CanFrame:
        ctr = self._next_ctr()
        addr_bytes = int(address).to_bytes(4, byteorder=byteorder, signed=False)
        payload = bytes([0x0F, ctr & 0xFF, size & 0xFF, extension & 0xFF]) + addr_bytes
        return self._frame(payload)


class NixnetSession:
    def __init__(self, interface: str, baudrate: int) -> None:
        self.interface = interface
        self.baudrate = int(baudrate)
        self._tx = None
        self._rx = None

    def open(self, rx_id: int) -> None:
        if nixnet is None:
            raise RuntimeError("nixnet package is not available")
        self._tx = nixnet.FrameOutStreamSession(self.interface)
        try:
            self._rx = nixnet.FrameInQueuedSession(self.interface, ":memory:", "", hex(int(rx_id)))
        except Exception:
            self._rx = nixnet.FrameInStreamSession(self.interface)
        try:
            self._tx.intf.baud_rate = self.baudrate
            self._rx.intf.baud_rate = self.baudrate
            self._tx.intf.can_tx_io_mode = nixnet.constants.CanIoMode.CAN
        except Exception:
            pass
        try:
            self._rx.start()
            self._tx.start()
        except Exception:
            pass

    def close(self) -> None:
        for s in (self._rx, self._tx):
            if s is None:
                continue
            try:
                s.close()
            except Exception:
                pass
        self._tx = None
        self._rx = None

    def send(self, frame: CanFrame) -> None:
        if self._tx is None:
            raise RuntimeError("TX session is not open")
        can_id = nixnet.types.CanIdentifier(frame.arbitration_id, extended=bool(frame.is_extended))  # type: ignore[attr-defined]
        can_frame = nixnet.types.CanFrame(can_id, payload=frame.data)  # type: ignore[attr-defined]
        self._tx.frames.write([can_frame])

    def recv(self, timeout_s: float = 0.2, only_id: Optional[int] = None) -> List[CanFrame]:
        if self._rx is None:
            raise RuntimeError("RX session is not open")
        deadline = time.time() + max(0.001, float(timeout_s))
        out: List[CanFrame] = []
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            step_timeout = min(max(remaining, 0.0), 0.01)
            if step_timeout <= 0.0:
                break
            try:
                frames = list(
                    self._rx.frames.read(
                        num_frames=1,
                        timeout=step_timeout,
                        frame_type=nixnet.types.CanFrame,
                    )
                )  # type: ignore[call-arg]
            except Exception:
                frames = []
            if not frames:
                if out:
                    break
                continue
            for fr in frames:
                cid = int(fr.identifier.identifier)
                if only_id is not None and cid != int(only_id):
                    continue
                out.append(
                    CanFrame(
                        arbitration_id=cid,
                        data=bytes(fr.payload),
                        is_extended=bool(fr.identifier.extended),
                    )
                )
            if out and (deadline - time.time()) > 0.02:
                deadline = time.time() + 0.02
        return out
