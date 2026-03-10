# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from typing import Dict, Any, Set, List, Optional
import threading
import time

from .base import BasePlugin, PluginStatus


class NiDAQPlugin(BasePlugin):
    id = "NI_DAQ"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._inventory: Dict[str, Any] = {}
        # Structured channel config sections
        self._ai_voltage: List[Dict[str, Any]] = []
        self._ai_temp: List[Dict[str, Any]] = []
        self._di: List[Dict[str, Any]] = []
        self._do: List[Dict[str, Any]] = []
        self._ao: List[Dict[str, Any]] = []
        # Sim state
        self._theta: float = 0.0
        self._do_states: Dict[str, int] = {}
        self._ao_states: Dict[str, float] = {}
        self._oversample_factor: int = 10
        self._sim_rate_hz: float = 10.0
        # Real path tasks/handles
        self._task_ai_fast = None  # legacy single-task handle (unused in multi-task mode)
        self._task_ai_temp = None
        self._task_di = None
        self._task_do = None
        self._task_ao = None
        self._ai_fast_aliases: List[str] = []  # legacy (unused in multi-task mode)
        # Multi-task fast AI per device
        self._fast_tasks: List[Dict[str, Any]] = []  # [{"task": Task, "device": str, "aliases": [...], "alias_to_cfg": {...}}]
        # Multi-task temperature AI per device (TC/RTD)
        self._temp_tasks: List[Dict[str, Any]] = []  # [{"task": Task, "device": str, "aliases": [...]}]
        # Per-device DI/DO/AO tasks
        self._di_tasks: List[Dict[str, Any]] = []   # [{"task": Task, "device": str, "aliases": [...]}]
        self._do_tasks: List[Dict[str, Any]] = []   # [{"task": Task, "device": str, "aliases": [...]}]
        self._ao_tasks: List[Dict[str, Any]] = []   # [{"task": Task, "device": str, "aliases": [...]}]
        # Diagnostics counters
        self._di_read_diag_count: int = 0
        self._ai_temp_aliases: List[str] = []
        self._di_aliases: List[str] = []
        self._do_aliases: List[str] = []
        self._ao_aliases: List[str] = []
        # Health monitoring
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
        # Debug failure injection (testing only)
        self._inject_fail_remaining: int = 0
        # Adaptive read timing
        self._fast_rate: float = 0.0
        self._read_timeout_margin_s: float = 0.05
        self._fast_warmup_until: float = 0.0
        # Threaded fast-AI acquisition (optional; decouples DAQmx reads from Core tick)
        self._threaded_fast_ai: bool = False
        self._fast_reader_threads: List[Dict[str, Any]] = []  # [{device, thread, stop, lock, buffers:{alias: deque}}]
        # Watchdog config (validation only for now)
        self._watchdog_cfg: Dict[str, Any] = {}
        # Short-lived diagnostics counters for fast AI reads (per device)
        self._fast_diag_counts: Dict[str, int] = {}
        # Short-lived error diagnostics counters for fast AI reads (per device)
        self._fast_err_counts: Dict[str, int] = {}
        # Last-read timestamps per fast device (for adaptive read sizing)
        self._fast_last_read_ts: Dict[str, float] = {}
        # One-time debug print guard for path selection
        self._fast_path_printed: bool = False
        # Latest-value snapshot (decouples core tick from NI read timing)
        self._snapshot_values: Dict[str, Any] = {}
        self._snapshot_lock = threading.Lock()
        self._snapshot_thread = None
        self._snapshot_stop = threading.Event()
        self._snapshot_period_s: float = 0.05

    def _nidaq_available(self) -> bool:
        try:
            import nidaqmx  # type: ignore
            return True
        except Exception:
            return False

    def configure(self) -> None:
        # Enumerate devices/channels if NI-DAQmx is available and mode is real
        if self.mode == "real" and self._nidaq_available():
            self._inventory = self._enumerate_system()
        # Parse structured channel configuration for simulation/real descriptive purposes
        self._parse_channels()
        try:
            self._sim_rate_hz = float(self.config.get("recording_rate_hz", 10.0))
        except Exception:
            self._sim_rate_hz = 10.0
        try:
            self._snapshot_period_s = max(0.01, 1.0 / max(1.0, float(self._sim_rate_hz)))
        except Exception:
            self._snapshot_period_s = 0.05
        # Acquisition tuning
        try:
            acq = (self.config.get("acquisition") or {})
            self._read_timeout_margin_s = float(acq.get("read_timeout_margin_s", self._read_timeout_margin_s))
            self._threaded_fast_ai = bool(acq.get("threaded_fast_ai", False))
            try:
                print(f"[NIDAQ] configure: threaded_fast_ai={self._threaded_fast_ai}")
            except Exception:
                pass
        except Exception:
            pass
        # Health configuration
        try:
            hcfg = (self.config.get("health") or {})
            self._health_poll_hz = float(hcfg.get("poll_hz", self._health_poll_hz))
            self._health_warn_thresh = int(hcfg.get("read_fail_warn_threshold", self._health_warn_thresh))
            self._health_fault_thresh = int(hcfg.get("read_fail_fault_threshold", self._health_fault_thresh))
            self._health_expose_channels = bool(hcfg.get("expose_status_channels", False))
        except Exception:
            pass
        # Watchdog configuration (optional)
        try:
            self._watchdog_cfg = (self.config.get("watchdog") or {})
        except Exception:
            self._watchdog_cfg = {}

    def validate(self) -> PluginStatus:
        if self.mode == "real" and not self._nidaq_available():
            return PluginStatus(ok=False, message="NI-DAQmx Python package not available")
        # Validate alias uniqueness across sections
        all_aliases: List[str] = []
        for section in (self._ai_voltage, self._ai_temp, self._di, self._do, self._ao):
            for ch in section:
                alias = ch.get("alias")
                if alias:
                    all_aliases.append(str(alias))
        if len(all_aliases) != len(set(all_aliases)):
            return PluginStatus(ok=False, message="Duplicate aliases within NI DAQ configuration")
        # Inventory vs config sanity check (real mode only)
        if self.mode == "real" and self._nidaq_available():
            try:
                inv = self._enumerate_system()
                if not self._inventory_matches_config(inv):
                    return PluginStatus(ok=False, message="Hardware inventory mismatch with ni_daq.yaml. Open Configure to regenerate from inventory.")
            except Exception:
                pass
        # Validate watchdog schema if provided
        wd_status = self._validate_watchdog_cfg()
        if not wd_status.ok:
            return wd_status
        return PluginStatus(ok=True)

    def aliases(self) -> Set[str]:
        aliases: Set[str] = set()
        for section in (self._ai_voltage, self._ai_temp, self._di, self._do, self._ao):
            for ch in section:
                if not bool(ch.get("enabled", True)):
                    continue
                alias = ch.get("alias")
                if alias:
                    aliases.add(str(alias))
        return aliases

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
        """Return a simple inventory of devices/modules and AI/DI/DO/AO channels."""
        inv: Dict[str, Any] = {"devices": []}
        try:
            from nidaqmx.system import System  # type: ignore
        except Exception:
            return inv
        sys = System.local()
        for dev in sys.devices:
            dev_info: Dict[str, Any] = {
                "name": dev.name,
                "product_type": getattr(dev, "product_type", ""),
                "ai": [],
                "di": [],
                "do": [],
                "ao": [],
            }
            try:
                for ch in getattr(dev, "ai_physical_chans", []):
                    dev_info["ai"].append(ch.name)
            except Exception:
                pass
            try:
                for ch in getattr(dev, "di_lines", []):
                    dev_info["di"].append(ch.name)
            except Exception:
                pass
            try:
                for ch in getattr(dev, "do_lines", []):
                    dev_info["do"].append(ch.name)
            except Exception:
                pass
            try:
                for ch in getattr(dev, "ao_physical_chans", []):
                    dev_info["ao"].append(ch.name)
            except Exception:
                pass
            inv["devices"].append(dev_info)
        return inv

    # -------------------------
    # Validation helpers
    # -------------------------
    def _inventory_matches_config(self, inv: Dict[str, Any]) -> bool:
        try:
            # Build sets of physical channels from inventory (flatten across devices)
            inv_ai = set([str(x) for d in inv.get("devices", []) for x in (d.get("ai") or [])])
            inv_di = set([str(x) for d in inv.get("devices", []) for x in (d.get("di") or [])])
            inv_do = set([str(x) for d in inv.get("devices", []) for x in (d.get("do") or [])])
            inv_ao = set([str(x) for d in inv.get("devices", []) for x in (d.get("ao") or [])])
            # Build sets from current config (all channels, enabled or not)
            ch = self.config.get("channels", {}) or {}
            cfg_ai = set([str(c.get("phys")) for c in (ch.get("ai_voltage") or []) if c.get("phys")])
            cfg_ai |= set([str(c.get("phys")) for c in (ch.get("ai_temp") or []) if c.get("phys")])
            cfg_di = set([str(c.get("phys")) for c in (ch.get("di") or []) if c.get("phys")])
            cfg_do = set([str(c.get("phys")) for c in (ch.get("do") or []) if c.get("phys")])
            cfg_ao = set([str(c.get("phys")) for c in (ch.get("ao") or []) if c.get("phys")])
            return inv_ai == cfg_ai and inv_di == cfg_di and inv_do == cfg_do and inv_ao == cfg_ao
        except Exception:
            return True
    def _validate_watchdog_cfg(self) -> PluginStatus:
        cfg = self._watchdog_cfg
        if not cfg:
            return PluginStatus(ok=True)
        try:
            enabled = bool(cfg.get("enabled", False))
        except Exception:
            enabled = False
        if not enabled:
            return PluginStatus(ok=True)
        # Mode
        mode = str(cfg.get("mode", "")).strip().lower()
        if mode not in ("driver", "digital_loopback"):
            return PluginStatus(ok=False, message="watchdog.mode must be 'driver' or 'digital_loopback'")
        # Helper to coerce positive values
        def _pos_float(v: Any, name: str) -> Optional[float]:
            try:
                f = float(v)
                return f if f > 0 else None
            except Exception:
                return None
        def _pos_int(v: Any, name: str) -> Optional[int]:
            try:
                i = int(v)
                return i if i > 0 else None
            except Exception:
                return None
        if mode == "driver":
            rr = _pos_float(cfg.get("refresh_rate_hz"), "refresh_rate_hz")
            to = _pos_int(cfg.get("timeout_ms"), "timeout_ms")
            if rr is None or to is None:
                return PluginStatus(ok=False, message="watchdog.driver requires positive refresh_rate_hz and timeout_ms")
            # Optional expir_states mapping of DO alias -> state
            expir = cfg.get("expir_states")
            if expir is not None and not isinstance(expir, dict):
                return PluginStatus(ok=False, message="watchdog.expir_states must be a mapping of DO alias -> state")
            if isinstance(expir, dict):
                do_aliases = {str(ch.get("alias")) for ch in self._do if ch.get("alias")}
                for k, v in expir.items():
                    if str(k) not in do_aliases:
                        return PluginStatus(ok=False, message=f"watchdog.expir_states references unknown DO alias: {k}")
                    if _pos_int(int(bool(v)), "state") is None and int(v) not in (0, 1):
                        return PluginStatus(ok=False, message=f"watchdog.expir_states state must be 0 or 1 for alias: {k}")
        else:  # digital_loopback
            do_line = str(cfg.get("do_line", "")).strip()
            di_return = str(cfg.get("di_return", "")).strip()
            if not do_line or not di_return or do_line == di_return:
                return PluginStatus(ok=False, message="watchdog.digital_loopback requires distinct do_line and di_return")
            tr = _pos_float(cfg.get("toggle_rate_hz"), "toggle_rate_hz")
            vto = _pos_int(cfg.get("verify_timeout_ms"), "verify_timeout_ms")
            mt = cfg.get("miss_threshold", 3)
            try:
                mt_int = int(mt)
            except Exception:
                mt_int = 0
            if tr is None or vto is None or mt_int < 1:
                return PluginStatus(ok=False, message="watchdog.digital_loopback requires positive toggle_rate_hz, verify_timeout_ms and miss_threshold>=1")
        return PluginStatus(ok=True)

    def start(self) -> None:
        self._theta = 0.0
        # Initialize output states
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
        if self.mode == "real" and self._nidaq_available():
            try:
                self._create_tasks_real()
                # Start per-device fast-AI reader threads when enabled
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
                # Fall back silently to no-op tasks; validation already warns
                self._task_ai_fast = None
                self._task_ai_temp = None
                self._task_di = None
        # Start health worker
        self._start_health_worker()
        # Seed snapshot with output states so core always has values.
        initial: Dict[str, Any] = {}
        for alias, state in self._do_states.items():
            initial[alias] = int(state)
        for alias, state in self._ao_states.items():
            initial[alias] = float(state)
        self._append_health_channels(initial)
        with self._snapshot_lock:
            self._snapshot_values = dict(initial)
        # Start decoupled real acquisition worker.
        if self.mode == "real" and self._nidaq_available():
            self._start_snapshot_worker()

    def simulate_step(self) -> Dict[str, Any]:
        """If mode==real, perform a real read; otherwise simulate.
        For sim: AI voltage uses 10× oversampling + averaging before decimation.
        Other channels update at R.
        """
        if self.mode == "real" and (bool(self._fast_tasks) or self._task_ai_fast is not None):
            with self._snapshot_lock:
                return dict(self._snapshot_values)
        vals: Dict[str, Any] = {}
        import math
        # Advance base phase modestly per tick
        self._theta += math.pi / 24.0
        # AI Voltage with oversampling and scaling
        for idx, ch in enumerate(self._ai_voltage):
            if not bool(ch.get("enabled", True)):
                continue
            alias = str(ch.get("alias", f"AI_V_{idx}"))
            scaling = ch.get("scaling") or {}
            m = float(scaling.get("m", 1.0))
            b = float(scaling.get("b", 0.0))
            # Generate 10× sub-samples in 0-10 V nominal range
            acc = 0.0
            for k in range(max(1, self._oversample_factor)):
                phase = self._theta + (k / float(self._oversample_factor)) * (math.pi / 24.0)
                v = 5.0 + 5.0 * math.sin(phase + idx * math.pi / 8.0)  # 0..10 V
                acc += v
            v_aa = acc / float(max(1, self._oversample_factor))
            # Scale to engineering units
            vals[alias] = m * v_aa + b
        # AI Temperature (thermocouple/RTD interpreted as engineering value already)
        for idx, ch in enumerate(self._ai_temp):
            if not bool(ch.get("enabled", True)):
                continue
            alias = str(ch.get("alias", f"AI_T_{idx}"))
            vals[alias] = 23.0 + 0.6 * math.sin(self._theta + idx * math.pi / 10.0)
        # DI: simple boolean, default high (1) to indicate OK contact
        for idx, ch in enumerate(self._di):
            if not bool(ch.get("enabled", True)):
                continue
            alias = str(ch.get("alias", f"DI_{idx}"))
            vals[alias] = int(ch.get("initial", 1))
        # DO and AO reflect current states
        for alias, state in self._do_states.items():
            vals[alias] = state
        for alias, state in self._ao_states.items():
            vals[alias] = state
        self._append_health_channels(vals)
        return vals

    def _start_snapshot_worker(self) -> None:
        try:
            if self._snapshot_thread is not None and getattr(self._snapshot_thread, "is_alive", lambda: False)():
                return
            self._snapshot_stop.clear()

            def _loop() -> None:
                while not self._snapshot_stop.is_set():
                    try:
                        vals = self._read_real()
                    except Exception:
                        vals = {}
                    self._append_health_channels(vals)
                    with self._snapshot_lock:
                        self._snapshot_values = dict(vals)
                    self._snapshot_stop.wait(self._snapshot_period_s)

            t = threading.Thread(target=_loop, daemon=True)
            t.start()
            self._snapshot_thread = t
        except Exception:
            self._snapshot_thread = None

    def _stop_snapshot_worker(self) -> None:
        try:
            self._snapshot_stop.set()
            if self._snapshot_thread is not None:
                self._snapshot_thread.join(timeout=1.0)
        except Exception:
            pass
        self._snapshot_thread = None

    def _parse_channels(self) -> None:
        """Support both legacy flat list and structured sections for channels."""
        self._ai_voltage = []
        self._ai_temp = []
        self._di = []
        self._do = []
        self._ao = []
        chs = self.config.get("channels", [])
        if isinstance(chs, list):
            # legacy flat config: [{alias, scaling:{unit}}]
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
            return

    def _create_tasks_real(self) -> None:
        from nidaqmx import Task  # type: ignore
        from nidaqmx.constants import AcquisitionType  # type: ignore
        # Tear down any existing
        self._teardown_tasks()
        self._ai_fast_aliases = []
        self._ai_temp_aliases = []
        self._di_aliases = []
        rec_rate = float(self.config.get("recording_rate_hz", 10.0))
        fast_rate = max(1.0, rec_rate * float(self._oversample_factor))
        # AI fast (voltage)
        # Group fast AI by device and create per-device tasks
        enabled_ai = [ch for ch in self._ai_voltage if bool(ch.get("enabled", True))]
        if enabled_ai:
            from collections import defaultdict
            groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for ch in enabled_ai:
                phys = str(ch.get("phys", ""))
                if not phys:
                    continue
                device = phys.split("/", 1)[0]
                groups[device].append(ch)
            self._fast_tasks = []
            self._fast_rate = fast_rate
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
                            # Increase input buffer to provide more headroom against jitter (e.g., NI-9239 on 9189)
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
                        self._fast_tasks.append({"task": t, "device": device, "aliases": local_aliases, "alias_to_cfg": alias_to_cfg})
                        # Reset diagnostics counter for this device
                        try:
                            self._fast_diag_counts[device] = 0
                            self._fast_err_counts[device] = 0
                            import time as _t
                            self._fast_last_read_ts[device] = _t.time()
                        except Exception:
                            pass
                        # Do not set t=None so we keep the task open; will be closed in teardown
                        t = None
                finally:
                    try:
                        if t is not None:
                            t.close()
                    except Exception:
                        pass
            # Warm-up window
            try:
                import time as _t
                self._fast_warmup_until = _t.time() + (max(1, self._oversample_factor) / max(1.0, self._fast_rate)) + 0.05
            except Exception:
                self._fast_warmup_until = 0.0
        # AI temperature (TC/RTD)
        try:
            enabled_ai_temp = [
                str(ch.get("alias", ch.get("phys", "")))
                for ch in self._ai_temp
                if bool(ch.get("enabled", True))
            ]
            if enabled_ai_temp:
                try:
                    print(f"[NIDAQ] AI_T enabled aliases: {enabled_ai_temp}")
                except Exception:
                    pass
        except Exception:
            pass
        # Group AI temperature channels per-device and create per-device tasks
        enabled_temp = [ch for ch in self._ai_temp if bool(ch.get("enabled", True))]
        if enabled_temp:
            from collections import defaultdict
            groups_t: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for ch in enabled_temp:
                phys = str(ch.get("phys", ""))
                if not phys:
                    continue
                device = phys.split("/", 1)[0]
                groups_t[device].append(ch)
            self._temp_tasks = []
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
                        # On-demand temperature measurement: no sample clock, optional no explicit start
                        try:
                            print(f"[NIDAQ] AI_T on-demand: device={device} channels={len(local_aliases)}")
                        except Exception:
                            pass
                        self._temp_tasks.append({"task": t, "device": device, "aliases": local_aliases})
                        t = None
                finally:
                    try:
                        if t is not None:
                            t.close()
                    except Exception:
                        pass
        # DI lines per-device
        enabled_di = [ch for ch in self._di if bool(ch.get("enabled", True))]
        if enabled_di:
            from collections import defaultdict
            groups_di: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for ch in enabled_di:
                phys = str(ch.get("phys", ""))
                if not phys:
                    continue
                device = phys.split("/", 1)[0]
                groups_di[device].append(ch)
            self._di_tasks = []
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
                        self._di_tasks.append({"task": t, "device": device, "aliases": local_aliases})
                        t = None
                finally:
                    try:
                        if t is not None:
                            t.close()
                    except Exception:
                        pass
        # DO lines per-device
        enabled_do = [ch for ch in self._do if bool(ch.get("enabled", True))]
        if enabled_do:
            from collections import defaultdict
            groups_do: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for ch in enabled_do:
                phys = str(ch.get("phys", ""))
                if not phys:
                    continue
                device = phys.split("/", 1)[0]
                groups_do[device].append(ch)
            self._do_tasks = []
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
                        self._do_tasks.append({"task": t, "device": device, "aliases": local_aliases})
                        # Initialize outputs once
                        try:
                            self._write_do_hardware()
                        except Exception:
                            pass
                        t = None
                finally:
                    try:
                        if t is not None:
                            t.close()
                    except Exception:
                        pass
        # AO voltage per-device
        enabled_ao = [ch for ch in self._ao if bool(ch.get("enabled", True))]
        if enabled_ao:
            from collections import defaultdict
            groups_ao: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for ch in enabled_ao:
                phys = str(ch.get("phys", ""))
                if not phys:
                    continue
                device = phys.split("/", 1)[0]
                groups_ao[device].append(ch)
            self._ao_tasks = []
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
                        self._ao_tasks.append({"task": t, "device": device, "aliases": local_aliases})
                        try:
                            self._write_ao_hardware()
                        except Exception:
                            pass
                        t = None
                finally:
                    try:
                        if t is not None:
                            t.close()
                    except Exception:
                        pass

    def _read_real(self) -> Dict[str, Any]:
        vals: Dict[str, Any] = {}
        # Track whether we obtained any input samples this tick
        any_success = False
        try:
            # Failure injection for testing
            if self._inject_fail_remaining > 0:
                self._inject_fail_remaining -= 1
                raise RuntimeError("Injected NI_DAQ read failure (test)")
            # Prepare timing parameters
            try:
                rec_rate = float(self.config.get("recording_rate_hz", 10.0))
            except Exception:
                rec_rate = 10.0
            n = max(1, self._oversample_factor)
            margin = float(self._read_timeout_margin_s)
            fast_rate = max(1.0, float(self._fast_rate) or 1.0)
            # Minimums to tolerate scheduling jitter with multiple tasks
            timeout_fast = max((n / fast_rate) + margin, 2.5 / max(1.0, rec_rate))
            timeout_temp = max((1.0 / max(1.0, rec_rate)) + margin, 2.5 / max(1.0, rec_rate))
            timeout_di = max((1.0 / max(1.0, rec_rate)) + margin, 2.0 / max(1.0, rec_rate))
            # Fast AI path
            if self._fast_tasks:
                if self._threaded_fast_ai and self._fast_reader_threads:
                    if not self._fast_path_printed:
                        try:
                            print("[NIDAQ] read: using THREADED fast-AI path")
                        except Exception:
                            pass
                        self._fast_path_printed = True
                    # Consume from per-device buffers (latest n)
                    produced_aliases: List[str] = []
                    deque_lengths: List[str] = []
                    for ft in self._fast_reader_threads:
                        try:
                            alias_to_buf = ft.get("buffers", {}) or {}
                            alias_to_cfg = None
                            # One-time per-device buffer keys/id print (first 5 cycles total)
                            try:
                                cb = int(getattr(self, "_thr_buf_keys_count", 0))
                                if cb < 5:
                                    print(f"[NIDAQ] read(thr): buffers device={ft.get('device','?')} keys={list(alias_to_buf.keys())} id={id(alias_to_buf)}")
                                    setattr(self, "_thr_buf_keys_count", cb + 1)
                            except Exception:
                                pass
                            # Find cfg map from original fast_tasks
                            device = str(ft.get("device", ""))
                            for t in self._fast_tasks:
                                if str(t.get("device", "")) == device:
                                    alias_to_cfg = t.get("alias_to_cfg", {})
                                    break
                            lock = ft.get("lock")
                            if alias_to_cfg is None:
                                alias_to_cfg = {}
                            # Snapshot under lock and compute averages
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
                    # One-time diagnostic of what the consumer produced
                    try:
                        c = int(getattr(self, "_thr_vals_diag_count", 0))
                        if c < 5:
                            print(f"[NIDAQ] read(thr): produced {len(produced_aliases)} alias(es): {produced_aliases[:5]} deques={deque_lengths[:5]}")
                            setattr(self, "_thr_vals_diag_count", c + 1)
                    except Exception:
                        pass
                else:
                    if not self._fast_path_printed:
                        try:
                            print("[NIDAQ] read: using LEGACY fast-AI path")
                        except Exception:
                            pass
                        self._fast_path_printed = True
                    # Legacy tick-coupled read path (no threads)
                    for ft in self._fast_tasks:
                        task = ft.get("task")
                        aliases = ft.get("aliases", [])
                        alias_to_cfg = ft.get("alias_to_cfg", {})
                        device = str(ft.get("device", ""))
                        if task is None or not aliases:
                            continue
                        try:
                            import time as _t
                            t0r = _t.time()
                            # Capture available samples before read for diagnostics
                            try:
                                avail_before = int(getattr(task.in_stream, "avail_samp_per_chan", 0))
                            except Exception:
                                avail_before = -1
                            # Adaptive read sizing: read at least n, but match produced samples since last read
                            try:
                                last_ts = float(self._fast_last_read_ts.get(device, t0r))
                            except Exception:
                                last_ts = t0r
                            produced = int(max(0.0, (t0r - last_ts)) * max(1.0, self._fast_rate) + 0.5)
                            # Drain backlog aggressively: prefer available buffer count
                            read_count = max(n, produced, max(0, avail_before))
                            # Clamp to avoid overly large drains at once
                            read_count = min(read_count, int(20 * n))
                            samples = task.read(number_of_samples_per_channel=int(read_count), timeout=timeout_fast)
                            dt_ms = (_t.time() - t0r) * 1000.0
                            # Update last-read timestamp on success
                            try:
                                self._fast_last_read_ts[device] = t0r
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
                            # Diagnostics: log first 20 reads per device
                            try:
                                cnt = int(self._fast_diag_counts.get(device, 0))
                                if cnt < 20:
                                    def _shape(x: Any) -> str:
                                        try:
                                            import numpy as _np  # type: ignore
                                            if isinstance(x, _np.ndarray):
                                                return f"np.ndarray{x.shape}"
                                        except Exception:
                                            pass
                                        if isinstance(x, list):
                                            if x and isinstance(x[0], list):
                                                return f"list[{len(x)}x{len(x[0])}]"
                                            return f"list[{len(x)}]"
                                        return type(x).__name__
                                    print(f"[NIDAQ] AI_V read diag: device={device} dt_ms={dt_ms:.1f} shape={_shape(samples)} read_count={int(read_count)} timeout={timeout_fast:.3f} avail_before={avail_before}")
                                    self._fast_diag_counts[device] = cnt + 1
                            except Exception:
                                pass
                        except Exception as e:
                            try:
                                import time as _t
                                dt_ms = (_t.time() - t0r) * 1000.0
                            except Exception:
                                dt_ms = 0.0
                            try:
                                ec = int(self._fast_err_counts.get(device, 0))
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
                                    self._fast_err_counts[device] = ec + 1
                                cnt = int(self._fast_diag_counts.get(device, 0))
                                if cnt < 20:
                                    print(f"[NIDAQ] AI_V read diag: device={device} dt_ms={dt_ms:.1f} ERROR (timeout={timeout_fast:.3f})")
                                    self._fast_diag_counts[device] = cnt + 1
                            except Exception:
                                pass
            # AI temperature per-device
            if self._temp_tasks:
                for tt in self._temp_tasks:
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
            # DI on-demand per-device
            if self._di_tasks:
                for dt in self._di_tasks:
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
                        # Diagnostics for first few DI reads overall
                        try:
                            if self._di_read_diag_count < 5:
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
                                self._di_read_diag_count += 1
                        except Exception:
                            pass
                    except Exception:
                        pass
            # Reflect current DO/AO states (write-only) for telemetry
            for alias, state in self._do_states.items():
                vals[alias] = int(state)
            for alias, state in self._ao_states.items():
                vals[alias] = float(state)
            # Health bookkeeping
            try:
                import time as _t
                now = _t.time()
                if any_success:
                    self._health["last_good_read_ts"] = now
                    self._health["consec_failures"] = 0
                    self._health["last_error"] = ""
                else:
                    # Skip counting during warm-up window
                    if now >= float(self._fast_warmup_until or 0.0):
                        self._health["consec_failures"] = int(self._health.get("consec_failures", 0)) + 1
                        self._health["last_error"] = "read_error"
            except Exception:
                pass
        except Exception:
            # Catastrophic path (e.g., injected failure outside per-task blocks)
            try:
                import time as _t
                now = _t.time()
                if now >= float(self._fast_warmup_until or 0.0):
                    self._health["consec_failures"] = int(self._health.get("consec_failures", 0)) + 1
                    self._health["last_error"] = "read_error"
                try:
                    print("[NIDAQ] _read_real error; consec_failures=", self._health.get("consec_failures", "?"))
                except Exception:
                    pass
            except Exception:
                pass
        return vals

    def _teardown_tasks(self) -> None:
        # Close fast tasks per device
        try:
            # Stop fast reader threads if running
            self._stop_fast_reader_threads()
            for ft in self._fast_tasks or []:
                t = ft.get("task")
                try:
                    if t is not None:
                        t.stop(); t.close()
                except Exception:
                    pass
        except Exception:
            pass
        # Close legacy single tasks if present
        for t in (self._task_ai_fast, self._task_ai_temp, self._task_di, self._task_do, self._task_ao):
            try:
                if t is not None:
                    t.stop()
                    t.close()
            except Exception:
                pass
        # Close per-device temp tasks
        try:
            for tt in self._temp_tasks or []:
                t = tt.get("task")
                try:
                    if t is not None:
                        t.stop(); t.close()
                except Exception:
                    pass
        except Exception:
            pass
        # Close per-device DI/DO/AO tasks
        try:
            for dt in self._di_tasks or []:
                t = dt.get("task")
                try:
                    if t is not None:
                        t.stop(); t.close()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            for d0 in self._do_tasks or []:
                t = d0.get("task")
                try:
                    if t is not None:
                        t.stop(); t.close()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            for ao in self._ao_tasks or []:
                t = ao.get("task")
                try:
                    if t is not None:
                        t.stop(); t.close()
                except Exception:
                    pass
        except Exception:
            pass
        self._task_ai_fast = None
        self._task_ai_temp = None
        self._task_di = None
        self._task_do = None
        self._task_ao = None
        self._fast_tasks = []
        self._temp_tasks = []
        self._di_tasks = []
        self._do_tasks = []
        self._ao_tasks = []

    def stop(self) -> None:
        # Stop decoupled snapshot worker first.
        self._stop_snapshot_worker()
        # Ensure NI-DAQmx tasks are properly closed to avoid DaqResourceWarning
        try:
            self._teardown_tasks()
        except Exception:
            pass
        # Stop health worker
        try:
            if self._health_stop is not None:
                self._health_stop.set()
            if self._health_thread is not None:
                self._health_thread.join(timeout=0.5)
        except Exception:
            pass
        # Ensure we can restart the health worker on reload/start
        self._health_thread = None
        self._health_stop = None

    def _start_fast_reader_threads(self) -> None:
        """Start per-device reader threads that continuously drain DAQmx into deques."""
        try:
            import threading
            from collections import deque
            try:
                print(f"[NIDAQ] starting fast reader threads for {len(self._fast_tasks)} device(s)")
            except Exception:
                pass
            self._fast_reader_threads = []
            n = max(1, self._oversample_factor)
            for group in self._fast_tasks or []:
                task = group.get("task")
                device = str(group.get("device", ""))
                aliases = list(group.get("aliases", []) or [])
                try:
                    print(f"[NIDAQ] fast reader pre-spawn: device={device} aliases={len(aliases)} task_none={task is None}")
                except Exception:
                    pass
                if task is None or not aliases:
                    continue
                state = {
                    "device": device,
                    "task": task,
                    "stop": threading.Event(),
                    "lock": threading.Lock(),
                    "buffers": {alias: deque(maxlen=int(5*n)) for alias in aliases},  # keep a few windows
                }
                try:
                    print(f"[NIDAQ] spawn buffers: device={device} keys={list(state['buffers'].keys())} id={id(state['buffers'])}")
                except Exception:
                    pass
                def _loop(dev: str, tsk, st, lk, bufs):  # type: ignore
                    import time as _t
                    margin = float(self._read_timeout_margin_s)
                    fast_rate = max(1.0, float(self._fast_rate) or 1.0)
                    # Use modest timeouts to avoid blocking
                    timeout_fast = max((n / fast_rate) + margin, 2.5 / max(1.0, float(self.config.get("recording_rate_hz", 10.0))))
                    last_ts = _t.time()
                    # One-time startup log
                    try:
                        print(f"[NIDAQ] Fast reader thread started: device={dev} timeout={timeout_fast:.3f}")
                    except Exception:
                        pass
                    while not st.is_set():
                        try:
                            avail = 0
                            try:
                                avail = int(getattr(tsk.in_stream, "avail_samp_per_chan", 0))
                            except Exception:
                                avail = 0
                            now = _t.time()
                            produced = int(max(0.0, (now - last_ts)) * fast_rate + 0.5)
                            read_count = max(n, produced, avail)
                            read_count = min(read_count, int(100 * n))  # higher clamp for catch-up
                            if read_count <= 0:
                                st.wait(0.005)
                                continue
                            t0r = _t.time()
                            samples = tsk.read(number_of_samples_per_channel=int(read_count), timeout=timeout_fast)
                            dt_ms = (_t.time() - t0r) * 1000.0
                            last_ts = now
                            # Demultiplex samples into buffers
                            if isinstance(samples, list) and samples and isinstance(samples[0], list):
                                lk.acquire()
                                try:
                                    for idx, alias in enumerate(list(bufs.keys())):
                                        ch_samples = samples[idx] if idx < len(samples) else []
                                        for v in ch_samples:
                                            bufs[alias].append(float(v))
                                finally:
                                    lk.release()
                                # Update health
                                try:
                                    self._health["last_good_read_ts"] = now
                                    self._health["consec_failures"] = 0
                                    self._health["last_error"] = ""
                                except Exception:
                                    pass
                                # Diagnostic (first few per device)
                                try:
                                    c = int(self._fast_diag_counts.get(dev, 0))
                                    if c < 5:
                                        def _shape(x: Any) -> str:
                                            try:
                                                import numpy as _np  # type: ignore
                                                if isinstance(x, _np.ndarray):
                                                    return f"np.ndarray{x.shape}"
                                            except Exception:
                                                pass
                                            if isinstance(x, list):
                                                if x and isinstance(x[0], list):
                                                    return f"list[{len(x)}x{len(x[0])}]"
                                                return f"list[{len(x)}]"
                                            return type(x).__name__
                                        print(f"[NIDAQ] Fast reader read: device={dev} dt_ms={dt_ms:.1f} read_count={int(read_count)} avail={avail} shape={_shape(samples)}")
                                        self._fast_diag_counts[dev] = c + 1
                                except Exception:
                                    pass
                            else:
                                # Single-channel list
                                lk.acquire()
                                try:
                                    alias = list(bufs.keys())[0] if bufs else None
                                    if alias is not None:
                                        for v in (samples or []):
                                            bufs[alias].append(float(v))
                                finally:
                                    lk.release()
                                try:
                                    self._health["last_good_read_ts"] = now
                                    self._health["consec_failures"] = 0
                                    self._health["last_error"] = ""
                                except Exception:
                                    pass
                                # Diagnostic (first few per device)
                                try:
                                    c = int(self._fast_diag_counts.get(dev, 0))
                                    if c < 5:
                                        ln = len(samples) if isinstance(samples, list) else 0
                                        print(f"[NIDAQ] Fast reader read: device={dev} dt_ms={dt_ms:.1f} read_count={int(read_count)} avail={avail} shape=list[{ln}]")
                                        self._fast_diag_counts[dev] = c + 1
                                except Exception:
                                    pass
                        except Exception as e:
                            # Count failures but keep looping
                            try:
                                self._health["consec_failures"] = int(self._health.get("consec_failures", 0)) + 1
                                self._health["last_error"] = "read_error"
                            except Exception:
                                pass
                            # Error diagnostics (first few per device)
                            try:
                                c = int(self._fast_err_counts.get(dev, 0))
                                if c < 5:
                                    err_code = getattr(e, "error_code", None)
                                    try:
                                        avail_now = int(getattr(tsk.in_stream, "avail_samp_per_chan", 0))
                                    except Exception:
                                        avail_now = -1
                                    print(f"[NIDAQ] Fast reader error: device={dev} code={err_code} avail_now={avail_now} msg={e}")
                                    self._fast_err_counts[dev] = c + 1
                            except Exception:
                                pass
                            st.wait(0.01)
                try:
                    t = threading.Thread(target=_loop, args=(device, task, state["stop"], state["lock"], state["buffers"]), daemon=True)
                    t.start()
                    state["thread"] = t
                    try:
                        print(f"[NIDAQ] Fast reader thread spawned: device={device}")
                    except Exception:
                        pass
                    self._fast_reader_threads.append(state)
                except Exception as e:
                    try:
                        import traceback as _tb
                        print(f"[NIDAQ] fast reader spawn failed: device={device} err={e}\n{_tb.format_exc()}")
                    except Exception:
                        pass
        except Exception as e:
            try:
                print(f"[NIDAQ] starting fast reader threads failed: {e}")
            except Exception:
                pass
            self._fast_reader_threads = []

    def _stop_fast_reader_threads(self) -> None:
        try:
            for st in self._fast_reader_threads or []:
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
        self._fast_reader_threads = []

    # Public command APIs for outputs
    def write_do(self, alias: str, state: int) -> None:
        try:
            self._do_states[str(alias)] = int(bool(state))
            if self.mode == "real" and self._do_tasks:
                self._write_do_hardware()
        except Exception:
            pass

    def write_ao(self, alias: str, value: float) -> None:
        try:
            self._ao_states[str(alias)] = float(value)
            if self.mode == "real" and self._ao_tasks:
                self._write_ao_hardware()
        except Exception:
            pass

    # Internal helpers to push current states to hardware in channel order
    def _write_do_hardware(self) -> None:
        if not self._do_tasks:
            return
        try:
            for dt in self._do_tasks:
                task = dt.get("task")
                aliases = list(dt.get("aliases", []) or [])
                if task is None or not aliases:
                    continue
                values = [int(bool(self._do_states.get(alias, 0))) for alias in aliases]
                task.write(values, auto_start=True)
        except Exception:
            pass

    def _write_ao_hardware(self) -> None:
        if not self._ao_tasks:
            return
        try:
            for at in self._ao_tasks:
                task = at.get("task")
                aliases = list(at.get("aliases", []) or [])
                if task is None or not aliases:
                    continue
                values = [float(self._ao_states.get(alias, 0.0)) for alias in aliases]
                task.write(values, auto_start=True)
        except Exception:
            pass

    # Health reporting helpers
    def _start_health_worker(self) -> None:
        try:
            import threading, time
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
            fast_alive = 1 if (self._fast_tasks and any(ft.get("task") is not None for ft in self._fast_tasks)) else 0
            vals["NI_DAQ/task_fast_alive"] = fast_alive
        except Exception:
            pass

    # Debug failure injection
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
        # Other modes (e.g., simulate device missing) can be added later


