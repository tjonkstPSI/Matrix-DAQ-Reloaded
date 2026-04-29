# Author: T. Onkst | Date: 04212026

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


Coeffs = Tuple[float, float, float, float, float, float]


@dataclass(frozen=True)
class A2LChannel:
    name: str
    address: Optional[int]
    data_type: Optional[str]
    limits: Optional[tuple[float, float]]
    unit: str = ""
    coeffs: Optional[Coeffs] = None


@dataclass(frozen=True)
class A2LDaqList:
    name: str
    tier: str
    period_ms: int
    list_number: Optional[int] = None
    odt_count: int = 0
    first_pid: Optional[int] = None
    can_id: Optional[int] = None
    raw_can_id: Optional[int] = None
    raster: Optional[int] = None


def normalize_dto_can_id(can_id: Optional[int]) -> Optional[int]:
    """Normalize ASAP2 CCP DTO CAN IDs to runtime CAN arbitration IDs.

    Some A2L files encode fixed extended DTO IDs with flag bits in the upper
    nibble. NI-XNET reports the actual 29-bit arbitration ID, so strip those
    metadata bits when present (for example 0x8CFF5200 -> 0x0CFF5200).
    """
    if can_id is None:
        return None
    value = int(can_id)
    if value > 0x1FFFFFFF:
        value &= 0x1FFFFFFF
    return value


def parse_address(token: str) -> Optional[int]:
    try:
        if token.startswith(("0x", "0X")):
            return int(token, 16)
        return int(token, 10)
    except Exception:
        return None


