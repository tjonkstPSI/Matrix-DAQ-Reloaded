# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import math
from typing import Dict, Any, List


def simulate_step(
    theta: float,
    ai_voltage: List[Dict[str, Any]],
    ai_temp: List[Dict[str, Any]],
    di: List[Dict[str, Any]],
    do_states: Dict[str, int],
    ao_states: Dict[str, float],
    oversample_factor: int,
) -> Dict[str, Any]:
    """Generate simulated data for one tick. Returns (vals, new_theta)."""
    vals: Dict[str, Any] = {}
    theta += math.pi / 24.0
    for idx, ch in enumerate(ai_voltage):
        if not bool(ch.get("enabled", True)):
            continue
        alias = str(ch.get("alias", f"AI_V_{idx}"))
        scaling = ch.get("scaling") or {}
        m = float(scaling.get("m", 1.0))
        b = float(scaling.get("b", 0.0))
        acc = 0.0
        for k in range(max(1, oversample_factor)):
            phase = theta + (k / float(oversample_factor)) * (math.pi / 24.0)
            v = 5.0 + 5.0 * math.sin(phase + idx * math.pi / 8.0)
            acc += v
        v_aa = acc / float(max(1, oversample_factor))
        vals[alias] = m * v_aa + b
    for idx, ch in enumerate(ai_temp):
        if not bool(ch.get("enabled", True)):
            continue
        alias = str(ch.get("alias", f"AI_T_{idx}"))
        vals[alias] = 23.0 + 0.6 * math.sin(theta + idx * math.pi / 10.0)
    for idx, ch in enumerate(di):
        if not bool(ch.get("enabled", True)):
            continue
        alias = str(ch.get("alias", f"DI_{idx}"))
        vals[alias] = int(ch.get("initial", 1))
    for alias, state in do_states.items():
        vals[alias] = state
    for alias, state in ao_states.items():
        vals[alias] = state
    return vals, theta
