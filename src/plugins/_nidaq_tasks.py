# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import time
import threading
from collections import defaultdict, deque
from typing import Dict, Any, List, TYPE_CHECKING

from ._nidaq_scaling import IIRFilter, apply_scaling

if TYPE_CHECKING:
    from .ni_daq import NiDAQPlugin


def create_tasks_real(p: NiDAQPlugin) -> None:
    from nidaqmx import Task  # type: ignore
    from nidaqmx.constants import AcquisitionType  # type: ignore
    teardown_tasks(p)
    p._ai_fast_aliases = []
    p._ai_temp_aliases = []
    p._di_aliases = []
    rec_rate = float(p._sim_rate_hz) if p._sim_rate_hz > 0 else 10.0
    fast_rate = max(1.0, rec_rate * float(p._oversample_factor))

    _create_fast_ai_tasks(p, Task, AcquisitionType, fast_rate)
    _create_temp_tasks(p, Task)
    _create_di_tasks(p, Task)
    _create_do_tasks(p, Task)
    _create_ao_tasks(p, Task)


def _create_fast_ai_tasks(p: NiDAQPlugin, Task: Any, AcquisitionType: Any, fast_rate: float) -> None:
    enabled_ai = [ch for ch in p._ai_voltage if bool(ch.get("enabled", True))]
    if not enabled_ai:
        return
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ch in enabled_ai:
        phys = str(ch.get("phys", ""))
        if not phys:
            continue
        device = phys.split("/", 1)[0]
        groups[device].append(ch)
    p._fast_tasks = []
    p._fast_rate = fast_rate
    for device, chans in groups.items():
        t = None
        try:
            t = Task()
            local_aliases: List[str] = []
            alias_to_cfg: Dict[str, Dict[str, Any]] = {}
            for ch in chans:
                phys = str(ch.get("phys", ""))
                if not phys:
                    continue
                try:
                    rng = ch.get("range_v", {}) or {}
                    vmin = float(rng.get("min", -10.0))
                    vmax = float(rng.get("max", 10.0))
                    t.ai_channels.add_ai_voltage_chan(phys, min_val=vmin, max_val=vmax)
                    alias = str(ch.get("alias", phys))
                    local_aliases.append(alias)
                    alias_to_cfg[alias] = ch
                    try:
                        print(f"[NIDAQ] AI_V add: device={device} phys={phys} alias={alias} vmin={vmin} vmax={vmax}")
                    except Exception:
                        pass
                except Exception:
                    continue
            if local_aliases:
                try:
                    t.timing.cfg_samp_clk_timing(
                        rate=fast_rate,
                        sample_mode=AcquisitionType.CONTINUOUS,
                        samps_per_chan=int(max(1, 2 * int(fast_rate)))
                    )
                    try:
                        t.in_stream.input_buf_size = int(max(1, 10 * int(fast_rate)))
                        buf_sz = int(max(1, 10 * int(fast_rate)))
                    except Exception:
                        buf_sz = int(max(1, 2 * int(fast_rate)))
                    print(f"[NIDAQ] AI_V timing: device={device} rate={fast_rate} samps_per_chan={int(max(1, 2 * int(fast_rate)))} buf={buf_sz}")
                except Exception as e:
                    try:
                        print(f"[NIDAQ] AI_V timing error: device={device} {e}")
                    except Exception:
                        pass
                    raise
                try:
                    t.start()
                    print(f"[NIDAQ] AI_V task started: device={device}")
                except Exception as e:
                    try:
                        print(f"[NIDAQ] AI_V start error: device={device} {e}")
                    except Exception:
                        pass
                    raise
                p._fast_tasks.append({"task": t, "device": device, "aliases": local_aliases, "alias_to_cfg": alias_to_cfg})
                try:
                    p._fast_diag_counts[device] = 0
                    p._fast_err_counts[device] = 0
                    p._fast_last_read_ts[device] = time.time()
                except Exception:
                    pass
                t = None
        finally:
            try:
                if t is not None:
                    t.close()
            except Exception:
                pass
    try:
        p._fast_warmup_until = time.time() + (max(1, p._oversample_factor) / max(1.0, p._fast_rate)) + 0.05
    except Exception:
        p._fast_warmup_until = 0.0


