# Author: T. Onkst | Date: 08122025

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Dict, Any, Set

from .base import BasePlugin, PluginStatus

try:
    import can as _pycan  # type: ignore
except Exception:
    _pycan = None

try:
    import cantools as _cantools  # type: ignore
except Exception:
    _cantools = None


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
        self._signal_map: Dict[str, Dict[str, Any]] = {}
        self._alias_decode_specs: Dict[str, Dict[str, Any]] = {}
        self._db = None
        self._bus = None
        self._diag: Dict[str, float] = {
            "frames_rx": 0.0,
            "decode_hits": 0.0,
            "last_decode_ts": 0.0,
        }

    def configure(self) -> None:
        self._signal_map = {}
        self._alias_decode_specs = {}
        try:
            hz = float(self.config.get("recording_rate_hz", 10.0))
        except Exception:
            hz = 10.0
        self._snapshot_period_s = max(0.01, 1.0 / max(1.0, hz))
        for item in self.config.get("signals", []) or []:
            if not isinstance(item, dict):
                continue
            if not bool(item.get("enabled", True)):
                continue
            alias = str(item.get("alias") or "").strip()
            if not alias:
                continue
            self._signal_map[alias] = {
                "message": str(item.get("message") or "").strip(),
                "signal": str(item.get("signal") or "").strip(),
            }
        if self.mode == "real":
            self._load_dbc()
            self._build_decode_specs()

    def validate(self) -> PluginStatus:
        signals = self.config.get("signals", [])
        if not isinstance(signals, list):
            return PluginStatus(ok=False, message="signals must be a list")
        aliases = [str(item.get("alias")) for item in (signals or []) if isinstance(item, dict) and item.get("alias")]
        if len(aliases) != len(set(aliases)):
            return PluginStatus(ok=False, message="Duplicate aliases within CAN plugin configuration")
        if self.mode == "real":
            dbc_path = str(self.config.get("dbc_path", "")).strip()
            if not dbc_path:
                return PluginStatus(ok=False, message="dbc_path is required for CAN real mode")
            if not Path(dbc_path).exists():
                return PluginStatus(ok=False, message=f"dbc_path not found: {dbc_path}")
            if _pycan is None:
                return PluginStatus(ok=False, message="python-can package is required for CAN real mode")
            if _cantools is None:
                return PluginStatus(ok=False, message="cantools package is required for CAN real mode")
        return PluginStatus(ok=True)

    def aliases(self) -> Set[str]:
        aliases: Set[str] = set()
        for item in self.config.get("signals", []) or []:
            alias = item.get("alias") if isinstance(item, dict) else None
            if alias and bool(item.get("enabled", True)):
                aliases.add(str(alias))
        aliases.update({"CAN/frames_rx", "CAN/decode_hits", "CAN/last_decode_age_s"})
        return aliases

    def units(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for item in self.config.get("signals", []) or []:
            if not isinstance(item, dict):
                continue
            alias = item.get("alias")
            unit = item.get("unit", "")
            if alias and bool(item.get("enabled", True)):
                mapping[str(alias)] = str(unit)
        mapping["CAN/frames_rx"] = "count"
        mapping["CAN/decode_hits"] = "count"
        mapping["CAN/last_decode_age_s"] = "s"
        return mapping

    def start(self) -> None:
        self._theta = 0.0
        self._snapshot_stop.clear()
        if self.mode == "real":
            self._open_bus()
        with self._snapshot_lock:
            self._snapshot_values = {}
        self._diag = {"frames_rx": 0.0, "decode_hits": 0.0, "last_decode_ts": 0.0}
        self._snapshot_thread = threading.Thread(target=self._snapshot_loop, daemon=True)
        self._snapshot_thread.start()

    def simulate_step(self) -> Dict[str, Any]:
        with self._snapshot_lock:
            return dict(self._snapshot_values)

    def stop(self) -> None:
        self._snapshot_stop.set()
        t = self._snapshot_thread
        if t is not None and t.is_alive():
            try:
                t.join(timeout=0.5)
            except Exception:
                pass
        self._snapshot_thread = None
        self._close_bus()

    def _snapshot_loop(self) -> None:
        while not self._snapshot_stop.is_set():
            if self.mode == "real":
                vals = self._read_real_step()
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
                self._snapshot_values = cur

    def _compute_sim_step_values(self) -> Dict[str, Any]:
        vals: Dict[str, Any] = {}
        self._theta += math.pi / 30.0
        for idx, item in enumerate(self.config.get("signals", []) or []):
            if not isinstance(item, dict) or not bool(item.get("enabled", True)):
                continue
            alias = item.get("alias")
            if not alias:
                continue
            phase = idx * math.pi / 6.0
            name = str(alias).lower()
            if "rpm" in name:
                vals[alias] = 1200.0 + 200.0 * math.sin(self._theta + phase)
            elif "oil" in name and "pressure" in name:
                vals[alias] = 300.0 + 20.0 * math.cos(self._theta + phase)
            else:
                vals[alias] = 1.0 * math.sin(self._theta + phase)
        return vals

    def _load_dbc(self) -> None:
        self._db = None
        if _cantools is None:
            return
        dbc_path = str(self.config.get("dbc_path", "")).strip()
        if not dbc_path or not Path(dbc_path).exists():
            return
        try:
            self._db = _cantools.database.load_file(dbc_path)
        except Exception:
            self._db = None

    def _build_decode_specs(self) -> None:
        self._alias_decode_specs = {}
        if self._db is None:
            return
        for alias, spec in self._signal_map.items():
            msg_name = str(spec.get("message") or "").strip()
            sig_name = str(spec.get("signal") or "").strip()
            if not msg_name or not sig_name:
                continue
            msg_obj = None
            try:
                msg_obj = self._db.get_message_by_name(msg_name)
            except Exception:
                msg_obj = None
            if msg_obj is None:
                continue
            pgn = self._pgn_from_message_name(msg_name)
            if pgn is None:
                try:
                    pgn = self._j1939_pgn_from_arbid(int(getattr(msg_obj, "frame_id", 0)))
                except Exception:
                    pgn = None
            self._alias_decode_specs[alias] = {
                "signal": sig_name,
                "message_obj": msg_obj,
                "message_name": msg_name,
                "pgn": pgn,
            }

    def _open_bus(self) -> None:
        self._close_bus()
        if _pycan is None:
            return
        session = self.config.get("session") or {}
        channel = str(session.get("channel") or "CAN1").strip()
        bitrate = int(session.get("baudrate", 500000))
        bustype = str(session.get("bustype") or "nixnet").strip()
        try:
            self._bus = _pycan.interface.Bus(channel=channel, bustype=bustype, bitrate=bitrate)
        except Exception:
            self._bus = None

    def _close_bus(self) -> None:
        if self._bus is None:
            return
        try:
            self._bus.shutdown()
        except Exception:
            pass
        self._bus = None

    def _read_real_step(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self._bus is None or self._db is None:
            self._snapshot_stop.wait(self._snapshot_period_s)
            return out
        frames = []
        try:
            # Event-driven wait for next frame; if none, short backoff.
            first = self._bus.recv(timeout=min(0.2, max(0.02, self._snapshot_period_s * 2.0)))
        except Exception:
            first = None
        if first is None:
            self._snapshot_stop.wait(0.01)
            return out
        frames.append(first)
        # Drain any immediately queued frames to reduce stale snapshot behavior.
        while True:
            try:
                nxt = self._bus.recv(timeout=0.0)
            except Exception:
                nxt = None
            if nxt is None:
                break
            frames.append(nxt)
            if len(frames) >= 32:
                break
        self._diag["frames_rx"] = float(self._diag.get("frames_rx", 0.0)) + float(len(frames))
        for msg in frames:
            decoded = self._decode_msg(msg)
            if not decoded:
                continue
            self._diag["decode_hits"] = float(self._diag.get("decode_hits", 0.0)) + 1.0
            self._diag["last_decode_ts"] = self._now_s()
            out.update(decoded)
        return out

    def _decode_msg(self, msg) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        msg_name = None
        try:
            mo = self._db.get_message_by_frame_id(int(msg.arbitration_id))
            if mo is not None:
                msg_name = str(getattr(mo, "name", "") or "")
        except Exception:
            msg_name = None
        # Path 1: direct database decode by arbitration id.
        try:
            decoded = self._db.decode_message(msg.arbitration_id, bytes(msg.data), decode_choices=False)
            if isinstance(decoded, dict):
                for alias, spec in self._signal_map.items():
                    exp_msg = str(spec.get("message") or "").strip()
                    sig_name = str(spec.get("signal") or "").strip()
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
        # Path 2: J1939 fallback by message PGN + configured message name.
        rx_pgn = self._j1939_pgn_from_arbid(int(msg.arbitration_id))
        for alias, spec in self._alias_decode_specs.items():
            exp_pgn = spec.get("pgn")
            if exp_pgn is not None and rx_pgn != exp_pgn:
                continue
            msg_obj = spec.get("message_obj")
            sig_name = str(spec.get("signal") or "").strip()
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

    def _now_s(self) -> float:
        try:
            import time as _t
            return float(_t.time())
        except Exception:
            return 0.0

    def _pgn_from_message_name(self, msg_name: str) -> int | None:
        # Message names like VEP1_65271 encode the PGN as suffix.
        try:
            tail = str(msg_name).rsplit("_", 1)[-1]
            return int(tail)
        except Exception:
            return None

    def _j1939_pgn_from_arbid(self, arbid: int) -> int | None:
        # 29-bit ID: priority(3), reserved(1), dp(1), pf(8), ps(8), sa(8)
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


