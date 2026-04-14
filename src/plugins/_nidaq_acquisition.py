# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import time
import threading
from typing import Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .ni_daq import NiDAQPlugin


def read_real(p: NiDAQPlugin) -> Dict[str, Any]:
    vals: Dict[str, Any] = {}
    any_success = False
    try:
        if p._inject_fail_remaining > 0:
            p._inject_fail_remaining -= 1
            raise RuntimeError("Injected NI_DAQ read failure (test)")
        try:
            rec_rate = float(p.config.get("recording_rate_hz", 10.0))
        except Exception:
            rec_rate = 10.0
        n = max(1, p._oversample_factor)
        margin = float(p._read_timeout_margin_s)
        fast_rate = max(1.0, float(p._fast_rate) or 1.0)
        timeout_fast = max((n / fast_rate) + margin, 2.5 / max(1.0, rec_rate))
        timeout_temp = max((1.0 / max(1.0, rec_rate)) + margin, 2.5 / max(1.0, rec_rate))
        timeout_di = max((1.0 / max(1.0, rec_rate)) + margin, 2.0 / max(1.0, rec_rate))

        if p._fast_tasks:
            if p._threaded_fast_ai and p._fast_reader_threads:
                any_success = _read_threaded_fast_ai(p, vals, n)
            else:
                if not p._fast_path_printed:
                    try:
                        print("[NIDAQ] read: using LEGACY fast-AI path")
                    except Exception:
                        pass
                    p._fast_path_printed = True
                any_success = _read_legacy_fast_ai(p, vals, n, timeout_fast)

        if p._temp_tasks:
            for tt in p._temp_tasks:
                task = tt.get("task")
                aliases = list(tt.get("aliases", []) or [])
                if task is None or not aliases:
                    continue
                try:
                    temp_samples = task.read(number_of_samples_per_channel=1, timeout=timeout_temp)
                    if isinstance(temp_samples, list) and temp_samples and isinstance(temp_samples[0], list):
                        for idx, alias in enumerate(aliases):
                            try:
                                vals[alias] = float(temp_samples[idx][0])
                                any_success = True
                            except Exception:
                                continue
                    elif isinstance(temp_samples, list):
                        try:
                            vals[aliases[0]] = float(temp_samples[0])
                            any_success = True
                        except Exception:
                            pass
                except Exception:
                    pass

        if p._di_tasks:
            for dt in p._di_tasks:
                task = dt.get("task")
                aliases = list(dt.get("aliases", []) or [])
                device = str(dt.get("device", ""))
                if task is None or not aliases:
                    continue
                try:
                    di_vals = task.read(number_of_samples_per_channel=1, timeout=timeout_di)
                    if isinstance(di_vals, list) and di_vals and isinstance(di_vals[0], list):
                        for idx, alias in enumerate(aliases):
                            v = di_vals[idx][0]
                            vals[alias] = int(bool(v))
                            any_success = True
                    elif isinstance(di_vals, list):
                        vals[aliases[0]] = int(bool(di_vals[0]))
                        any_success = True
                    try:
                        if p._di_read_diag_count < 5:
                            shape = f"list[{len(di_vals)}]"
                            if isinstance(di_vals, list) and di_vals and isinstance(di_vals[0], list):
                                shape = f"list[{len(di_vals)}x{len(di_vals[0])}]"
                            sample_preview = []
                            if isinstance(di_vals, list):
                                if di_vals and isinstance(di_vals[0], list):
                                    sample_preview = [int(bool(x[0])) for x in di_vals[:min(3, len(di_vals))]]
                                else:
                                    sample_preview = [int(bool(di_vals[0]))]
                            print(f"[NIDAQ] DI read diag: device={device} shape={shape} aliases={aliases[:3]} values={sample_preview}")
                            p._di_read_diag_count += 1
                    except Exception:
                        pass
                except Exception:
                    pass

        for alias, state in p._do_states.items():
            vals[alias] = int(state)
        for alias, state in p._ao_states.items():
            vals[alias] = float(state)

        try:
            now = time.time()
            if any_success:
                p._health["last_good_read_ts"] = now
                p._health["consec_failures"] = 0
                p._health["last_error"] = ""
            else:
                if now >= float(p._fast_warmup_until or 0.0):
                    p._health["consec_failures"] = int(p._health.get("consec_failures", 0)) + 1
                    p._health["last_error"] = "read_error"
        except Exception:
            pass
    except Exception:
        try:
            now = time.time()
            if now >= float(p._fast_warmup_until or 0.0):
                p._health["consec_failures"] = int(p._health.get("consec_failures", 0)) + 1
                p._health["last_error"] = "read_error"
            try:
                print("[NIDAQ] _read_real error; consec_failures=", p._health.get("consec_failures", "?"))
            except Exception:
                pass
        except Exception:
            pass
    return vals