def _create_temp_tasks(p: NiDAQPlugin, Task: Any) -> None:
    try:
        enabled_ai_temp = [
            str(ch.get("alias", ch.get("phys", "")))
            for ch in p._ai_temp
            if bool(ch.get("enabled", True))
        ]
        if enabled_ai_temp:
            try:
                print(f"[NIDAQ] AI_T enabled aliases: {enabled_ai_temp}")
            except Exception:
                pass
    except Exception:
        pass
    enabled_temp = [ch for ch in p._ai_temp if bool(ch.get("enabled", True))]
    if not enabled_temp:
        return
    groups_t: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ch in enabled_temp:
        phys = str(ch.get("phys", ""))
        if not phys:
            continue
        device = phys.split("/", 1)[0]
        groups_t[device].append(ch)
    p._temp_tasks = []
    for device, chans in groups_t.items():
        t = None
        try:
            t = Task()
            local_aliases: List[str] = []
            try:
                from nidaqmx.constants import (
                    ThermocoupleType,
                    TemperatureUnits,
                    CJCSource,
                    RTDType,
                    ResistanceConfiguration,
                    ExcitationSource,
                )  # type: ignore
            except Exception:
                ThermocoupleType = TemperatureUnits = CJCSource = RTDType = ResistanceConfiguration = ExcitationSource = None  # type: ignore
            for ch in chans:
                phys = str(ch.get("phys", ""))
                if not phys:
                    continue
                sensor = ch.get("sensor", {}) or {}
                stype = str(sensor.get("type", "TC")).upper()
                try:
                    print(f"[NIDAQ] AI_T add attempt: device={device} phys={phys} type={stype} sensor={sensor}")
                except Exception:
                    pass
                try:
                    if (
                        stype == "RTD"
                        and RTDType is not None
                        and TemperatureUnits is not None
                        and ResistanceConfiguration is not None
                    ):
                        subtype = str(sensor.get("subtype", "PT100")).upper()
                        wires = int(sensor.get("wires", 3))
                        try:
                            rtd_enum_map = {m.name: m for m in RTDType}
                        except Exception:
                            rtd_enum_map = {}
                        rtd_type = rtd_enum_map.get(subtype) or rtd_enum_map.get("PT100") or (next(iter(rtd_enum_map.values())) if rtd_enum_map else None)
                        wire_cfg_map = {
                            2: ResistanceConfiguration.TWO_WIRE,
                            3: ResistanceConfiguration.THREE_WIRE,
                            4: ResistanceConfiguration.FOUR_WIRE,
                        }
                        cfg = wire_cfg_map.get(wires, ResistanceConfiguration.THREE_WIRE)
                        if rtd_type is not None:
                            excit_current = float(sensor.get("excitation_current_a", 0.001))
                            if ExcitationSource is not None:
                                t.ai_channels.add_ai_rtd_chan(
                                    phys,
                                    rtd_type=rtd_type,
                                    resistance_config=cfg,
                                    units=TemperatureUnits.DEG_C,
                                    current_excit_source=ExcitationSource.INTERNAL,
                                    current_excit_val=excit_current,
                                )
                            else:
                                t.ai_channels.add_ai_rtd_chan(
                                    phys,
                                    rtd_type=rtd_type,
                                    resistance_config=cfg,
                                    units=TemperatureUnits.DEG_C,
                                )
                        else:
                            t.ai_channels.add_ai_voltage_chan(phys, min_val=-1.0, max_val=1.0)
                    else:
                        tc_sub = str(sensor.get("subtype", "K")).upper()
                        tc_map = {}
                        if ThermocoupleType is not None:
                            try:
                                tc_map = {k.name: k for k in ThermocoupleType}
                            except Exception:
                                tc_map = {}
                        tc_enum = tc_map.get(tc_sub)
                        if tc_enum is not None and TemperatureUnits is not None and CJCSource is not None:
                            t.ai_channels.add_ai_thrmcpl_chan(
                                phys,
                                thermocouple_type=tc_enum,
                                units=TemperatureUnits.DEG_C,
                                cjc_source=CJCSource.BUILT_IN,
                            )
                        else:
                            t.ai_channels.add_ai_voltage_chan(phys, min_val=-1.0, max_val=1.0)
                    local_aliases.append(str(ch.get("alias", phys)))
                except Exception as e:
                    try:
                        print(f"[NIDAQ] AI_T add error: device={device} phys={phys} err={e}")
                    except Exception:
                        pass
            if local_aliases:
                try:
                    print(f"[NIDAQ] AI_T on-demand: device={device} channels={len(local_aliases)}")
                except Exception:
                    pass
                p._temp_tasks.append({"task": t, "device": device, "aliases": local_aliases})
                t = None
        finally:
            try:
                if t is not None:
                    t.close()
            except Exception:
                pass


