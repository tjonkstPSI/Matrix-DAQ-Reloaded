# Author: T. Onkst | Date: 04282026
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - runtime tool dependency check.
    yaml = None  # type: ignore

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.plugins._ccp_a2l import A2LChannel, A2LDaqList, decode_value, dtype_size, parse_a2l, parse_a2l_daq_lists
from src.plugins._ccp_protocol import CanFrame, CcpProto, NixnetSession, compute_key_from_seed_algo


DEFAULT_CHANNELS = ["AAT", "IAT", "VBat", "Vsw"]
DTO_PAYLOAD_BYTES = 7
_CCP_NOTIFICATION_CODES = {0x30, 0x31, 0x32, 0x33}
_CCP_NOTIFICATION_NAMES = {
    0x30: "cold_start_request",
    0x31: "cal_init_request",
    0x32: "daq_init_request",
    0x33: "code_update_request",
}


@dataclass
class ProbeEntry:
    name: str
    address: int
    extension: int
    size: int
    dtype: Optional[str]
    limits: Optional[tuple[float, float]]
    coeffs: Any
    odt: int
    offset: int


def _parse_int(value: Any, default: int = 0) -> int:
    if value is None:
        return int(default)
    if isinstance(value, int):
        return int(value)
    text = str(value).strip()
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text, 10)
    except Exception:
        return int(default)


def _load_config(path: Path) -> Dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required to read configs/ccp.yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _first_device(cfg: Dict[str, Any]) -> Dict[str, Any]:
    devices = cfg.get("devices")
    if isinstance(devices, list) and devices:
        for dev in devices:
            if isinstance(dev, dict):
                return dev
    return {
        "name": "CCP",
        "role": "primary",
        "session": dict(cfg.get("session") or {}),
        "security": dict(cfg.get("security") or {}),
        "a2l": dict(cfg.get("a2l") or {}),
        "measurements": dict(cfg.get("measurements") or {}),
    }


def _build_frame(proto: CcpProto, payload: bytes) -> CanFrame:
    return CanFrame(arbitration_id=proto.tx_id, data=payload.ljust(8, b"\x00")[:8], is_extended=proto.is_extended)


def _command_ctr(proto: CcpProto) -> int:
    return proto._next_ctr()


def _resolve_access_key(sec_cfg: Dict[str, Any], top_security: Optional[Dict[str, Any]] = None) -> str:
    """Resolve access_key from device security, env vars, or top-level security (mirrors production plugin)."""
    raw = str(sec_cfg.get("access_key") or "").strip()
    if raw:
        return raw
    for env_name in ("CCP_ACCESS_KEY", "ccp_access_key", "CCP_ACCESSKEY", "CCP_KEY"):
        v = os.environ.get(env_name, "").strip()
        if v:
            return v
    if top_security:
        return str(top_security.get("access_key") or "").strip()
    return ""


def _crm_ok(data: bytes, ctr: int) -> Tuple[bool, int]:
    d = data.ljust(8, b"\x00")
    if d[0] != 0xFF:
        return False, -1
    if d[1] == 0x00 and d[2] == ctr:
        return True, 0
    if d[2] == 0x00 and d[1] == ctr:
        return True, 0
    if d[2] == ctr:
        rc = int(d[1])
        if rc in _CCP_NOTIFICATION_CODES:
            return True, rc
        return False, rc
    if d[1] == ctr:
        rc = int(d[2])
        if rc in _CCP_NOTIFICATION_CODES:
            return True, rc
        return False, rc
    return False, -1


def _send_wait_crm(
    session: NixnetSession,
    frame: CanFrame,
    ctr: int,
    rx_id: int,
    label: str,
    timeout_s: float,
) -> bytes:
    session.send(frame)
    deadline = time.time() + max(0.01, float(timeout_s))
    last_rc = -1
    while time.time() < deadline:
        for fr in session.recv(timeout_s=0.01, only_id=rx_id):
            data = fr.data.ljust(8, b"\x00")
            ok, rc = _crm_ok(data, ctr)
            if ok:
                if rc in _CCP_NOTIFICATION_CODES:
                    note = _CCP_NOTIFICATION_NAMES.get(rc, "notification")
                    print(f"[INFO] {label}: ACK with notification rc=0x{rc:02X} ({note})")
                return data
            if rc >= 0:
                last_rc = rc
    raise RuntimeError(f"{label} failed or timed out (last_rc={last_rc})")


def _build_get_daq_size(proto: CcpProto, daq_list: int, dto_id: int) -> Tuple[CanFrame, int]:
    ctr = _command_ctr(proto)
    payload = bytes([0x14, ctr & 0xFF, 0x00, daq_list & 0xFF]) + int(dto_id).to_bytes(4, "little", signed=False)
    return _build_frame(proto, payload), ctr