def _read_threaded_fast_ai(p: NiDAQPlugin, vals: Dict[str, Any], n: int) -> bool:
    if not p._fast_path_printed:
        try:
            print("[NIDAQ] read: using THREADED fast-AI path")
        except Exception:
            pass
        p._fast_path_printed = True
    any_success = False
    produced_aliases: List[str] = []
    deque_lengths: List[str] = []
    for ft in p._fast_reader_threads:
        try:
            alias_to_buf = ft.get("buffers", {}) or {}
            alias_to_cfg = None
            try:
                cb = int(getattr(p, "_thr_buf_keys_count", 0))
                if cb < 5:
                    print(f"[NIDAQ] read(thr): buffers device={ft.get('device','?')} keys={list(alias_to_buf.keys())} id={id(alias_to_buf)}")
                    setattr(p, "_thr_buf_keys_count", cb + 1)
            except Exception:
                pass
            device = str(ft.get("device", ""))
            for t in p._fast_tasks:
                if str(t.get("device", "")) == device:
                    alias_to_cfg = t.get("alias_to_cfg", {})
                    break
            lock = ft.get("lock")
            if alias_to_cfg is None:
                alias_to_cfg = {}
            if lock is not None:
                lock.acquire()
            try:
                for alias, dq in (alias_to_buf or {}).items():
                    deque_lengths.append(f"{alias}:{len(dq)}")
                    data = list(dq)[-n:] if dq else []
                    if not data:
                        continue
                    avg = sum(data) / float(len(data) or 1)
                    ch = alias_to_cfg.get(alias, {})
                    sc = ch.get("scaling") or {}
                    m = float(sc.get("m", 1.0)); b = float(sc.get("b", 0.0))
                    vals[alias] = m * avg + b
                    produced_aliases.append(alias)
                    any_success = True
            finally:
                if lock is not None:
                    lock.release()
        except Exception:
            pass
    try:
        c = int(getattr(p, "_thr_vals_diag_count", 0))
        if c < 5:
            print(f"[NIDAQ] read(thr): produced {len(produced_aliases)} alias(es): {produced_aliases[:5]} deques={deque_lengths[:5]}")
            setattr(p, "_thr_vals_diag_count", c + 1)
    except Exception:
        pass
    return any_success


