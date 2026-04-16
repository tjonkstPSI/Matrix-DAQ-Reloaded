# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import re
from typing import Dict, Any, List, Optional

from .base import PluginStatus


_TC_RTD_BRIDGE_RE = re.compile(
    r"\b92(?:10|11|12|13|14|16|17|19|26|35|36|37)\b",
    re.IGNORECASE,
)


def _ai_subtype_from_product(product_type: str) -> str:
    """Guess AI sub-type from NI product number: 'temp' for TC/RTD/bridge, 'voltage' otherwise."""
    if not product_type:
        return ""
    return "temp" if _TC_RTD_BRIDGE_RE.search(product_type) else "voltage"


def _ai_subtype_from_channels(channels_by_key: Dict[str, List[Dict[str, Any]]]) -> str:
    """Derive AI sub-type from old config: 'temp' if ai_temp channels exist, else 'voltage'."""
    if channels_by_key.get("ai_temp"):
        return "temp"
    if channels_by_key.get("ai_voltage"):
        return "voltage"
    return ""


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


def build_device_map(inv: Dict[str, Any]) -> Dict[str, str]:
    """Build {device_name: product_type} from an inventory dict."""
    dm: Dict[str, str] = {}
    for d in inv.get("devices", []):
        name = str(d.get("name", "")).strip()
        ptype = str(d.get("product_type", "")).strip()
        if name:
            dm[name] = ptype
    return dm


def _cfg_device_names(config: Dict[str, Any]) -> set:
    """Extract unique device names from all phys strings in a config."""
    names: set = set()
    ch = config.get("channels", {}) or {}
    for key in ("ai_voltage", "ai_temp", "di", "do", "ao"):
        for c in (ch.get(key) or []):
            phys = str(c.get("phys", ""))
            if "/" in phys:
                names.add(phys.split("/", 1)[0])
    return names


def _cfg_channels_for_device(config: Dict[str, Any], device: str) -> Dict[str, List[Dict[str, Any]]]:
    """Return all config channel entries whose phys belongs to a given device."""
    prefix = device + "/"
    ch = config.get("channels", {}) or {}
    result: Dict[str, List[Dict[str, Any]]] = {}
    for key in ("ai_voltage", "ai_temp", "di", "do", "ao"):
        entries = [dict(c) for c in (ch.get(key) or []) if str(c.get("phys", "")).startswith(prefix)]
        if entries:
            result[key] = entries
    return result


def _channel_count(dev_or_channels) -> int:
    """Count total channels in a device inventory dict or a channels-by-key dict."""
    total = 0
    for k in ("ai", "di", "do", "ao", "ai_voltage", "ai_temp"):
        lst = dev_or_channels.get(k)
        if isinstance(lst, list):
            total += len(lst)
    return total


def _cap_str(has_ai: bool, has_di: bool, has_do: bool, has_ao: bool) -> str:
    """Canonical capability string for a device."""
    cats: List[str] = []
    if has_ai:
        cats.append("ai")
    if has_di or has_do:
        cats.append("digital")
    if has_ao:
        cats.append("ao")
    return "+".join(cats) if cats else "none"


def _capability_from_config_channels(channels_by_key: Dict[str, List[Dict[str, Any]]]) -> str:
    """Infer a device's capability category from its saved channel config."""
    has_ai = bool(channels_by_key.get("ai_voltage") or channels_by_key.get("ai_temp"))
    has_di = bool(channels_by_key.get("di"))
    has_do = bool(channels_by_key.get("do"))
    has_ao = bool(channels_by_key.get("ao"))
    return _cap_str(has_ai, has_di, has_do, has_ao)


def _capability_from_inventory(device: Dict[str, Any]) -> str:
    """Infer a device's capability category from its discovered inventory."""
    has_ai = bool(device.get("ai"))
    has_di = bool(device.get("di"))
    has_do = bool(device.get("do"))
    has_ao = bool(device.get("ao"))
    return _cap_str(has_ai, has_di, has_do, has_ao)