def _try_float(s: str) -> Optional[float]:
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def parse_a2l(path: Path) -> Dict[str, A2LChannel]:
    channels: Dict[str, A2LChannel] = {}
    if not path.exists():
        return channels

    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()

    data_types = {
        "UBYTE", "SBYTE", "UWORD", "SWORD",
        "ULONG", "SLONG", "FLOAT32_IEEE", "FLOAT64_IEEE",
    }

    # --- Pass 1: parse COMPU_METHOD blocks for units and COEFFS ---
    compu_units: Dict[str, str] = {}
    compu_coeffs: Dict[str, Coeffs] = {}

    in_compu = False
    compu_name: Optional[str] = None
    compu_is_rat = False
    got_format_unit = False
    unit_q_count = 0

    def _extract_quoted(text: str) -> List[str]:
        vals: List[str] = []
        s = text
        while '"' in s:
            try:
                _, rest = s.split('"', 1)
                q, s = rest.split('"', 1)
                vals.append(q)
            except Exception:
                break
        return vals

    for raw_line in lines:
        line = raw_line.strip()

        if line.startswith("/begin COMPU_METHOD"):
            parts = line.split()
            compu_name = parts[2] if len(parts) > 2 else None
            in_compu = True
            compu_is_rat = False
            got_format_unit = False
            unit_q_count = 0
            continue

        if line.startswith("/end COMPU_METHOD"):
            in_compu = False
            compu_name = None
            continue

        if not in_compu or not compu_name:
            continue

        if line.startswith("RAT_FUNC") or line == "RAT_FUNC":
            compu_is_rat = True
            continue

        if line.startswith("IDENTICAL") or line == "IDENTICAL":
            compu_coeffs[compu_name] = (0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
            continue

        if not compu_is_rat:
            continue

        if line.startswith("COEFFS"):
            toks = line.split()
            if len(toks) >= 7:
                try:
                    c = (
                        float(toks[1]), float(toks[2]), float(toks[3]),
                        float(toks[4]), float(toks[5]), float(toks[6]),
                    )
                    compu_coeffs[compu_name] = c
                except (ValueError, IndexError):
                    pass
            continue

        if not got_format_unit:
            quoted = _extract_quoted(line)
            for q in quoted:
                unit_q_count += 1
                if unit_q_count == 2:
                    compu_units[compu_name] = q.strip()
                    got_format_unit = True
                    break

    # --- Pass 2: parse MEASUREMENT / CHARACTERISTIC blocks ---
    in_block = False
    cur_name: Optional[str] = None
    cur_addr: Optional[int] = None
    cur_type: Optional[str] = None
    cur_compu_ref: Optional[str] = None
    cur_limits: Optional[tuple[float, float]] = None
    numeric_line_index = 0

    for raw_line in lines:
        line = raw_line.strip()

        if line.startswith("/begin MEASUREMENT") or line.startswith("/begin CHARACTERISTIC"):
            parts = line.split()
            cur_name = parts[2] if len(parts) > 2 else None
            cur_addr = None
            cur_type = None
            cur_compu_ref = None
            cur_limits = None
            numeric_line_index = 0
            in_block = True
            continue

        if line.startswith("/end MEASUREMENT") or line.startswith("/end CHARACTERISTIC"):
            if in_block and cur_name:
                unit = compu_units.get(str(cur_compu_ref or ""), "")
                coeffs = compu_coeffs.get(str(cur_compu_ref or ""))
                channels[cur_name] = A2LChannel(
                    name=cur_name,
                    address=cur_addr,
                    data_type=cur_type,
                    limits=cur_limits,
                    unit=unit,
                    coeffs=coeffs,
                )
            in_block = False
            cur_name = None
            continue

        if not in_block or cur_name is None:
            continue

        tokens = line.split()
        if not tokens:
            continue
        token = tokens[0]

        if cur_type is None and token in data_types:
            cur_type = token
            continue

        if cur_compu_ref is None and "/* Conversion */" in line and token:
            cur_compu_ref = token
            continue
        if cur_compu_ref is None and cur_type is not None and token.startswith("Compu_"):
            cur_compu_ref = token
            continue

        if line.startswith("ECU_ADDRESS") or line.startswith("ADDRESS"):
            if len(tokens) >= 2:
                cur_addr = parse_address(tokens[1])
            continue

        # Numeric lines: first is Resolution/Accuracy, second is Limits.
        # Accept lines starting with a digit OR a minus sign (negative lower limits).
        if line and (line[0].isdigit() or line[0] == '-'):
            if len(tokens) >= 2:
                a_val = _try_float(tokens[0])
                b_val = _try_float(tokens[1])
                if a_val is not None and b_val is not None:
                    numeric_line_index += 1
                    # Skip first numeric line (Resolution / Accuracy);
                    # take the second (actual physical limits).
                    if numeric_line_index == 1:
                        continue
                    cur_limits = (a_val, b_val)

    return channels


_DAQ_TIERS = frozenset({"1ms", "10ms", "50ms", "100ms"})


def is_daq_tier(value: str) -> bool:
    return _canonical_poll_tier(value) in _DAQ_TIERS


def _canonical_poll_tier(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "")
    aliases = {
        "high": "high", "hi": "high", "h": "high",
        "highpoll": "high", "high_poll": "high",
        "low": "low", "lo": "low", "l": "low",
        "lowpoll": "low", "low_poll": "low",
        "1": "1ms", "1ms": "1ms",
        "10": "10ms", "10ms": "10ms",
        "50": "50ms", "50ms": "50ms",
        "100": "100ms", "100ms": "100ms",
        "daq1ms": "1ms", "daq10ms": "10ms",
        "daq50ms": "50ms", "daq100ms": "100ms",
    }
    return aliases.get(text, "high")


def parse_a2l_daq_lists(path: Path) -> Dict[str, A2LDaqList]:
    """Parse CCP DAQ-list declarations from ASAP1B_CCP IF_DATA.

    The runtime still uses SHORT_UP. This metadata is used by the config UI to
    estimate old-tool-style per-tier list capacity from A2L ODT declarations.
    """
    out: Dict[str, A2LDaqList] = {}
    if not path.exists():
        return out

    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    in_source = False
    in_qp_blob = False
    source_lines: List[str] = []
    qp_lines: List[str] = []

    def _first_int(line: str) -> Optional[int]:
        tokens = line.strip().split()
        if not tokens:
            return None
        return parse_address(tokens[0])

    def _quoted(line: str) -> str:
        if '"' not in line:
            return ""
        try:
            _, rest = line.split('"', 1)
            val, _ = rest.split('"', 1)
            return val.strip()
        except Exception:
            return ""

    def _finish_source() -> None:
        if not source_lines:
            return
        name = _quoted(source_lines[0])
        numeric = [_first_int(x) for x in source_lines[1:] if _first_int(x) is not None]
        period_ms = int(numeric[1]) if len(numeric) >= 2 else 0
        tier = _canonical_poll_tier(f"{period_ms}ms")
        list_number: Optional[int] = None
        odt_count = 0
        first_pid: Optional[int] = None
        can_id: Optional[int] = None
        raster: Optional[int] = None
        for raw in qp_lines:
            line = raw.strip()
            if not line:
                continue
            tokens = line.split()
            head = tokens[0]
            if head == "LENGTH" and len(tokens) >= 2:
                odt_count = int(parse_address(tokens[1]) or 0)
            elif head == "FIRST_PID" and len(tokens) >= 2:
                first_pid = parse_address(tokens[1])
            elif head == "CAN_ID_FIXED" and len(tokens) >= 2:
                can_id = parse_address(tokens[1])
            elif head == "RASTER" and len(tokens) >= 2:
                raster = parse_address(tokens[1])
            elif list_number is None:
                list_number = parse_address(head)
        if name or odt_count or period_ms:
            out[tier] = A2LDaqList(
                name=name or f"{tier} DAQ",
                tier=tier,
                period_ms=period_ms or {"1ms": 1, "10ms": 10, "50ms": 50, "100ms": 100}.get(tier, 10),
                list_number=list_number,
                odt_count=odt_count,
                first_pid=first_pid,
                can_id=normalize_dto_can_id(can_id),
                raw_can_id=can_id,
                raster=raster,
            )

    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("/begin SOURCE"):
            in_source = True
            in_qp_blob = False
            source_lines = []
            qp_lines = []
            continue
        if line.startswith("/end SOURCE"):
            _finish_source()
            in_source = False
            in_qp_blob = False
            source_lines = []
            qp_lines = []
            continue
        if not in_source:
            continue
        if line.startswith("/begin QP_BLOB"):
            in_qp_blob = True
            continue
        if line.startswith("/end QP_BLOB"):
            in_qp_blob = False
            continue
        if in_qp_blob:
            qp_lines.append(line)
        else:
            source_lines.append(line)

    return out


def dtype_size(dtype: Optional[str]) -> int:
    sizes = {
        "UBYTE": 1,
        "SBYTE": 1,
        "UWORD": 2,
        "SWORD": 2,
        "ULONG": 4,
        "SLONG": 4,
        "FLOAT32_IEEE": 4,
        "FLOAT64_IEEE": 8,
    }
    return int(sizes.get(str(dtype or "").upper(), 4))


def _apply_rat_func_inv(raw_int: float, coeffs: Coeffs) -> float:
    """Convert internal (raw) value to physical using inverted RAT_FUNC.

    ASAM MCD-2MC defines COEFFS a,b,c,d,e,f as:
        INT = (a*PHYS^2 + b*PHYS + c) / (d*PHYS^2 + e*PHYS + f)

    For the common linear case (a=0, d=0, e=0):
        INT = (b*PHYS + c) / f   =>   PHYS = (f*INT - c) / b
    """
    a, b, c, d, e, f = coeffs

    if a == 0.0 and d == 0.0 and e == 0.0:
        # Linear: INT = (b*PHYS + c) / f  =>  PHYS = (f*INT - c) / b
        if b == 0.0:
            return raw_int
        return (f * raw_int - c) / b

    if d == 0.0 and a == 0.0:
        # INT = (b*PHYS + c) / (e*PHYS + f)
        # => INT*(e*PHYS + f) = b*PHYS + c
        # => PHYS*(INT*e - b) = c - INT*f
        denom = raw_int * e - b
        if abs(denom) < 1e-30:
            return raw_int
        return (c - raw_int * f) / denom

    # Full quadratic: solve a*PHYS^2 + b*PHYS + c = INT*(d*PHYS^2 + e*PHYS + f)
    # => (a - INT*d)*PHYS^2 + (b - INT*e)*PHYS + (c - INT*f) = 0
    qa = a - raw_int * d
    qb = b - raw_int * e
    qc = c - raw_int * f

    if abs(qa) < 1e-30:
        if abs(qb) < 1e-30:
            return raw_int
        return -qc / qb

    disc = qb * qb - 4.0 * qa * qc
    if disc < 0:
        return raw_int
    import math
    sqrt_disc = math.sqrt(disc)
    r1 = (-qb + sqrt_disc) / (2.0 * qa)
    r2 = (-qb - sqrt_disc) / (2.0 * qa)
    # Pick the root closest to the raw value (heuristic for ambiguous cases)
    if abs(r1 - raw_int) <= abs(r2 - raw_int):
        return r1
    return r2


_IDENTITY_COEFFS: Coeffs = (0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def decode_value(
    dtype: Optional[str],
    raw: bytes,
    byteorder: str,
    limits: Optional[tuple[float, float]] = None,
    coeffs: Optional[Coeffs] = None,
) -> float:
    """Decode raw CCP payload bytes into a physical value.

    Priority:
      1. Decode raw bytes according to ``dtype`` (signed, unsigned, float).
      2. Apply COMPU_METHOD COEFFS (RAT_FUNC inversion) if available.
      3. Fall back to legacy limits-based linear scaling when no COEFFS.
    """
    dt = str(dtype or "").upper()

    # --- Step 1: decode raw bytes to a numeric internal value ---
    if dt == "FLOAT32_IEEE":
        if len(raw) < 4:
            raw = raw.ljust(4, b"\x00")
        fmt = ">f" if byteorder == "big" else "<f"
        int_val = struct.unpack(fmt, raw[:4])[0]
        # FLOAT32 values typically use identity COEFFS; apply if non-trivial.
        if coeffs and coeffs != _IDENTITY_COEFFS:
            return _apply_rat_func_inv(int_val, coeffs)
        return float(int_val)

    if dt == "FLOAT64_IEEE":
        if len(raw) < 8:
            raw = raw.ljust(8, b"\x00")
        fmt = ">d" if byteorder == "big" else "<d"
        int_val = struct.unpack(fmt, raw[:8])[0]
        if coeffs and coeffs != _IDENTITY_COEFFS:
            return _apply_rat_func_inv(int_val, coeffs)
        return float(int_val)

    signed = dt in ("SBYTE", "SWORD", "SLONG")
    int_val = int.from_bytes(raw, byteorder=byteorder, signed=signed)

    # --- Step 2: apply COMPU_METHOD if available ---
    if coeffs and coeffs != _IDENTITY_COEFFS:
        return _apply_rat_func_inv(float(int_val), coeffs)

    # --- Step 3: legacy fallback using limits ---
    if limits and limits != (0.0, 0.0):
        lo, hi = limits
        if dt in ("UWORD", "UBYTE", "ULONG"):
            max_raw = {
                "UBYTE": 0xFF,
                "UWORD": 0xFFFF,
                "ULONG": 0xFFFFFFFF,
            }.get(dt, 0xFFFF)
            if max_raw > 0 and hi != lo:
                return lo + float(int_val) * ((hi - lo) / float(max_raw))
            if max_raw > 0:
                return float(int_val) * (hi / float(max_raw))
        elif dt in ("SWORD", "SBYTE", "SLONG"):
            max_pos = {
                "SBYTE": 0x7F,
                "SWORD": 0x7FFF,
                "SLONG": 0x7FFFFFFF,
            }.get(dt, 0x7FFF)
            if max_pos > 0 and hi != lo:
                return lo + float(int_val + max_pos + 1) * ((hi - lo) / float(2 * max_pos + 2))
            if max_pos > 0:
                return float(int_val) * (hi / float(max_pos))

    return float(int_val)
