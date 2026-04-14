# Author: T. Onkst | Date: 03092026

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class A2LChannel:
    name: str
    address: Optional[int]
    data_type: Optional[str]
    limits: Optional[tuple[float, float]]
    unit: str = ""


def parse_address(token: str) -> Optional[int]:
    try:
        if token.startswith(("0x", "0X")):
            return int(token, 16)
        return int(token, 10)
    except Exception:
        return None


def parse_a2l(path: Path) -> Dict[str, A2LChannel]:
    channels: Dict[str, A2LChannel] = {}
    if not path.exists():
        return channels
    data_types = {"UBYTE", "SBYTE", "UWORD", "SWORD", "ULONG", "SLONG", "FLOAT32_IEEE", "FLOAT64_IEEE"}
    compu_units: Dict[str, str] = {}
    in_compu = False
    compu_name: Optional[str] = None
    rat_mode = False
    rat_q_count = 0

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

    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if line.startswith("/begin COMPU_METHOD"):
            parts = line.split()
            compu_name = parts[2] if len(parts) > 2 else None
            in_compu = True
            rat_mode = False
            rat_q_count = 0
            continue
        if line.startswith("/end COMPU_METHOD"):
            in_compu = False
            compu_name = None
            rat_mode = False
            rat_q_count = 0
            continue
        if not in_compu or not compu_name:
            continue
        if line.startswith("RAT_FUNC"):
            rat_mode = True
            rat_q_count = 0
            continue
        if not rat_mode:
            continue
        quoted = _extract_quoted(line)
        if not quoted:
            continue
        for q in quoted:
            rat_q_count += 1
            if rat_q_count == 2:
                compu_units[str(compu_name)] = str(q).strip()
                rat_mode = False
                break

    in_block = False
    cur_name: Optional[str] = None
    cur_addr: Optional[int] = None
    cur_type: Optional[str] = None
    cur_compu_ref: Optional[str] = None
    cur_limits: Optional[tuple[float, float]] = None
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if line.startswith("/begin MEASUREMENT") or line.startswith("/begin CHARACTERISTIC"):
            parts = line.split()
            cur_name = parts[2] if len(parts) > 2 else None
            cur_addr = None
            cur_type = None
            cur_compu_ref = None
            cur_limits = None
            in_block = True
            continue
        if line.startswith("/end MEASUREMENT") or line.startswith("/end CHARACTERISTIC"):
            if in_block and cur_name:
                unit = str(compu_units.get(str(cur_compu_ref or ""), "")).strip()
                channels[cur_name] = A2LChannel(
                    name=cur_name,
                    address=cur_addr,
                    data_type=cur_type,
                    limits=cur_limits,
                    unit=unit,
                )
            in_block = False
            cur_name = None
            continue
        if not in_block or cur_name is None:
            continue
        token = line.split()[0] if line else ""
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
            parts = line.split()
            if len(parts) >= 2:
                cur_addr = parse_address(parts[1])
            continue
        if line and line[0].isdigit():
            parts = line.split()
            if len(parts) >= 2:
                try:
                    candidate = (float(parts[0]), float(parts[1]))
                    if cur_limits is None or cur_limits == (0.0, 0.0):
                        cur_limits = candidate
                except Exception:
                    pass
    return channels


def dtype_size(dtype: Optional[str]) -> int:
    sizes = {
        "UBYTE": 1,
        "SBYTE": 1,
        "UWORD": 2,
        "SWORD": 2,
        "ULONG": 4,
        "SLONG": 4,
        "FLOAT32_IEEE": 4,
    }
    return int(sizes.get(str(dtype or "").upper(), 4))


def decode_value(dtype: Optional[str], raw: bytes, byteorder: str, limits: Optional[tuple[float, float]]) -> float:
    dt = str(dtype or "").upper()
    if dt == "SWORD":
        v = int.from_bytes(raw, byteorder=byteorder, signed=True)
        if limits:
            return float(v) * (float(limits[1]) / 0x7FFF)
        return float(v)
    v = int.from_bytes(raw, byteorder=byteorder, signed=False)
    if dt == "UWORD" and limits:
        return float(v) * (float(limits[1]) / 0xFFFF)
    return float(v)