def _create_di_tasks(p: NiDAQPlugin, Task: Any) -> None:
    enabled_di = [ch for ch in p._di if bool(ch.get("enabled", True))]
    if not enabled_di:
        return
    groups_di: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ch in enabled_di:
        phys = str(ch.get("phys", ""))
        if not phys:
            continue
        device = phys.split("/", 1)[0]
        groups_di[device].append(ch)
    p._di_tasks = []
    for device, chans in groups_di.items():
        t = None
        try:
            t = Task()
            local_aliases: List[str] = []
            try:
                print(f"[NIDAQ] DI create: device={device} lines={len(chans)}")
            except Exception:
                pass
            for ch in chans:
                phys = str(ch.get("phys", ""))
                if not phys:
                    continue
                try:
                    t.di_channels.add_di_chan(phys)
                    local_aliases.append(str(ch.get("alias", phys)))
                except Exception:
                    continue
            if local_aliases:
                t.start()
                try:
                    print(f"[NIDAQ] DI task started: device={device} lines={len(local_aliases)}")
                except Exception:
                    pass
                p._di_tasks.append({"task": t, "device": device, "aliases": local_aliases})
                t = None
        finally:
            try:
                if t is not None:
                    t.close()
            except Exception:
                pass


def _create_do_tasks(p: NiDAQPlugin, Task: Any) -> None:
    enabled_do = [ch for ch in p._do if bool(ch.get("enabled", True))]
    if not enabled_do:
        return
    groups_do: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ch in enabled_do:
        phys = str(ch.get("phys", ""))
        if not phys:
            continue
        device = phys.split("/", 1)[0]
        groups_do[device].append(ch)
    p._do_tasks = []
    for device, chans in groups_do.items():
        t = None
        try:
            t = Task()
            local_aliases: List[str] = []
            for ch in chans:
                phys = str(ch.get("phys", ""))
                if not phys:
                    continue
                try:
                    t.do_channels.add_do_chan(phys)
                    local_aliases.append(str(ch.get("alias", phys)))
                except Exception:
                    continue
            if local_aliases:
                t.start()
                p._do_tasks.append({"task": t, "device": device, "aliases": local_aliases})
                try:
                    write_do_hardware(p._do_tasks, p._do_states)
                except Exception:
                    pass
                t = None
        finally:
            try:
                if t is not None:
                    t.close()
            except Exception:
                pass


def _create_ao_tasks(p: NiDAQPlugin, Task: Any) -> None:
    enabled_ao = [ch for ch in p._ao if bool(ch.get("enabled", True))]
    if not enabled_ao:
        return
    groups_ao: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ch in enabled_ao:
        phys = str(ch.get("phys", ""))
        if not phys:
            continue
        device = phys.split("/", 1)[0]
        groups_ao[device].append(ch)
    p._ao_tasks = []
    for device, chans in groups_ao.items():
        t = None
        try:
            t = Task()
            local_aliases: List[str] = []
            for ch in chans:
                phys = str(ch.get("phys", ""))
                if not phys:
                    continue
                try:
                    rng = ch.get("range_v", {}) or {}
                    vmin = float(rng.get("min", 0.0))
                    vmax = float(rng.get("max", 10.0))
                    t.ao_channels.add_ao_voltage_chan(phys, min_val=vmin, max_val=vmax)
                    local_aliases.append(str(ch.get("alias", phys)))
                except Exception:
                    continue
            if local_aliases:
                t.start()
                p._ao_tasks.append({"task": t, "device": device, "aliases": local_aliases})
                try:
                    write_ao_hardware(p._ao_tasks, p._ao_states)
                except Exception:
                    pass
                t = None
        finally:
            try:
                if t is not None:
                    t.close()
            except Exception:
                pass


