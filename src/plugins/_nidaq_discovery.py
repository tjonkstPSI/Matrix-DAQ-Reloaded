# Author: T. Onkst | Date: 03092026

from __future__ import annotations

from typing import Dict, Any, List, Optional

from .base import PluginStatus


def nidaq_available() -> bool:
    try:
        import nidaqmx  # type: ignore
        return True
    except Exception:
        return False


def enumerate_system() -> Dict[str, Any]:
    """Return a simple inventory of devices/modules and AI/DI/DO/AO channels."""
    inv: Dict[str, Any] = {"devices": []}
    try:
        from nidaqmx.system import System  # type: ignore
    except Exception:
        return inv
    sys = System.local()
    for dev in sys.devices:
        dev_info: Dict[str, Any] = {
            "name": dev.name,
            "product_type": getattr(dev, "product_type", ""),
            "ai": [],
            "di": [],
            "do": [],
            "ao": [],
        }
        try:
            for ch in getattr(dev, "ai_physical_chans", []):
                dev_info["ai"].append(ch.name)
        except Exception:
            pass
        try:
            for ch in getattr(dev, "di_lines", []):
                dev_info["di"].append(ch.name)
        except Exception:
            pass
        try:
            for ch in getattr(dev, "do_lines", []):
                dev_info["do"].append(ch.name)
        except Exception:
            pass
        try:
            for ch in getattr(dev, "ao_physical_chans", []):
                dev_info["ao"].append(ch.name)
        except Exception:
            pass
        inv["devices"].append(dev_info)
    return inv


def inventory_matches_config(
    config: Dict[str, Any],
    inv: Dict[str, Any],
) -> bool:
    try:
        inv_ai = set([str(x) for d in inv.get("devices", []) for x in (d.get("ai") or [])])
        inv_di = set([str(x) for d in inv.get("devices", []) for x in (d.get("di") or [])])
        inv_do = set([str(x) for d in inv.get("devices", []) for x in (d.get("do") or [])])
        inv_ao = set([str(x) for d in inv.get("devices", []) for x in (d.get("ao") or [])])
        ch = config.get("channels", {}) or {}
        cfg_ai = set([str(c.get("phys")) for c in (ch.get("ai_voltage") or []) if c.get("phys")])
        cfg_ai |= set([str(c.get("phys")) for c in (ch.get("ai_temp") or []) if c.get("phys")])
        cfg_di = set([str(c.get("phys")) for c in (ch.get("di") or []) if c.get("phys")])
        cfg_do = set([str(c.get("phys")) for c in (ch.get("do") or []) if c.get("phys")])
        cfg_ao = set([str(c.get("phys")) for c in (ch.get("ao") or []) if c.get("phys")])
        return inv_ai == cfg_ai and inv_di == cfg_di and inv_do == cfg_do and inv_ao == cfg_ao
    except Exception:
        return True


def validate_watchdog_cfg(
    watchdog_cfg: Dict[str, Any],
    do_channels: List[Dict[str, Any]],
) -> PluginStatus:
    cfg = watchdog_cfg
    if not cfg:
        return PluginStatus(ok=True)
    try:
        enabled = bool(cfg.get("enabled", False))
    except Exception:
        enabled = False
    if not enabled:
        return PluginStatus(ok=True)
    mode = str(cfg.get("mode", "")).strip().lower()
    if mode not in ("driver", "digital_loopback"):
        return PluginStatus(ok=False, message="watchdog.mode must be 'driver' or 'digital_loopback'")

    def _pos_float(v: Any, name: str) -> Optional[float]:
        try:
            f = float(v)
            return f if f > 0 else None
        except Exception:
            return None

    def _pos_int(v: Any, name: str) -> Optional[int]:
        try:
            i = int(v)
            return i if i > 0 else None
        except Exception:
            return None

    if mode == "driver":
        rr = _pos_float(cfg.get("refresh_rate_hz"), "refresh_rate_hz")
        to = _pos_int(cfg.get("timeout_ms"), "timeout_ms")
        if rr is None or to is None:
            return PluginStatus(ok=False, message="watchdog.driver requires positive refresh_rate_hz and timeout_ms")
        expir = cfg.get("expir_states")
        if expir is not None and not isinstance(expir, dict):
            return PluginStatus(ok=False, message="watchdog.expir_states must be a mapping of DO alias -> state")
        if isinstance(expir, dict):
            do_aliases = {str(ch.get("alias")) for ch in do_channels if ch.get("alias")}
            for k, v in expir.items():
                if str(k) not in do_aliases:
                    return PluginStatus(ok=False, message=f"watchdog.expir_states references unknown DO alias: {k}")
                if _pos_int(int(bool(v)), "state") is None and int(v) not in (0, 1):
                    return PluginStatus(ok=False, message=f"watchdog.expir_states state must be 0 or 1 for alias: {k}")
    else:
        do_line = str(cfg.get("do_line", "")).strip()
        di_return = str(cfg.get("di_return", "")).strip()
        if not do_line or not di_return or do_line == di_return:
            return PluginStatus(ok=False, message="watchdog.digital_loopback requires distinct do_line and di_return")
        tr = _pos_float(cfg.get("toggle_rate_hz"), "toggle_rate_hz")
        vto = _pos_int(cfg.get("verify_timeout_ms"), "verify_timeout_ms")
        mt = cfg.get("miss_threshold", 3)
        try:
            mt_int = int(mt)
        except Exception:
            mt_int = 0
        if tr is None or vto is None or mt_int < 1:
            return PluginStatus(ok=False, message="watchdog.digital_loopback requires positive toggle_rate_hz, verify_timeout_ms and miss_threshold>=1")
    return PluginStatus(ok=True)
