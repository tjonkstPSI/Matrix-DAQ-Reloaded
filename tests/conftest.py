# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
# Project root must be on path so `src` is a package (plugins.base uses relative imports).
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture
def project_root() -> Path:
    return _ROOT


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def sample_alarm_config() -> dict:
    return {
        "channels": [
            {
                "alias": "T_Engine",
                "warning": {"low": 10.0, "high": 90.0},
                "alarm": {"low": 0.0, "high": 100.0},
            }
        ],
    }


@pytest.fixture
def sample_calculated_channel_config() -> dict:
    return {
        "recording_rate_hz": 10.0,
        "channels": [
            {
                "alias": "calc_sum",
                "expr": "a + b",
                "symbols": {"a": "ch_a", "b": "ch_b"},
            }
        ],
    }
