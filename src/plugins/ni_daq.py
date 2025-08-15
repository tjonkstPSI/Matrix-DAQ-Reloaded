# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from typing import Dict, Any, Set, List, Optional

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
        # Watchdog config (validation only for now)
        self._watchdog_cfg: Dict[str, Any] = {}

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
        # Acquisition tuning
        try:
            acq = (self.config.get("acquisition") or {})
            self._read_timeout_margin_s = float(acq.get("read_timeout_margin_s", self._read_timeout_margin_s))
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
            except Exception:
                # Fall back silently to no-op tasks; validation already warns
                self._task_ai_fast = None
                self._task_ai_temp = None
                self._task_di = None
        # Start health worker
        self._start_health_worker()

    def simulate_step(self) -> Dict[str, Any]:
        """If mode==real, perform a real read; otherwise simulate.
        For sim: AI voltage uses 10× oversampling + averaging before decimation.
        Other channels update at R.
        """
        if self.mode == "real" and (bool(self._fast_tasks) or self._task_ai_fast is not None):
            try:
                vals = self._read_real()
                self._append_health_channels(vals)
                return vals
            except Exception:
                # On any runtime error, return empty for robustness
                out: Dict[str, Any] = {}
                self._append_health_channels(out)
                return out
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
                            t.timing.cfg_samp_clk_timing(rate=fast_rate, sample_mode=AcquisitionType.CONTINUOUS, samps_per_chan=int(max(1, 2 * int(fast_rate))))
                            print(f"[NIDAQ] AI_V timing: device={device} rate={fast_rate} samps_per_chan={int(max(1, 2 * int(fast_rate)))}")
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
        if any(bool(ch.get("enabled", True)) for ch in self._ai_temp):
            t = None
            try:
                t = Task()
                try:
                    from nidaqmx.constants import (ThermocoupleType, TemperatureUnits, CJCSource, RTDType, ResistanceConfiguration)  # type: ignore
                except Exception:
                    ThermocoupleType = TemperatureUnits = CJCSource = RTDType = ResistanceConfiguration = None  # type: ignore
                local_aliases: List[str] = []
                for ch in self._ai_temp:
                    if not bool(ch.get("enabled", True)):
                        continue
                    phys = str(ch.get("phys", ""))
                    if not phys:
                        continue
                    sensor = ch.get("sensor", {}) or {}
                    stype = str(sensor.get("type", "TC")).upper()
                    try:
                        if stype == "RTD" and RTDType is not None:
                            subtype = str(sensor.get("subtype", "PT100")).upper()
                            wires = int(sensor.get("wires", 3))
                            rtd_map = {"PT100": RTDType.PT100} if hasattr(RTDType, "PT100") else {}
                            rtd_type = rtd_map.get(subtype, list(rtd_map.values())[0] if rtd_map else None)
                            cfg = ResistanceConfiguration.THREE_WIRE if wires == 3 else (
                                ResistanceConfiguration.FOUR_WIRE if wires == 4 else ResistanceConfiguration.TWO_WIRE)
                            t.ai_channels.add_ai_rtd_chan(phys, rtd_type=rtd_type, resistance_config=cfg, units=TemperatureUnits.DEG_C)
                        else:
                            # Default TC K unless specified
                            tc_sub = str(sensor.get("subtype", "K")).upper()
                            tc_map = {}
                            if ThermocoupleType is not None:
                                tc_map = {k.name: k for k in ThermocoupleType}
                            tc_enum = tc_map.get(tc_sub)
                            if tc_enum is not None and TemperatureUnits is not None and CJCSource is not None:
                                t.ai_channels.add_ai_thrmcpl_chan(phys, tc_type=tc_enum, units=TemperatureUnits.DEG_C, cjc_source=CJCSource.BUILT_IN)
                            else:
                                t.ai_channels.add_ai_voltage_chan(phys, min_val=-1.0, max_val=1.0)
                        local_aliases.append(str(ch.get("alias", phys)))
                    except Exception:
                        # Skip this sensor channel on error
                        continue
                if local_aliases:
                    try:
                        t.timing.cfg_samp_clk_timing(rate=rec_rate, sample_mode=AcquisitionType.CONTINUOUS, samps_per_chan=int(rec_rate))
                        print(f"[NIDAQ] AI_T timing: rate={rec_rate} samps_per_chan={int(rec_rate)}")
                    except Exception as e:
                        try:
                            print(f"[NIDAQ] AI_T timing error: {e}")
                        except Exception:
                            pass
                        raise
                    try:
                        t.start()
                        print("[NIDAQ] AI_T task started")
                    except Exception as e:
                        try:
                            print(f"[NIDAQ] AI_T start error: {e}")
                        except Exception:
                            pass
                        raise
                    self._task_ai_temp = t
                    self._ai_temp_aliases = local_aliases
                    t = None
            finally:
                try:
                    if t is not None:
                        t.close()
                except Exception:
                    pass
        # DI lines
        if any(bool(ch.get("enabled", True)) for ch in self._di):
            t = None
            try:
                t = Task()
                local_aliases: List[str] = []
                for ch in self._di:
                    if not bool(ch.get("enabled", True)):
                        continue
                    phys = str(ch.get("phys", ""))
                    if not phys:
                        continue
                    try:
                        t.di_channels.add_di_chan(phys)
                        local_aliases.append(str(ch.get("alias", phys)))
                    except Exception:
                        continue
                if local_aliases:
                    t.start()  # on-demand read is fine; start still needed for lifecycle
                    self._task_di = t
                    self._di_aliases = local_aliases
                    t = None
            finally:
                try:
                    if t is not None:
                        t.close()
                except Exception:
                    pass
        # DO lines (on-demand write)
        if any(bool(ch.get("enabled", True)) for ch in self._do):
            t = None
            try:
                t = Task()
                local_aliases: List[str] = []
                for ch in self._do:
                    if not bool(ch.get("enabled", True)):
                        continue
                    phys = str(ch.get("phys", ""))
                    if not phys:
                        continue
                    try:
                        t.do_channels.add_do_chan(phys)
                        local_aliases.append(str(ch.get("alias", phys)))
                    except Exception:
                        continue
                if local_aliases:
                    # No need to start explicitly for on-demand writes; start for lifecycle symmetry
                    t.start()
                    self._task_do = t
                    self._do_aliases = local_aliases
                    # Write initial states once
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
        # AO voltage (on-demand write)
        if any(bool(ch.get("enabled", True)) for ch in self._ao):
            t = None
            try:
                t = Task()
                local_aliases: List[str] = []
                for ch in self._ao:
                    if not bool(ch.get("enabled", True)):
                        continue
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
                    self._task_ao = t
                    self._ao_aliases = local_aliases
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
            # Read each fast AI task per device with isolation
            if self._fast_tasks:
                for ft in self._fast_tasks:
                    task = ft.get("task")
                    aliases = ft.get("aliases", [])
                    alias_to_cfg = ft.get("alias_to_cfg", {})
                    if task is None or not aliases:
                        continue
                    try:
                        samples = task.read(number_of_samples_per_channel=n, timeout=timeout_fast)
                        if isinstance(samples, list) and samples and isinstance(samples[0], list):
                            for idx, alias in enumerate(aliases):
                                ch_samples = samples[idx]
                                avg = sum(ch_samples) / float(len(ch_samples) or 1)
                                ch = alias_to_cfg.get(alias, {})
                                sc = ch.get("scaling") or {}
                                m = float(sc.get("m", 1.0)); b = float(sc.get("b", 0.0))
                                vals[alias] = m * avg + b
                            any_success = True
                        elif isinstance(samples, list):
                            avg = sum(samples) / float(len(samples) or 1)
                            alias = aliases[0]
                            ch = alias_to_cfg.get(alias, {})
                            sc = ch.get("scaling") or {}
                            m = float(sc.get("m", 1.0)); b = float(sc.get("b", 0.0))
                            vals[alias] = m * avg + b
                            any_success = True
                    except Exception:
                        # Isolate failures of one device/task from others
                        pass
            # AI temperature
            if self._task_ai_temp is not None and self._ai_temp_aliases:
                try:
                    temp_samples = self._task_ai_temp.read(number_of_samples_per_channel=1, timeout=timeout_temp)
                    if isinstance(temp_samples, list) and temp_samples and isinstance(temp_samples[0], list):
                        for idx, alias in enumerate(self._ai_temp_aliases):
                            vals[alias] = float(temp_samples[idx][0])
                        any_success = True
                    elif isinstance(temp_samples, list):
                        vals[self._ai_temp_aliases[0]] = float(temp_samples[0])
                        any_success = True
                except Exception:
                    pass
            # DI on-demand
            if self._task_di is not None and self._di_aliases:
                try:
                    di_vals = self._task_di.read(number_of_samples_per_channel=1, timeout=timeout_di)
                    # Normalize to per-line scalar 0/1
                    if isinstance(di_vals, list) and di_vals and isinstance(di_vals[0], list):
                        for idx, alias in enumerate(self._di_aliases):
                            v = di_vals[idx][0]
                            vals[alias] = int(bool(v))
                        any_success = True
                    elif isinstance(di_vals, list):
                        vals[self._di_aliases[0]] = int(bool(di_vals[0]))
                        any_success = True
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
            for ft in self._fast_tasks or []:
                t = ft.get("task")
                try:
                    if t is not None:
                        t.stop(); t.close()
                except Exception:
                    pass
        except Exception:
            pass
        for t in (self._task_ai_fast, self._task_ai_temp, self._task_di, self._task_do, self._task_ao):
            try:
                if t is not None:
                    t.stop()
                    t.close()
            except Exception:
                pass
        self._task_ai_fast = None
        self._task_ai_temp = None
        self._task_di = None
        self._task_do = None
        self._task_ao = None
        self._fast_tasks = []

    def stop(self) -> None:
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

    # Public command APIs for outputs
    def write_do(self, alias: str, state: int) -> None:
        try:
            self._do_states[str(alias)] = int(bool(state))
            if self.mode == "real" and self._task_do is not None and self._do_aliases:
                self._write_do_hardware()
        except Exception:
            pass

    def write_ao(self, alias: str, value: float) -> None:
        try:
            self._ao_states[str(alias)] = float(value)
            if self.mode == "real" and self._task_ao is not None and self._ao_aliases:
                self._write_ao_hardware()
        except Exception:
            pass

    # Internal helpers to push current states to hardware in channel order
    def _write_do_hardware(self) -> None:
        if self._task_do is None or not self._do_aliases:
            return
        try:
            values = [int(bool(self._do_states.get(alias, 0))) for alias in self._do_aliases]
            # nidaqmx allows list writes for multiple lines
            self._task_do.write(values, auto_start=True)
        except Exception:
            pass

    def _write_ao_hardware(self) -> None:
        if self._task_ao is None or not self._ao_aliases:
            return
        try:
            values = [float(self._ao_states.get(alias, 0.0)) for alias in self._ao_aliases]
            self._task_ao.write(values, auto_start=True)
        except Exception:
            pass

    # Health reporting helpers
    def _start_health_worker(self) -> None:
        try:
            import threading, time
            if self._health_thread is not None:
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


