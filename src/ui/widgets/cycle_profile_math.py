# Author: T. Onkst | Date: 05072026

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence


@dataclass(frozen=True)
class ExpandedCycleProfile:
    times: List[float]
    loads: List[float]
    cycle_len_s: float
    total_duration_s: float
    loop_boundaries_s: List[float]


def build_expanded_cycle_profile(
    times: Sequence[float],
    loads: Sequence[float],
    loops: int = 1,
    dwell_s: float = 0.0,
) -> ExpandedCycleProfile:
    """Build the same step-expanded cycle profile used by the config preview."""
    pairs = [(float(t), float(v)) for t, v in zip(times, loads)]
    if not pairs:
        return ExpandedCycleProfile([], [], 0.0, 0.0, [])

    loops = max(1, int(loops))
    dwell_s = max(0.0, float(dwell_s))
    t0 = pairs[0][0]
    cycle_len = pairs[-1][0] - t0 if len(pairs) > 1 else 0.0

    step_t: List[float] = []
    step_v: List[float] = []
    for i, (t, v) in enumerate(pairs):
        rel_t = t - t0
        if i > 0:
            step_t.append(rel_t)
            step_v.append(pairs[i - 1][1])
        step_t.append(rel_t)
        step_v.append(v)

    all_t: List[float] = []
    all_v: List[float] = []
    for loop_i in range(loops):
        offset = loop_i * (cycle_len + dwell_s)
        for t, v in zip(step_t, step_v):
            all_t.append(t + offset)
            all_v.append(v)
        if dwell_s > 0.0 and loop_i < loops - 1:
            all_t.append(offset + cycle_len)
            all_v.append(step_v[-1])
            all_t.append(offset + cycle_len + dwell_s)
            all_v.append(step_v[-1])

    loop_boundaries = [li * (cycle_len + dwell_s) for li in range(1, loops)]
    total_duration = max(all_t) if all_t else 0.0
    return ExpandedCycleProfile(all_t, all_v, cycle_len, total_duration, loop_boundaries)
