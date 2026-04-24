# Author: T. Onkst | Date: 04212026

from __future__ import annotations

import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, TYPE_CHECKING

from ._nidaq_scaling import apply_scaling, convert_temp_unit

if TYPE_CHECKING:
    from .ni_daq import NiDAQPlugin


def _read_fast_ai(p: NiDAQPlugin) -> Dict[str, Any]:
    """Copy latest values from fast AI reader threads (non-blocking)."""
    vals: Dict[str, Any] = {}
    any_success = False
    n = max(1, p._oversample_factor)

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
            rec_rate = float(p._sim_rate_hz) if p._sim_rate_hz > 0 else 10.0
            margin = float(p._read_timeout_margin_s)
            fast_rate = max(1.0, float(p._fast_rate) or 1.0)
            timeout_fast = max((n / fast_rate) + margin, 2.5 / max(1.0, rec_rate))
            any_success = _read_legacy_fast_ai(p, vals, n, timeout_fast)

    if any_success:
        try:
            p._health["last_good_read_ts"] = time.time()
            p._health["consec_failures"] = 0
            p._health["last_error"] = ""
        except Exception:
            pass
    return vals


def _read_one_temp_task(
    tt: Dict[str, Any],
    timeout_temp: float,
    temp_unit_map: Dict[str, str],
) -> Dict[str, Any]:
    """Read a single temperature DAQmx task (thread-safe, no shared state).

    For hw-timed tasks: read all available samples from the buffer and take the
    last (most recent) value per channel.
    For on-demand tasks: request exactly 1 sample (triggers ADC conversion).
    """
    vals: Dict[str, Any] = {}
    task = tt.get("task")
    aliases = list(tt.get("aliases", []) or [])
    if task is None or not aliases:
        return vals

    hw_timed = bool(tt.get("hw_timed", False))

    try:
        if hw_timed:
            try:
                avail = int(getattr(task.in_stream, "avail_samp_per_chan", 0))
            except Exception:
                avail = 0
            if avail < 1:
                return vals
            temp_samples = task.read(
                number_of_samples_per_channel=avail,
                timeout=0.0,
            )
        else:
            temp_samples = task.read(
                number_of_samples_per_channel=1,
                timeout=timeout_temp,
            )

        if isinstance(temp_samples, list) and temp_samples and isinstance(temp_samples[0], list):
            for idx, alias in enumerate(aliases):
                try:
                    raw_c = float(temp_samples[idx][-1])
                    vals[alias] = convert_temp_unit(raw_c, temp_unit_map.get(alias, "C"))
                except Exception:
                    continue
        elif isinstance(temp_samples, list):
            try:
                raw_c = float(temp_samples[-1] if hw_timed else temp_samples[0])
                vals[aliases[0]] = convert_temp_unit(raw_c, temp_unit_map.get(aliases[0], "C"))
            except Exception:
                pass
    except Exception:
        pass
    return vals


