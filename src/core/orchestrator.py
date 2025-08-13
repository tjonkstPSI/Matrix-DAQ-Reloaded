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
from .alarms.engine import AlarmEngine
from .storage.alarm_events import AlarmEventsSink
from ..plugins.statistics import StatisticsPlugin
from .storage.stats_snapshots import StatsSnapshotsSink
from ..plugins.vaisala import VaisalaPlugin


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
        for pid, plugin in self.plugins.items():
            plugin.load_config()
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
        # Prepare run directory for event logging (simple timestamped folder)
        try:
            import time as _t
            run_base = _t.strftime("%m%d%y_%H%M%S")
            run_dir = (self.configs_dir.parent / f"runs/{run_base}").resolve()
            self._events_sink = AlarmEventsSink(run_dir)
            self._stats_sink = StatsSnapshotsSink(run_dir)
            print(f"[INFO] AlarmEvents sink: {str(run_dir)}")
        except Exception as e:
            print(f"[WARN] AlarmEvents sink initialization failed: {e}")

    def run(self) -> None:
        # Simple lifecycle exercise: configure/validate/arm/start/stop simulated Modbus
        modbus = self.plugins.get("Modbus")
        if modbus is None:
            return
        modbus.configure()
        status = modbus.validate()
        if not status.ok:
            print(f"[ERROR] Modbus validate failed in run(): {status.message}")
            return
        # Simulate a short run: generate a few samples
        modbus.arm()
        modbus.start()
        try:
            # Generate ticks per run_mode, publish telemetry merging plugins
            import time, json
            can = self.plugins.get("CAN")
            ccp = self.plugins.get("CCP")
            lb = self.plugins.get("LoadBank")
            cycle = self.plugins.get("Cycle")
            stats = self.plugins.get("Statistics")
            vaisala = self.plugins.get("Vaisala")
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
            if cycle:
                cycle.configure(); cycle.validate(); cycle.start()
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
                    vals.update(getattr(modbus, "simulate_step")())
                    units.update(getattr(modbus, "units")())
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
                    # Handle control messages (e.g., manual stats)
                    for raw in self.bus.recv_controls_nonblocking():
                        try:
                            ctrl_msg = json.loads(raw.decode("utf-8"))
                            if ctrl_msg.get("type") == "stats_snapshot" and stats:
                                getattr(stats, "request_manual_snapshot")(now_ts)
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
                    }).encode("utf-8")
                    self.bus.publish_telemetry(payload)
                    time.sleep(interval)
                    i += 1
            else:
                while self._running:
                    vals = {}
                    units = {}
                    vals.update(getattr(modbus, "simulate_step")())
                    units.update(getattr(modbus, "units")())
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
                            if ctrl_msg.get("type") == "stats_snapshot" and stats:
                                getattr(stats, "request_manual_snapshot")(now_ts)
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
                    }).encode("utf-8")
                    self.bus.publish_telemetry(payload)
                    time.sleep(interval)
                    i += 1
        finally:
            # Stop plugins and bus gracefully
            try:
                modbus.stop()
            except Exception:
                pass
            for pid in ("CAN", "CCP", "LoadBank"):
                try:
                    p = self.plugins.get(pid)
                    if p:
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

    def request_stop(self) -> None:
        self._running = False

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
            PluginSpec(id="Calculated_Channels", cls=_Stub, config_name="calculated_channels.yaml"),
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