def teardown_tasks(p: NiDAQPlugin) -> None:
    try:
        stop_fast_reader_threads(p)
        for ft in p._fast_tasks or []:
            t = ft.get("task")
            try:
                if t is not None:
                    t.stop(); t.close()
            except Exception:
                pass
    except Exception:
        pass
    for t in (p._task_ai_fast, p._task_ai_temp, p._task_di, p._task_do, p._task_ao):
        try:
            if t is not None:
                t.stop()
                t.close()
        except Exception:
            pass
    try:
        for tt in p._temp_tasks or []:
            t = tt.get("task")
            try:
                if t is not None:
                    t.stop(); t.close()
            except Exception:
                pass
    except Exception:
        pass
    try:
        for dt in p._di_tasks or []:
            t = dt.get("task")
            try:
                if t is not None:
                    t.stop(); t.close()
            except Exception:
                pass
    except Exception:
        pass
    try:
        for d0 in p._do_tasks or []:
            t = d0.get("task")
            try:
                if t is not None:
                    t.stop(); t.close()
            except Exception:
                pass
    except Exception:
        pass
    try:
        for ao in p._ao_tasks or []:
            t = ao.get("task")
            try:
                if t is not None:
                    t.stop(); t.close()
            except Exception:
                pass
    except Exception:
        pass
    p._task_ai_fast = None
    p._task_ai_temp = None
    p._task_di = None
    p._task_do = None
    p._task_ao = None
    p._fast_tasks = []
    p._temp_tasks = []
    p._di_tasks = []
    p._do_tasks = []
    p._ao_tasks = []


def write_do_hardware(
    do_tasks: List[Dict[str, Any]],
    do_states: Dict[str, int],
) -> None:
    if not do_tasks:
        return
    try:
        for dt in do_tasks:
            task = dt.get("task")
            aliases = list(dt.get("aliases", []) or [])
            if task is None or not aliases:
                continue
            values = [int(bool(do_states.get(alias, 0))) for alias in aliases]
            task.write(values, auto_start=True)
    except Exception:
        pass


def write_ao_hardware(
    ao_tasks: List[Dict[str, Any]],
    ao_states: Dict[str, float],
) -> None:
    if not ao_tasks:
        return
    try:
        for at in ao_tasks:
            task = at.get("task")
            aliases = list(at.get("aliases", []) or [])
            if task is None or not aliases:
                continue
            values = [float(ao_states.get(alias, 0.0)) for alias in aliases]
            task.write(values, auto_start=True)
    except Exception:
        pass


def start_fast_reader_threads(p: NiDAQPlugin) -> None:
    """Start per-device reader threads that continuously drain DAQmx.

    In butterworth mode, each thread applies an IIR low-pass filter per
    channel, then scaling, and writes the final float to a shared dict
    under a brief lock (no deques needed).

    In average/none mode, the legacy deque path is used.
    """
    try:
        filter_type = getattr(p, "_filter_type", "average")
        try:
            print(f"[NIDAQ] starting fast reader threads for {len(p._fast_tasks)} device(s) filter={filter_type}")
        except Exception:
            pass
        p._fast_reader_threads = []
        n = max(1, p._oversample_factor)
        core_rate = float(p._sim_rate_hz) if p._sim_rate_hz > 0 else 10.0
        bw_order = getattr(p, "_butterworth_order", 4)

        for group in p._fast_tasks or []:
            task = group.get("task")
            device = str(group.get("device", ""))
            aliases = list(group.get("aliases", []) or [])
            alias_to_cfg = group.get("alias_to_cfg", {})
            try:
                print(f"[NIDAQ] fast reader pre-spawn: device={device} aliases={len(aliases)} task_none={task is None}")
            except Exception:
                pass
            if task is None or not aliases:
                continue

            if filter_type == "butterworth":
                sample_hz = max(1.0, float(p._fast_rate))
                cutoff_hz = core_rate / 2.0
                filters: Dict[str, IIRFilter] = {}
                for alias in aliases:
                    filters[alias] = IIRFilter(bw_order, cutoff_hz, sample_hz)
                filtered_values: Dict[str, float] = {}
                state: Dict[str, Any] = {
                    "device": device,
                    "task": task,
                    "stop": threading.Event(),
                    "lock": threading.Lock(),
                    "filter_type": "butterworth",
                    "filters": filters,
                    "filtered_values": filtered_values,
                    "alias_to_cfg": alias_to_cfg,
                }
                try:
                    print(f"[NIDAQ] butterworth init: device={device} cutoff={cutoff_hz:.1f}Hz sample={sample_hz:.0f}Hz order={bw_order}")
                except Exception:
                    pass
                _spawn_butterworth_reader(p, state, n, aliases)
            else:
                state = {
                    "device": device,
                    "task": task,
                    "stop": threading.Event(),
                    "lock": threading.Lock(),
                    "filter_type": filter_type,
                    "buffers": {alias: deque(maxlen=int(5 * n)) for alias in aliases},
                }
                try:
                    print(f"[NIDAQ] deque init: device={device} keys={list(state['buffers'].keys())}")
                except Exception:
                    pass
                _spawn_deque_reader(p, state, n)

    except Exception as e:
        try:
            print(f"[NIDAQ] starting fast reader threads failed: {e}")
        except Exception:
            pass
        p._fast_reader_threads = []