def _build_set_daq_ptr(proto: CcpProto, daq_list: int, odt: int, element: int) -> Tuple[CanFrame, int]:
    ctr = _command_ctr(proto)
    payload = bytes([0x15, ctr & 0xFF, daq_list & 0xFF, odt & 0xFF, element & 0xFF, 0x00, 0x00, 0x00])
    return _build_frame(proto, payload), ctr


def _build_write_daq(proto: CcpProto, size: int, extension: int, address: int, byteorder: str) -> Tuple[CanFrame, int]:
    ctr = _command_ctr(proto)
    payload = bytes([0x16, ctr & 0xFF, size & 0xFF, extension & 0xFF]) + int(address).to_bytes(4, byteorder, signed=False)
    return _build_frame(proto, payload), ctr


def _build_start_stop(
    proto: CcpProto,
    mode: int,
    daq_list: int,
    last_odt: int,
    event_channel: int,
    prescaler: int,
) -> Tuple[CanFrame, int]:
    ctr = _command_ctr(proto)
    payload = bytes(
        [
            0x06,
            ctr & 0xFF,
            mode & 0xFF,
            daq_list & 0xFF,
            last_odt & 0xFF,
            event_channel & 0xFF,
            prescaler & 0xFF,
            0x00,
        ]
    )
    return _build_frame(proto, payload), ctr


