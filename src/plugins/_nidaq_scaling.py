# Author: T. Onkst | Date: 03092026
from __future__ import annotations

try:
    from scipy.signal import butter as _butter, sosfilt as _sosfilt, sosfilt_zi as _sosfilt_zi
    import numpy as _np
    _SCIPY_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SCIPY_AVAILABLE = False
    _butter = _sosfilt = _sosfilt_zi = _np = None  # type: ignore


class IIRFilter:
    """Stateful IIR Butterworth low-pass filter for a single channel.

    Uses SOS (second-order sections) form for numerical stability.
    Coefficients are computed once at construction; per-sample cost is
    ~8 multiply-adds per filter order (negligible).
    Falls back to passthrough if scipy is unavailable.
    """

    def __init__(self, order: int, cutoff_hz: float, sample_hz: float) -> None:
        self._available = _SCIPY_AVAILABLE
        self._initialized = False
        self._sos = None
        self._zi = None
        if self._available and cutoff_hz > 0 and sample_hz > 0:
            nyquist = sample_hz / 2.0
            cutoff_hz = min(cutoff_hz, nyquist * 0.99)
            self._sos = _butter(order, cutoff_hz, btype="low", fs=sample_hz, output="sos")
            self._zi = _sosfilt_zi(self._sos)

    def process_batch(self, samples: list) -> float:
        """Filter a batch of raw samples, return the last filtered output."""
        if not samples:
            return 0.0
        if not self._available or self._sos is None:
            return float(samples[-1])
        arr = _np.asarray(samples, dtype=_np.float64)
        if not self._initialized:
            self._zi = self._zi * arr[0]
            self._initialized = True
        out, self._zi = _sosfilt(self._sos, arr, zi=self._zi)
        return float(out[-1])

    def process(self, sample: float) -> float:
        """Filter a single sample."""
        return self.process_batch([sample])


def presort_scaling_points(scaling: dict) -> dict:
    """Return a copy with table points pre-sorted by raw value.

    No-op for non-table scaling types or already-sorted points.
    """
    if scaling.get("type") != "table":
        return scaling
    pts = scaling.get("points")
    if not pts or not isinstance(pts, list) or len(pts) < 2:
        return scaling
    sorted_pts = sorted(pts, key=lambda p: p[0])
    if sorted_pts == pts:
        return scaling
    out = dict(scaling)
    out["points"] = sorted_pts
    return out


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

    pts = points  # assumed pre-sorted by presort_scaling_points

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