def _spawn_butterworth_reader(p: NiDAQPlugin, state: Dict[str, Any], n: int, aliases: List[str]) -> None:
    """Spawn a fast reader thread that applies Butterworth filter + scaling per sample."""
    device = state["device"]
    tsk = state["task"]
    stop_ev = state["stop"]
    lk = state["lock"]
    filters = state["filters"]
    filt_vals = state["filtered_values"]
    alias_to_cfg = state["alias_to_cfg"]

    def _loop() -> None:
        margin = float(p._read_timeout_margin_s)
        fast_rate = max(1.0, float(p._fast_rate) or 1.0)
        rec_rate = float(p._sim_rate_hz) if p._sim_rate_hz > 0 else 10.0
        timeout_fast = max((n / fast_rate) + margin, 2.5 / max(1.0, rec_rate))
        last_ts = time.time()
        try:
            print(f"[NIDAQ] BW reader started: device={device} timeout={timeout_fast:.3f}")
        except Exception:
            pass
        while not stop_ev.is_set():
            try:
                avail = 0
                try:
                    avail = int(getattr(tsk.in_stream, "avail_samp_per_chan", 0))
                except Exception:
                    avail = 0
                now = time.time()
                produced = int(max(0.0, (now - last_ts)) * fast_rate + 0.5)
                read_count = max(n, produced, avail)
                read_count = min(read_count, int(100 * n))
                if read_count <= 0:
                    stop_ev.wait(0.005)
                    continue
                t0r = time.time()
                samples = tsk.read(number_of_samples_per_channel=int(read_count), timeout=timeout_fast)
                dt_ms = (time.time() - t0r) * 1000.0
                last_ts = now

                local: Dict[str, float] = {}
                if isinstance(samples, list) and samples and isinstance(samples[0], list):
                    for idx, alias in enumerate(aliases):
                        ch_samples = samples[idx] if idx < len(samples) else []
                        if not ch_samples:
                            continue
                        filtered = filters[alias].process_batch(ch_samples)
                        ch_cfg = alias_to_cfg.get(alias, {})
                        local[alias] = apply_scaling(filtered, ch_cfg.get("scaling") or {})
                else:
                    alias = aliases[0]
                    if samples:
                        raw_list = samples if isinstance(samples, list) else [samples]
                        filtered = filters[alias].process_batch(raw_list)
                        ch_cfg = alias_to_cfg.get(alias, {})
                        local[alias] = apply_scaling(filtered, ch_cfg.get("scaling") or {})

                if local:
                    with lk:
                        filt_vals.update(local)

                try:
                    p._health["last_good_read_ts"] = now
                    p._health["consec_failures"] = 0
                    p._health["last_error"] = ""
                except Exception:
                    pass
                try:
                    c = int(p._fast_diag_counts.get(device, 0))
                    if c < 5:
                        print(f"[NIDAQ] BW reader read: device={device} dt_ms={dt_ms:.1f} read_count={int(read_count)} avail={avail}")
                        p._fast_diag_counts[device] = c + 1
                except Exception:
                    pass
            except Exception as e:
                try:
                    p._health["consec_failures"] = int(p._health.get("consec_failures", 0)) + 1
                    p._health["last_error"] = "read_error"
                except Exception:
                    pass
                try:
                    c = int(p._fast_err_counts.get(device, 0))
                    if c < 5:
                        err_code = getattr(e, "error_code", None)
                        try:
                            avail_now = int(getattr(tsk.in_stream, "avail_samp_per_chan", 0))
                        except Exception:
                            avail_now = -1
                        print(f"[NIDAQ] BW reader error: device={device} code={err_code} avail_now={avail_now} msg={e}")
                        p._fast_err_counts[device] = c + 1
                except Exception:
                    pass
                stop_ev.wait(0.01)

    try:
        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        state["thread"] = t
        try:
            print(f"[NIDAQ] BW reader thread spawned: device={device}")
        except Exception:
            pass
        p._fast_reader_threads.append(state)
    except Exception as e:
        try:
            import traceback as _tb
            print(f"[NIDAQ] BW reader spawn failed: device={device} err={e}\n{_tb.format_exc()}")
        except Exception:
            pass


