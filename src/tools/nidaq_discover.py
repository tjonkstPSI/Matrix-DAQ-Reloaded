# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, List


def discover_system() -> Dict[str, Any]:
    devices: List[Dict[str, Any]] = []
    try:
        from nidaqmx.system import System  # type: ignore
    except Exception:
        return {"devices": devices}
    sys = System.local()
    for dev in sys.devices:
        info: Dict[str, Any] = {
            "name": dev.name,
            "product_type": getattr(dev, "product_type", ""),
            "ai": [getattr(ch, "name", "") for ch in getattr(dev, "ai_physical_chans", [])],
            "di": [getattr(ch, "name", "") for ch in getattr(dev, "di_lines", [])],
            "do": [getattr(ch, "name", "") for ch in getattr(dev, "do_lines", [])],
            "ao": [getattr(ch, "name", "") for ch in getattr(dev, "ao_physical_chans", [])],
        }
        devices.append(info)
    return {"devices": devices}


def generate_yaml_template(inv: Dict[str, Any]) -> str:
    import yaml  # type: ignore

    def suggest_alias(phys: str) -> str:
        base = phys.replace("/", "_")
        return base

    def model_code(product_type: str) -> str:
        import re
        m = re.search(r"(\d{4})", product_type)
        return m.group(1) if m else ""

    # Module family maps (not exhaustive; extend as needed)
    TC_MODELS = {"9211", "9212", "9213", "9214"}
    RTD_MODELS = {"9216", "9217", "9226"}
    VOLT_AI_MODELS = {"9201", "9205", "9206", "9215", "9220", "9222", "9239"}
    UNIVERSAL_MODELS = {"9219"}
    AO_VOLT_MODELS = {"9263", "9264", "9269"}
    AO_CURR_MODELS = {"9265"}

    tmpl: Dict[str, Any] = {
        "mode": "real",
        "recording_rate_hz": 10,
        "decimation": {"filter": "IIR_Butterworth", "cutoff_hz": "auto"},
        "channels": {
            "ai_voltage": [],
            "ai_temp": [],
            "di": [],
            "do": [],
            "ao": [],
        },
    }

    for dev in inv.get("devices", []) or []:
        # AI candidates
        ptype = str(dev.get("product_type") or "")
        code = model_code(ptype)
        for phys in dev.get("ai", []) or []:
            alias = suggest_alias(phys)
            if code in TC_MODELS:
                tmpl["channels"]["ai_temp"].append({
                    "phys": phys,
                    "alias": alias,
                    "enabled": False,
                    "sensor": {"type": "TC", "subtype": "K"},
                    "unit": "C",
                })
            elif code in RTD_MODELS:
                tmpl["channels"]["ai_temp"].append({
                    "phys": phys,
                    "alias": alias,
                    "enabled": False,
                    "sensor": {"type": "RTD", "subtype": "PT100", "wires": 3},
                    "unit": "C",
                })
            elif code in UNIVERSAL_MODELS:
                tmpl["channels"]["ai_voltage"].append({
                    "phys": phys,
                    "alias": alias,
                    "enabled": False,
                    "range_v": {"min": 0, "max": 10},
                    "scaling": {"m": 1.0, "b": 0.0, "unit": "V"},
                    "meas_hint": ["TC", "RTD", "Voltage"],
                })
            else:
                # Default to voltage AI
                tmpl["channels"]["ai_voltage"].append({
                    "phys": phys,
                    "alias": alias,
                    "enabled": False,
                    "range_v": {"min": 0, "max": 10},
                    "scaling": {"m": 1.0, "b": 0.0, "unit": "V"},
                })
        # DI/DO
        for phys in dev.get("di", []) or []:
            alias = suggest_alias(phys)
            tmpl["channels"]["di"].append({"phys": phys, "alias": alias, "enabled": False, "initial": 0})
        for phys in dev.get("do", []) or []:
            alias = suggest_alias(phys)
            tmpl["channels"]["do"].append({"phys": phys, "alias": alias, "enabled": False, "initial": 0})
        for phys in dev.get("ao", []) or []:
            alias = suggest_alias(phys)
            if code in AO_CURR_MODELS:
                tmpl["channels"]["ao"].append({
                    "phys": phys,
                    "alias": alias,
                    "enabled": False,
                    "io_type": "current",
                    "range_mA": {"min": 0, "max": 20},
                    "scaling": {"m": 1.0, "b": 0.0, "unit": "mA"},
                })
            else:
                # Default to voltage AO
                tmpl["channels"]["ao"].append({
                    "phys": phys,
                    "alias": alias,
                    "enabled": False,
                    "io_type": "voltage",
                    "range_v": {"min": 0, "max": 10},
                    "scaling": {"m": 1.0, "b": 0.0, "unit": "V"},
                })

    return yaml.safe_dump(tmpl, sort_keys=False)


def main() -> int:
    inv = discover_system()
    yaml_text = generate_yaml_template(inv)
    # Write to configs/ni_daq.generated.yaml
    project_root = Path(__file__).resolve().parents[2]
    out_path = project_root / "configs" / "ni_daq.generated.yaml"
    out_path.write_text(yaml_text, encoding="utf-8")
    print(f"[INFO] Wrote NI DAQ template: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


