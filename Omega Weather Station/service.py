# Author: T. Onkst
# Date: 12302025

"""Modbus TCP polling service with mock fallback."""

from __future__ import annotations

import math
import struct
import threading
import time
from typing import Dict, List, Optional

from pymodbus.client import ModbusTcpClient  # type: ignore

from app.models.config import ModbusSettings, SignalValue


class ModbusService:
    """Manage Modbus TCP polling for three fixed channels, with mock mode."""

    def __init__(self, settings: ModbusSettings) -> None:
        self.settings = settings
        if settings.mock:
            self._backend: _BaseModbusBackend = _MockModbusBackend(settings)
        else:
            self._backend = _RealModbusBackend(settings)

    def connect(self) -> None:
        self._backend.connect()

    def disconnect(self) -> None:
        self._backend.disconnect()

    def start_polling(self) -> None:
        self._backend.start_polling()

    def stop_polling(self) -> None:
        self._backend.stop_polling()

    def get_latest(self) -> Dict[str, SignalValue]:
        return self._backend.get_latest()

    def get_units(self) -> Dict[str, str]:
        return self._backend.get_units()


class _BaseModbusBackend:
    def connect(self) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError

    def start_polling(self) -> None:
        raise NotImplementedError

    def stop_polling(self) -> None:
        raise NotImplementedError

    def get_latest(self) -> Dict[str, SignalValue]:
        raise NotImplementedError

    def get_units(self) -> Dict[str, str]:
        return {}


class _RealModbusBackend(_BaseModbusBackend):
    """Real Modbus TCP backend for three fixed channels."""

    def __init__(self, settings: ModbusSettings) -> None:
        self.settings = settings
        self._latest: Dict[str, SignalValue] = {}
        self._running = False
        self._client: Optional[ModbusTcpClient] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._order: List[str] = list(settings.channels.keys())
        self._units = {
            "temp": "C",
            "baro": "kPa",
            "humidity": "Pct",
        }

    def connect(self) -> None:
        self._client = ModbusTcpClient(host=self.settings.ip, port=self.settings.port, timeout=2)
        self._client.connect()
        self._running = True

    def disconnect(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        if self._client:
            self._client.close()
            self._client = None

    def start_polling(self) -> None:
        if not self._client:
            self.connect()
        self._running = True
        self._stop_event.clear()
        interval = 1.0 / (self.settings.poll_rate_hz or 1.0)
        self._thread = threading.Thread(target=self._poll_loop, args=(interval,), daemon=True)
        self._thread.start()

    def stop_polling(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def get_latest(self) -> Dict[str, SignalValue]:
        return self._latest

    def get_units(self) -> Dict[str, str]:
        return dict(self._units)

    def _poll_loop(self, interval: float) -> None:
        while not self._stop_event.is_set():
            self._poll_once()
            time.sleep(interval)

    def _poll_once(self) -> None:
        if not self._client:
            return
        now = time.time()
        for name, ch in self.settings.channels.items():
            try:
                rr = self._client.read_holding_registers(address=ch.address, count=ch.count)
                if not rr or not hasattr(rr, "registers"):
                    continue
                regs = rr.registers
                if len(regs) < 2:
                    continue
                raw = (regs[0] << 16) | regs[1]
                # Handle error/NaN codes per spec
                if raw in (0x7F800000, 0x7F800001, 0x7F800002, 0x7F800003):
                    val = math.nan
                    status = "error"
                else:
                    val = struct.unpack(">f", raw.to_bytes(4, byteorder="big"))[0]
                    status = "ok"
                unit = self._units.get(name, "")
                self._latest[name] = SignalValue(
                    name=name,
                    value=val,
                    unit=unit,
                    status=status,
                    timestamp=now,
                )
            except Exception:
                # On any poll error, leave previous value and continue
                continue


class _MockModbusBackend(_BaseModbusBackend):
    """Mock Modbus backend generating synthetic probe values."""

    def __init__(self, settings: ModbusSettings) -> None:
        self.settings = settings
        self._latest: Dict[str, SignalValue] = {}
        self._running = False
        self._order: List[str] = list(settings.channels.keys())
        self._units = {
            "temp": "C",
            "baro": "kPa",
            "humidity": "Pct",
        }

    def connect(self) -> None:
        self._running = True

    def disconnect(self) -> None:
        self._running = False

    def start_polling(self) -> None:
        self._running = True

    def stop_polling(self) -> None:
        self._running = False

    def get_latest(self) -> Dict[str, SignalValue]:
        if not self._running:
            return self._latest
        now = time.time()
        # Generate simple, distinct synthetic signals for temp/baro/humidity
        for idx, name in enumerate(self._order):
            base = 20.0 + 5.0 * idx  # different baseline per channel
            val = base + 5.0 * math.sin(now + idx)
            unit = {
                "temp": "C",
                "baro": "kPa",
                "humidity": "Pct",
            }.get(name, "")
            self._latest[name] = SignalValue(
                name=name,
                value=val,
                unit=unit,
                status="ok",
                timestamp=now,
            )
        return self._latest

    def get_units(self) -> Dict[str, str]:
        return dict(self._units)

