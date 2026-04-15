# Author: T. Onkst | Date: 03092026
from __future__ import annotations


def apply_scaling(raw: float, scaling: dict) -> float:
    scale_type = scaling.get("type", "none")

    if scale_type == "none":
        return raw

    if scale_type == "linear":
        gain = scaling.get("gain", 1.0)
        offset = scaling.get("offset", 0.0)
        return raw * gain + offset

    if scale_type == "table":
        return _table_interp(raw, scaling.get("points", []), bool(scaling.get("extrapolate", False)))

    return raw


def _table_interp(raw: float, points: list, extrapolate: bool = False) -> float:
    if len(points) < 2:
        return raw

    pts = sorted(points, key=lambda p: p[0])

    if raw <= pts[0][0]:
        if extrapolate:
            x0, y0 = pts[0]
            x1, y1 = pts[1]
            if x1 != x0:
                return y0 + (raw - x0) * (y1 - y0) / (x1 - x0)
        return pts[0][1]
    if raw >= pts[-1][0]:
        if extrapolate:
            x0, y0 = pts[-2]
            x1, y1 = pts[-1]
            if x1 != x0:
                return y1 + (raw - x1) * (y1 - y0) / (x1 - x0)
        return pts[-1][1]

    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        if x0 <= raw <= x1:
            if x1 == x0:
                return y0
            t = (raw - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)

    return raw


def convert_temp_unit(value_c: float, target_unit: str) -> float:
    if target_unit == "F":
        return value_c * 9.0 / 5.0 + 32.0
    if target_unit == "K":
        return value_c + 273.15
    return value_c


def scaling_summary(scaling: dict) -> str:
    scale_type = scaling.get("type", "none")
    unit = scaling.get("unit", "")

    if scale_type in ("none", "") or scale_type is None:
        if unit and unit != "V":
            return f"No Scale ({unit})"
        return "No Scale"

    if scale_type == "linear":
        gain = scaling.get("gain", 1.0)
        offset = scaling.get("offset", 0.0)
        return f"Linear: {gain}x + {offset} {unit}".rstrip()

    if scale_type == "table":
        n = len(scaling.get("points", []))
        ext = " extrap" if scaling.get("extrapolate") else ""
        return f"Table: {n}pt{ext} {unit}".rstrip()

    return "No Scale"