def _build_start_stop_all(proto: CcpProto, mode: int) -> Tuple[CanFrame, int]:
    ctr = _command_ctr(proto)
    payload = bytes([0x08, ctr & 0xFF, mode & 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
    return _build_frame(proto, payload), ctr


def _build_set_mta(proto: CcpProto, address: int, extension: int, byteorder: str) -> Tuple[CanFrame, int]:
    ctr = _command_ctr(proto)
    addr_bytes = int(address).to_bytes(4, byteorder=byteorder, signed=False)
    payload = bytes([0x02, ctr & 0xFF, 0x00, extension & 0xFF]) + addr_bytes
    return _build_frame(proto, payload), ctr


def _build_dnload(proto: CcpProto, size: int, data_bytes: bytes) -> Tuple[CanFrame, int]:
    ctr = _command_ctr(proto)
    payload = bytes([0x03, ctr & 0xFF, size & 0xFF]) + data_bytes[:5].ljust(5, b"\x00")
    return _build_frame(proto, payload), ctr


def _build_set_s_status(proto: CcpProto, status: int) -> Tuple[CanFrame, int]:
    ctr = _command_ctr(proto)
    payload = bytes([0x0C, ctr & 0xFF, status & 0xFF]).ljust(8, b"\x00")
    return _build_frame(proto, payload), ctr


def _pack_entries(
    names: Iterable[str],
    parsed: Dict[str, A2LChannel],
    measurement_cfg: Dict[str, Dict[str, Any]],
    addr_ext_high: bool,
) -> List[ProbeEntry]:
    entries: List[ProbeEntry] = []
    odt = 0
    offset = 0
    for name in names:
        item = measurement_cfg.get(name, {})
        ch = parsed.get(name)
        address = item.get("address")
        if address is None and ch is not None:
            address = ch.address
        if address is None:
            print(f"[WARN] Skipping {name}: no address in config or A2L")
            continue
        extension = int(item.get("address_extension", 0) or 0)
        address = int(address)
        if addr_ext_high:
            extension = (address >> 24) & 0xFF
            address &= 0x00FFFFFF
        dtype = str(item.get("data_type") or (ch.data_type if ch else "") or "").upper() or None
        size = max(1, min(7, int(item.get("size") or dtype_size(dtype))))
        if offset + size > DTO_PAYLOAD_BYTES:
            odt += 1
            offset = 0
        entries.append(
            ProbeEntry(
                name=name,
                address=address,
                extension=extension,
                size=size,
                dtype=dtype,
                limits=ch.limits if ch else None,
                coeffs=ch.coeffs if ch else None,
                odt=odt,
                offset=offset,
            )
        )
        offset += size
    return entries


def _measurement_cfg_by_name(device: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    meas = device.get("measurements") or {}
    out: Dict[str, Dict[str, Any]] = {}
    for item in meas.get("list", []) or []:
        if isinstance(item, dict) and item.get("name"):
            out[str(item.get("name"))] = dict(item)
    return out


def _connect_unlock(
    session: NixnetSession,
    proto: CcpProto,
    session_cfg: Dict[str, Any],
    sec_cfg: Dict[str, Any],
    rx_id: int,
    timeout_s: float,
    sec_type_override: Optional[str],
    resource_override: Optional[int],
) -> None:
    station = _parse_int(session_cfg.get("station_address"), 0)
    connect_ctr = _parse_int(sec_cfg.get("connect_ctr"), 0x19)
    seed_ctr = _parse_int(sec_cfg.get("seed_ctr"), 0x07)
    unlock_ctr = _parse_int(sec_cfg.get("unlock_ctr"), 0x08)
    resource = int(resource_override if resource_override is not None else _parse_int(sec_cfg.get("seed_resource"), 0x01))
    seed_endian = str(sec_cfg.get("seed_endian") or "big").lower()
    sec_type = str(sec_type_override or sec_cfg.get("sec_type") or "CAL").upper()
    unlock_pad = _parse_int(sec_cfg.get("unlock_pad"), 0x55)
    access_key = _resolve_access_key(sec_cfg)
    if not access_key:
        raise RuntimeError("security.access_key is required (set in YAML or CCP_ACCESS_KEY env var)")

    print("[INFO] CONNECT")
    conn = proto.build_connect(station_address=station, ctr_override=connect_ctr)
    session.send(conn)
    session.recv(timeout_s=timeout_s, only_id=rx_id)

    print(f"[INFO] GET_SEED resource=0x{resource:02X} sec_type={sec_type}")
    seed = proto.build_get_seed(resource=resource, ctr_override=seed_ctr)
    session.send(seed)
    frames = session.recv(timeout_s=timeout_s, only_id=rx_id)
    if not frames:
        raise RuntimeError("No GET_SEED response")
    data = frames[-1].data.ljust(8, b"\x00")
    seed_bytes = bytes(data[4:8])
    status = int(data[3])
    if status:
        print(f"[INFO] Seed protection status={status}")
    key_int = int(access_key.replace(" ", "").replace("0x", "").replace("0X", ""), 16)
    key = compute_key_from_seed_algo(seed=seed_bytes, access_key=key_int, seed_endian=seed_endian, sec_type=sec_type)
    print("[INFO] UNLOCK")
    unlock = proto.build_unlock(key=key, ctr_override=unlock_ctr, pad=unlock_pad)
    session.send(unlock)
    session.recv(timeout_s=timeout_s, only_id=rx_id)


def _dual_unlock_and_daq_ena(
    session: NixnetSession,
    proto: CcpProto,
    session_cfg: Dict[str, Any],
    sec_cfg: Dict[str, Any],
    acq_cfg: Dict[str, Any],
    rx_id: int,
    timeout_s: float,
) -> None:
    """CONNECT, CAL unlock, SET_S_STATUS, write CCP_DAQ_ena, DAQ unlock."""
    station = _parse_int(session_cfg.get("station_address"), 0)
    connect_ctr = _parse_int(sec_cfg.get("connect_ctr"), 0x19)
    seed_ctr = _parse_int(sec_cfg.get("seed_ctr"), 0x07)
    unlock_ctr = _parse_int(sec_cfg.get("unlock_ctr"), 0x08)
    unlock_pad = _parse_int(sec_cfg.get("unlock_pad"), 0x55)
    seed_endian = str(sec_cfg.get("seed_endian") or "big").lower()
    access_key_text = _resolve_access_key(sec_cfg)
    if not access_key_text:
        raise RuntimeError("security.access_key is required (set in YAML or CCP_ACCESS_KEY env var)")
    access_key = int(access_key_text.replace(" ", "").replace("0x", "").replace("0X", ""), 16)
    byteorder = str(acq_cfg.get("mta_addr_endian") or "big").lower()

    print("[STEP] CONNECT")
    conn = proto.build_connect(station_address=station, ctr_override=connect_ctr)
    session.send(conn)
    session.recv(timeout_s=timeout_s, only_id=rx_id)

    cal_resource = _parse_int(sec_cfg.get("seed_resource"), 0x01)
    cal_sec_type = str(sec_cfg.get("sec_type") or "CAL").upper()
    print(f"[STEP] CAL GET_SEED resource=0x{cal_resource:02X} sec_type={cal_sec_type}")
    session.send(proto.build_get_seed(resource=cal_resource, ctr_override=seed_ctr))
    cal_frames = session.recv(timeout_s=timeout_s, only_id=rx_id)
    if not cal_frames:
        raise RuntimeError("No CAL GET_SEED response")
    cal_seed = cal_frames[-1].data.ljust(8, b"\x00")
    cal_key = compute_key_from_seed_algo(seed=bytes(cal_seed[4:8]), access_key=access_key, seed_endian=seed_endian, sec_type=cal_sec_type)
    print("[STEP] CAL UNLOCK")
    session.send(proto.build_unlock(key=cal_key, ctr_override=unlock_ctr, pad=unlock_pad))
    cal_resp = session.recv(timeout_s=timeout_s, only_id=rx_id)
    if cal_resp:
        cu = cal_resp[-1].data.ljust(8, b"\x00")
        cal_rc = int(cu[1]) if cu[0] == 0xFF else -1
        print(f"[STEP] CAL UNLOCK status={'ok' if cal_rc == 0 else f'rc={cal_rc}'}")

    s_status = _parse_int(sec_cfg.get("s_status"), 0x83)
    print(f"[STEP] SET_S_STATUS 0x{s_status:02X}")
    frame, ctr = _build_set_s_status(proto, s_status)
    _send_wait_crm(session, frame, ctr, rx_id, "SET_S_STATUS", timeout_s)

    daq_ena_addr = _parse_int(acq_cfg.get("daq_ena_address"), -1)
    daq_ena_val = _parse_int(acq_cfg.get("daq_ena_value"), -1)
    if daq_ena_addr >= 0 and daq_ena_val >= 0:
        print(f"[STEP] SET_MTA 0x{daq_ena_addr:08X}")
        frame, ctr = _build_set_mta(proto, daq_ena_addr, 0, byteorder)
        _send_wait_crm(session, frame, ctr, rx_id, "SET_MTA (daq_ena)", timeout_s)
        print(f"[STEP] DNLOAD CCP_DAQ_ena = 0x{daq_ena_val:02X}")
        frame, ctr = _build_dnload(proto, 1, bytes([daq_ena_val & 0xFF]))
        _send_wait_crm(session, frame, ctr, rx_id, "DNLOAD (daq_ena)", timeout_s)
        print(f"[STEP] DAQ enable: wrote 0x{daq_ena_val:02X} to 0x{daq_ena_addr:08X}")
    else:
        print("[STEP] No daq_ena configured, skipping DNLOAD")

    daq_resource = _parse_int(acq_cfg.get("seed_resource"), 0x02)
    daq_sec_type = str(acq_cfg.get("sec_type") or "DAQ").upper()
    print(f"[STEP] DAQ GET_SEED resource=0x{daq_resource:02X} sec_type={daq_sec_type}")
    session.send(proto.build_get_seed(resource=daq_resource, ctr_override=seed_ctr))
    daq_frames = session.recv(timeout_s=timeout_s, only_id=rx_id)
    if not daq_frames:
        raise RuntimeError("No DAQ GET_SEED response")
    daq_seed = daq_frames[-1].data.ljust(8, b"\x00")
    daq_key = compute_key_from_seed_algo(seed=bytes(daq_seed[4:8]), access_key=access_key, seed_endian=seed_endian, sec_type=daq_sec_type)
    print("[STEP] DAQ UNLOCK")
    session.send(proto.build_unlock(key=daq_key, ctr_override=unlock_ctr, pad=unlock_pad))
    daq_resp = session.recv(timeout_s=timeout_s, only_id=rx_id)
    if daq_resp:
        du = daq_resp[-1].data.ljust(8, b"\x00")
        daq_rc = int(du[1]) if du[0] == 0xFF else -1
        print(f"[STEP] DAQ UNLOCK status={'ok' if daq_rc == 0 else f'rc={daq_rc}'}")


def run_probe(args: argparse.Namespace) -> int:
    cfg = _load_config(Path(args.config))
    device = _first_device(cfg)
    session_cfg = dict(device.get("session") or {})
    sec_cfg = dict(device.get("security") or {})
    if not str(sec_cfg.get("access_key") or "").strip():
        top_key = _resolve_access_key(sec_cfg, dict(cfg.get("security") or {}))
        if top_key:
            sec_cfg["access_key"] = top_key
    a2l_path = Path(str((device.get("a2l") or {}).get("path") or ""))
    if not a2l_path.exists():
        raise RuntimeError(f"A2L path does not exist: {a2l_path}")

    parsed = parse_a2l(a2l_path)
    daq_lists = parse_a2l_daq_lists(a2l_path)
    daq_meta = daq_lists.get(args.tier) or next(iter(daq_lists.values()), None)

    daq_list = int(args.daq_list if args.daq_list is not None else (daq_meta.list_number if daq_meta and daq_meta.list_number is not None else 0))
    rx_id = _parse_int(session_cfg.get("rx_id"), 0)
    tx_id = _parse_int(session_cfg.get("tx_id"), 0)
    raw_meta_dto = getattr(daq_meta, "raw_can_id", None) if daq_meta is not None else None
    runtime_meta_dto = daq_meta.can_id if daq_meta and daq_meta.can_id is not None else None
    command_dto_id = int(args.command_dto_id if args.command_dto_id is not None else (raw_meta_dto if raw_meta_dto is not None else (runtime_meta_dto if runtime_meta_dto is not None else rx_id)))
    dto_id = int(args.dto_id if args.dto_id is not None else (runtime_meta_dto if runtime_meta_dto is not None else command_dto_id))
    event_channel = int(args.event_channel if args.event_channel is not None else (daq_meta.raster if daq_meta and daq_meta.raster is not None else 0))
    first_pid_hint = int(args.first_pid if args.first_pid is not None else (daq_meta.first_pid if daq_meta and daq_meta.first_pid is not None else 0))
    byteorder = str(cfg.get("mta_addr_endian") or "big").lower()
    addr_ext_high = bool(cfg.get("addr_ext_high", False))
    timeout_s = max(0.01, float(args.timeout_s))

    channels = [x.strip() for x in str(args.channels).split(",") if x.strip()]
    entries = _pack_entries(channels, parsed, _measurement_cfg_by_name(device), addr_ext_high)
    if not entries:
        raise RuntimeError("No probe entries could be built")
    last_odt = max(x.odt for x in entries)

    print(f"[INFO] Device={device.get('name', 'CCP')} interface={session_cfg.get('interface')} tx=0x{tx_id:X} rx=0x{rx_id:X}")
    print(f"[INFO] DAQ list={daq_list} dto_id=0x{dto_id:X} command_dto_id=0x{command_dto_id:X} event_channel={event_channel} first_pid_hint={first_pid_hint}")
    for entry in entries:
        print(
            f"[INFO] ODT{entry.odt} offset={entry.offset} {entry.name}: "
            f"addr=0x{entry.address:X} ext=0x{entry.extension:X} size={entry.size} dtype={entry.dtype}"
        )

    session = NixnetSession(
        str(session_cfg.get("interface") or ""),
        _parse_int(session_cfg.get("baudrate"), 250000),
        force_stream_rx=True,
    )
    proto = CcpProto(tx_id=tx_id, is_extended=bool(session_cfg.get("is_extended", True)))
    started = False
    try:
        session.open(rx_id=rx_id)
        _connect_unlock(
            session,
            proto,
            session_cfg,
            sec_cfg,
            rx_id=rx_id,
            timeout_s=timeout_s,
            sec_type_override=args.sec_type,
            resource_override=args.seed_resource,
        )

        print("[INFO] START_STOP stop")
        frame, ctr = _build_start_stop(proto, 0, daq_list, 0, 0, 0)
        _send_wait_crm(session, frame, ctr, rx_id, "START_STOP stop", timeout_s)

        print("[INFO] GET_DAQ_SIZE")
        frame, ctr = _build_get_daq_size(proto, daq_list, command_dto_id)
        data = _send_wait_crm(session, frame, ctr, rx_id, "GET_DAQ_SIZE", timeout_s)
        daq_size = int(data[3])
        first_pid = int(data[4]) if first_pid_hint <= 0 else first_pid_hint
        print(f"[INFO] GET_DAQ_SIZE response: daq_size={daq_size} first_pid={first_pid}")
        if daq_size and last_odt + 1 > daq_size:
            raise RuntimeError(f"Probe needs {last_odt + 1} ODT(s), ECU reports {daq_size}")

        odt_element_counts: Dict[int, int] = {}
        for entry in entries:
            element = int(odt_element_counts.get(entry.odt, 0))
            odt_element_counts[entry.odt] = element + 1
            print(f"[INFO] SET_DAQ_PTR list={daq_list} odt={entry.odt} element={element}")
            frame, ctr = _build_set_daq_ptr(proto, daq_list, entry.odt, element)
            _send_wait_crm(session, frame, ctr, rx_id, "SET_DAQ_PTR", timeout_s)
            print(f"[INFO] WRITE_DAQ {entry.name}")
            frame, ctr = _build_write_daq(proto, entry.size, entry.extension, entry.address, byteorder)
            _send_wait_crm(session, frame, ctr, rx_id, "WRITE_DAQ", timeout_s)

        print("[INFO] START_STOP start")
        frame, ctr = _build_start_stop(proto, 1, daq_list, last_odt, event_channel, max(1, int(args.prescaler)))
        _send_wait_crm(session, frame, ctr, rx_id, "START_STOP start", timeout_s)
        started = True

        print(f"[INFO] Listening for DTO frames for {args.duration_s:.1f}s...")
        deadline = time.time() + max(1.0, float(args.duration_s))
        dto_count = 0
        raw_counts: Dict[int, int] = {}
        raw_samples: Dict[int, bytes] = {}
        listen_filter = None if bool(args.discover_ids) else dto_id
        while time.time() < deadline:
            frames = session.recv(timeout_s=0.05, only_id=listen_filter)
            for fr in frames:
                data = fr.data.ljust(8, b"\x00")
                raw_counts[fr.arbitration_id] = int(raw_counts.get(fr.arbitration_id, 0)) + 1
                raw_samples.setdefault(fr.arbitration_id, data)
                pid = int(data[0])
                odt = pid - first_pid
                odt_entries = [x for x in entries if x.odt == odt]
                if not odt_entries:
                    continue
                dto_count += 1
                decoded = []
                for entry in odt_entries:
                    raw = data[1 + entry.offset:1 + entry.offset + entry.size]
                    value = decode_value(entry.dtype, raw, str(args.poll_endian), limits=entry.limits, coeffs=entry.coeffs)
                    decoded.append(f"{entry.name}={value:.3f}")
                print(f"[DTO] pid={pid} odt={odt} " + " ".join(decoded))
        print(f"[INFO] DTO frames decoded: {dto_count}")
        if raw_counts:
            print("[INFO] Raw frames observed during listen:")
            for can_id, count in sorted(raw_counts.items(), key=lambda kv: (-kv[1], kv[0])):
                sample = raw_samples.get(can_id, b"").ljust(8, b"\x00")
                sample_hex = " ".join(f"{b:02X}" for b in sample[:8])
                print(f"[RAW] id=0x{can_id:X} count={count} sample={sample_hex}")
        else:
            print("[INFO] Raw frames observed during listen: 0")
    finally:
        if started:
            try:
                print("[INFO] START_STOP cleanup stop")
                frame, ctr = _build_start_stop(proto, 0, daq_list, 0, 0, 0)
                _send_wait_crm(session, frame, ctr, rx_id, "START_STOP cleanup stop", timeout_s)
            except Exception as exc:
                print(f"[WARN] Cleanup stop failed: {exc}")
        session.close()
    return 0


# ---------------------------------------------------------------------------
# Multi-list probe
# ---------------------------------------------------------------------------

@dataclass
class _ListPlan:
    tier: str
    list_num: int
    cmd_dto: int
    event_ch: int
    first_pid: int
    prescaler: int
    entries: List[ProbeEntry]
    last_odt: int


def run_multi_list_probe(args: argparse.Namespace) -> int:
    cfg = _load_config(Path(args.config))
    device = _first_device(cfg)
    session_cfg = dict(device.get("session") or {})
    sec_cfg = dict(device.get("security") or {})
    if not str(sec_cfg.get("access_key") or "").strip():
        top_key = _resolve_access_key(sec_cfg, dict(cfg.get("security") or {}))
        if top_key:
            sec_cfg["access_key"] = top_key
    acq_cfg = dict(cfg.get("acquisition") or {})
    a2l_path = Path(str((device.get("a2l") or {}).get("path") or ""))
    if not a2l_path.exists():
        raise RuntimeError(f"A2L path does not exist: {a2l_path}")

    parsed = parse_a2l(a2l_path)
    daq_lists = parse_a2l_daq_lists(a2l_path)
    meas_cfg = _measurement_cfg_by_name(device)
    rx_id = _parse_int(session_cfg.get("rx_id"), 0)
    tx_id = _parse_int(session_cfg.get("tx_id"), 0)
    byteorder = str(cfg.get("mta_addr_endian") or "big").lower()
    addr_ext_high = bool(cfg.get("addr_ext_high", False))
    timeout_s = max(0.01, float(args.timeout_s))
    prescaler = max(1, int(args.prescaler))

    tier_channel_pairs: List[Tuple[str, List[str]]] = []
    channels1 = [x.strip() for x in str(args.channels).split(",") if x.strip()]
    if not channels1:
        raise RuntimeError("--multi-list requires --channels")
    tier_channel_pairs.append((str(args.tier), channels1))

    channels2 = [x.strip() for x in str(args.channels2 or "").split(",") if x.strip()]
    if not channels2:
        raise RuntimeError("--multi-list requires --channels2")
    tier_channel_pairs.append((str(args.tier2 or "50ms"), channels2))

    channels3 = [x.strip() for x in str(args.channels3 or "").split(",") if x.strip()]
    if channels3:
        tier_channel_pairs.append((str(args.tier3 or "100ms"), channels3))

    plans: List[_ListPlan] = []
    for tier, channels in tier_channel_pairs:
        meta = daq_lists.get(tier)
        if meta is None:
            raise RuntimeError(f"A2L has no DAQ list for tier '{tier}'. Available: {list(daq_lists.keys())}")
        entries = _pack_entries(channels, parsed, meas_cfg, addr_ext_high)
        if not entries:
            raise RuntimeError(f"No entries could be built for tier '{tier}' channels: {channels}")
        last_odt = max(e.odt for e in entries)
        raw_dto = getattr(meta, "raw_can_id", None)
        runtime_dto = meta.can_id
        cmd_dto = int(raw_dto if raw_dto is not None else (runtime_dto if runtime_dto is not None else rx_id))
        plans.append(_ListPlan(
            tier=tier,
            list_num=int(meta.list_number if meta.list_number is not None else 0),
            cmd_dto=cmd_dto,
            event_ch=int(meta.raster if meta.raster is not None else 0),
            first_pid=int(meta.first_pid if meta.first_pid is not None else 0),
            prescaler=prescaler,
            entries=entries,
            last_odt=last_odt,
        ))

    print("=" * 60)
    print("MULTI-LIST DAQ PROBE")
    print("=" * 60)
    print(f"[INFO] Device={device.get('name', 'CCP')} interface={session_cfg.get('interface')} tx=0x{tx_id:X} rx=0x{rx_id:X}")
    for i, p in enumerate(plans):
        print(f"\n[INFO] --- List {i+1}: {p.tier} (list_num={p.list_num}, event_ch={p.event_ch}, first_pid={p.first_pid}) ---")
        for e in p.entries:
            print(f"[INFO]   ODT{e.odt} off={e.offset} {e.name}: addr=0x{e.address:X} ext=0x{e.extension:X} size={e.size} dtype={e.dtype}")

    session = NixnetSession(
        str(session_cfg.get("interface") or ""),
        _parse_int(session_cfg.get("baudrate"), 250000),
        force_stream_rx=True,
    )
    proto = CcpProto(tx_id=tx_id, is_extended=bool(session_cfg.get("is_extended", True)))
    started_lists: List[int] = []

    try:
        session.open(rx_id=rx_id)

        print("\n" + "=" * 60)
        print("PHASE 1: DUAL UNLOCK + DAQ ENABLE")
        print("=" * 60)
        _dual_unlock_and_daq_ena(session, proto, session_cfg, sec_cfg, acq_cfg, rx_id, timeout_s)

        print("\n" + "=" * 60)
        print("PHASE 2: CONFIGURE ALL LISTS")
        print("=" * 60)
        pid_map: Dict[int, Tuple[_ListPlan, List[ProbeEntry]]] = {}
        for i, p in enumerate(plans):
            print(f"\n--- Configuring list {i+1}: {p.tier} (list_num={p.list_num}) ---")

            print(f"[STEP] STOP list {p.list_num}")
            try:
                frame, ctr = _build_start_stop(proto, 0, p.list_num, 0, 0, 0)
                _send_wait_crm(session, frame, ctr, rx_id, f"STOP list {p.list_num}", timeout_s)
                print(f"[STEP] STOP list {p.list_num}: ok")
            except Exception as e:
                print(f"[STEP] STOP list {p.list_num}: {e} (continuing)")

            print(f"[STEP] GET_DAQ_SIZE list={p.list_num} cmd_dto=0x{p.cmd_dto:08X}")
            frame, ctr = _build_get_daq_size(proto, p.list_num, p.cmd_dto)
            data = _send_wait_crm(session, frame, ctr, rx_id, f"GET_DAQ_SIZE list {p.list_num}", timeout_s)
            ecu_odts = int(data[3])
            ecu_first_pid = int(data[4])
            first_pid = p.first_pid if p.first_pid > 0 else ecu_first_pid
            print(f"[STEP] GET_DAQ_SIZE result: ecu_odts={ecu_odts}, first_pid={first_pid} (hint={p.first_pid}, ecu={ecu_first_pid})")
            if ecu_odts and p.last_odt + 1 > ecu_odts:
                raise RuntimeError(f"List {p.list_num} ({p.tier}) needs {p.last_odt+1} ODTs, ECU allows {ecu_odts}")

            odt_element_counts: Dict[int, int] = {}
            for entry in p.entries:
                element = int(odt_element_counts.get(entry.odt, 0))
                odt_element_counts[entry.odt] = element + 1
                print(f"[STEP] SET_DAQ_PTR list={p.list_num} odt={entry.odt} element={element}")
                frame, ctr = _build_set_daq_ptr(proto, p.list_num, entry.odt, element)
                _send_wait_crm(session, frame, ctr, rx_id, "SET_DAQ_PTR", timeout_s)
                print(f"[STEP] WRITE_DAQ {entry.name} (addr=0x{entry.address:X}, size={entry.size})")
                frame, ctr = _build_write_daq(proto, entry.size, entry.extension, entry.address, byteorder)
                _send_wait_crm(session, frame, ctr, rx_id, "WRITE_DAQ", timeout_s)

            for entry in p.entries:
                pid = first_pid + entry.odt
                odt_entries = pid_map.get(pid, (p, []))[1] if pid in pid_map else []
                if not odt_entries:
                    pid_map[pid] = (p, [])
                pid_map[pid][1].append(entry)
            p.first_pid = first_pid

        print("\n" + "=" * 60)
        print("PHASE 3: START ALL LISTS")
        print("=" * 60)
        for i, p in enumerate(plans):
            print(f"[STEP] START list {p.list_num} ({p.tier}): last_odt={p.last_odt}, event_ch={p.event_ch}, prescaler={p.prescaler}")
            frame, ctr = _build_start_stop(proto, 1, p.list_num, p.last_odt, p.event_ch, p.prescaler)
            _send_wait_crm(session, frame, ctr, rx_id, f"START list {p.list_num}", timeout_s)
            started_lists.append(p.list_num)
            print(f"[STEP] START list {p.list_num}: ok")

        print("[STEP] START_STOP_ALL (mode=1)")
        try:
            frame, ctr = _build_start_stop_all(proto, 1)
            _send_wait_crm(session, frame, ctr, rx_id, "START_STOP_ALL", timeout_s)
            print("[STEP] START_STOP_ALL: ok")
        except Exception as e:
            print(f"[STEP] START_STOP_ALL: {e} (continuing)")

        print("\n" + "=" * 60)
        print(f"PHASE 4: LISTEN FOR DTOs ({args.duration_s:.1f}s)")
        print("=" * 60)
        deadline = time.time() + max(1.0, float(args.duration_s))
        list_dto_counts: Dict[str, int] = {p.tier: 0 for p in plans}
        unknown_pids: Dict[int, int] = {}
        total_dtos = 0
        raw_counts: Dict[int, int] = {}
        raw_samples: Dict[int, bytes] = {}

        while time.time() < deadline:
            frames = session.recv(timeout_s=0.05, only_id=None)
            for fr in frames:
                data = fr.data.ljust(8, b"\x00")
                raw_counts[fr.arbitration_id] = raw_counts.get(fr.arbitration_id, 0) + 1
                raw_samples.setdefault(fr.arbitration_id, data)
                pid = int(data[0])
                if pid not in pid_map:
                    known_range = any(p.first_pid <= pid <= p.first_pid + p.last_odt for p in plans)
                    if known_range:
                        unknown_pids[pid] = unknown_pids.get(pid, 0) + 1
                    continue
                plan_ref, odt_entries = pid_map[pid]
                total_dtos += 1
                list_dto_counts[plan_ref.tier] = list_dto_counts.get(plan_ref.tier, 0) + 1
                odt_idx = pid - plan_ref.first_pid
                decoded = []
                for entry in odt_entries:
                    raw = data[1 + entry.offset:1 + entry.offset + entry.size]
                    value = decode_value(entry.dtype, raw, str(args.poll_endian), limits=entry.limits, coeffs=entry.coeffs)
                    decoded.append(f"{entry.name}={value:.3f}")
                print(f"[DTO] list={plan_ref.tier} pid={pid} odt={odt_idx} " + " ".join(decoded))

        elapsed = float(args.duration_s)
        print("\n" + "=" * 60)
        print("RESULTS")
        print("=" * 60)
        for p in plans:
            cnt = list_dto_counts.get(p.tier, 0)
            hz = cnt / elapsed if elapsed > 0 else 0
            print(f"[RESULT] List {p.list_num} ({p.tier}): {cnt} DTOs in {elapsed:.1f}s = {hz:.1f} Hz")
        print(f"[RESULT] Total: {total_dtos} DTOs, {sum(unknown_pids.values())} unknown-PID frames")
        if unknown_pids:
            print(f"[RESULT] Unknown PIDs: {dict(sorted(unknown_pids.items()))}")
        if raw_counts:
            print("[INFO] Raw CAN IDs observed:")
            for can_id, count in sorted(raw_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]:
                sample = raw_samples.get(can_id, b"").ljust(8, b"\x00")
                sample_hex = " ".join(f"{b:02X}" for b in sample[:8])
                print(f"[RAW] id=0x{can_id:X} count={count} sample=[{sample_hex}]")

    finally:
        for ln in started_lists:
            try:
                print(f"[CLEANUP] STOP list {ln}")
                frame, ctr = _build_start_stop(proto, 0, ln, 0, 0, 0)
                _send_wait_crm(session, frame, ctr, rx_id, f"STOP list {ln}", timeout_s)
            except Exception as exc:
                print(f"[WARN] Cleanup STOP list {ln} failed: {exc}")
        try:
            print("[CLEANUP] START_STOP_ALL stop")
            frame, ctr = _build_start_stop_all(proto, 0)
            _send_wait_crm(session, frame, ctr, rx_id, "START_STOP_ALL stop", timeout_s)
        except Exception:
            pass
        session.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sidecar CCP DAQ/ODT probe. Does not modify production CCP mode.")
    parser.add_argument("--config", default=str(ROOT / "configs" / "ccp.yaml"))
    parser.add_argument("--channels", default=",".join(DEFAULT_CHANNELS))
    parser.add_argument("--tier", default="10ms", help="A2L DAQ tier to prefer when metadata is available.")
    parser.add_argument("--duration-s", type=float, default=5.0)
    parser.add_argument("--timeout-s", type=float, default=0.05)
    parser.add_argument("--daq-list", type=int, default=None)
    parser.add_argument("--dto-id", type=lambda x: _parse_int(x), default=None)
    parser.add_argument("--command-dto-id", type=lambda x: _parse_int(x), default=None)
    parser.add_argument("--event-channel", type=int, default=None)
    parser.add_argument("--first-pid", type=int, default=None)
    parser.add_argument("--prescaler", type=int, default=1)
    parser.add_argument("--seed-resource", type=lambda x: _parse_int(x), default=None)
    parser.add_argument("--sec-type", default=None)
    parser.add_argument("--poll-endian", default="big", choices=["big", "little"])
    parser.add_argument("--discover-ids", action="store_true", help="Listen without CAN ID filtering and print raw frame IDs observed.")
    parser.add_argument("--multi-list", action="store_true", help="Test multi-list DAQ: configure two lists and stream both.")
    parser.add_argument("--channels2", default=None, help="Channels for the second DAQ list (comma-separated). Required with --multi-list.")
    parser.add_argument("--tier2", default="50ms", help="A2L DAQ tier for the second list (default: 50ms).")
    parser.add_argument("--channels3", default=None, help="Channels for a third DAQ list (comma-separated). Optional.")
    parser.add_argument("--tier3", default="100ms", help="A2L DAQ tier for the third list (default: 100ms).")
    args = parser.parse_args()
    try:
        if args.multi_list:
            return run_multi_list_probe(args)
        return run_probe(args)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
