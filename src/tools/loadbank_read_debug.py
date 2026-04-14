# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List

try:
    from pymodbus.client import ModbusTcpClient  # type: ignore
except Exception:
    try:
        from pymodbus.client.tcp import ModbusTcpClient  # type: ignore
    except Exception:
        ModbusTcpClient = None  # type: ignore


DEFAULT_METER_NAMES = ["Vab", "Vbc", "Vca", "Ia", "Ib", "Ic", "kW"]
DEFAULT_INDICATOR_NAMES = ["Control Available", "Fan On", "Normal Operation", "Load Available", "Load Bank Failure"]

PROFILES: Dict[str, Dict[str, Any]] = {
    # 1.5MW baseline interpretation from current Matrix map/VI walkthrough.
    "simplex1500": {
        "meter_start": 2945,
        "meter_count": 14,
        "meter_names": ["Vab", "Vbc", "Vca", "Ia", "Ib", "Ic", "kW"],
        "coils_start": 3520,
        "coils_count": 5,
        "indicator_names": ["Control Available", "Fan On", "Normal Operation", "Load Available", "Load Bank Failure"],
        "address_base": 1,
        "word_order": "AB",
        "write_kind": "coil_steps",
        "write_address": 3459,
        "write_steps_kw": [1, 2, 2, 5, 5, 10, 25, 50, 50, 100, 200, 500, 500],
        "write_min_kw": 0,
        "write_max_kw": 1500,
    },
    # 700kW SystemData VI: start=2689, count=8 (IA, IB, IC, W + paired registers).
    "simplex700_system": {
        "meter_start": 2689,
        "meter_count": 8,
        "meter_names": ["IA", "IB", "IC", "W"],
        "coils_start": 3360,
        "coils_count": 2,
        "indicator_names": ["Failure on System", "Unit Heartbeat"],
        "address_base": 1,
        "word_order": "BA",
        "write_kind": "bcd_double",
        "write_address": 2625,
        "write_word_order": "AB",
        "write_min_kw": 0,
        "write_max_kw": 700,
    },
    # 700kW UnitData VI: start=2945, count=14 (Vab..kW), same status bits block.
    "simplex700_unit": {
        "meter_start": 2945,
        "meter_count": 14,
        "meter_names": ["Vab", "Vbc", "Vca", "Ia", "Ib", "Ic", "kW"],
        "coils_start": 3360,
        "coils_count": 2,
        "indicator_names": ["Failure on System", "Unit Heartbeat"],
        "address_base": 1,
        "word_order": "BA",
        "write_kind": "bcd_double",
        "write_address": 2625,
        "write_word_order": "AB",
        "write_min_kw": 0,
        "write_max_kw": 700,
    },
}


@dataclass
class Config:
    host: str
    port: int
    unit_id: int
    timeout_s: float
    meter_start: int
    meter_count: int
    meter_names: List[str]
    coils_start: int
    coils_count: int
    indicator_names: List[str]
    address_base: int
    word_order: str
    show_both_word_orders: bool
    interval_s: float
    iterations: int
    json_out: bool


def _effective_addr(addr: int, base: int) -> int:
    return int(addr) - int(base)


def _is_error(resp: Any) -> bool:
    if resp is None:
        return True
    try:
        fn = getattr(resp, "isError", None)
        if callable(fn):
            return bool(fn())
    except Exception:
        pass
    return False


def _read_holding(client: Any, addr: int, count: int, unit_id: int) -> Any:
    try:
        return client.read_holding_registers(addr, count=count, slave=unit_id)
    except TypeError:
        try:
            return client.read_holding_registers(addr, count=count, unit=unit_id)
        except TypeError:
            return client.read_holding_registers(addr, count=count, device_id=unit_id)


def _read_coils(client: Any, addr: int, count: int, unit_id: int) -> Any:
    try:
        return client.read_coils(addr, count=count, slave=unit_id)
    except TypeError:
        try:
            return client.read_coils(addr, count=count, unit=unit_id)
        except TypeError:
            return client.read_coils(addr, count=count, device_id=unit_id)


