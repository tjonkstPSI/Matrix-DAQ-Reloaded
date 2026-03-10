# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List, Deque, Tuple, Optional, Set
from collections import deque
import threading

from .base import BasePlugin, PluginStatus


ALLOWED_STATS: Set[str] = {"mean", "stdev", "min", "max"}


@dataclass
class ChannelConfig:
    alias: str
    stats: List[str]
    enabled: bool = True


@dataclass
class WindowConfig:
    size_seconds: Optional[float]
    size_samples: Optional[int]
    capture_mode: str  # "backward" | "forward"
    notify_on_skip: bool


class StatisticsPlugin(BasePlugin):
    id = "Statistics"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._channels: List[ChannelConfig] = []
        self._win: WindowConfig = WindowConfig(
            size_seconds=5.0,
            size_samples=None,
            capture_mode="backward",
            notify_on_skip=True,
        )
        # State per alias buffers
        self._buffers: Dict[str, Deque[Tuple[float, float]]] = {}
        self._last_emit_ts: float = 0.0
        self._source_units: Dict[str, str] = {}
        # Metrics selection
        self._default_metrics: List[str] = ["mean", "stdev", "min", "max", "p2p"]
        # Automatic trigger config/state
        self._auto_enabled: bool = False
        self._trig_channel: Optional[str] = None
        self._trig_comparator: str = ">"
        self._trig_threshold: float = 0.0
        self._trig_edge: str = "rising"
        self._trig_holdoff_s: float = 0.0
        self._armed: bool = False
        self._last_trig_val: Optional[float] = None
        self._last_fire_ts: float = 0.0
        # Snapshot lifecycle
        self._pending_request: Optional[Tuple[str, float]] = None  # (mode, ts_request)
        self._forward_start_ts: Optional[float] = None
        self._ready_vals: Dict[str, float] = {}
        self._ready_units: Dict[str, str] = {}
        self._events: List[Dict[str, Any]] = []
        # Decoupled processing state
        self._state_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending_update: Optional[Tuple[Dict[str, Any], Dict[str, str], float]] = None
        self._worker_thread = None
        self._worker_stop = threading.Event()
        self._worker_period_s: float = 0.01

    def configure(self) -> None:
        cfg = self.config or {}
        # Global metrics selected
        self._default_metrics = list((cfg.get("metrics", {}) or {}).get("selected", self._default_metrics))
        # Channels
        self._channels = []
        for item in cfg.get("channels", []) or []:
            if not isinstance(item, dict):
                continue
            alias = item.get("alias")
            stats = item.get("stats")
            enabled = bool(item.get("enabled", True))
            if not alias:
                continue
            chosen = [s for s in (stats or self._default_metrics) if s in (ALLOWED_STATS | {"p2p"})]
            if not chosen:
                continue
            self._channels.append(ChannelConfig(alias=str(alias), stats=chosen, enabled=enabled))
        # Snapshot window
        snap = (cfg.get("snapshot") or {})
        size = snap.get("window") or {}
        self._win = WindowConfig(
            size_seconds=self._opt_float(size.get("seconds")),
            size_samples=self._opt_int(size.get("samples")),
            capture_mode=str(snap.get("capture_mode", "backward")),
            notify_on_skip=bool(snap.get("notify_on_skip", True)),
        )
        # Automatic trigger config
        aut = (cfg.get("automatic_logging") or {})
        self._auto_enabled = bool(aut.get("enabled", False))
        trig = aut.get("trigger") or {}
        self._trig_channel = trig.get("channel")
        self._trig_comparator = str(trig.get("comparator", ">"))
        self._trig_threshold = float(trig.get("threshold", 0.0))
        self._trig_edge = str(trig.get("edge", "rising"))
        self._trig_holdoff_s = float(trig.get("holdoff_s", 0.0))
        self._armed = self._auto_enabled
        self._last_trig_val = None
        self._last_fire_ts = 0.0
        # Reset state
        self._buffers = {ch.alias: deque() for ch in self._channels if ch.enabled}
        self._last_emit_ts = 0.0
        self._source_units = {}
        self._pending_request = None
        self._forward_start_ts = None
        self._ready_vals = {}
        self._ready_units = {}
        self._events = []
        # Track dynamic aliases when no channels configured
        self._dynamic_mode = not bool(self._channels)
        try:
            hz = float(self.config.get("recording_rate_hz", 10.0))
        except Exception:
            hz = 10.0
        self._worker_period_s = max(0.005, 1.0 / max(10.0, hz * 2.0))

    def validate(self) -> PluginStatus:
        # Basic structure
        chans = self.config.get("channels", []) or []
        if not isinstance(chans, list):
            return PluginStatus(ok=False, message="channels must be a list")
        for item in chans:
            if not isinstance(item, dict):
                continue
            stats = item.get("stats", []) or []
            for s in stats:
                if s not in (ALLOWED_STATS | {"p2p"}):
                    return PluginStatus(ok=False, message=f"unsupported stat: {s}")
        return PluginStatus(ok=True)

    @staticmethod
    def _opt_float(v: Any) -> Optional[float]:
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    @staticmethod
    def _opt_int(v: Any) -> Optional[int]:
        try:
            return int(v) if v is not None else None
        except Exception:
            return None

    def start(self) -> None:
        self._worker_stop.clear()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def stop(self) -> None:
        self._worker_stop.set()
        t = self._worker_thread
        if t is not None and t.is_alive():
            try:
                t.join(timeout=0.5)
            except Exception:
                pass
        self._worker_thread = None

    def update(self, values: Dict[str, Any], units: Dict[str, str], now_ts: float) -> None:
        # Non-blocking: keep latest upstream payload for background processing.
        with self._pending_lock:
            self._pending_update = (dict(values), dict(units), float(now_ts))

    def _worker_loop(self) -> None:
        while not self._worker_stop.is_set():
            payload = None
            with self._pending_lock:
                if self._pending_update is not None:
                    payload = self._pending_update
                    self._pending_update = None
            if payload is not None:
                values, units, now_ts = payload
                with self._state_lock:
                    self._process_update(values, units, now_ts)
            self._worker_stop.wait(self._worker_period_s)

    def _process_update(self, values: Dict[str, Any], units: Dict[str, str], now_ts: float) -> None:
        # Update buffers per configured channel
        for ch in self._channels:
            if not ch.enabled:
                continue
            if ch.alias not in values:
                continue
            try:
                v = float(values[ch.alias])
            except Exception:
                continue
            self._source_units[ch.alias] = units.get(ch.alias, "")
            buf = self._buffers.get(ch.alias)
            if buf is None:
                buf = deque()
                self._buffers[ch.alias] = buf
            buf.append((now_ts, v))
        # Dynamic mode: include all numeric aliases not explicitly configured
        if self._dynamic_mode:
            stat_suffixes = list(ALLOWED_STATS | {"p2p"})
            for alias, raw in values.items():
                if alias == "Time_Relative_s":
                    continue
                # Skip any stat outputs to avoid recursion
                if any(str(alias).endswith(f"_{s}") for s in stat_suffixes):
                    continue
                try:
                    v = float(raw)
                except Exception:
                    continue
                self._source_units[alias] = units.get(alias, "")
                buf = self._buffers.get(alias)
                if buf is None:
                    buf = deque()
                    self._buffers[alias] = buf
                buf.append((now_ts, v))
        # Trim buffers to trailing window for backward readiness
        self._trim_trailing(now_ts)
        # Automatic trigger detection
        self._handle_auto_trigger(values, now_ts)
        # Forward capture completion
        self._maybe_finish_forward(now_ts)

    def outputs(self, now_ts: float) -> Tuple[Dict[str, float], Dict[str, str], List[Dict[str, Any]]]:
        with self._state_lock:
            vals = dict(self._ready_vals)
            u = dict(self._ready_units)
            ev = list(self._events)
            # Clear ready snapshot/events after one read
            self._ready_vals.clear()
            self._ready_units.clear()
            self._events.clear()
            return vals, u, ev

    def request_manual_snapshot(self, now_ts: float, capture_mode: Optional[str] = None) -> None:
        with self._state_lock:
            mode = capture_mode or self._win.capture_mode
            self._pending_request = (mode, now_ts)
            if mode == "forward":
                self._forward_start_ts = now_ts

    @staticmethod
    def _compute_stats(series: List[float], stats: List[str]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        import math
        n = len(series)
        if n == 0:
            return out
        s_min = min(series)
        s_max = max(series)
        s_mean = sum(series) / float(n)
        s_stdev = 0.0
        if n >= 2:
            var = sum((x - s_mean) ** 2 for x in series) / float(n - 1)
            s_stdev = math.sqrt(var)
        for s in stats:
            if s == "min":
                out[s] = s_min
            elif s == "max":
                out[s] = s_max
            elif s == "mean":
                out[s] = s_mean
            elif s == "stdev":
                out[s] = s_stdev
            elif s == "p2p":
                out[s] = s_max - s_min
        return out

    def _trim_trailing(self, now_ts: float) -> None:
        # Trim by seconds
        if self._win.size_seconds is not None:
            cutoff = now_ts - max(0.0, float(self._win.size_seconds))
            for buf in self._buffers.values():
                while buf and buf[0][0] < cutoff:
                    buf.popleft()
        # Trim by samples
        if self._win.size_samples is not None:
            maxlen = max(1, int(self._win.size_samples))
            for buf in self._buffers.values():
                while len(buf) > maxlen:
                    buf.popleft()

    def _handle_auto_trigger(self, values: Dict[str, Any], now_ts: float) -> None:
        if not self._auto_enabled or not self._trig_channel:
            return
        val = values.get(self._trig_channel)
        try:
            fval = float(val)
        except Exception:
            self._last_trig_val = None
            return
        prev_cond = self._compare(self._last_trig_val, self._trig_threshold) if self._last_trig_val is not None else False
        curr_cond = self._compare(fval, self._trig_threshold)
        fired = False
        if self._trig_edge == "rising":
            fired = (not prev_cond) and curr_cond
        else:
            fired = prev_cond and (not curr_cond)
        if fired and self._armed and (now_ts - self._last_fire_ts >= max(0.0, self._trig_holdoff_s)):
            mode = self._win.capture_mode
            self._pending_request = (mode, now_ts)
            if mode == "forward":
                self._forward_start_ts = now_ts
            self._last_fire_ts = now_ts
        # Re-arm policy: always armed, holdoff enforced by time
        self._armed = True
        self._last_trig_val = fval

    def _maybe_finish_forward(self, now_ts: float) -> None:
        if self._pending_request is None:
            return
        mode, req_ts = self._pending_request
        if mode == "backward":
            # Compute immediately
            self._emit_snapshot(now_ts)
            self._pending_request = None
        else:
            # Forward mode: wait until window completes
            start = self._forward_start_ts or req_ts
            sec = self._win.size_seconds
            sam = self._win.size_samples
            sec_done = (sec is not None) and ((now_ts - start) >= max(0.0, float(sec)))
            sam_done = False
            if sam is not None:
                needed = int(sam)
                # Check each channel has enough samples since start
                enough = True
                for ch in self._channels:
                    buf = self._buffers.get(ch.alias) or deque()
                    cnt = sum(1 for (t, _) in buf if t >= start)
                    if cnt < needed:
                        enough = False
                        break
                sam_done = enough
            # Prefer seconds if provided; else use samples
            done = sec_done if (sec is not None) else sam_done
            if done:
                # Ready to emit
                self._emit_snapshot(now_ts, start_ts=start)
                self._pending_request = None
                self._forward_start_ts = None

    def _emit_snapshot(self, now_ts: float, start_ts: Optional[float] = None) -> None:
        vals: Dict[str, float] = {}
        u: Dict[str, str] = {}
        # Determine which aliases to snapshot: configured ones or dynamic set from buffers
        aliases: List[str]
        if self._dynamic_mode:
            aliases = list(self._buffers.keys())
        else:
            aliases = [ch.alias for ch in self._channels if ch.enabled]
        # Build series per alias based on capture mode
        for alias in aliases:
            buf = self._buffers.get(alias) or deque()
            series_vals: List[float] = []
            series_ts: List[float] = []
            if start_ts is None:
                # Backward: use trimmed buffer as of now
                series_ts = [t for (t, _) in buf]
                series_vals = [v for (_, v) in buf]
            else:
                # Forward: use samples with t >= start_ts and within window seconds if specified
                sec = self._win.size_seconds
                if sec is not None:
                    end_cut = start_ts + max(0.0, float(sec))
                    series_vals = [v for (t, v) in buf if start_ts <= t <= end_cut]
                    series_ts = [t for (t, _) in buf if start_ts <= t <= end_cut]
                else:
                    series_vals = [v for (t, v) in buf if t >= start_ts]
                    series_ts = [t for (t, _) in buf if t >= start_ts]
            # Readiness check based on configured dimension
            if self._win.size_seconds is not None:
                ready = False
                n_ts = len(series_ts)
                if n_ts >= 2:
                    duration = series_ts[-1] - series_ts[0]
                    # Option A: tolerance of approximately one sample period
                    dt_est = duration / float(n_ts - 1) if (n_ts - 1) > 0 else 0.0
                    required = max(0.0, float(self._win.size_seconds) - dt_est)
                    ready = duration >= required
                if not ready:
                    continue
            elif self._win.size_samples is not None:
                if len(series_vals) < int(self._win.size_samples or 0):
                    continue
            else:
                # If neither provided, require at least 1 sample
                if not series_vals:
                    continue
            ch_cfg = self._find_channel_cfg(alias)
            stats = self._compute_stats(series_vals, self._metrics_for_cfg(ch_cfg))
            for key, val in stats.items():
                out_alias = f"{alias}_{key}"
                vals[out_alias] = val
                u[out_alias] = self._source_units.get(alias, "")
        if not vals:
            if self._win.notify_on_skip:
                reason = "insufficient_window"
                self._events.append({"type": "stats_skip", "reason": reason, "ts": now_ts})
            return
        self._ready_vals = vals
        self._ready_units = u
        # Emit a snapshot event with timestamp
        self._events.append({"type": "stats_snapshot", "ts": now_ts})

    def _find_channel_cfg(self, alias: str) -> Optional[ChannelConfig]:
        for ch in self._channels:
            if ch.alias == alias:
                return ch
        return None

    def _metrics_for_cfg(self, ch: Optional[ChannelConfig]) -> List[str]:
        if ch is None:
            return self._default_metrics
        return ch.stats if ch.stats else self._default_metrics

    def aliases(self) -> Set[str]:
        out: Set[str] = set()
        # If dynamic mode, we cannot reliably declare aliases ahead of time
        for ch in self._channels:
            if not ch.enabled:
                continue
            for s in self._metrics_for_cfg(ch):
                out.add(f"{ch.alias}_{s}")
        return out

    def units(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for ch in self._channels:
            if not ch.enabled:
                continue
            src_unit = self._source_units.get(ch.alias, "")
            for s in self._metrics_for_cfg(ch):
                mapping[f"{ch.alias}_{s}"] = src_unit
        return mapping

    def _compare(self, val: float, thr: float) -> bool:
        cmp = self._trig_comparator
        if cmp == ">=":
            return val >= thr
        if cmp == "<":
            return val < thr
        if cmp == "<=":
            return val <= thr
        # default '>'
        return val > thr


