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
        self._task_ai_fast = None
        self._task_ai_temp = None
        self._task_di = None
        self._ai_fast_aliases: List[str] = []
        self._ai_temp_aliases: List[str] = []
        self._di_aliases: List[str] = []

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

    def simulate_step(self) -> Dict[str, Any]:
        """If mode==real, perform a real read; otherwise simulate.
        For sim: AI voltage uses 10× oversampling + averaging before decimation.
        Other channels update at R.
        """
        if self.mode == "real" and self._task_ai_fast is not None:
            try:
                return self._read_real()
            except Exception:
                # On any runtime error, return empty for robustness
                return {}
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
        if any(bool(ch.get("enabled", True)) for ch in self._ai_voltage):
            t = None
            try:
                t = Task()
                # Track aliases added in this section
                local_aliases: List[str] = []
                for ch in self._ai_voltage:
                    if not bool(ch.get("enabled", True)):
                        continue
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
                    except Exception:
                        # Skip invalid channel; continue
                        continue
                if local_aliases:
                    t.timing.cfg_samp_clk_timing(rate=fast_rate, sample_mode=AcquisitionType.CONTINUOUS, samps_per_chan=int(fast_rate))
                    t.start()
                    self._task_ai_fast = t
                    self._ai_fast_aliases = local_aliases
                    t = None  # prevent close in finally
            finally:
                try:
                    if t is not None:
                        t.close()
                except Exception:
                    pass
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
                    t.timing.cfg_samp_clk_timing(rate=rec_rate, sample_mode=AcquisitionType.CONTINUOUS, samps_per_chan=int(rec_rate))
                    t.start()
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

    def _read_real(self) -> Dict[str, Any]:
        vals: Dict[str, Any] = {}
        try:
            if self._task_ai_fast is not None and self._ai_fast_aliases:
                n = max(1, self._oversample_factor)
                samples = self._task_ai_fast.read(number_of_samples_per_channel=n, timeout=0.1)
                # samples can be list for single-chan or list-of-lists for multi-chan
                if isinstance(samples, list) and samples and isinstance(samples[0], list):
                    for idx, alias in enumerate(self._ai_fast_aliases):
                        ch_samples = samples[idx]
                        avg = sum(ch_samples) / float(len(ch_samples) or 1)
                        # Scale using configured m/b
                        ch = self._ai_voltage[idx]
                        sc = ch.get("scaling") or {}
                        m = float(sc.get("m", 1.0)); b = float(sc.get("b", 0.0))
                        vals[alias] = m * avg + b
                elif isinstance(samples, list):
                    avg = sum(samples) / float(len(samples) or 1)
                    ch = self._ai_voltage[0]
                    sc = ch.get("scaling") or {}
                    m = float(sc.get("m", 1.0)); b = float(sc.get("b", 0.0))
                    vals[self._ai_fast_aliases[0]] = m * avg + b
            if self._task_ai_temp is not None and self._ai_temp_aliases:
                temp_samples = self._task_ai_temp.read(number_of_samples_per_channel=1, timeout=0.1)
                if isinstance(temp_samples, list) and temp_samples and isinstance(temp_samples[0], list):
                    for idx, alias in enumerate(self._ai_temp_aliases):
                        vals[alias] = float(temp_samples[idx][0])
                elif isinstance(temp_samples, list):
                    vals[self._ai_temp_aliases[0]] = float(temp_samples[0])
            if self._task_di is not None and self._di_aliases:
                di_vals = self._task_di.read(number_of_samples_per_channel=1, timeout=0.05)
                # Normalize to per-line scalar 0/1
                if isinstance(di_vals, list) and di_vals and isinstance(di_vals[0], list):
                    for idx, alias in enumerate(self._di_aliases):
                        v = di_vals[idx][0]
                        vals[alias] = int(bool(v))
                elif isinstance(di_vals, list):
                    vals[self._di_aliases[0]] = int(bool(di_vals[0]))
        except Exception:
            pass
        return vals

    def _teardown_tasks(self) -> None:
        for t in (self._task_ai_fast, self._task_ai_temp, self._task_di):
            try:
                if t is not None:
                    t.stop()
                    t.close()
            except Exception:
                pass
        self._task_ai_fast = None
        self._task_ai_temp = None
        self._task_di = None

    def stop(self) -> None:
        # Ensure NI-DAQmx tasks are properly closed to avoid DaqResourceWarning
        try:
            self._teardown_tasks()
        except Exception:
            pass