def _read_slow_channels(p: NiDAQPlugin) -> Dict[str, Any]:
    """Read temperature and DI channels.

    Temperature tasks are read in parallel (one thread per module) to cut
    total blocking time roughly in half when multiple TC modules are present.
    """
    vals: Dict[str, Any] = {}
    any_success = False
    rec_rate = float(p._sim_rate_hz) if p._sim_rate_hz > 0 else 10.0
    margin = float(p._read_timeout_margin_s)
    timeout_temp = max((1.0 / max(1.0, rec_rate)) + margin, 2.5 / max(1.0, rec_rate))
    timeout_di = max((1.0 / max(1.0, rec_rate)) + margin, 2.0 / max(1.0, rec_rate))

    if p._inject_fail_remaining > 0:
        p._inject_fail_remaining -= 1
        return vals

    temp_unit_map = getattr(p, "_temp_unit_map", None)
    if temp_unit_map is None:
        temp_unit_map = {}
        for ch in p._ai_temp:
            a = ch.get("alias")
            if a:
                temp_unit_map[str(a)] = str(ch.get("unit", "C"))

    if p._temp_tasks:
        valid_tasks = [tt for tt in p._temp_tasks if tt.get("task") and tt.get("aliases")]
        if len(valid_tasks) > 1:
            with ThreadPoolExecutor(max_workers=len(valid_tasks)) as pool:
                futures = {
                    pool.submit(_read_one_temp_task, tt, timeout_temp, temp_unit_map): tt
                    for tt in valid_tasks
                }
                for fut in as_completed(futures):
                    try:
                        result = fut.result()
                        if result:
                            vals.update(result)
                            any_success = True
                    except Exception:
                        pass
        elif valid_tasks:
            result = _read_one_temp_task(valid_tasks[0], timeout_temp, temp_unit_map)
            if result:
                vals.update(result)
                any_success = True

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
                    for idx, alias in enumerate(aliases):
                        if idx < len(di_vals):
                            vals[alias] = int(bool(di_vals[idx]))
                            any_success = True
                else:
                    vals[aliases[0]] = int(bool(di_vals))
                    any_success = True
                try:
                    if p._di_read_diag_count < 5:
                        if isinstance(di_vals, list) and di_vals and isinstance(di_vals[0], list):
                            shape = f"list[{len(di_vals)}x{len(di_vals[0])}]"
                            sample_preview = [int(bool(x[0])) for x in di_vals[:min(3, len(di_vals))]]
                        elif isinstance(di_vals, list):
                            shape = f"list[{len(di_vals)}]"
                            sample_preview = [int(bool(x)) for x in di_vals[:min(3, len(di_vals))]]
                        else:
                            shape = "scalar"
                            sample_preview = [int(bool(di_vals))]
                        print(f"[NIDAQ] DI read diag: device={device} shape={shape} aliases={aliases[:3]} values={sample_preview}")
                        p._di_read_diag_count += 1
                except Exception:
                    pass
            except Exception:
                pass

    if any_success:
        try:
            p._health["last_good_read_ts"] = time.time()
            p._health["consec_failures"] = 0
            p._health["last_error"] = ""
        except Exception:
            pass
    return vals


def read_real(p: NiDAQPlugin) -> Dict[str, Any]:
    """Full read of all channel types (used by legacy callers and sim fallback)."""
    vals = _read_fast_ai(p)
    vals.update(_read_slow_channels(p))
    for alias, state in p._do_states.items():
        vals[alias] = int(state)
    for alias, state in p._ao_states.items():
        vals[alias] = float(state)
    return vals


def _read_threaded_fast_ai(p: NiDAQPlugin, vals: Dict[str, Any], n: int) -> bool:
    if not p._fast_path_printed:
        try:
            ft_type = getattr(p, "_filter_type", "average")
            print(f"[NIDAQ] read: using THREADED fast-AI path (filter={ft_type})")
        except Exception:
            pass
        p._fast_path_printed = True

    filter_type = getattr(p, "_filter_type", "average")

    if filter_type == "butterworth":
        return _read_threaded_butterworth(p, vals)
    return _read_threaded_deque(p, vals, n)


def _read_threaded_butterworth(p: NiDAQPlugin, vals: Dict[str, Any]) -> bool:
    """Copy pre-computed filtered+scaled values from fast reader threads."""
    any_success = False
    for ft in p._fast_reader_threads:
        try:
            filt_vals = ft.get("filtered_values")
            if not filt_vals:
                continue
            lock = ft.get("lock")
            if lock is not None:
                lock.acquire()
            try:
                snapshot = dict(filt_vals)
            finally:
                if lock is not None:
                    lock.release()
            if snapshot:
                vals.update(snapshot)
                any_success = True
        except Exception:
            pass
    try:
        c = int(getattr(p, "_thr_vals_diag_count", 0))
        if c < 5:
            print(f"[NIDAQ] read(bw): copied {len(vals)} alias(es)")
            setattr(p, "_thr_vals_diag_count", c + 1)
    except Exception:
        pass
    return any_success


