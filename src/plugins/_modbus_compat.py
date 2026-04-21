# Author: T. Onkst | Date: 04202026
"""
Pymodbus version compatibility shim.

pymodbus renamed the unit/slave identifier parameter across major versions:
  3.0-3.2  : unit=
  3.3-3.9  : slave=   (unit= raises TypeError in 3.1+)
  3.10+    : device_id=  (slave= removed)

This module detects the installed version once at import time and provides
a helper that returns the correct kwarg dict for any read/write call.
"""
from __future__ import annotations

_PARAM_NAME: str = "slave"

try:
    import pymodbus  # type: ignore
    _ver = tuple(int(x) for x in pymodbus.__version__.split(".")[:2])
    if _ver >= (3, 10):
        _PARAM_NAME = "device_id"
    elif _ver >= (3, 3):
        _PARAM_NAME = "slave"
    else:
        _PARAM_NAME = "unit"
except Exception:
    _PARAM_NAME = "slave"


def uid_kwargs(unit_id: int) -> dict:
    """Return ``{correct_param: unit_id}`` for the installed pymodbus."""
    return {_PARAM_NAME: unit_id}
