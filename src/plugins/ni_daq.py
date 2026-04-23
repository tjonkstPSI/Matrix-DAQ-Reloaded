# Author: T. Onkst | Date: 03092026

from __future__ import annotations

from typing import Dict, Any, Set, List
import threading
import time

from .base import BasePlugin, PluginStatus
from ._nidaq_discovery import (
    nidaq_available,
    enumerate_system,
    inventory_matches_config,
    validate_watchdog_cfg,
)
from ._nidaq_simulation import simulate_step as _sim_step
from ._nidaq_tasks import (
    create_tasks_real,
    teardown_tasks,
    write_do_hardware,
    write_ao_hardware,
    start_fast_reader_threads,
    stop_fast_reader_threads,
)
from ._nidaq_acquisition import (
    read_real,
    start_snapshot_worker,
    stop_snapshot_worker,
)
from ._nidaq_scaling import presort_scaling_points


class NiDAQPlugin(BasePlugin):
    id = "NI_DAQ"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._inventory: Dict[str, Any] = {}
        self._ai_voltage: List[Dict[str, Any]] = []
        self._ai_temp: List[Dict[str, Any]] = []
        self._di: List[Dict[str, Any]] = []
        self._do: List[Dict[str, Any]] = []
        self._ao: List[Dict[str, Any]] = []
        self._theta: float = 0.0
        self._do_states: Dict[str, int] = {}
        self._ao_states: Dict[str, float] = {}
        self._oversample_factor: int = 10
        self._oversample_applies_to: str = "voltage"
        self._filter_type: str = "butterworth"
        self._butterworth_order: int = 4
        self._core_tick_rate_hz: float = 0.0
        self._temp_unit_map: Dict[str, str] = {}
        self._sim_rate_hz: float = 10.0
        self._task_ai_fast = None
        self._task_ai_temp = None
        self._task_di = None
        self._task_do = None
        self._task_ao = None
        self._ai_fast_aliases: List[str] = []
        self._fast_tasks: List[Dict[str, Any]] = []
        self._temp_tasks: List[Dict[str, Any]] = []
        self._di_tasks: List[Dict[str, Any]] = []
        self._do_tasks: List[Dict[str, Any]] = []
        self._ao_tasks: List[Dict[str, Any]] = []
        self._di_read_diag_count: int = 0
        self._ai_temp_aliases: List[str] = []
        self._di_aliases: List[str] = []
        self._do_aliases: List[str] = []
        self._ao_aliases: List[str] = []
        self._health: Dict[str, Any] = {
            "health_ok": True,
            "status": "OK",
            "last_error": "",
            "last_good_read_ts": 0.0,
            "consec_failures": 0,
        }
        self._health_poll_hz: float = 2.0
        self._health_warn_thresh: int = 3
        self._health_fault_thresh: int = 10
        self._health_expose_channels: bool = False
        self._health_thread = None
        self._health_stop = None
        self._inject_fail_remaining: int = 0
        self._fast_rate: float = 0.0
        self._read_timeout_margin_s: float = 0.05
        self._fast_warmup_until: float = 0.0
        self._threaded_fast_ai: bool = False
        self._fast_reader_threads: List[Dict[str, Any]] = []
        self._watchdog_cfg: Dict[str, Any] = {}
        self._do_condition_list: List[Dict[str, Any]] = []
        self._fast_diag_counts: Dict[str, int] = {}
        self._fast_err_counts: Dict[str, int] = {}
        self._fast_last_read_ts: Dict[str, float] = {}
        self._fast_path_printed: bool = False
        self._snapshot_values: Dict[str, Any] = {}
        self._snapshot_lock = threading.Lock()
        self._snapshot_thread = None
        self._snapshot_stop = threading.Event()
        self._snapshot_period_s: float = 0.05

    def _nidaq_available(self) -> bool:
        return nidaq_available()

    def configure(self) -> None:
        if self.mode == "real" and self._nidaq_available():
            self._inventory = self._enumerate_system()
        self._parse_channels()

        # --- Tick rate alignment ---
        # Prefer authoritative core tick rate set by orchestrator; fall back to local config.
        core_rate = self._core_tick_rate_hz
        local_rate_raw = self.config.get("recording_rate_hz", 10.0)
        if core_rate > 0:
            self._sim_rate_hz = core_rate
            if str(local_rate_raw).lower() != "auto" and local_rate_raw != core_rate:
                try:
                    print(f"[NIDAQ] recording_rate_hz={local_rate_raw} overridden by core tick rate {core_rate} Hz")
                except Exception:
                    pass
        else:
            try:
                self._sim_rate_hz = float(local_rate_raw) if str(local_rate_raw).lower() != "auto" else 10.0
            except Exception:
                self._sim_rate_hz = 10.0
        try:
            self._snapshot_period_s = max(0.01, 1.0 / max(1.0, float(self._sim_rate_hz)))
        except Exception:
            self._snapshot_period_s = 0.05

        # --- Acquisition / oversample config ---
        try:
            acq = (self.config.get("acquisition") or {})
            self._read_timeout_margin_s = float(acq.get("read_timeout_margin_s", self._read_timeout_margin_s))
            self._threaded_fast_ai = bool(acq.get("threaded_fast_ai", False))
            ovs = acq.get("oversample") or {}
            self._oversample_factor = int(ovs.get("factor", self._oversample_factor))
            self._oversample_applies_to = str(ovs.get("applies_to", self._oversample_applies_to))
            self._filter_type = str(ovs.get("filter", self._filter_type)).lower()
            self._butterworth_order = int(ovs.get("butterworth_order", self._butterworth_order))
            try:
                print(f"[NIDAQ] configure: threaded_fast_ai={self._threaded_fast_ai} "
                      f"oversample={self._oversample_factor}x applies_to={self._oversample_applies_to} "
                      f"filter={self._filter_type} tick_rate={self._sim_rate_hz}Hz")
            except Exception:
                pass
        except Exception:
            pass

        # --- Pre-sort table scaling points for voltage channels ---
        for ch in self._ai_voltage:
            sc = ch.get("scaling")
            if sc and isinstance(sc, dict):
                ch["scaling"] = presort_scaling_points(sc)

        try:
            hcfg = (self.config.get("health") or {})
            self._health_poll_hz = float(hcfg.get("poll_hz", self._health_poll_hz))
            self._health_warn_thresh = int(hcfg.get("read_fail_warn_threshold", self._health_warn_thresh))
            self._health_fault_thresh = int(hcfg.get("read_fail_fault_threshold", self._health_fault_thresh))
            self._health_expose_channels = bool(hcfg.get("expose_status_channels", False))
        except Exception:
            pass
        try:
            self._watchdog_cfg = (self.config.get("watchdog") or {})
        except Exception:
            self._watchdog_cfg = {}

    def validate(self) -> PluginStatus:
        if self.mode == "real" and not self._nidaq_available():
            return PluginStatus(ok=False, message="NI-DAQmx Python package not available")
        all_aliases: List[str] = []
        for section in (self._ai_voltage, self._ai_temp, self._di, self._do, self._ao):
            for ch in section:
                if not ch.get("enabled", False):
                    continue
                alias = ch.get("alias")
                if alias:
                    all_aliases.append(str(alias))
        if len(all_aliases) != len(set(all_aliases)):
            return PluginStatus(ok=False, message="Duplicate aliases within NI DAQ configuration")
        if self.mode == "real" and self._nidaq_available():
            try:
                inv = self._enumerate_system()
                if not self._inventory_matches_config(inv):
                    return PluginStatus(ok=False, message="Hardware inventory mismatch with ni_daq.yaml. Open Configure to regenerate from inventory.")
            except Exception:
                pass
        wd_status = self._validate_watchdog_cfg()
        if not wd_status.ok:
            return wd_status
        return PluginStatus(ok=True)

    def aliases(self) -> Set[str]:
        result: Set[str] = set()
        for section in (self._ai_voltage, self._ai_temp, self._di, self._do, self._ao):
            for ch in section:
                if not bool(ch.get("enabled", True)):
                    continue
                alias = ch.get("alias")
                if alias:
                    result.add(str(alias))
        return result

    def units(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for ch in self._ai_voltage:
            if not bool(ch.get("enabled", True)):
                continue
            alias = ch.get("alias")
            unit = (ch.get("scaling") or {}).get("unit") or ch.get("unit", "")
            if alias:
                mapping[str(alias)] = str(unit)
        for ch in self._ai_temp:
            if not bool(ch.get("enabled", True)):
                continue
            alias = ch.get("alias")
            unit = ch.get("unit", "C")
            if alias:
                mapping[str(alias)] = str(unit)
        for ch in self._di:
            if not bool(ch.get("enabled", True)):
                continue
            alias = ch.get("alias")
            if alias:
                mapping[str(alias)] = ""
        for ch in self._do:
            if not bool(ch.get("enabled", True)):
                continue
            alias = ch.get("alias")
            if alias:
                mapping[str(alias)] = ""
        for ch in self._ao:
            if not bool(ch.get("enabled", True)):
                continue
            alias = ch.get("alias")
            unit = (ch.get("scaling") or {}).get("unit") or ch.get("unit", "")
            if alias:
                mapping[str(alias)] = str(unit)
        return mapping

    def inventory(self) -> Dict[str, Any]:
        return dict(self._inventory)

    def _enumerate_system(self) -> Dict[str, Any]:
        return enumerate_system()

    def _inventory_matches_config(self, inv: Dict[str, Any]) -> bool:
        return inventory_matches_config(self.config, inv)

    def _validate_watchdog_cfg(self) -> PluginStatus:
        return validate_watchdog_cfg(self._watchdog_cfg, self._do)

    def start(self) -> None:
        self._theta = 0.0
        self._do_states = {
            str(ch.get("alias")): int(ch.get("initial", 0))
            for ch in self._do
            if ch.get("alias") and bool(ch.get("enabled", True))
        }
        self._ao_states = {
            str(ch.get("alias")): float(ch.get("initial", 0.0))
            for ch in self._ao
            if ch.get("alias") and bool(ch.get("enabled", True))
        }
        self._temp_unit_map = {
            str(ch.get("alias")): str(ch.get("unit", "C"))
            for ch in self._ai_temp
            if ch.get("alias") and bool(ch.get("enabled", True))
        }
        if self.mode == "real" and self._nidaq_available():
            try:
                self._create_tasks_real()
                try:
                    print(f"[NIDAQ] start: mode={self.mode} threaded_fast_ai={self._threaded_fast_ai} fast_tasks={len(self._fast_tasks)}")
                except Exception:
                    pass
                if self._threaded_fast_ai:
                    self._start_fast_reader_threads()
                    try:
                        print(f"[NIDAQ] start: fast reader threads active={len(self._fast_reader_threads)}")
                    except Exception:
                        pass
            except Exception:
                self._task_ai_fast = None
                self._task_ai_temp = None
                self._task_di = None
        self._start_health_worker()
        initial: Dict[str, Any] = {}
        for alias, state in self._do_states.items():
            initial[alias] = int(state)
        for alias, state in self._ao_states.items():
            initial[alias] = float(state)
        self._append_health_channels(initial)
        with self._snapshot_lock:
            self._snapshot_values = dict(initial)
        if self.mode == "real" and self._nidaq_available():
            self._start_snapshot_worker()

    def simulate_step(self) -> Dict[str, Any]:
        if self.mode == "real" and (bool(self._fast_tasks) or self._task_ai_fast is not None):
            with self._snapshot_lock:
                return dict(self._snapshot_values)
        vals, self._theta = _sim_step(
            self._theta,
            self._ai_voltage,
            self._ai_temp,
            self._di,
            self._do_states,
            self._ao_states,
            self._oversample_factor,
        )
        self._append_health_channels(vals)
        return vals

    def stop(self) -> None:
        self._stop_snapshot_worker()
        try:
            self._teardown_tasks()
        except Exception:
            pass
        try:
            if self._health_stop is not None:
                self._health_stop.set()
            if self._health_thread is not None:
                self._health_thread.join(timeout=0.5)
        except Exception:
            pass
        self._health_thread = None
        self._health_stop = None

    # --- Delegation to helper modules ---

    def _create_tasks_real(self) -> None:
        create_tasks_real(self)

    def _read_real(self) -> Dict[str, Any]:
        return read_real(self)

    def _teardown_tasks(self) -> None:
        teardown_tasks(self)

    def _start_fast_reader_threads(self) -> None:
        start_fast_reader_threads(self)

    def _stop_fast_reader_threads(self) -> None:
        stop_fast_reader_threads(self)

    def _start_snapshot_worker(self) -> None:
        start_snapshot_worker(self)

    def _stop_snapshot_worker(self) -> None:
        stop_snapshot_worker(self)

    def _write_do_hardware(self) -> None:
        write_do_hardware(self._do_tasks, self._do_states)

    def _write_ao_hardware(self) -> None:
        write_ao_hardware(self._ao_tasks, self._ao_states)

    def _parse_channels(self) -> None:
        self._ai_voltage = []
        self._ai_temp = []
        self._di = []
        self._do = []
        self._ao = []
        self._do_condition_list = []
        chs = self.config.get("channels", [])
        if isinstance(chs, list):
            self._ai_voltage = [c for c in chs if isinstance(c, dict)]
            return
        if isinstance(chs, dict):
            def _list(key: str) -> List[Dict[str, Any]]:
                v = chs.get(key) or []
                return [x for x in v if isinstance(x, dict)]
            self._ai_voltage = _list("ai_voltage")
            self._ai_temp = _list("ai_temp")
            self._di = _list("di")
            self._do = _list("do")
            self._ao = _list("ao")
            for do_ch in self._do:
                if not do_ch.get("enabled", False):
                    continue
                alias = str(do_ch.get("alias", "")).strip()
                cond = do_ch.get("condition")
                if not alias or not isinstance(cond, dict):
                    continue
                operator = str(cond.get("operator", "")).strip()
                if operator in ("TRUE", "FALSE"):
                    self._do_condition_list.append({
                        "alias": alias,
                        "source": "",
                        "operator": operator,
                        "threshold": 0.0,
                    })
                    continue
                source = str(cond.get("source", "")).strip()
                try:
                    threshold = float(cond.get("threshold", 0.0))
                except (TypeError, ValueError):
                    continue
                if source and operator in (">", ">=", "<", "<=", "==", "!="):
                    self._do_condition_list.append({
                        "alias": alias,
                        "source": source,
                        "operator": operator,
                        "threshold": threshold,
                    })
            return

    def do_conditions(self) -> List[Dict[str, Any]]:
        """Return parsed DO conditions for orchestrator evaluation."""
        return list(self._do_condition_list)

    # --- Public command APIs ---

    _do_write_diag_count: int = 0

    def write_do(self, alias: str, state: int) -> None:
        try:
            self._do_states[str(alias)] = int(bool(state))
            if self.mode == "real" and self._do_tasks:
                self._write_do_hardware()
            elif self._do_write_diag_count < 5:
                print(f"[NIDAQ] write_do SKIPPED: alias={alias} state={state} mode={self.mode} do_tasks={len(self._do_tasks)}")
                self._do_write_diag_count += 1
        except Exception as exc:
            if self._do_write_diag_count < 10:
                print(f"[NIDAQ] write_do ERROR: alias={alias} state={state} exc={exc}")
                self._do_write_diag_count += 1

    def write_ao(self, alias: str, value: float) -> None:
        try:
            self._ao_states[str(alias)] = float(value)
            if self.mode == "real" and self._ao_tasks:
                self._write_ao_hardware()
        except Exception:
            pass

    # --- Health worker ---

    def _start_health_worker(self) -> None:
        try:
            if self._health_thread is not None and getattr(self._health_thread, "is_alive", lambda: False)():
                return
            self._health_stop = threading.Event()
            def _loop() -> None:
                poll_interval = 1.0 / max(0.1, float(self._health_poll_hz))
                while not self._health_stop.is_set():
                    try:
                        now = time.time()
                        last_ok = float(self._health.get("last_good_read_ts", 0.0))
                        age = max(0.0, now - last_ok) if last_ok > 0 else 1e9
                        self._health["last_good_read_age_s"] = age
                        cf = int(self._health.get("consec_failures", 0))
                        if cf >= self._health_fault_thresh:
                            self._health["health_ok"] = False
                            self._health["status"] = "FAULT"
                        elif cf >= self._health_warn_thresh:
                            self._health["health_ok"] = True
                            self._health["status"] = "WARN"
                        else:
                            self._health["health_ok"] = True
                            self._health["status"] = "OK"
                    except Exception:
                        pass
                    self._health_stop.wait(poll_interval)
            t = threading.Thread(target=_loop, daemon=True)
            t.start()
            self._health_thread = t
        except Exception:
            pass

    def _append_health_channels(self, vals: Dict[str, Any]) -> None:
        if not self._health_expose_channels:
            return
        try:
            vals["NI_DAQ/health_ok"] = 1 if bool(self._health.get("health_ok", True)) else 0
            vals["NI_DAQ/consec_failures"] = int(self._health.get("consec_failures", 0))
            vals["NI_DAQ/last_good_read_age_s"] = float(self._health.get("last_good_read_age_s", 0.0))
            vals["NI_DAQ/last_error"] = str(self._health.get("last_error") or "")
            fast_alive = 1 if (self._fast_tasks and any(ft.get("task") is not None for ft in self._fast_tasks)) else 0
            vals["NI_DAQ/task_fast_alive"] = fast_alive
        except Exception:
            pass

    def inject_failure(self, mode: str, count: int = 1, duration_s: float = 0.0) -> None:
        if mode == "read_error":
            try:
                self._inject_fail_remaining += max(1, int(count))
            except Exception:
                self._inject_fail_remaining += 1
            try:
                print(f"[NIDAQ] Inject request: mode={mode} remaining={self._inject_fail_remaining}")
            except Exception:
                pass