def _decode_float32(w0: int, w1: int, order: str) -> float:
    if str(order).upper() == "BA":
        w0, w1 = w1, w0
    b = struct.pack(">HH", int(w0) & 0xFFFF, int(w1) & 0xFFFF)
    return float(struct.unpack(">f", b)[0])


def _decode_metering(regs: List[int], order: str, meter_names: List[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if len(regs) < 2:
        return out
    n_pairs = min(len(meter_names), len(regs) // 2)
    for i in range(n_pairs):
        name = meter_names[i]
        j = i * 2
        out[name] = _decode_float32(regs[j], regs[j + 1], order)
    return out


def _write_registers(client: Any, addr: int, values: List[int], unit_id: int) -> Any:
    try:
        return client.write_registers(addr, values=values, slave=unit_id)
    except TypeError:
        try:
            return client.write_registers(addr, values=values, unit=unit_id)
        except TypeError:
            return client.write_registers(addr, values=values, device_id=unit_id)


def _write_coils(client: Any, addr: int, values: List[bool], unit_id: int) -> Any:
    try:
        return client.write_coils(addr, values=values, slave=unit_id)
    except TypeError:
        try:
            return client.write_coils(addr, values=values, unit=unit_id)
        except TypeError:
            return client.write_coils(addr, values=values, device_id=unit_id)


def _encode_bcd_double(value: int, order: str = "AB") -> List[int]:
    digits = f"{max(0, min(99_999_999, int(value))):08d}"
    b = bytes(
        [
            (int(digits[0]) << 4) | int(digits[1]),
            (int(digits[2]) << 4) | int(digits[3]),
            (int(digits[4]) << 4) | int(digits[5]),
            (int(digits[6]) << 4) | int(digits[7]),
        ]
    )
    regs = [((b[0] << 8) | b[1]), ((b[2] << 8) | b[3])]
    if str(order).upper() == "BA":
        regs = [regs[1], regs[0]]
    return regs


def _compose_step_vector(target_kw: int, steps_kw: List[int]) -> tuple[List[bool], int]:
    vec = [False] * len(steps_kw)
    rem = int(target_kw)
    for i in range(len(steps_kw) - 1, -1, -1):
        step = int(steps_kw[i])
        if step <= 0:
            continue
        if rem >= step:
            vec[i] = True
            rem -= step
        if rem <= 0:
            break
    return vec, rem


def write_load_command(
    client: Any,
    unit_id: int,
    address_base: int,
    write_kind: str,
    write_address: int,
    load_kw: float,
    write_min_kw: float,
    write_max_kw: float,
    write_word_order: str,
    write_steps_kw: List[int],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": False, "kind": write_kind, "target_kw": float(load_kw)}
    clamped = int(round(max(write_min_kw, min(write_max_kw, float(load_kw)))))
    out["clamped_kw"] = clamped
    wire_addr = _effective_addr(int(write_address), int(address_base))
    out["wire_addr"] = wire_addr
    try:
        if write_kind == "bcd_double":
            regs = _encode_bcd_double(clamped, order=write_word_order)
            wr = _write_registers(client, wire_addr, regs, unit_id)
            out["registers"] = regs
            out["ok"] = not _is_error(wr)
            return out
        if write_kind == "coil_steps":
            vec, rem = _compose_step_vector(clamped, write_steps_kw)
            wr = _write_coils(client, wire_addr, vec, unit_id)
            out["coils"] = vec
            out["remainder_kw"] = rem
            out["ok"] = not _is_error(wr)
            return out
        out["error"] = f"unsupported write kind: {write_kind}"
        return out
    except Exception as e:
        out["error"] = str(e)
        return out


def read_once(client: Any, cfg: Config) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ts": time.time(),
        "metering": {},
        "indicators": {},
        "raw": {},
        "errors": [],
    }

    meter_addr = _effective_addr(cfg.meter_start, cfg.address_base)
    coils_addr = _effective_addr(cfg.coils_start, cfg.address_base)
    result["raw"]["meter_addr_wire"] = meter_addr
    result["raw"]["coils_addr_wire"] = coils_addr

    rr = _read_holding(client, meter_addr, cfg.meter_count, cfg.unit_id)
    if _is_error(rr):
        result["errors"].append("holding_read_failed")
    else:
        regs = list(getattr(rr, "registers", []) or [])
        result["raw"]["meter_registers"] = regs
        result["metering"][cfg.word_order.upper()] = _decode_metering(regs, cfg.word_order, cfg.meter_names)
        if cfg.show_both_word_orders:
            alt = "BA" if cfg.word_order.upper() == "AB" else "AB"
            result["metering"][alt] = _decode_metering(regs, alt, cfg.meter_names)

    rc = _read_coils(client, coils_addr, cfg.coils_count, cfg.unit_id)
    if _is_error(rc):
        result["errors"].append("coils_read_failed")
    else:
        bits = [bool(x) for x in list(getattr(rc, "bits", []) or [])[: cfg.coils_count]]
        result["raw"]["coils"] = bits
        for i, name in enumerate(cfg.indicator_names):
            result["indicators"][name] = bits[i] if i < len(bits) else False
        # Legacy 1.5MW helper aliases.
        if len(cfg.indicator_names) >= 5:
            result["indicators"]["Power (from idx 1)"] = bits[1] if len(bits) > 1 else False
            result["indicators"]["Error (from idx 4)"] = bits[4] if len(bits) > 4 else False

    return result


def print_human(r: Dict[str, Any], cfg: Config) -> None:
    ts = time.strftime("%H:%M:%S", time.localtime(float(r.get("ts", time.time()))))
    print(f"\n[{ts}] host={cfg.host}:{cfg.port} unit={cfg.unit_id}")
    print(f"wire_addrs: meter={r['raw'].get('meter_addr_wire')} coils={r['raw'].get('coils_addr_wire')} (base={cfg.address_base})")
    if r.get("errors"):
        print("errors:", ", ".join(r["errors"]))

    regs = r.get("raw", {}).get("meter_registers", [])
    if regs:
        print("meter_raw:", regs)
    met = r.get("metering", {})
    for order, vals in met.items():
        print(f"metering[{order}]:", ", ".join(f"{k}={vals.get(k, float('nan')):.6g}" for k in cfg.meter_names))

    indicators = r.get("indicators", {})
    if indicators:
        print("indicators:", ", ".join(f"{k}={'ON' if bool(v) else 'OFF'}" for k, v in indicators.items()))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Standalone LoadBank read debug with selectable loadbank profiles.")
    p.add_argument("--host", required=True, help="Load bank IP")
    p.add_argument("--port", type=int, default=502, help="Modbus TCP port")
    p.add_argument("--unit-id", type=int, default=1, help="Modbus unit/slave id")
    p.add_argument("--timeout-s", type=float, default=1.5, help="Socket timeout")
    p.add_argument(
        "--profile",
        type=str,
        default="simplex1500",
        choices=["simplex1500", "simplex700_system", "simplex700_unit", "custom"],
        help="Address/count preset profile",
    )
    p.add_argument("--meter-start", type=int, default=None, help="Metering block start address")
    p.add_argument("--meter-count", type=int, default=None, help="Metering register count")
    p.add_argument("--coils-start", type=int, default=None, help="Indicator coils start address")
    p.add_argument("--coils-count", type=int, default=None, help="Indicator coil count")
    p.add_argument("--address-base", type=int, default=None, choices=[0, 1], help="Subtract base from configured addresses")
    p.add_argument("--word-order", default=None, choices=["AB", "BA"], help="Float word order")
    p.add_argument("--show-both-word-orders", action="store_true", help="Also decode with the alternate order")
    p.add_argument("--interval-s", type=float, default=0.5, help="Poll interval seconds")
    p.add_argument("--iterations", type=int, default=0, help="0 means run forever")
    p.add_argument("--json", action="store_true", help="Print JSON lines instead of human text")
    p.add_argument("--write-load-kw", type=float, default=None, help="Optional: issue load write command (kW)")
    p.add_argument("--write-repeat", action="store_true", help="Optional: repeat load write every loop")
    p.add_argument(
        "--write-kind",
        type=str,
        default="auto",
        choices=["auto", "bcd_double", "coil_steps"],
        help="Write command type; auto uses profile default",
    )
    p.add_argument("--write-start", type=int, default=None, help="Override write start address")
    p.add_argument("--write-word-order", type=str, default=None, choices=["AB", "BA"], help="BCD word order")
    return p


def main(argv: List[str] | None = None) -> int:
    if ModbusTcpClient is None:
        print("[ERROR] pymodbus is not available in this Python environment.")
        return 2

    a = build_parser().parse_args(argv)
    profile_name = str(a.profile or "simplex1500")
    base = dict(PROFILES.get(profile_name, PROFILES["simplex1500"])) if profile_name != "custom" else dict(PROFILES["simplex1500"])

    meter_start = int(a.meter_start if a.meter_start is not None else base["meter_start"])
    meter_count = int(a.meter_count if a.meter_count is not None else base["meter_count"])
    meter_names = list(base.get("meter_names", DEFAULT_METER_NAMES))
    coils_start = int(a.coils_start if a.coils_start is not None else base["coils_start"])
    coils_count = int(a.coils_count if a.coils_count is not None else base["coils_count"])
    indicator_names = list(base.get("indicator_names", DEFAULT_INDICATOR_NAMES))
    address_base = int(a.address_base if a.address_base is not None else base["address_base"])
    word_order = str(a.word_order if a.word_order is not None else base["word_order"]).upper()
    write_load_kw = a.write_load_kw
    write_kind = str(base.get("write_kind", "bcd_double"))
    if str(a.write_kind).lower() != "auto":
        write_kind = str(a.write_kind).lower()
    write_address = int(a.write_start if a.write_start is not None else base.get("write_address", 0))
    write_word_order = str(a.write_word_order if a.write_word_order is not None else base.get("write_word_order", "AB")).upper()
    write_min_kw = float(base.get("write_min_kw", 0.0))
    write_max_kw = float(base.get("write_max_kw", 1000.0))
    write_steps_kw = [int(x) for x in (base.get("write_steps_kw", []) or [])]

    cfg = Config(
        host=str(a.host),
        port=int(a.port),
        unit_id=int(a.unit_id),
        timeout_s=float(a.timeout_s),
        meter_start=meter_start,
        meter_count=meter_count,
        meter_names=meter_names,
        coils_start=coils_start,
        coils_count=coils_count,
        indicator_names=indicator_names,
        address_base=address_base,
        word_order=word_order,
        show_both_word_orders=bool(a.show_both_word_orders),
        interval_s=max(0.05, float(a.interval_s)),
        iterations=int(a.iterations),
        json_out=bool(a.json),
    )

    client = ModbusTcpClient(host=cfg.host, port=cfg.port, timeout=cfg.timeout_s)  # type: ignore
    if not client.connect():
        print(f"[ERROR] failed to connect to {cfg.host}:{cfg.port}")
        return 3

    print(f"[INFO] connected to {cfg.host}:{cfg.port} unit={cfg.unit_id} profile={profile_name}")
    if write_load_kw is not None:
        mode = "repeat" if bool(a.write_repeat) else "one-shot"
        print(f"[INFO] write enabled: kind={write_kind} load_kw={write_load_kw} mode={mode}")
    try:
        i = 0
        wrote_once = False
        while True:
            i += 1
            if write_load_kw is not None and (bool(a.write_repeat) or not wrote_once):
                wr = write_load_command(
                    client=client,
                    unit_id=cfg.unit_id,
                    address_base=cfg.address_base,
                    write_kind=write_kind,
                    write_address=write_address,
                    load_kw=float(write_load_kw),
                    write_min_kw=write_min_kw,
                    write_max_kw=write_max_kw,
                    write_word_order=write_word_order,
                    write_steps_kw=write_steps_kw,
                )
                wrote_once = True
                if cfg.json_out:
                    print(json.dumps({"type": "write", "result": wr}))
                else:
                    if wr.get("ok"):
                        print(f"[WRITE] ok kind={wr.get('kind')} target={wr.get('target_kw')} clamped={wr.get('clamped_kw')} wire_addr={wr.get('wire_addr')}")
                    else:
                        print(f"[WRITE] failed kind={wr.get('kind')} error={wr.get('error')}")

            r = read_once(client, cfg)
            if cfg.json_out:
                print(json.dumps(r))
            else:
                print_human(r, cfg)

            if cfg.iterations > 0 and i >= cfg.iterations:
                break
            time.sleep(cfg.interval_s)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            client.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