def compute_hardware_diff(
    config: Dict[str, Any],
    inv: Dict[str, Any],
) -> Dict[str, Any]:
    """Compare old config devices against new inventory devices.

    Returns a dict with keys: missing, new, unchanged, suggested_mappings.
    Chassis (devices with no I/O channels) are excluded from 'new'.
    Each missing/new entry includes a 'capability' field for type-safe matching.
    """
    inv_devs = {str(d["name"]): d for d in inv.get("devices", []) if d.get("name")}
    cfg_names = _cfg_device_names(config)
    inv_names = set(inv_devs.keys())

    device_map = config.get("device_map") or {}

    missing = []
    for name in sorted(cfg_names - inv_names):
        ptype = str(device_map.get(name, ""))
        channels = _cfg_channels_for_device(config, name)
        capability = _capability_from_config_channels(channels)
        ai_sub = _ai_subtype_from_channels(channels) if capability == "ai" else ""
        missing.append({
            "name": name,
            "product_type": ptype,
            "channels": channels,
            "capability": capability,
            "ai_subtype": ai_sub,
            "ch_count": sum(len(v) for v in channels.values() if isinstance(v, list)),
        })

    new = []
    for name in sorted(inv_names - cfg_names):
        d = inv_devs[name]
        capability = _capability_from_inventory(d)
        if capability == "none":
            continue
        entry = dict(d)
        entry["capability"] = capability
        entry["ai_subtype"] = _ai_subtype_from_product(str(d.get("product_type", ""))) if capability == "ai" else ""
        entry["ch_count"] = _channel_count(d)
        new.append(entry)

    unchanged = []
    for name in sorted(cfg_names & inv_names):
        d = inv_devs.get(name, {"name": name})
        unchanged.append(dict(d))

    suggested: List[Dict[str, str]] = []
    claimed_new: set = set()
    for m in missing:
        ptype = m["product_type"]
        cap = m["capability"]
        old_sub = m.get("ai_subtype", "")
        old_ct = int(m.get("ch_count", 0) or 0)

        candidates = [
            n for n in new
            if n["name"] not in claimed_new
            and n.get("capability") == cap
            and int(n.get("ch_count", 0) or 0) >= old_ct
        ]
        if cap == "ai" and old_sub:
            sub_match = [n for n in candidates if n.get("ai_subtype") == old_sub]
            if sub_match:
                candidates = sub_match
        if ptype:
            typed = [n for n in candidates if str(n.get("product_type", "")) == ptype]
            if typed:
                candidates = typed
        if candidates:
            pick = candidates[0]
            suggested.append({
                "old": m["name"],
                "new": pick["name"],
                "product_type": str(pick.get("product_type", "")),
            })
            claimed_new.add(pick["name"])

    return {
        "missing": missing,
        "new": new,
        "unchanged": unchanged,
        "suggested_mappings": suggested,
    }


def apply_migration(
    config: Dict[str, Any],
    inv: Dict[str, Any],
    mappings: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Rewrite config phys strings according to confirmed mappings.

    Returns a new config dict ready to be written to YAML.
    """
    mapping_old_to_new = {m["old"]: m["new"] for m in mappings}

    new_cfg = dict(config)
    ch = dict((config.get("channels") or {}))
    new_ch: Dict[str, List[Dict[str, Any]]] = {}

    inv_all_phys: Dict[str, set] = {"ai": set(), "di": set(), "do": set(), "ao": set()}
    for d in inv.get("devices", []):
        for k in ("ai", "di", "do", "ao"):
            for p in (d.get(k) or []):
                inv_all_phys[k].add(str(p))

    for key in ("ai_voltage", "ai_temp", "di", "do", "ao"):
        old_entries = list(ch.get(key) or [])
        migrated: List[Dict[str, Any]] = []
        for entry in old_entries:
            phys = str(entry.get("phys", ""))
            if "/" not in phys:
                continue
            device = phys.split("/", 1)[0]
            suffix = phys.split("/", 1)[1]
            if device in mapping_old_to_new:
                new_device = mapping_old_to_new[device]
                new_entry = dict(entry)
                new_entry["phys"] = new_device + "/" + suffix
                migrated.append(new_entry)
            elif device not in {m["old"] for m in mappings}:
                migrated.append(dict(entry))

        new_ch[key] = migrated

    inv_covered: set = set()
    for entries in new_ch.values():
        for e in entries:
            inv_covered.add(str(e.get("phys", "")))

    inv_map = {"ai": "ai_voltage", "di": "di", "do": "do", "ao": "ao"}
    for inv_key, cfg_key in inv_map.items():
        for phys in sorted(inv_all_phys[inv_key]):
            if phys not in inv_covered:
                if cfg_key == "ai_voltage":
                    new_ch.setdefault(cfg_key, []).append({
                        "phys": phys, "alias": "", "enabled": False,
                        "scaling": {"type": "none", "unit": "V"},
                    })
                elif cfg_key in ("di", "do"):
                    new_ch.setdefault(cfg_key, []).append({
                        "phys": phys, "alias": "", "enabled": False, "initial": 0,
                    })
                elif cfg_key == "ao":
                    new_ch.setdefault(cfg_key, []).append({
                        "phys": phys, "alias": "", "enabled": False,
                        "scaling": {"unit": "V"}, "range_v": {"min": 0.0, "max": 10.0},
                    })
                inv_covered.add(phys)

    new_cfg["channels"] = new_ch
    new_cfg["device_map"] = build_device_map(inv)
    return new_cfg


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
