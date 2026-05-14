# Author: T. Onkst | Date: 05142026
from __future__ import annotations

import re
from typing import Any, List


_CAN_NAME_RE = re.compile(r"\bCAN\d+\b", re.IGNORECASE)


def _natural_channel_key(name: str) -> tuple:
    parts = re.split(r"(\d+)", str(name).upper())
    return tuple(int(p) if p.isdigit() else p for p in parts)


def _candidate_name(candidate: Any) -> str:
    if isinstance(candidate, str):
        return candidate.strip()
    if isinstance(candidate, dict):
        for key in ("channel", "interface", "name", "interface_name", "canonical_name"):
            value = candidate.get(key)
            if value:
                return str(value).strip()
        return " ".join(str(v) for v in candidate.values() if v is not None).strip()
    for attr in ("channel", "interface", "name", "interface_name", "canonical_name"):
        try:
            value = getattr(candidate, attr)
            if value:
                return str(value).strip()
        except Exception:
            pass
    return str(candidate).strip()


def _can_names_from_candidates(candidates: Any) -> List[str]:
    names: List[str] = []
    if candidates is None:
        return names
    if isinstance(candidates, str):
        iterable = [candidates]
    else:
        try:
            iterable = list(candidates)
        except Exception:
            iterable = [candidates]
    for candidate in iterable:
        name = _candidate_name(candidate)
        match = _CAN_NAME_RE.search(name)
        if match:
            names.append(match.group(0).upper())
    return names


def discover_can_channels() -> List[str]:
    """Return NI-XNET CAN interface names, or [] when discovery is unavailable."""
    found: List[str] = []

    def _add(candidates: Any) -> None:
        for name in _can_names_from_candidates(candidates):
            if name not in found:
                found.append(name)

    try:
        import can  # type: ignore
        detect = getattr(can, "detect_available_configs", None)
        if callable(detect):
            try:
                _add(detect(interfaces=["nixnet"]))
            except TypeError:
                try:
                    _add(detect(["nixnet"]))
                except TypeError:
                    _add(detect())
    except Exception:
        pass

    try:
        import nixnet  # type: ignore
    except Exception:
        return sorted(found, key=_natural_channel_key)

    try:
        system_mod = getattr(nixnet, "system", None)
        system_cls = getattr(system_mod, "System", None) if system_mod is not None else None
        if system_cls is None:
            from nixnet.system import System as system_cls  # type: ignore
        system = system_cls.local() if system_cls is not None else None
    except Exception:
        system = None

    if system is not None:
        for attr in (
            "intf_refs_can",
            "interface_refs_can",
            "interfaces_can",
            "intf_refs",
            "interface_refs",
            "interfaces",
        ):
            try:
                value = getattr(system, attr)
                _add(value() if callable(value) else value)
            except Exception:
                continue
        try:
            constants = getattr(nixnet, "constants", None)
            can_type = getattr(getattr(constants, "InterfaceProtocol", None), "CAN", None)
            method = getattr(system, "intf_refs_for_type", None)
            if callable(method) and can_type is not None:
                _add(method(can_type))
        except Exception:
            pass

    return sorted(found, key=_natural_channel_key)