def _read_threaded_deque(p: NiDAQPlugin, vals: Dict[str, Any], n: int) -> bool:
    """Legacy path: iterate deques to compute averages under lock."""
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
                    vals[alias] = apply_scaling(avg, ch.get("scaling") or {})
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
                    vals[alias] = apply_scaling(avg, ch.get("scaling") or {})
                any_success = True
            elif isinstance(samples, list):
                take = samples[-n:] if len(samples) >= n else samples
                avg = sum(take) / float(len(take) or 1)
                alias = aliases[0]
                ch = alias_to_cfg.get(alias, {})
                vals[alias] = apply_scaling(avg, ch.get("scaling") or {})
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
    """Start decoupled snapshot workers.

    Fast path: copies AI voltage from reader threads + DO/AO states using a
    monotonic deadline loop for precise tick-rate cadence.

    Slow path: reads temperature and DI as fast as the hardware can sustain
    (no artificial sleep) and caches results for the fast path to merge.
    """
    try:
        if p._snapshot_thread is not None and getattr(p._snapshot_thread, "is_alive", lambda: False)():
            return
        p._snapshot_stop.clear()

        slow_vals: Dict[str, Any] = {}
        slow_lock = threading.Lock()

        def _slow_loop() -> None:
            """Continuously read temp + DI at hardware's natural rate."""
            _polls = 0
            _data_reads = 0
            _last_diag_ts = time.monotonic()
            while not p._snapshot_stop.is_set():
                try:
                    new_slow = _read_slow_channels(p)
                    _polls += 1
                    if new_slow:
                        with slow_lock:
                            slow_vals.update(new_slow)
                        _data_reads += 1
                except Exception:
                    pass
                now = time.monotonic()
                if now - _last_diag_ts >= 5.0:
                    try:
                        elapsed = max(0.001, now - _last_diag_ts)
                        data_hz = _data_reads / elapsed
                        print(f"[NIDAQ] slow loop: {_data_reads} data updates "
                              f"in 5s ({data_hz:.1f} Hz), "
                              f"{_polls} polls, "
                              f"{len(slow_vals)} ch cached")
                    except Exception:
                        pass
                    _polls = 0
                    _data_reads = 0
                    _last_diag_ts = now
                p._snapshot_stop.wait(0.001)

        def _fast_loop() -> None:
            """Copy fast AI + slow cache + DO/AO into snapshot at tick rate."""
            period = p._snapshot_period_s
            deadline = time.monotonic() + period
            _ticks = 0
            _last_diag_ts = time.monotonic()
            while not p._snapshot_stop.is_set():
                try:
                    vals: Dict[str, Any] = {}
                    fast = _read_fast_ai(p)
                    vals.update(fast)

                    with slow_lock:
                        vals.update(slow_vals)

                    for alias, state in p._do_states.items():
                        vals[alias] = int(state)
                    for alias, state in p._ao_states.items():
                        vals[alias] = float(state)

                    p._append_health_channels(vals)
                    with p._snapshot_lock:
                        p._snapshot_values = dict(vals)
                    _ticks += 1
                except Exception:
                    pass

                now = time.monotonic()
                if now - _last_diag_ts >= 5.0:
                    try:
                        hz = _ticks / max(0.001, now - _last_diag_ts)
                        print(f"[NIDAQ] fast loop: {_ticks} snapshots in 5s "
                              f"({hz:.1f} Hz), {len(fast)} fast + "
                              f"{len(slow_vals)} slow cached")
                    except Exception:
                        pass
                    _ticks = 0
                    _last_diag_ts = now

                sleep_s = max(0.0, deadline - time.monotonic())
                if sleep_s > 0:
                    p._snapshot_stop.wait(sleep_s)
                now_m = time.monotonic()
                if now_m > deadline + period:
                    deadline = now_m + period
                else:
                    deadline += period

        has_slow = bool(p._temp_tasks or p._di_tasks)
        if has_slow:
            t_slow = threading.Thread(target=_slow_loop, daemon=True)
            t_slow.start()
            try:
                print("[NIDAQ] slow channel reader thread started (temp + DI, no sleep)")
            except Exception:
                pass

        t_fast = threading.Thread(target=_fast_loop, daemon=True)
        t_fast.start()
        p._snapshot_thread = t_fast
        try:
            print(f"[NIDAQ] snapshot worker started (decoupled={has_slow}, "
                  f"fast_period={p._snapshot_period_s*1000:.0f}ms)")
        except Exception:
            pass
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
