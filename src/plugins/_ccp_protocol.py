# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import ctypes
import sys
import time
from dataclasses import dataclass
from typing import Any, List, Optional

try:
    import nixnet  # type: ignore
except Exception:
    nixnet = None


def _win_timer_begin(period_ms: int = 1) -> bool:
    """Request high-resolution Windows timer (affects WaitForSingleObject, Sleep, etc.)."""
    if sys.platform != "win32":
        return False
    try:
        return ctypes.windll.winmm.timeBeginPeriod(ctypes.c_uint(period_ms)) == 0
    except Exception:
        return False


def _win_timer_end(period_ms: int = 1) -> bool:
    """Restore default Windows timer resolution."""
    if sys.platform != "win32":
        return False
    try:
        return ctypes.windll.winmm.timeEndPeriod(ctypes.c_uint(period_ms)) == 0
    except Exception:
        return False


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

    def build_set_mta(self, address: int, extension: int = 0, mta_num: int = 0, byteorder: str = "big") -> CanFrame:
        ctr = self._next_ctr()
        addr_bytes = int(address).to_bytes(4, byteorder=byteorder, signed=False)
        payload = bytes([0x02, ctr & 0xFF, mta_num & 0xFF, extension & 0xFF]) + addr_bytes
        return self._frame(payload)

    def build_dnload(self, size: int, data_bytes: bytes) -> CanFrame:
        ctr = self._next_ctr()
        payload = bytes([0x03, ctr & 0xFF, size & 0xFF]) + data_bytes[:5].ljust(5, b"\x00")
        return self._frame(payload)

    def build_short_up(self, size: int, address: int, extension: int = 0, byteorder: str = "big") -> CanFrame:
        ctr = self._next_ctr()
        addr_bytes = int(address).to_bytes(4, byteorder=byteorder, signed=False)
        payload = bytes([0x0F, ctr & 0xFF, size & 0xFF, extension & 0xFF]) + addr_bytes
        return self._frame(payload)

    def build_get_daq_size(self, daq_list: int, dto_id: int) -> CanFrame:
        ctr = self._next_ctr()
        payload = bytes([0x14, ctr & 0xFF, 0x00, daq_list & 0xFF])
        payload += int(dto_id).to_bytes(4, byteorder="little", signed=False)
        return self._frame(payload)

    def build_set_daq_ptr(self, daq_list: int, odt: int, element: int) -> CanFrame:
        ctr = self._next_ctr()
        payload = bytes([0x15, ctr & 0xFF, daq_list & 0xFF, odt & 0xFF, element & 0xFF, 0x00, 0x00, 0x00])
        return self._frame(payload)

    def build_write_daq(self, size: int, address: int, extension: int = 0, byteorder: str = "big") -> CanFrame:
        ctr = self._next_ctr()
        payload = bytes([0x16, ctr & 0xFF, size & 0xFF, extension & 0xFF])
        payload += int(address).to_bytes(4, byteorder=byteorder, signed=False)
        return self._frame(payload)

    def build_start_stop(self, mode: int, daq_list: int, last_odt: int, event_channel: int, prescaler: int = 1) -> CanFrame:
        ctr = self._next_ctr()
        payload = bytes(
            [
                0x06,
                ctr & 0xFF,
                mode & 0xFF,
                daq_list & 0xFF,
                last_odt & 0xFF,
                event_channel & 0xFF,
                prescaler & 0xFF,
                0x00,
            ]
        )
        return self._frame(payload)

    def build_start_stop_all(self, mode: int) -> CanFrame:
        ctr = self._next_ctr()
        payload = bytes([0x08, ctr & 0xFF, mode & 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
        return self._frame(payload)


class NixnetSession:
    _FRAME_NAME = "CCP_Rx"
    _CLUSTER_NAME = "CCP_Net"

    def __init__(self, interface: str, baudrate: int, force_stream_rx: bool = False) -> None:
        self.interface = interface
        self.baudrate = int(baudrate)
        self.force_stream_rx = bool(force_stream_rx)
        self._tx = None
        self._rx = None
        self._db = None
        self.rx_mode: str = "closed"
        self.last_recv_stats: dict[str, float] = {}
        self._win_timer_set: bool = False

    def _create_queued_rx(self, rx_id: int, is_extended: bool = True) -> bool:
        """Try to set up a hardware-filtered FrameInQueuedSession.

        Creates an in-memory XNET database with one CAN frame
        definition so the driver knows which CAN ID to filter.
        Returns True on success, False on failure.
        """
        try:
            db = nixnet.database.Database(":memory:")
            cluster = db.clusters.add(self._CLUSTER_NAME)
            frame = cluster.frames.add(self._FRAME_NAME)
            frame.id = int(rx_id)
            frame.payload_len = 8
            try:
                frame.can_ext_id = bool(is_extended)
            except Exception:
                pass
            self._db = db
            self._rx = nixnet.FrameInQueuedSession(
                self.interface, ":memory:", self._CLUSTER_NAME, self._FRAME_NAME,
            )
            self.rx_mode = "queued"
            print(f"[NixnetSession] Queued RX session OK (hw filter 0x{rx_id:X})")
            return True
        except Exception as eq:
            print(f"[NixnetSession] FrameInQueuedSession failed ({eq}), using stream fallback")
            if self._db is not None:
                try:
                    self._db.close()
                except Exception:
                    pass
                self._db = None
            return False

    def open(self, rx_id: int, is_extended: bool = True) -> None:
        if nixnet is None:
            raise RuntimeError("nixnet package is not available")
        if _win_timer_begin(1):
            self._win_timer_set = True
            print("[NixnetSession] Windows timer resolution set to 1 ms")
        self._tx = nixnet.FrameOutStreamSession(self.interface)
        if self.force_stream_rx or not self._create_queued_rx(rx_id, is_extended):
            if self._rx is None:
                self._rx = nixnet.FrameInStreamSession(self.interface)
                self.rx_mode = "stream"
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
        if self._db is not None:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None
        self.rx_mode = "closed"
        if self._win_timer_set:
            _win_timer_end(1)
            self._win_timer_set = False

    def send(self, frame: CanFrame) -> None:
        if self._tx is None:
            raise RuntimeError("TX session is not open")
        can_id = nixnet.types.CanIdentifier(frame.arbitration_id, extended=bool(frame.is_extended))  # type: ignore[attr-defined]
        can_frame = nixnet.types.CanFrame(can_id, payload=frame.data)  # type: ignore[attr-defined]
        self._tx.frames.write([can_frame])

    def recv(
        self,
        timeout_s: float = 0.2,
        only_id: Optional[int] = None,
        batch_size: int = 1,
    ) -> List[CanFrame]:
        """Read CAN frames, optionally filtering by arbitration ID.

        ``batch_size`` controls how many frames the driver returns per
        read call.  Keep at 1 — NI-XNET stream sessions do not
        reliably return partial batches for num_frames > 1.
        """
        if self._rx is None:
            raise RuntimeError("RX session is not open")
        _pc = time.perf_counter
        n = max(1, int(batch_size))
        started = _pc()
        deadline = _pc() + max(0.001, float(timeout_s))
        out: List[CanFrame] = []
        read_calls = 0
        empty_reads = 0
        raw_frames = 0
        while _pc() < deadline:
            remaining = max(0.0, deadline - _pc())
            step_timeout = min(max(remaining, 0.0), 0.003)
            if step_timeout <= 0.0:
                break
            try:
                read_calls += 1
                frames = list(
                    self._rx.frames.read(
                        num_frames=n,
                        timeout=step_timeout,
                        frame_type=nixnet.types.CanFrame,
                    )
                )  # type: ignore[call-arg]
            except Exception:
                frames = []
            if not frames:
                empty_reads += 1
                if out:
                    break
                continue
            raw_frames += len(frames)
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
            if out and (deadline - _pc()) > 0.02:
                deadline = _pc() + 0.02
        self.last_recv_stats = {
            "duration_ms": (_pc() - started) * 1000.0,
            "read_calls": float(read_calls),
            "empty_reads": float(empty_reads),
            "raw_frames": float(raw_frames),
            "returned_frames": float(len(out)),
            "rx_mode_code": 1.0 if self.rx_mode == "queued" else (2.0 if self.rx_mode == "stream" else 0.0),
        }
        return out
