# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any

from .registry import PluginRegistry, PluginSpec
from ..plugins.base import BasePlugin
from ..plugins.modbus import ModbusPlugin
from ..plugins.can import CANPlugin
from ..plugins.ccp import CCPPlugin
from ..plugins.ni_daq import NiDAQPlugin
from ..plugins.loadbank import LoadBankPlugin
from .ipc.bus import IPCBus
from ..config.loader import load_yaml_config
from ..plugins.cycle import CyclePlugin
from ..plugins.calculated import CalculatedChannelsPlugin
from .alarms.engine import AlarmEngine
from .storage.alarm_events import AlarmEventsSink
from ..plugins.statistics import StatisticsPlugin
from .storage.stats_snapshots import StatsSnapshotsSink
from ..plugins.vaisala import VaisalaPlugin
from .storage.parquet_writer import ParquetWriter, ParquetWriterSettings


@dataclass
class Settings:
    recording_rate_hz: float = 10.0
    ui_update_hz: float = 5.0


class Orchestrator:
    def __init__(self, configs_dir: Path) -> None:
        self.configs_dir = configs_dir
        self.settings = Settings()
        self._running = False
        self.registry = PluginRegistry(configs_dir)
        self.plugins: Dict[str, BasePlugin] = {}
        self.bus = IPCBus()
        self.core_cfg: Dict[str, Any] = {}
        self.channel_cfg: Dict[str, Any] = {}
        self.alarm_engine: AlarmEngine | None = None
        self._alarm_tick_logged: bool = False
        self._events_sink: AlarmEventsSink | None = None
        self._stats_sink: StatsSnapshotsSink | None = None
        self._parquet: ParquetWriter | None = None
        self._run_dir: Path | None = None
        self._last_run_dir: Path | None = None
        self._recording: bool = False
        self._export_in_progress: bool = False
        self._plugin_enabled: Dict[str, bool] = {}

    def start(self) -> None:
        # Placeholder: load configs, initialize IPC bus, register simulated plugins
        self._register_builtin_specs()
        self.plugins = self.registry.create_all()
        # Load core config
        core_cfg_path = (self.configs_dir / "core.yaml").resolve()
        self.core_cfg = load_yaml_config(core_cfg_path)
        # Load channel manager config (optional)
        ch_cfg_path = (self.configs_dir / "channel_manager.yaml").resolve()
        self.channel_cfg = load_yaml_config(ch_cfg_path)
        if self.channel_cfg:
            try:
                self.alarm_engine = AlarmEngine(self.channel_cfg)
                chan_items = (self.channel_cfg.get("channels") or [])
                chan_count = len(chan_items)
                print(f"[INFO] AlarmEngine initialized: {chan_count} channel(s)")
                if chan_count:
                    aliases = [str(it.get("alias")) for it in chan_items if isinstance(it, dict) and it.get("alias")]
                    print("[INFO] AlarmEngine channels:", ", ".join(aliases))
            except Exception as e:
                print(f"[WARN] Failed to initialize AlarmEngine: {e}")
        else:
            print("[INFO] Channel Manager config not found or empty; alarms disabled")
        # Load configs for each plugin and run basic validation
        all_ok = True
        ALWAYS_ON = {"Channel_Manager", "EngineTest"}
        for pid, plugin in self.plugins.items():
            plugin.load_config()
            enabled = True
            try:
                if pid not in ALWAYS_ON:
                    enabled = bool(plugin.config.get("enabled", True))
            except Exception:
                enabled = True
            self._plugin_enabled[pid] = enabled
            if not enabled:
                print(f"[INFO] Plugin '{pid}' disabled by config; skipping init")
                continue
            status = plugin.validate()
            if not status.ok:
                all_ok = False
                print(f"[ERROR] Plugin '{pid}' validation failed: {status.message}")
            # Optional early configure for NI_DAQ to enumerate inventory
            if pid == "NI_DAQ" and status.ok:
                try:
                    plugin.configure()
                    inv = getattr(plugin, "inventory")()
                    devices = inv.get("devices", []) if isinstance(inv, dict) else []
                    print(f"[INFO] NI_DAQ inventory: {len(devices)} device(s)")
                    for d in devices:
                        name = d.get("name", "?")
                        ptype = d.get("product_type", "?")
                        ai_n = len(d.get("ai", []))
                        di_n = len(d.get("di", []))
                        do_n = len(d.get("do", []))
                        ao_n = len(d.get("ao", []))
                        print(f"  - {name} [{ptype}] AI:{ai_n} DI:{di_n} DO:{do_n} AO:{ao_n}")
                except Exception as e:
                    print(f"[WARN] NI_DAQ inventory enumeration failed: {e}")
            # Optional early load for LoadBank to confirm model map and units
            if pid == "LoadBank" and status.ok:
                try:
                    plugin.configure()
                    units = getattr(plugin, "units")()
                    print("[INFO] LoadBank units:", units)
                except Exception as e:
                    print(f"[WARN] LoadBank map/units check failed: {e}")
        if all_ok:
            print("[INFO] Plugin validation passed for all registered plugins")
        # Global alias aggregation/validation
        alias_sets = []
        for pid, plugin in self.plugins.items():
            if not self._plugin_enabled.get(pid, True):
                alias_sets.append(set())
                continue
            try:
                alias_sets.append(plugin.aliases())
            except Exception:
                alias_sets.append(set())
        try:
            self.registry.validate_global_aliases(alias_sets)
            print("[INFO] Global alias validation passed (no duplicates)")
        except ValueError as e:
            print(f"[ERROR] Global alias validation failed: {e}")
            raise
        self.bus.start()
        self._running = True
        print("[INFO] Core started; recording is idle. Use Start Recording from UI to begin.")

    def run(self) -> None:
        # Initialize plugins; Modbus is optional (can be disabled)
        modbus = self.plugins.get("Modbus") if self._plugin_enabled.get("Modbus", True) else None
        if modbus is not None:
            try:
                modbus.configure()
                status = modbus.validate()
                if not status.ok:
                    print(f"[ERROR] Modbus validate failed in run(): {status.message}")
                    modbus = None
                else:
                    modbus.arm()
                    modbus.start()
            except Exception as e:
                print(f"[WARN] Modbus initialization failed: {e}")
                modbus = None
        try:
            # Generate ticks per run_mode, publish telemetry merging plugins
            import time, json
            can = self.plugins.get("CAN") if self._plugin_enabled.get("CAN", True) else None
            ccp = self.plugins.get("CCP") if self._plugin_enabled.get("CCP", True) else None
            lb = self.plugins.get("LoadBank") if self._plugin_enabled.get("LoadBank", True) else None
            cycle = self.plugins.get("Cycle") if self._plugin_enabled.get("Cycle", True) else None
            stats = self.plugins.get("Statistics") if self._plugin_enabled.get("Statistics", True) else None
            vaisala = self.plugins.get("Vaisala") if self._plugin_enabled.get("Vaisala", True) else None
            nidaq = self.plugins.get("NI_DAQ") if self._plugin_enabled.get("NI_DAQ", True) else None
            calc = self.plugins.get("Calculated_Channels") if self._plugin_enabled.get("Calculated_Channels", True) else None
            if can:
                can.configure(); can.validate(); can.start()
            if ccp:
                ccp.configure(); ccp.validate(); ccp.start()
            if lb:
                lb.configure(); lb.validate(); lb.start()
            if stats:
                stats.configure(); stats.validate(); stats.start()
            if vaisala:
                vaisala.configure(); vaisala.validate(); vaisala.start()
            if nidaq:
                nidaq.configure(); nidaq.validate(); nidaq.start()
            if cycle:
                cycle.configure(); cycle.validate(); cycle.start()
            calc = self.plugins.get("Calculated_Channels") if self._plugin_enabled.get("Calculated_Channels", True) else None
            if calc:
                calc.configure(); calc.validate(); calc.start()
            prev_complete = getattr(cycle, "is_complete")() if cycle else False
            run_mode = str(self.core_cfg.get("run_mode", "demo")).lower()
            demo_ticks = int(self.core_cfg.get("demo_ticks", 50))
            interval = float(self.core_cfg.get("tick_interval_s", 0.1))
            i = 0
            t0 = time.time()
            
            def _format_local_hms(epoch_seconds: float) -> str:
                """Return local workstation time as HH:MM:SS.fff for an epoch timestamp."""
                try:
                    import time as _t
                    whole = int(epoch_seconds)
                    frac_ms = int(round((epoch_seconds - whole) * 1000.0))
                    # Handle rounding to next second
                    if frac_ms >= 1000:
                        whole += 1
                        frac_ms = 0
                    hhmmss = _t.strftime("%H:%M:%S", _t.localtime(whole))
                    return f"{hhmmss}.{frac_ms:03d}"
                except Exception:
                    return "00:00:00.000"
            if run_mode == "demo":
                for _ in range(demo_ticks):
                    if not self._running:
                        break
                    vals = {}
                    units = {}
                    if modbus is not None:
                        vals.update(getattr(modbus, "simulate_step")())
                        units.update(getattr(modbus, "units")())
                    if nidaq:
                        vals.update(getattr(nidaq, "simulate_step")())
                        units.update(getattr(nidaq, "units")())
                    if can:
                        vals.update(getattr(can, "simulate_step")())
                        units.update(getattr(can, "units")())
                    if ccp:
                        vals.update(getattr(ccp, "simulate_step")())
                        units.update(getattr(ccp, "units")())
                    if lb and cycle:
                        sp = float(getattr(cycle, "current_setpoint_kw")())
                        now_complete = getattr(cycle, "is_complete")()
                        # Edge-aware final send: send when not complete, or on transition to complete
                        if (not now_complete) or (not prev_complete and now_complete):
                            getattr(lb, "command_setpoint_pct")(sp)
                        prev_complete = now_complete
                        vals.update(getattr(lb, "simulate_step")())
                        units.update(getattr(lb, "units")())
                    # Vaisala simulated environment values
                    if vaisala:
                        vals.update(getattr(vaisala, "simulate_step")())
                        units.update(getattr(vaisala, "units")())
                    # Capture current timestamp for this tick
                    now_ts = time.time()
                    # Run calculated channels using merged source values before alarms/stats
                    if calc is not None:
                        try:
                            calc_vals = getattr(calc, "simulate_step")(vals)
                            vals.update(calc_vals)
                            # Units from plugin
                            units.update(getattr(calc, "units")())
                        except Exception:
                            pass
                    # Update statistics plugin and handle outputs (persist only)
                    if stats:
                        getattr(stats, "update")(vals, units, now_ts)
                        stat_vals, stat_units, stat_events = getattr(stats, "outputs")(now_ts)
                        if self._stats_sink is not None and stat_vals and stat_events:
                            for sev in stat_events:
                                if sev.get("type") == "stats_snapshot":
                                    record = {"ts_hms": _format_local_hms(now_ts)}
                                    record.update(stat_vals)
                                    try:
                                        self._stats_sink.append_snapshot(record)
                                    except Exception:
                                        pass
                        # Optionally print stat events once
                        for sev in stat_events or []:
                            if sev.get("type") == "stats_skip":
                                reason = sev.get("reason", "unknown")
                                print(f"[STATS] Snapshot skipped: {reason}")
                            elif sev.get("type") == "stats_snapshot":
                                print(f"[STATS] Snapshot taken at { _format_local_hms(now_ts) }")
                    # Add relative time channel from core
                    elapsed = time.time() - t0
                    vals["Time_Relative_s"] = elapsed
                    units["Time_Relative_s"] = "s"
                    # Evaluate alarms
                    states, summary, events = ({}, {"any_warning": False, "any_shutdown": False}, [])
                    # Handle control messages (e.g., manual stats, recording control, export)
                    for raw in self.bus.recv_controls_nonblocking():
                        try:
                            ctrl_msg = json.loads(raw.decode("utf-8"))
                            try:
                                print(f"[CTRL] Received: {ctrl_msg}")
                            except Exception:
                                pass
                            if ctrl_msg.get("type") == "stats_snapshot" and stats:
                                getattr(stats, "request_manual_snapshot")(now_ts)
                            elif ctrl_msg.get("type") == "start_recording":
                                self._begin_recording()
                            elif ctrl_msg.get("type") == "stop_recording":
                                self._end_recording()
                            elif ctrl_msg.get("type") == "export_excel":
                                self._kickoff_export()
                            elif ctrl_msg.get("type") == "do_write":
                                self._handle_do_write(ctrl_msg)
                            elif ctrl_msg.get("type") == "ao_write":
                                self._handle_ao_write(ctrl_msg)
                            elif ctrl_msg.get("type") == "plugin_inject_fail":
                                self._handle_inject_fail(ctrl_msg)
                        except Exception as e:
                            try:
                                print(f"[WARN] Control handling error: {e}")
                            except Exception:
                                pass
                    if self.alarm_engine is not None:
                        states, summary, events = self.alarm_engine.evaluate(vals, now_ts)
                        for ev in events:
                            ev_ts = float(ev.get("ts", now_ts))
                            ev["ts_hms"] = _format_local_hms(ev_ts)
                            print(f"[ALARM] {ev['alias']}: {ev['from']} -> {ev['to']} val={ev.get('value')} t={ev['ts_hms']}")
                        if self._events_sink is not None and events:
                            try:
                                self._events_sink.append_many(events)
                            except Exception:
                                pass
                    if self.alarm_engine is not None and not self._alarm_tick_logged:
                        try:
                            print(f"[INFO] AlarmEngine tick sample: states={states} summary={summary} events={len(events)}")
                        except Exception:
                            pass
                        self._alarm_tick_logged = True
                    payload = json.dumps({
                        "ts": time.time(),
                        "values": vals,
                        "units": units,
                        "states": states,
                        "alarm_summary": summary,
                        "alarm_events": events,
                        "recording": bool(self._recording),
                    }).encode("utf-8")
                    self.bus.publish_telemetry(payload)
                    # Append to Parquet data stream when recording
                    try:
                        if self._recording and self._parquet is not None:
                            self._parquet.append(now_ts, vals, units)
                    except Exception:
                        pass
                    time.sleep(interval)
                    i += 1
            else:
                while self._running:
                    vals = {}
                    units = {}
                    if modbus is not None:
                        vals.update(getattr(modbus, "simulate_step")())
                        units.update(getattr(modbus, "units")())
                    if nidaq:
                        vals.update(getattr(nidaq, "simulate_step")())
                        units.update(getattr(nidaq, "units")())
                    if can:
                        vals.update(getattr(can, "simulate_step")())
                        units.update(getattr(can, "units")())
                    if ccp:
                        vals.update(getattr(ccp, "simulate_step")())
                        units.update(getattr(ccp, "units")())
                    if lb and cycle:
                        sp = float(getattr(cycle, "current_setpoint_kw")())
                        now_complete = getattr(cycle, "is_complete")()
                        if (not now_complete) or (not prev_complete and now_complete):
                            getattr(lb, "command_setpoint_pct")(sp)
                        prev_complete = now_complete
                        vals.update(getattr(lb, "simulate_step")())
                        units.update(getattr(lb, "units")())
                    if vaisala:
                        vals.update(getattr(vaisala, "simulate_step")())
                        units.update(getattr(vaisala, "units")())
                    # Capture current timestamp for this tick
                    now_ts = time.time()
                    # Run calculated channels before alarms/stats
                    if calc is not None:
                        try:
                            calc_vals = getattr(calc, "simulate_step")(vals)
                            vals.update(calc_vals)
                            units.update(getattr(calc, "units")())
                        except Exception:
                            pass
                    # Update statistics plugin and handle outputs (persist only)
                    if stats:
                        getattr(stats, "update")(vals, units, now_ts)
                        stat_vals, stat_units, stat_events = getattr(stats, "outputs")(now_ts)
                        if self._stats_sink is not None and stat_vals and stat_events:
                            for sev in stat_events:
                                if sev.get("type") == "stats_snapshot":
                                    record = {"ts_hms": _format_local_hms(now_ts)}
                                    record.update(stat_vals)
                                    try:
                                        self._stats_sink.append_snapshot(record)
                                    except Exception:
                                        pass
                        for sev in stat_events or []:
                            if sev.get("type") == "stats_skip":
                                reason = sev.get("reason", "unknown")
                                print(f"[STATS] Snapshot skipped: {reason}")
                            elif sev.get("type") == "stats_snapshot":
                                print(f"[STATS] Snapshot taken at { _format_local_hms(now_ts) }")
                    # Add relative time channel from core
                    elapsed = time.time() - t0
                    vals["Time_Relative_s"] = elapsed
                    units["Time_Relative_s"] = "s"
                    # Evaluate alarms
                    states, summary, events = ({}, {"any_warning": False, "any_shutdown": False}, [])
                    # Handle control messages
                    for raw in self.bus.recv_controls_nonblocking():
                        try:
                            ctrl_msg = json.loads(raw.decode("utf-8"))
                            try:
                                print(f"[CTRL] Received: {ctrl_msg}")
                            except Exception:
                                pass
                            if ctrl_msg.get("type") == "stats_snapshot" and stats:
                                getattr(stats, "request_manual_snapshot")(now_ts)
                            elif ctrl_msg.get("type") == "start_recording":
                                self._begin_recording()
                            elif ctrl_msg.get("type") == "stop_recording":
                                self._end_recording()
                            elif ctrl_msg.get("type") == "export_excel":
                                self._kickoff_export()
                            elif ctrl_msg.get("type") == "do_write":
                                self._handle_do_write(ctrl_msg)
                            elif ctrl_msg.get("type") == "ao_write":
                                self._handle_ao_write(ctrl_msg)
                            elif ctrl_msg.get("type") == "plugin_inject_fail":
                                self._handle_inject_fail(ctrl_msg)
                        except Exception as e:
                            try:
                                print(f"[WARN] Control handling error: {e}")
                            except Exception:
                                pass
                    if self.alarm_engine is not None:
                        states, summary, events = self.alarm_engine.evaluate(vals, now_ts)
                        for ev in events:
                            ev_ts = float(ev.get("ts", now_ts))
                            ev["ts_hms"] = _format_local_hms(ev_ts)
                            print(f"[ALARM] {ev['alias']}: {ev['from']} -> {ev['to']} val={ev.get('value')} t={ev['ts_hms']}")
                        if self._events_sink is not None and events:
                            try:
                                self._events_sink.append_many(events)
                            except Exception:
                                pass
                    if self.alarm_engine is not None and not self._alarm_tick_logged:
                        try:
                            print(f"[INFO] AlarmEngine tick sample: states={states} summary={summary} events={len(events)}")
                        except Exception:
                            pass
                        self._alarm_tick_logged = True
                    payload = json.dumps({
                        "ts": time.time(),
                        "values": vals,
                        "units": units,
                        "states": states,
                        "alarm_summary": summary,
                        "alarm_events": events,
                        "recording": bool(self._recording),
                    }).encode("utf-8")
                    self.bus.publish_telemetry(payload)
                    # Append to Parquet data stream when recording
                    try:
                        if self._recording and self._parquet is not None:
                            self._parquet.append(now_ts, vals, units)
                    except Exception:
                        pass
                    time.sleep(interval)
                    i += 1
        finally:
            # Stop plugins and bus gracefully
            try:
                modbus.stop()
            except Exception:
                pass
            for pid in ("CAN", "CCP", "LoadBank", "NI_DAQ", "Vaisala", "Statistics", "Calculated_Channels", "Modbus", "Cycle"):
                try:
                    p = self.plugins.get(pid)
                    if p and self._plugin_enabled.get(pid, True):
                        p.stop()
                except Exception:
                    pass
            self.bus.stop()
            # Finalize events sink (JSONL only for now)
            try:
                if self._events_sink is not None:
                    self._events_sink.finalize()
            except Exception:
                pass
            # Finalize Parquet writer
            try:
                if self._parquet is not None:
                    self._parquet.finalize()
            except Exception:
                pass

    def _kickoff_export(self) -> None:
        if self._export_in_progress:
            try:
                print("[INFO] Export already in progress; ignoring duplicate request")
            except Exception:
                pass
            return
        if self._recording:
            try:
                print("[WARN] Cannot export while recording is active. Stop recording first.")
            except Exception:
                pass
            return
        run_dir = self._run_dir or self._last_run_dir
        if run_dir is None:
            try:
                print("[WARN] Cannot export: run directory not initialized (recording=%s, _run_dir=%s, _last_run_dir=%s)" % (self._recording, self._run_dir, self._last_run_dir))
            except Exception:
                pass
            return
        self._export_in_progress = True
        def _worker() -> None:
            try:
                import importlib
                mod = importlib.import_module("src.tools.export_excel")
                outputs = mod.export_excel(run_dir)
                try:
                    print("[INFO] Excel export completed:")
                    for p in outputs:
                        print(f"  - {p}")
                except Exception:
                    pass
            except Exception as e:
                try:
                    print(f"[WARN] Excel export failed: {e}")
                except Exception:
                    pass
            finally:
                self._export_in_progress = False
        try:
            import threading
            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            print("[INFO] Started Excel export in background")
        except Exception as e:
            self._export_in_progress = False
            try:
                print(f"[WARN] Failed to start export thread: {e}")
            except Exception:
                pass

    def _handle_do_write(self, msg: Dict[str, Any]) -> None:
        alias = str(msg.get("alias", ""))
        state = int(bool(msg.get("state", 0)))
        if not alias:
            return
        try:
            if not self._plugin_enabled.get("NI_DAQ", True):
                print("[WARN] DO write ignored: NI_DAQ disabled")
                return
            nidaq = self.plugins.get("NI_DAQ")
            if nidaq is None:
                print("[WARN] DO write ignored: NI_DAQ not present")
                return
            getattr(nidaq, "write_do")(alias, state)
        except Exception:
            pass

    def _handle_ao_write(self, msg: Dict[str, Any]) -> None:
        alias = str(msg.get("alias", ""))
        try:
            value = float(msg.get("value", 0.0))
        except Exception:
            value = 0.0
        if not alias:
            return
        try:
            if not self._plugin_enabled.get("NI_DAQ", True):
                print("[WARN] AO write ignored: NI_DAQ disabled")
                return
            nidaq = self.plugins.get("NI_DAQ")
            if nidaq is None:
                print("[WARN] AO write ignored: NI_DAQ not present")
                return
            getattr(nidaq, "write_ao")(alias, value)
        except Exception:
            pass

    def request_stop(self) -> None:
        self._running = False

    def _handle_inject_fail(self, msg: Dict[str, Any]) -> None:
        plugin = str(msg.get("plugin", ""))
        mode = str(msg.get("mode", "read_error"))
        count = int(msg.get("count", 1))
        duration_s = float(msg.get("duration_s", 0.0))
        if not plugin:
            return
        try:
            p = self.plugins.get(plugin)
            if not p:
                print(f"[WARN] Inject fail ignored: plugin not found: {plugin}")
                return
            if hasattr(p, "inject_failure"):
                getattr(p, "inject_failure")(mode, count, duration_s)
                print(f"[INFO] Injected failure into {plugin}: mode={mode} count={count} duration_s={duration_s}")
        except Exception as e:
            try:
                print(f"[WARN] Inject fail error: {e}")
            except Exception:
                pass

    def stop(self) -> None:
        self._running = False

    def _register_builtin_specs(self) -> None:
        # Register known plugin specs with config file names; real classes TBD
        class _Stub(BasePlugin):
            id = "stub"

        specs = [
            PluginSpec(id="NI_DAQ", cls=NiDAQPlugin, config_name="ni_daq.yaml"),
            PluginSpec(id="CAN", cls=CANPlugin, config_name="can.yaml"),
            PluginSpec(id="CCP", cls=CCPPlugin, config_name="ccp.yaml"),
            PluginSpec(id="Calculated_Channels", cls=CalculatedChannelsPlugin, config_name="calculated_channels.yaml"),
            PluginSpec(id="Cycle", cls=CyclePlugin, config_name="cycle.yaml"),
            PluginSpec(id="LoadBank", cls=LoadBankPlugin, config_name="loadbank.yaml"),
            PluginSpec(id="Modbus", cls=ModbusPlugin, config_name="modbus.yaml"),
            PluginSpec(id="Statistics", cls=StatisticsPlugin, config_name="statistics.yaml"),
            PluginSpec(id="Vaisala", cls=VaisalaPlugin, config_name="vaisala.yaml"),
            PluginSpec(id="EngineTest", cls=_Stub, config_name="engine_test.yaml"),
            PluginSpec(id="Channel_Manager", cls=_Stub, config_name="channel_manager.yaml"),
        ]
        for s in specs:
            self.registry.register(s)

    def _begin_recording(self) -> None:
        if self._recording:
            print("[INFO] Recording already active")
            return
        try:
            import time as _t
            import yaml as _yaml  # type: ignore
            # Build run folder name: testcell_mmddyy_hhmmss_enginetype_engineserialnumber_testtype
            mmddyy = _t.strftime("%m%d%y")
            hhmmss = _t.strftime("%H%M%S")
            # Load test_cell from launch selections
            test_cell = "unknown"
            try:
                plug_cfg_path = (self.configs_dir / "plugins.yaml").resolve()
                plug_cfg = _yaml.safe_load(plug_cfg_path.read_text(encoding="utf-8")) or {}
                test_cell = str(plug_cfg.get("test_cell", "unknown")).strip() or "unknown"
            except Exception:
                pass
            # Load EngineTest metadata fields
            engine_type = "unknown"; engine_sn = "unknown"; test_type = "unknown"
            try:
                et_path = (self.configs_dir / "engine_test.yaml").resolve()
                et_cfg = _yaml.safe_load(et_path.read_text(encoding="utf-8")) or {}
                req = et_cfg.get("required_fields") or {}
                engine_type = str(req.get("engine_type", "unknown")).strip() or "unknown"
                engine_sn = str(req.get("engine_serial_number", "unknown")).strip() or "unknown"
                test_type = str(req.get("test_type", "unknown")).strip() or "unknown"
            except Exception:
                pass
            def _sanitize(part: str) -> str:
                try:
                    ok = []
                    for ch in str(part):
                        if ch.isalnum() or ch in (" ", "_", "-", "."):
                            ok.append(ch)
                    s = ("".join(ok)).strip()
                    # Avoid empty segments
                    return s if s else "unknown"
                except Exception:
                    return "unknown"
            run_name = f"{_sanitize(test_cell)}_{mmddyy}_{hhmmss}_{_sanitize(engine_type)}_{_sanitize(engine_sn)}_{_sanitize(test_type)}"
            run_dir = (self.configs_dir.parent / f"runs/{run_name}").resolve()
            self._run_dir = run_dir
            self._last_run_dir = run_dir
            self._events_sink = AlarmEventsSink(run_dir)
            self._stats_sink = StatsSnapshotsSink(run_dir)
            # Build Parquet settings from Channel Manager config if provided
            settings = self._build_parquet_settings_from_channel_cfg()
            self._parquet = ParquetWriter(run_dir, settings)
            try:
                self._parquet.snapshot_configs(self.configs_dir)
            except Exception:
                pass
            meta = {
                "run_id": run_name,
                "run_start_iso8601": _t.strftime("%Y-%m-%dT%H:%M:%S", _t.localtime()),
                "recording_rate_hz": float(self.channel_cfg.get("recording_rate_hz", self.settings.recording_rate_hz)),
                "plugins": sorted(list(self.plugins.keys())),
                "test_cell": test_cell,
                "engine_type": engine_type,
                "engine_serial_number": engine_sn,
                "test_type": test_type,
            }
            try:
                (run_dir / "metadata.yaml").write_text(_yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
            except Exception:
                pass
            self._recording = True
            print(f"[INFO] Started recording: {str(run_dir)}")
        except Exception as e:
            print(f"[WARN] Failed to start recording: {e}")

    def _end_recording(self) -> None:
        if not self._recording:
            print("[INFO] Recording already stopped")
            return
        try:
            try:
                if self._parquet is not None:
                    self._parquet.finalize()
            except Exception:
                pass
            try:
                if self._events_sink is not None:
                    self._events_sink.finalize()
            except Exception:
                pass
            print("[INFO] Recording stopped and files finalized")
        finally:
            self._recording = False
            self._parquet = None
            self._events_sink = None
            self._stats_sink = None
            self._run_dir = None

    def _build_parquet_settings_from_channel_cfg(self) -> ParquetWriterSettings:
        """Create ParquetWriterSettings from Channel Manager config (optional).
        Expected YAML (configs/channel_manager.yaml):
          storage:
            chunk_duration_s: 1
            segment_time_limit_s: 14400
            segment_size_limit_mb: 100
            coalesce_on_finalize: true
            keep_chunk_files: false
        """
        cfg = self.channel_cfg or {}
        storage_cfg = cfg.get("storage", {}) or {}
        defaults = ParquetWriterSettings()
        def _get_float(key: str, default: float) -> float:
            try:
                v = storage_cfg.get(key)
                return float(v) if v is not None else default
            except Exception:
                return default
        def _get_bool(key: str, default: bool) -> bool:
            try:
                v = storage_cfg.get(key)
                return bool(v) if v is not None else default
            except Exception:
                return default
        return ParquetWriterSettings(
            chunk_duration_s=_get_float("chunk_duration_s", defaults.chunk_duration_s),
            segment_time_limit_s=_get_float("segment_time_limit_s", defaults.segment_time_limit_s),
            segment_size_limit_mb=_get_float("segment_size_limit_mb", defaults.segment_size_limit_mb),
            coalesce_on_finalize=_get_bool("coalesce_on_finalize", defaults.coalesce_on_finalize),
            keep_chunk_files=_get_bool("keep_chunk_files", defaults.keep_chunk_files),
        )


