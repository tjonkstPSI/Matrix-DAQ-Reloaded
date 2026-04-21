# Author: T. Onkst
# Date: 12302025

"""Data models for persistent config and per-run metadata."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ModbusChannel:
    name: str
    address: int
    count: int = 2
    type: str = "float32"


@dataclass
class ModbusSettings:
    ip: str = "192.168.76.45"
    port: int = 502
    poll_rate_hz: float = 1.0
    channels: Dict[str, ModbusChannel] = field(default_factory=dict)
    mock: bool = False


@dataclass
class CanSettings:
    interface: str = "CAN1"
    baud: int = 250000
    dbc_path: str = "Reference/J1939 Channels_wOilTemp.dbc"
    selected_signals: List[str] = field(default_factory=list)
    mock: bool = False


@dataclass
class LoggingSettings:
    base_dir: str = "logs"
    sample_rate_hz: float = 10.0


@dataclass
class AppConfig:
    can: CanSettings = field(default_factory=CanSettings)
    modbus: ModbusSettings = field(default_factory=ModbusSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)


@dataclass
class Metadata:
    engine_type: str = ""
    engine_serial: str = ""
    operator: str = ""
    test_type: str = ""
    test_id: str = ""
    pre_test_comments: str = ""
    post_test_comments: str = ""


@dataclass
class SignalValue:
    name: str
    value: Optional[float]
    unit: str = ""
    status: str = "ok"
    timestamp: Optional[float] = None


@dataclass
class Sample:
    timestamp: float
    source: str  # "can" or "modbus"
    name: str
    value: Optional[float]
    unit: str
    status: str
    run_id: str
    metadata: Metadata


def default_modbus_channels() -> Dict[str, ModbusChannel]:
    """Fixed probe channels: temp, baro, humidity."""
    return {
        "temp": ModbusChannel(name="temp", address=8),
        "baro": ModbusChannel(name="baro", address=10),
        "humidity": ModbusChannel(name="humidity", address=12),
    }


def default_app_config() -> AppConfig:
    cfg = AppConfig()
    cfg.modbus.channels = default_modbus_channels()
    return cfg