def _spawn_deque_reader(p: NiDAQPlugin, state: Dict[str, Any], n: int) -> None:
    """Spawn a legacy fast reader thread that drains DAQmx into deques."""
    device = state["device"]
    tsk = state["task"]
    stop_ev = state["stop"]
    lk = state["lock"]
    bufs = state["buffers"]

    def _loop() -> None:
        margin = float(p._read_timeout_margin_s)
        fast_rate = max(1.0, float(p._fast_rate) or 1.0)
        rec_rate = float(p._sim_rate_hz) if p._sim_rate_hz > 0 else 10.0
        timeout_fast = max((n / fast_rate) + margin, 2.5 / max(1.0, rec_rate))
        last_ts = time.time()
        try:
            print(f"[NIDAQ] Deque reader started: device={device} timeout={timeout_fast:.3f}")
        except Exception:
            pass
        while not stop_ev.is_set():
            try:
                avail = 0
                try:
                    avail = int(getattr(tsk.in_stream, "avail_samp_per_chan", 0))
                except Exception:
                    avail = 0
                now = time.time()
                produced = int(max(0.0, (now - last_ts)) * fast_rate + 0.5)
                read_count = max(n, produced, avail)
                read_count = min(read_count, int(100 * n))
                if read_count <= 0:
                    stop_ev.wait(0.005)
                    continue
                t0r = time.time()
                samples = tsk.read(number_of_samples_per_channel=int(read_count), timeout=timeout_fast)
                dt_ms = (time.time() - t0r) * 1000.0
                last_ts = now
                if isinstance(samples, list) and samples and isinstance(samples[0], list):
                    lk.acquire()
                    try:
                        for idx, alias in enumerate(list(bufs.keys())):
                            ch_samples = samples[idx] if idx < len(samples) else []
                            for v in ch_samples:
                                bufs[alias].append(float(v))
                    finally:
                        lk.release()
                else:
                    lk.acquire()
                    try:
                        alias = list(bufs.keys())[0] if bufs else None
                        if alias is not None:
                            for v in (samples or []):
                                bufs[alias].append(float(v))
                    finally:
                        lk.release()
                try:
                    p._health["last_good_read_ts"] = now
                    p._health["consec_failures"] = 0
                    p._health["last_error"] = ""
                except Exception:
                    pass
                try:
                    c = int(p._fast_diag_counts.get(device, 0))
                    if c < 5:
                        print(f"[NIDAQ] Deque reader read: device={device} dt_ms={dt_ms:.1f} read_count={int(read_count)} avail={avail}")
                        p._fast_diag_counts[device] = c + 1
                except Exception:
                    pass
            except Exception as e:
                try:
                    p._health["consec_failures"] = int(p._health.get("consec_failures", 0)) + 1
                    p._health["last_error"] = "read_error"
                except Exception:
                    pass
                try:
                    c = int(p._fast_err_counts.get(device, 0))
                    if c < 5:
                        err_code = getattr(e, "error_code", None)
                        try:
                            avail_now = int(getattr(tsk.in_stream, "avail_samp_per_chan", 0))
                        except Exception:
                            avail_now = -1
                        print(f"[NIDAQ] Deque reader error: device={device} code={err_code} avail_now={avail_now} msg={e}")
                        p._fast_err_counts[device] = c + 1
                except Exception:
                    pass
                stop_ev.wait(0.01)

    try:
        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        state["thread"] = t
        try:
            print(f"[NIDAQ] Deque reader thread spawned: device={device}")
        except Exception:
            pass
        p._fast_reader_threads.append(state)
    except Exception as e:
        try:
            import traceback as _tb
            print(f"[NIDAQ] Deque reader spawn failed: device={device} err={e}\n{_tb.format_exc()}")
        except Exception:
            pass


def stop_fast_reader_threads(p: NiDAQPlugin) -> None:
    try:
        for st in p._fast_reader_threads or []:
            try:
                ev = st.get("stop")
                if ev is not None:
                    ev.set()
            except Exception:
                pass
            try:
                th = st.get("thread")
                if th is not None:
                    th.join(timeout=0.5)
            except Exception:
                pass
    except Exception:
        pass
    p._fast_reader_threads = []