def _read_legacy_fast_ai(p: NiDAQPlugin, vals: Dict[str, Any], n: int, timeout_fast: float) -> bool:
    any_success = False
    for ft in p._fast_tasks:
        task = ft.get("task")
        aliases = ft.get("aliases", [])
        alias_to_cfg = ft.get("alias_to_cfg", {})
        device = str(ft.get("device", ""))
        if task is None or not aliases:
            continue
        try:
            t0r = time.time()
            try:
                avail_before = int(getattr(task.in_stream, "avail_samp_per_chan", 0))
            except Exception:
                avail_before = -1
            try:
                last_ts = float(p._fast_last_read_ts.get(device, t0r))
            except Exception:
                last_ts = t0r
            produced = int(max(0.0, (t0r - last_ts)) * max(1.0, p._fast_rate) + 0.5)
            read_count = max(n, produced, max(0, avail_before))
            read_count = min(read_count, int(20 * n))
            samples = task.read(number_of_samples_per_channel=int(read_count), timeout=timeout_fast)
            dt_ms = (time.time() - t0r) * 1000.0
            try:
                p._fast_last_read_ts[device] = t0r
            except Exception:
                pass
            if isinstance(samples, list) and samples and isinstance(samples[0], list):
                for idx, alias in enumerate(aliases):
                    ch_samples = samples[idx]
                    take = ch_samples[-n:] if len(ch_samples) >= n else ch_samples
                    avg = sum(take) / float(len(take) or 1)
                    ch = alias_to_cfg.get(alias, {})
                    sc = ch.get("scaling") or {}
                    m = float(sc.get("m", 1.0)); b = float(sc.get("b", 0.0))
                    vals[alias] = m * avg + b
                any_success = True
            elif isinstance(samples, list):
                take = samples[-n:] if len(samples) >= n else samples
                avg = sum(take) / float(len(take) or 1)
                alias = aliases[0]
                ch = alias_to_cfg.get(alias, {})
                sc = ch.get("scaling") or {}
                m = float(sc.get("m", 1.0)); b = float(sc.get("b", 0.0))
                vals[alias] = m * avg + b
                any_success = True
            try:
                cnt = int(p._fast_diag_counts.get(device, 0))
                if cnt < 20:
                    print(f"[NIDAQ] AI_V read diag: device={device} dt_ms={dt_ms:.1f} read_count={int(read_count)} timeout={timeout_fast:.3f} avail_before={avail_before}")
                    p._fast_diag_counts[device] = cnt + 1
            except Exception:
                pass
        except Exception as e:
            try:
                dt_ms = (time.time() - t0r) * 1000.0
            except Exception:
                dt_ms = 0.0
            try:
                ec = int(p._fast_err_counts.get(device, 0))
                if ec < 10:
                    try:
                        from nidaqmx.errors import DaqError  # type: ignore
                    except Exception:
                        DaqError = None  # type: ignore
                    err_code = getattr(e, "error_code", None)
                    err_msg = str(e)
                    try:
                        avail_now = int(getattr(task.in_stream, "avail_samp_per_chan", 0))
                    except Exception:
                        avail_now = -1
                    if DaqError is not None and isinstance(e, DaqError):
                        print(f"[NIDAQ] AI_V read error: device={device} dt_ms={dt_ms:.1f} code={err_code} timeout={timeout_fast:.3f} avail_before={avail_before} avail_now={avail_now} msg={err_msg}")
                    else:
                        print(f"[NIDAQ] AI_V read error: device={device} dt_ms={dt_ms:.1f} timeout={timeout_fast:.3f} avail_before={avail_before} avail_now={avail_now} msg={err_msg}")
                    p._fast_err_counts[device] = ec + 1
                cnt = int(p._fast_diag_counts.get(device, 0))
                if cnt < 20:
                    print(f"[NIDAQ] AI_V read diag: device={device} dt_ms={dt_ms:.1f} ERROR (timeout={timeout_fast:.3f})")
                    p._fast_diag_counts[device] = cnt + 1
            except Exception:
                pass
    return any_success


def start_snapshot_worker(p: NiDAQPlugin) -> None:
    try:
        if p._snapshot_thread is not None and getattr(p._snapshot_thread, "is_alive", lambda: False)():
            return
        p._snapshot_stop.clear()

        def _loop() -> None:
            while not p._snapshot_stop.is_set():
                try:
                    vals = read_real(p)
                except Exception:
                    vals = {}
                p._append_health_channels(vals)
                with p._snapshot_lock:
                    p._snapshot_values = dict(vals)
                p._snapshot_stop.wait(p._snapshot_period_s)

        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        p._snapshot_thread = t
    except Exception:
        p._snapshot_thread = None


def stop_snapshot_worker(p: NiDAQPlugin) -> None:
    try:
        p._snapshot_stop.set()
        if p._snapshot_thread is not None:
            p._snapshot_thread.join(timeout=1.0)
    except Exception:
        pass
    p._snapshot_thread = None
