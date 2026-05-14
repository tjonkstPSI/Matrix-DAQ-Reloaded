# Author: T. Onkst | Date: 04202026

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Dict, Any, List, Set

from .base import BasePlugin, PluginStatus

try:
    import can as _pycan  # type: ignore
except Exception:
    _pycan = None

try:
    import cantools as _cantools  # type: ignore
except Exception:
    _cantools = None


class _BusContext:
    """Runtime state for a single CAN bus."""
    __slots__ = (
        "name", "channel", "baudrate", "bustype", "dbc_path",
        "signals", "signal_map", "alias_decode_specs", "db", "bus",
    )

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.name: str = str(cfg.get("name", "CAN Bus")).strip()
        self.channel: str = str(cfg.get("channel", "CAN1")).strip()
        self.baudrate: int = int(cfg.get("baudrate", 500000))
        self.bustype: str = str(cfg.get("bustype", "nixnet")).strip()
        self.dbc_path: str = str(cfg.get("dbc_path", "")).strip()
        self.signals: List[Dict[str, Any]] = cfg.get("signals", []) or []
        self.signal_map: Dict[str, Dict[str, Any]] = {}
        self.alias_decode_specs: Dict[str, Dict[str, Any]] = {}
        self.db = None
        self.bus = None


class CANPlugin(BasePlugin):
    id = "CAN"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._theta = 0.0
        self._snapshot_values: Dict[str, Any] = {}
        self._snapshot_lock = threading.Lock()
        self._snapshot_thread = None
        self._snapshot_stop = threading.Event()
        self._snapshot_period_s: float = 0.1
        self._bus_contexts: List[_BusContext] = []
        self._diag: Dict[str, float] = {
            "frames_rx": 0.0,
            "decode_hits": 0.0,
            "last_decode_ts": 0.0,
        }
        self._conn_ok: bool = False
        self._reported_no_hardware: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def configure(self) -> None:
        try:
            hz = float(self.config.get("recording_rate_hz", 10.0))
        except Exception:
            hz = 10.0
        self._snapshot_period_s = max(0.01, 1.0 / max(1.0, hz))
        self._bus_contexts = self._build_bus_contexts()
        self._reported_no_hardware = False
        n_buses = len(self._bus_contexts)
        n_sigs = sum(len(bc.signal_map) for bc in self._bus_contexts)
        print(f"[INFO] CAN: {n_buses} bus(es), {n_sigs} signal(s) resolved")

    def validate(self) -> PluginStatus:
        contexts = self._build_bus_contexts()
        if not contexts:
            return PluginStatus(ok=False, message="No CAN buses configured")

        all_aliases: List[str] = []
        for bc in contexts:
            for sig_cfg in bc.signals:
                if not isinstance(sig_cfg, dict) or not bool(sig_cfg.get("enabled", True)):
                    continue
                alias = str(sig_cfg.get("alias", "")).strip()
                if alias:
                    all_aliases.append(alias)
        if len(all_aliases) != len(set(all_aliases)):
            return PluginStatus(ok=False, message="Duplicate aliases across CAN buses")

        if self.mode == "real":
            if _pycan is None:
                return PluginStatus(ok=False, message="python-can package is required for CAN real mode")
            if _cantools is None:
                return PluginStatus(ok=False, message="cantools package is required for CAN real mode")
            for bc in contexts:
                if not bc.dbc_path:
                    return PluginStatus(ok=False, message=f"Bus '{bc.name}': dbc_path is required")
                if not Path(bc.dbc_path).exists():
                    return PluginStatus(ok=False, message=f"Bus '{bc.name}': dbc_path not found: {bc.dbc_path}")
        return PluginStatus(ok=True)

    def aliases(self) -> Set[str]:
        out: Set[str] = set()
        for bc in self._bus_contexts:
            for sig_cfg in bc.signals:
                if not isinstance(sig_cfg, dict) or not bool(sig_cfg.get("enabled", True)):
                    continue
                alias = str(sig_cfg.get("alias", "")).strip()
                if alias:
                    out.add(alias)
        out.update({"CAN/frames_rx", "CAN/decode_hits", "CAN/last_decode_age_s", "CAN/conn_ok"})
        return out

    def units(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for bc in self._bus_contexts:
            for sig_cfg in bc.signals:
                if not isinstance(sig_cfg, dict) or not bool(sig_cfg.get("enabled", True)):
                    continue
                alias = str(sig_cfg.get("alias", "")).strip()
                unit = str(sig_cfg.get("unit", ""))
                if alias:
                    mapping[alias] = unit
        mapping["CAN/frames_rx"] = "count"
        mapping["CAN/decode_hits"] = "count"
        mapping["CAN/last_decode_age_s"] = "s"
        mapping["CAN/conn_ok"] = ""
        return mapping

    def start(self) -> None:
        self._theta = 0.0
        self._snapshot_stop.clear()
        self._diag = {"frames_rx": 0.0, "decode_hits": 0.0, "last_decode_ts": 0.0}
        if self.mode == "real":
            any_open = False
            for bc in self._bus_contexts:
                if not bc.channel:
                    continue
                self._open_bus(bc)
                self._load_dbc(bc)
                self._build_decode_specs(bc)
                if bc.bus is not None:
                    any_open = True
            self._conn_ok = any_open
            if not any_open:
                print("[CAN] WARNING: no buses could be opened; plugin will report disconnected")
                self._report_no_hardware()
        else:
            self._conn_ok = True
        with self._snapshot_lock:
            self._snapshot_values = {}
        self._snapshot_thread = threading.Thread(target=self._snapshot_loop, daemon=True)
        self._snapshot_thread.start()

    def simulate_step(self) -> Dict[str, Any]:
        with self._snapshot_lock:
            vals = dict(self._snapshot_values)
        msgs = self._drain_console_msgs()
        if msgs:
            vals["__console_msgs__"] = msgs
        return vals

    def _report_no_hardware(self) -> None:
        if self._reported_no_hardware:
            return
        self._reported_no_hardware = True
        self._console_msg(
            "[CAN] No CAN hardware/interface configured or available. "
            "Open CAN config and select a detected CAN channel."
        )

    def stop(self) -> None:
        self._snapshot_stop.set()
        t = self._snapshot_thread
        if t is not None and t.is_alive():
            try:
                t.join(timeout=0.5)
            except Exception:
                pass
        self._snapshot_thread = None
        for bc in self._bus_contexts:
            self._close_bus(bc)

    # ------------------------------------------------------------------
    # Bus context builder (multi-bus + legacy fallback)
    # ------------------------------------------------------------------

    def _build_bus_contexts(self) -> List[_BusContext]:
        buses_cfg = self.config.get("buses", [])
        if isinstance(buses_cfg, list) and buses_cfg:
            contexts = [_BusContext(b) for b in buses_cfg if isinstance(b, dict)]
        else:
            legacy = self._legacy_single_bus_cfg()
            if legacy:
                contexts = [_BusContext(legacy)]
            else:
                contexts = []
        for bc in contexts:
            bc.signal_map = {}
            for sig_cfg in bc.signals:
                if not isinstance(sig_cfg, dict):
                    continue
                if not bool(sig_cfg.get("enabled", True)):
                    continue
                alias = str(sig_cfg.get("alias", "")).strip()
                if not alias:
                    continue
                bc.signal_map[alias] = {
                    "message": str(sig_cfg.get("message", "")).strip(),
                    "signal": str(sig_cfg.get("signal", "")).strip(),
                }
        return contexts

    def _legacy_single_bus_cfg(self) -> Dict[str, Any] | None:
        session = self.config.get("session")
        signals = self.config.get("signals")
        dbc_path = self.config.get("dbc_path", "")
        if not session and not signals:
            return None
        sess = session or {}
        return {
            "name": "CAN Bus 1 (legacy)",
            "channel": str(sess.get("channel", "CAN1")),
            "baudrate": int(sess.get("baudrate", 500000)),
            "bustype": str(sess.get("bustype", "nixnet")),
            "dbc_path": str(dbc_path),
            "signals": signals or [],
        }

    # ------------------------------------------------------------------
    # Snapshot loop
    # ------------------------------------------------------------------

    def _snapshot_loop(self) -> None:
        while not self._snapshot_stop.is_set():
            if self.mode == "real":
                vals = self._read_all_buses()
            else:
                vals = self._compute_sim_step_values()
            with self._snapshot_lock:
                cur = dict(self._snapshot_values)
                if vals:
                    cur.update(vals)
                now = self._now_s()
                last_ts = float(self._diag.get("last_decode_ts", 0.0))
                cur["CAN/frames_rx"] = float(self._diag.get("frames_rx", 0.0))
                cur["CAN/decode_hits"] = float(self._diag.get("decode_hits", 0.0))
                cur["CAN/last_decode_age_s"] = float(max(0.0, now - last_ts)) if last_ts > 0.0 else -1.0
                cur["CAN/conn_ok"] = 1.0 if self._conn_ok else 0.0
                self._snapshot_values = cur

    def _compute_sim_step_values(self) -> Dict[str, Any]:
        vals: Dict[str, Any] = {}
        self._theta += math.pi / 30.0
        idx = 0
        for bc in self._bus_contexts:
            for sig_cfg in bc.signals:
                if not isinstance(sig_cfg, dict) or not bool(sig_cfg.get("enabled", True)):
                    continue
                alias = sig_cfg.get("alias")
                if not alias:
                    continue
                phase = idx * math.pi / 6.0
                name = str(alias).lower()
                if "rpm" in name or "sp" in name:
                    vals[alias] = 1200.0 + 200.0 * math.sin(self._theta + phase)
                elif "pr" in name:
                    vals[alias] = 300.0 + 20.0 * math.cos(self._theta + phase)
                elif "tp" in name:
                    vals[alias] = 85.0 + 5.0 * math.sin(self._theta + phase)
                else:
                    vals[alias] = 1.0 * math.sin(self._theta + phase)
                idx += 1
        return vals

    # ------------------------------------------------------------------
    # Real-mode bus I/O
    # ------------------------------------------------------------------

    def _open_bus(self, bc: _BusContext) -> None:
        self._close_bus(bc)
        if _pycan is None:
            return
        try:
            bc.bus = _pycan.interface.Bus(
                channel=bc.channel, bustype=bc.bustype, bitrate=bc.baudrate,
            )
        except Exception:
            bc.bus = None

    def _close_bus(self, bc: _BusContext) -> None:
        if bc.bus is None:
            return
        try:
            bc.bus.shutdown()
        except Exception:
            pass
        bc.bus = None

    def _load_dbc(self, bc: _BusContext) -> None:
        bc.db = None
        if _cantools is None:
            return
        if not bc.dbc_path or not Path(bc.dbc_path).exists():
            return
        try:
            bc.db = _cantools.database.load_file(bc.dbc_path)
        except Exception:
            bc.db = None

    def _build_decode_specs(self, bc: _BusContext) -> None:
        bc.alias_decode_specs = {}
        if bc.db is None:
            return
        for alias, spec in bc.signal_map.items():
            msg_name = str(spec.get("message", "")).strip()
            sig_name = str(spec.get("signal", "")).strip()
            if not msg_name or not sig_name:
                continue
            msg_obj = None
            try:
                msg_obj = bc.db.get_message_by_name(msg_name)
            except Exception:
                pass
            if msg_obj is None:
                continue
            pgn = self._pgn_from_message_name(msg_name)
            if pgn is None:
                try:
                    pgn = self._j1939_pgn_from_arbid(int(getattr(msg_obj, "frame_id", 0)))
                except Exception:
                    pgn = None
            bc.alias_decode_specs[alias] = {
                "signal": sig_name,
                "message_obj": msg_obj,
                "message_name": msg_name,
                "pgn": pgn,
            }

    def _read_all_buses(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for bc in self._bus_contexts:
            out.update(self._read_bus(bc))
        return out

    def _read_bus(self, bc: _BusContext) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if bc.bus is None or bc.db is None:
            self._snapshot_stop.wait(self._snapshot_period_s)
            return out
        frames = []
        try:
            first = bc.bus.recv(timeout=min(0.2, max(0.02, self._snapshot_period_s * 2.0)))
        except Exception:
            first = None
        if first is None:
            self._snapshot_stop.wait(0.01)
            return out
        frames.append(first)
        while True:
            try:
                nxt = bc.bus.recv(timeout=0.0)
            except Exception:
                nxt = None
            if nxt is None:
                break
            frames.append(nxt)
            if len(frames) >= 32:
                break
        self._diag["frames_rx"] = float(self._diag.get("frames_rx", 0.0)) + float(len(frames))
        for msg in frames:
            decoded = self._decode_msg(bc, msg)
            if not decoded:
                continue
            self._diag["decode_hits"] = float(self._diag.get("decode_hits", 0.0)) + 1.0
            self._diag["last_decode_ts"] = self._now_s()
            out.update(decoded)
        return out

    def _decode_msg(self, bc: _BusContext, msg) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        msg_name = None
        try:
            mo = bc.db.get_message_by_frame_id(int(msg.arbitration_id))
            if mo is not None:
                msg_name = str(getattr(mo, "name", "") or "")
        except Exception:
            msg_name = None
        try:
            decoded = bc.db.decode_message(msg.arbitration_id, bytes(msg.data), decode_choices=False)
            if isinstance(decoded, dict):
                for alias, spec in bc.signal_map.items():
                    exp_msg = str(spec.get("message", "")).strip()
                    sig_name = str(spec.get("signal", "")).strip()
                    if not sig_name or sig_name not in decoded:
                        continue
                    if exp_msg and msg_name and exp_msg != msg_name:
                        continue
                    try:
                        out[alias] = float(decoded[sig_name])
                    except Exception:
                        pass
                if out:
                    return out
        except Exception:
            pass
        rx_pgn = self._j1939_pgn_from_arbid(int(msg.arbitration_id))
        for alias, spec in bc.alias_decode_specs.items():
            exp_pgn = spec.get("pgn")
            if exp_pgn is not None and rx_pgn != exp_pgn:
                continue
            msg_obj = spec.get("message_obj")
            sig_name = str(spec.get("signal", "")).strip()
            if msg_obj is None or not sig_name:
                continue
            try:
                decoded = msg_obj.decode(bytes(msg.data), decode_choices=False)
            except Exception:
                continue
            if not isinstance(decoded, dict) or sig_name not in decoded:
                continue
            try:
                out[alias] = float(decoded[sig_name])
            except Exception:
                pass
        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _now_s(self) -> float:
        try:
            import time as _t
            return float(_t.time())
        except Exception:
            return 0.0

    def _pgn_from_message_name(self, msg_name: str) -> int | None:
        try:
            tail = str(msg_name).rsplit("_", 1)[-1]
            return int(tail)
        except Exception:
            return None

    def _j1939_pgn_from_arbid(self, arbid: int) -> int | None:
        if arbid < 0 or arbid > 0x1FFFFFFF:
            return None
        pf = (arbid >> 16) & 0xFF
        ps = (arbid >> 8) & 0xFF
        dp = (arbid >> 24) & 0x01
        if pf < 240:
            pgn = (dp << 16) | (pf << 8)
        else:
            pgn = (dp << 16) | (pf << 8) | ps
        return int(pgn)
