# Author: T. Onkst | Date: 08122025

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional

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
from ..plugins.omega import OmegaPlugin
from .storage.sqlite_writer import SqliteWriter, SqliteWriterSettings
from ..plugins.channel_manager import ChannelManagerPlugin
from ..plugins.engine_test import EngineTestPlugin
from .recording import begin_recording, end_recording, kickoff_export, build_storage_settings
import json
import os


_DEBUG_PREFIXES = ("CAN/", "CCP/", "Core/", "EngineTest/", "NI_DAQ/")
_HEALTH_SUFFIXES = ("/health_ok", "/conn_ok")
# NI_DAQ/* is normally stripped as debug; keep operator diagnostics when exposed by the plugin.
_NI_DAQ_TELEMETRY_KEYS = frozenset(
    {
        "NI_DAQ/health_ok",
        "NI_DAQ/consec_failures",
        "NI_DAQ/last_good_read_age_s",
        "NI_DAQ/task_fast_alive",
        "NI_DAQ/last_error",
    }
)


def _strip_debug_keys(d: dict) -> dict:
    return {
        k: v
        for k, v in d.items()
        if (
            not k.startswith(_DEBUG_PREFIXES)
            or k.endswith(_HEALTH_SUFFIXES)
            or k in _NI_DAQ_TELEMETRY_KEYS
        )
    }


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
        self._db_writer: SqliteWriter | None = None
        self._run_dir: Path | None = None
        self._last_run_dir: Path | None = None
        self._recording: bool = False
        self._export_in_progress: bool = False
        self._plugin_enabled: Dict[str, bool] = {}
        self._tick_interval_s: float = 0.1
        self._global_sim_mode: bool = False
        self._source_map: Dict[str, str] = {}
        self._display_aliases: Dict[str, str] = {}
        self._source_map_dirty: bool = True
        self._ready_acknowledged: bool = False
        self._last_ready_publish_mono: float = 0.0
        self._ready_request_logged: bool = False
        self._core_timing_diag: Dict[str, Any] = {}
        self._perf_diag_enabled = str(os.environ.get("MATRIX_UI_PERF_DIAG", "")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self._publish_perf_diag: Dict[str, Any] = {}
        self._do_condition_states: Dict[str, int] = {}

    def start(self) -> None:
        # Placeholder: load configs, initialize IPC bus, register simulated plugins
        self._do_condition_states.clear()
        self._register_builtin_specs()
        self.plugins = self.registry.create_all()
        # Load core config
        core_cfg_path = (self.configs_dir / "core.yaml").resolve()
        self.core_cfg = load_yaml_config(core_cfg_path)
        # Load channel manager config (optional)
        ch_cfg_path = (self.configs_dir / "channel_manager.yaml").resolve()
        self.channel_cfg = load_yaml_config(ch_cfg_path)
        self._apply_channel_manager_runtime()
        # Load launch selections to determine which plugins the user enabled
        selected_set: set = set()
        global_data_mode: str = ""
        try:
            plugins_cfg_path = (self.configs_dir / "plugins.yaml").resolve()
            plugins_cfg = load_yaml_config(plugins_cfg_path)
            selected_set = {str(x) for x in (plugins_cfg.get("selected_plugins") or [])}
            global_data_mode = str(plugins_cfg.get("data_mode", "")).strip().lower()
        except Exception:
            pass
        if selected_set:
            print(f"[INFO] selected_plugins from plugins.yaml: {sorted(selected_set)}")
        else:
            print("[WARN] No selected_plugins found in plugins.yaml; all plugins default to enabled")
        self._global_sim_mode = (global_data_mode == "sim")
        if self._global_sim_mode:
            print("[INFO] Offline mode active: all plugins will run in simulation mode")
        else:
            print("[INFO] Online mode: all plugins will run in real/hardware mode")
        # Load configs for each plugin and run basic validation
        all_ok = True
        ALWAYS_ON = {"Channel_Manager", "EngineTest"}
        for pid, plugin in self.plugins.items():
            plugin.load_config()
            if self._global_sim_mode:
                plugin.mode = "sim"
            else:
                plugin.mode = "real"
            enabled = True
            try:
                if pid not in ALWAYS_ON:
                    config_enabled = bool(plugin.config.get("enabled", True))
                    in_selection = (pid in selected_set) if selected_set else True
                    enabled = in_selection and config_enabled
            except Exception:
                enabled = True
            self._plugin_enabled[pid] = enabled
            if not enabled:
                if selected_set and pid not in selected_set:
                    print(f"[INFO] Plugin '{pid}' not in selected_plugins; skipping")
                else:
                    print(f"[INFO] Plugin '{pid}' disabled by config; skipping init")
                continue
            status = plugin.validate()
            if not status.ok:
                all_ok = False
                print(f"[ERROR] Plugin '{pid}' validation failed: {status.message}")
            # Optional early configure for NI_DAQ to enumerate inventory
            if pid == "NI_DAQ" and status.ok:
                try:
                    plugin._core_tick_rate_hz = self.settings.recording_rate_hz
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
        self._publish_core_ready(force=True)
        print("[INFO] Core started; recording is idle. Use Start Recording from UI to begin.")

    def _build_source_map(self) -> Dict[str, str]:
        """Build alias -> source-group mapping from all active plugins."""
        smap: Dict[str, str] = {}

        def _tag(plugin_id, group, use_device_aliases=False, use_section_aliases=False):
            p = self.plugins.get(plugin_id) if self._plugin_enabled.get(plugin_id, True) else None
            if p is None:
                print(f"[CORE] source_map: {plugin_id} not available (disabled or not registered)")
                return
            try:
                if use_device_aliases:
                    for dev_name, aliases in p.device_aliases().items():
                        g = f"CCP {dev_name}" if dev_name else "CCP"
                        for a in aliases:
                            smap[a] = g
                elif use_section_aliases:
                    for section, aliases in p.section_aliases().items():
                        for a in aliases:
                            smap[a] = section
                else:
                    for a in p.aliases():
                        if a not in smap:
                            smap[a] = group
            except Exception as exc:
                print(f"[CORE] source_map: {plugin_id} query FAILED: {exc}")

        _tag("CCP", "CCP", use_device_aliases=True)
        _tag("NI_DAQ", "NI", use_section_aliases=True)
        _tag("Vaisala", "Environment")
        _tag("Omega", "Environment")
        _tag("CAN", "CAN")
        _tag("Modbus", "Modbus")
        _tag("Calculated_Channels", "Calculated")
        _tag("LoadBank", "System")
        _tag("Cycle", "System")
        _tag("EngineTest", "System")

        for key in ("Time_Relative_s", "iOT_Warning", "iOT_Alarm",
                     "iOT_AlmSftSdn", "iOT_AlmEmgSdn", "iDG_EngRunStp"):
            smap[key] = "System"

        return smap

    def _build_display_aliases(self) -> Dict[str, str]:
        """Build UI-only full-alias -> display-label mapping."""
        labels: Dict[str, str] = {}
        for plugin_id in ("CCP",):
            p = self.plugins.get(plugin_id) if self._plugin_enabled.get(plugin_id, True) else None
            if p is None:
                continue
            method = getattr(p, "display_aliases", None)
            if not callable(method):
                continue
            try:
                for alias, label in method().items():
                    alias_text = str(alias)
                    label_text = str(label)
                    if alias_text and label_text:
                        labels[alias_text] = label_text
            except Exception as exc:
                print(f"[CORE] display_aliases: {plugin_id} query FAILED: {exc}")
        return labels

    def _refresh_source_map(self, reason: str = "") -> None:
        """Rebuild cached display metadata used for channel placement/labels."""
        self._source_map = self._build_source_map()
        self._display_aliases = self._build_display_aliases()
        reason_text = f" ({reason})" if reason else ""
        if self._source_map:
            groups = {}
            for alias, grp in self._source_map.items():
                groups.setdefault(grp, []).append(alias)
            print(f"[CORE] source_map{reason_text}: {len(self._source_map)} aliases across {len(groups)} groups")
            for g, aliases in sorted(groups.items()):
                print(f"[CORE]   {g}: {len(aliases)} channels")
        else:
            print(f"[CORE] source_map{reason_text} is EMPTY — channels will fall to 'Other'")

    def _record_core_timing(
        self,
        *,
        tick_ms: float,
        interval_s: float,
        ccp_ms: float,
        nidaq_ms: float,
        other_plugins_ms: float,
        stats_ms: float,
        controls_ms: float,
        alarms_ms: float,
        calc_ms: float,
        outputs_ms: float,
        do_conditions_ms: float,
        console_msgs_ms: float,
        strip_ms: float,
        json_ms: float,
        publish_ms: float,
        db_ms: float,
        row_appended: bool,
    ) -> None:
        """Print low-volume timing summaries while recording."""
        if not self._recording:
            self._core_timing_diag = {}
            return
        try:
            import time as _time

            now = _time.monotonic()
            diag = self._core_timing_diag
            if not diag:
                diag.update(
                    {
                        "start": now,
                        "ticks": 0,
                        "rows": 0,
                        "overruns": 0,
                        "tick_ms": [],
                        "ccp_ms": [],
                        "nidaq_ms": [],
                        "other_plugins_ms": [],
                        "stats_ms": [],
                        "controls_ms": [],
                        "alarms_ms": [],
                        "calc_ms": [],
                        "outputs_ms": [],
                        "do_conditions_ms": [],
                        "console_msgs_ms": [],
                        "strip_ms": [],
                        "json_ms": [],
                        "publish_ms": [],
                        "db_ms": [],
                    }
                )

            diag["ticks"] = int(diag.get("ticks", 0)) + 1
            if row_appended:
                diag["rows"] = int(diag.get("rows", 0)) + 1
            if float(tick_ms) > (float(interval_s) * 1000.0):
                diag["overruns"] = int(diag.get("overruns", 0)) + 1

            for key, value in (
                ("tick_ms", tick_ms),
                ("ccp_ms", ccp_ms),
                ("nidaq_ms", nidaq_ms),
                ("other_plugins_ms", other_plugins_ms),
                ("stats_ms", stats_ms),
                ("controls_ms", controls_ms),
                ("alarms_ms", alarms_ms),
                ("calc_ms", calc_ms),
                ("outputs_ms", outputs_ms),
                ("do_conditions_ms", do_conditions_ms),
                ("console_msgs_ms", console_msgs_ms),
                ("strip_ms", strip_ms),
                ("json_ms", json_ms),
                ("publish_ms", publish_ms),
                ("db_ms", db_ms),
            ):
                vals = diag.setdefault(key, [])
                if isinstance(vals, list):
                    vals.append(float(value))

            elapsed = max(0.001, now - float(diag.get("start", now)))
            if elapsed < 5.0:
                return

            def _avg(values: Any) -> float:
                return (sum(values) / float(len(values))) if isinstance(values, list) and values else 0.0

            def _max(values: Any) -> float:
                return max(values) if isinstance(values, list) and values else 0.0

            def _p95(values: Any) -> float:
                if not isinstance(values, list) or not values:
                    return 0.0
                ordered = sorted(values)
                idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * 0.95))))
                return float(ordered[idx])

            rows = int(diag.get("rows", 0))
            actual_hz = float(rows) / elapsed
            target_hz = 1.0 / max(0.001, float(interval_s))
            print(
                "[CORE_TIMING] "
                f"target={target_hz:.2f}Hz actual={actual_hz:.2f}Hz "
                f"rows={rows}/{elapsed:.2f}s "
                f"tick_ms avg={_avg(diag.get('tick_ms')):.1f} "
                f"p95={_p95(diag.get('tick_ms')):.1f} "
                f"max={_max(diag.get('tick_ms')):.1f} "
                f"overruns={int(diag.get('overruns', 0))} "
                f"ccp_ms avg={_avg(diag.get('ccp_ms')):.1f} "
                f"max={_max(diag.get('ccp_ms')):.1f} "
                f"nidaq_ms avg={_avg(diag.get('nidaq_ms')):.1f} "
                f"max={_max(diag.get('nidaq_ms')):.1f} "
                f"other_plugins_ms avg={_avg(diag.get('other_plugins_ms')):.1f} "
                f"max={_max(diag.get('other_plugins_ms')):.1f} "
                f"stats_ms avg={_avg(diag.get('stats_ms')):.1f} "
                f"max={_max(diag.get('stats_ms')):.1f} "
                f"controls_ms avg={_avg(diag.get('controls_ms')):.1f} "
                f"max={_max(diag.get('controls_ms')):.1f} "
                f"alarms_ms avg={_avg(diag.get('alarms_ms')):.1f} "
                f"max={_max(diag.get('alarms_ms')):.1f} "
                f"calc_ms avg={_avg(diag.get('calc_ms')):.1f} "
                f"max={_max(diag.get('calc_ms')):.1f} "
                f"outputs_ms avg={_avg(diag.get('outputs_ms')):.1f} "
                f"max={_max(diag.get('outputs_ms')):.1f} "
                f"do_conditions_ms avg={_avg(diag.get('do_conditions_ms')):.1f} "
                f"max={_max(diag.get('do_conditions_ms')):.1f} "
                f"console_msgs_ms avg={_avg(diag.get('console_msgs_ms')):.1f} "
                f"max={_max(diag.get('console_msgs_ms')):.1f} "
                f"strip_ms avg={_avg(diag.get('strip_ms')):.1f} "
                f"max={_max(diag.get('strip_ms')):.1f} "
                f"db_ms avg={_avg(diag.get('db_ms')):.1f} "
                f"max={_max(diag.get('db_ms')):.1f} "
                f"json_ms avg={_avg(diag.get('json_ms')):.1f} "
                f"max={_max(diag.get('json_ms')):.1f} "
                f"publish_ms avg={_avg(diag.get('publish_ms')):.1f} "
                f"max={_max(diag.get('publish_ms')):.1f}"
            )
            self._core_timing_diag = {}
        except Exception:
            pass

    def _record_publish_perf(
        self,
        *,
        interval_s: float,
        payload_len: int,
        value_count: int,
        json_ms: float = 0.0,
        publish_ms: float = 0.0,
    ) -> None:
        """Low-volume core/UI rate diagnostics when MATRIX_UI_PERF_DIAG=1."""
        if not self._perf_diag_enabled:
            return
        try:
            import time as _time

            now = _time.perf_counter()
            diag = self._publish_perf_diag
            if not diag:
                diag.update(
                    {
                        "start": now,
                        "ticks": 0,
                        "payload_kb": [],
                        "value_count": [],
                        "json_ms": [],
                        "publish_ms": [],
                        "target_hz": 1.0 / max(0.001, float(interval_s)),
                    }
                )
            diag["ticks"] = int(diag.get("ticks", 0)) + 1
            for key, value in (
                ("payload_kb", float(payload_len) / 1024.0),
                ("value_count", float(value_count)),
                ("json_ms", float(json_ms)),
                ("publish_ms", float(publish_ms)),
            ):
                vals = diag.setdefault(key, [])
                if isinstance(vals, list):
                    vals.append(float(value))
            elapsed = max(0.001, now - float(diag.get("start", now)))
            if elapsed < 5.0:
                return

            def _avg(values: Any) -> float:
                return (sum(values) / float(len(values))) if isinstance(values, list) and values else 0.0

            def _max(values: Any) -> float:
                return max(values) if isinstance(values, list) and values else 0.0

            ticks = int(diag.get("ticks", 0))
            print(
                "[CORE_PERF] publish "
                f"target_hz={float(diag.get('target_hz', 0.0)):.2f} actual_hz={ticks / elapsed:.2f} "
                f"ticks={ticks} "
                f"payload_kb_avg={_avg(diag.get('payload_kb')):.2f} payload_kb_max={_max(diag.get('payload_kb')):.2f} "
                f"value_count_max={_max(diag.get('value_count')):.0f} "
                f"json_ms_avg={_avg(diag.get('json_ms')):.2f} json_ms_max={_max(diag.get('json_ms')):.2f} "
                f"publish_ms_avg={_avg(diag.get('publish_ms')):.2f} publish_ms_max={_max(diag.get('publish_ms')):.2f}",
                flush=True,
            )
            self._publish_perf_diag = {}
        except Exception:
            pass

    def run(self) -> None:
        # Initialize plugins; Modbus is optional (can be disabled)
        modbus = self.plugins.get("Modbus") if self._plugin_enabled.get("Modbus", True) else None
        if modbus is not None:
            modbus = self._start_plugin_runtime(
                "Modbus",
                modbus,
                arm=True,
                continue_on_validate_error=False,
            )
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
            engine_test = self.plugins.get("EngineTest") if self._plugin_enabled.get("EngineTest", True) else None
            if engine_test:
                try:
                    engine_test.configure()
                except Exception:
                    pass
            can = self._start_plugin_runtime("CAN", can) if can else None
            ccp = self._start_plugin_runtime("CCP", ccp) if ccp else None
            lb = self._start_plugin_runtime("LoadBank", lb) if lb else None
            stats = self._start_plugin_runtime("Statistics", stats) if stats else None
            vaisala = self._start_plugin_runtime("Vaisala", vaisala) if vaisala else None
            if nidaq:
                nidaq._core_tick_rate_hz = self.settings.recording_rate_hz
                nidaq = self._start_plugin_runtime("NI_DAQ", nidaq)
            cycle = self._start_plugin_runtime("Cycle", cycle) if cycle else None
            calc = self.plugins.get("Calculated_Channels") if self._plugin_enabled.get("Calculated_Channels", True) else None
            calc = self._start_plugin_runtime("Calculated_Channels", calc) if calc else None
            run_mode = str(self.core_cfg.get("run_mode", "demo")).lower()
            demo_ticks = int(self.core_cfg.get("demo_ticks", 50))
            interval = float(self.core_cfg.get("tick_interval_s", 0.1))
            try:
                interval = float(self._tick_interval_s)
            except Exception:
                interval = float(self.core_cfg.get("tick_interval_s", 0.1))
            i = 0
            t0 = time.time()
            last_tick_start_mono: float | None = None
            next_tick_deadline = time.monotonic() + max(0.001, interval)
            
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
            _last_cycle_setpoint: Optional[float] = None
            self._refresh_source_map("startup")
            self._publish_core_ready(force=True)
            if run_mode == "demo":
                for _ in range(demo_ticks):
                    if not self._running:
                        break
                    self._publish_core_ready()
                    interval = max(0.001, float(self._tick_interval_s))
                    tick_start_mono = time.monotonic()
                    tick_dt_s = interval if last_tick_start_mono is None else max(0.0, tick_start_mono - last_tick_start_mono)
                    tick_jitter_s = tick_dt_s - interval
                    last_tick_start_mono = tick_start_mono
                    modbus = self.plugins.get("Modbus") if self._plugin_enabled.get("Modbus") else None
                    can = self.plugins.get("CAN") if self._plugin_enabled.get("CAN") else None
                    ccp = self.plugins.get("CCP") if self._plugin_enabled.get("CCP") else None
                    lb = self.plugins.get("LoadBank") if self._plugin_enabled.get("LoadBank") else None
                    cycle = self.plugins.get("Cycle") if self._plugin_enabled.get("Cycle") else None
                    stats = self.plugins.get("Statistics") if self._plugin_enabled.get("Statistics") else None
                    vaisala = self.plugins.get("Vaisala") if self._plugin_enabled.get("Vaisala") else None
                    omega = self.plugins.get("Omega") if self._plugin_enabled.get("Omega") else None
                    nidaq = self.plugins.get("NI_DAQ") if self._plugin_enabled.get("NI_DAQ") else None
                    calc = self.plugins.get("Calculated_Channels") if self._plugin_enabled.get("Calculated_Channels") else None
                    engine_test = self.plugins.get("EngineTest") if self._plugin_enabled.get("EngineTest") else None
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
                    if lb:
                        vals.update(getattr(lb, "simulate_step")())
                        units.update(getattr(lb, "units")())
                    if vaisala:
                        vals.update(getattr(vaisala, "simulate_step")())
                        units.update(getattr(vaisala, "units")())
                    if omega:
                        vals.update(getattr(omega, "simulate_step")())
                        units.update(getattr(omega, "units")())
                    if engine_test:
                        vals.update(getattr(engine_test, "simulate_step")())
                        units.update(getattr(engine_test, "units")())
                    if cycle:
                        _cyc_was_running = getattr(cycle, "_state", "idle") == "running"
                        vals.update(getattr(cycle, "simulate_step")())
                        units.update(getattr(cycle, "units")())
                        _cyc_state = getattr(cycle, "_state", "idle")
                        if lb and (_cyc_state == "running" or _cyc_was_running):
                            _sp = cycle.current_setpoint_kw()
                            if _sp != _last_cycle_setpoint:
                                lb.command_setpoint_kw(_sp)
                                print(f"[CYCLE->LB] Setpoint changed: {_last_cycle_setpoint} -> {_sp} kW")
                                _last_cycle_setpoint = _sp
                        elif _cyc_state != "running":
                            if _last_cycle_setpoint is not None:
                                print(f"[CYCLE->LB] Cycle state={_cyc_state}, holding last setpoint ({_last_cycle_setpoint} kW)")
                            _last_cycle_setpoint = None
                    # Capture current timestamp for this tick
                    now_ts = time.time()
                    if vaisala:
                        try:
                            vaisala.update_telemetry(vals)
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
                            try:
                                self.bus.publish_status(json.dumps(sev).encode("utf-8"))
                            except Exception:
                                pass
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
                            if ctrl_msg.get("type") == "shutdown":
                                self.request_stop()
                            elif ctrl_msg.get("type") == "core_ready_request":
                                self._publish_core_ready(force=True, reason="request")
                            elif ctrl_msg.get("type") == "ui_ready_ack":
                                self._ready_acknowledged = True
                            elif ctrl_msg.get("type") == "stats_snapshot" and stats:
                                getattr(stats, "request_manual_snapshot")(now_ts)
                            elif ctrl_msg.get("type") == "lock_test":
                                self._engine_test_lock()
                            elif ctrl_msg.get("type") == "unlock_test":
                                self._engine_test_unlock()
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
                            elif ctrl_msg.get("type") == "loadbank_command":
                                self._handle_loadbank_command(ctrl_msg)
                            elif ctrl_msg.get("type") == "plugin_inject_fail":
                                self._handle_inject_fail(ctrl_msg)
                            elif ctrl_msg.get("type") == "ccp_test":
                                self._kickoff_ccp_test(ctrl_msg)
                            elif ctrl_msg.get("type") == "reload_plugin":
                                pid = str(ctrl_msg.get("plugin", ""))
                                self._stash_session_keys(ctrl_msg)
                                self._reload_plugin(pid)
                            elif ctrl_msg.get("type") == "sync_plugin_selections":
                                self._sync_all_plugin_selections()
                            elif ctrl_msg.get("type") in ("cycle_play", "cycle_pause", "cycle_seek", "cycle_set_loops", "cycle_set_start_with_test"):
                                self._handle_cycle_command(ctrl_msg)
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
                    # Publish internal alarm/status booleans.
                    vals["iOT_Warning"] = 1.0 if bool(summary.get("any_warning", False)) else 0.0
                    vals["iOT_Alarm"] = 1.0 if bool(summary.get("any_shutdown", False)) else 0.0
                    vals["iOT_AlmSftSdn"] = 1.0 if summary.get("any_soft_shutdown") else 0.0
                    vals["iOT_AlmEmgSdn"] = 1.0 if summary.get("any_hard_shutdown") else 0.0
                    vals["iDG_EngRunStp"] = 1.0 if summary.get("engine_running") else 0.0
                    units["iOT_Warning"] = ""
                    units["iOT_Alarm"] = ""
                    units["iOT_AlmSftSdn"] = ""
                    units["iOT_AlmEmgSdn"] = ""
                    units["iDG_EngRunStp"] = ""
                    # Calculated channels run LAST so all source values (plugins, alarms, iOT) are available
                    if calc is not None:
                        try:
                            calc_vals = getattr(calc, "simulate_step")(vals)
                            vals.update(calc_vals)
                            units.update(getattr(calc, "units")())
                        except Exception:
                            pass
                    self._evaluate_do_conditions(vals)
                    self._forward_console_msgs(vals)
                    pub_vals = _strip_debug_keys(vals)
                    pub_units = _strip_debug_keys(units)
                    payload = json.dumps({
                        "ts": time.time(),
                        "values": pub_vals,
                        "units": pub_units,
                        "states": states,
                        "alarm_summary": summary,
                        "alarm_events": events,
                        "recording": bool(self._recording),
                        "source_map": self._source_map,
                        "display_aliases": self._display_aliases,
                    }).encode("utf-8")
                    # One-time NI_DAQ value count diagnostic
                    try:
                        c = int(getattr(self, "_core_pub_diag_count", 0))
                        if c < 5:
                            print(f"[CORE] publish: total values={len(vals or {})}")
                            setattr(self, "_core_pub_diag_count", c + 1)
                    except Exception:
                        pass
                    self.bus.publish_telemetry(payload)
                    self._record_publish_perf(
                        interval_s=interval,
                        payload_len=len(payload),
                        value_count=len(pub_vals),
                    )
                    try:
                        if self._recording and self._db_writer is not None:
                            self._db_writer.append(now_ts, pub_vals, pub_units)
                    except Exception:
                        pass
                    sleep_s = max(0.0, next_tick_deadline - time.monotonic())
                    if sleep_s > 0.0:
                        time.sleep(sleep_s)
                    now_mono = time.monotonic()
                    if now_mono > next_tick_deadline + interval:
                        next_tick_deadline = now_mono + interval
                    else:
                        next_tick_deadline += interval
                    i += 1
            else:
                while self._running:
                    self._publish_core_ready()
                    interval = max(0.001, float(self._tick_interval_s))
                    tick_start_mono = time.monotonic()
                    tick_body_start = time.perf_counter()
                    ccp_ms = 0.0
                    nidaq_ms = 0.0
                    other_plugins_ms = 0.0
                    stats_ms = 0.0
                    controls_ms = 0.0
                    alarms_ms = 0.0
                    calc_ms = 0.0
                    outputs_ms = 0.0
                    do_conditions_ms = 0.0
                    console_msgs_ms = 0.0
                    strip_ms = 0.0
                    json_ms = 0.0
                    publish_ms = 0.0
                    db_ms = 0.0
                    row_appended = False
                    tick_dt_s = interval if last_tick_start_mono is None else max(0.0, tick_start_mono - last_tick_start_mono)
                    tick_jitter_s = tick_dt_s - interval
                    last_tick_start_mono = tick_start_mono
                    modbus = self.plugins.get("Modbus") if self._plugin_enabled.get("Modbus") else None
                    can = self.plugins.get("CAN") if self._plugin_enabled.get("CAN") else None
                    ccp = self.plugins.get("CCP") if self._plugin_enabled.get("CCP") else None
                    lb = self.plugins.get("LoadBank") if self._plugin_enabled.get("LoadBank") else None
                    cycle = self.plugins.get("Cycle") if self._plugin_enabled.get("Cycle") else None
                    stats = self.plugins.get("Statistics") if self._plugin_enabled.get("Statistics") else None
                    vaisala = self.plugins.get("Vaisala") if self._plugin_enabled.get("Vaisala") else None
                    omega = self.plugins.get("Omega") if self._plugin_enabled.get("Omega") else None
                    nidaq = self.plugins.get("NI_DAQ") if self._plugin_enabled.get("NI_DAQ") else None
                    calc = self.plugins.get("Calculated_Channels") if self._plugin_enabled.get("Calculated_Channels") else None
                    engine_test = self.plugins.get("EngineTest") if self._plugin_enabled.get("EngineTest") else None
                    vals = {}
                    units = {}
                    if modbus is not None:
                        _phase_start = time.perf_counter()
                        vals.update(getattr(modbus, "simulate_step")())
                        units.update(getattr(modbus, "units")())
                        other_plugins_ms += (time.perf_counter() - _phase_start) * 1000.0
                    if nidaq:
                        _phase_start = time.perf_counter()
                        vals.update(getattr(nidaq, "simulate_step")())
                        units.update(getattr(nidaq, "units")())
                        nidaq_ms += (time.perf_counter() - _phase_start) * 1000.0
                    if can:
                        _phase_start = time.perf_counter()
                        vals.update(getattr(can, "simulate_step")())
                        units.update(getattr(can, "units")())
                        other_plugins_ms += (time.perf_counter() - _phase_start) * 1000.0
                    if ccp:
                        _phase_start = time.perf_counter()
                        vals.update(getattr(ccp, "simulate_step")())
                        units.update(getattr(ccp, "units")())
                        ccp_ms += (time.perf_counter() - _phase_start) * 1000.0
                    if lb:
                        _phase_start = time.perf_counter()
                        vals.update(getattr(lb, "simulate_step")())
                        units.update(getattr(lb, "units")())
                        other_plugins_ms += (time.perf_counter() - _phase_start) * 1000.0
                    if vaisala:
                        _phase_start = time.perf_counter()
                        vals.update(getattr(vaisala, "simulate_step")())
                        units.update(getattr(vaisala, "units")())
                        other_plugins_ms += (time.perf_counter() - _phase_start) * 1000.0
                    if omega:
                        _phase_start = time.perf_counter()
                        vals.update(getattr(omega, "simulate_step")())
                        units.update(getattr(omega, "units")())
                        other_plugins_ms += (time.perf_counter() - _phase_start) * 1000.0
                    if engine_test:
                        _phase_start = time.perf_counter()
                        vals.update(getattr(engine_test, "simulate_step")())
                        units.update(getattr(engine_test, "units")())
                        other_plugins_ms += (time.perf_counter() - _phase_start) * 1000.0
                    if cycle:
                        _phase_start = time.perf_counter()
                        _cyc_was_running = getattr(cycle, "_state", "idle") == "running"
                        vals.update(getattr(cycle, "simulate_step")())
                        units.update(getattr(cycle, "units")())
                        _cyc_state = getattr(cycle, "_state", "idle")
                        if lb and (_cyc_state == "running" or _cyc_was_running):
                            _sp = cycle.current_setpoint_kw()
                            if _sp != _last_cycle_setpoint:
                                lb.command_setpoint_kw(_sp)
                                print(f"[CYCLE->LB] Setpoint changed: {_last_cycle_setpoint} -> {_sp} kW")
                                _last_cycle_setpoint = _sp
                        elif _cyc_state != "running":
                            if _last_cycle_setpoint is not None:
                                print(f"[CYCLE->LB] Cycle state={_cyc_state}, holding last setpoint ({_last_cycle_setpoint} kW)")
                            _last_cycle_setpoint = None
                        other_plugins_ms += (time.perf_counter() - _phase_start) * 1000.0
                    now_ts = time.time()
                    if vaisala:
                        _phase_start = time.perf_counter()
                        try:
                            vaisala.update_telemetry(vals)
                        except Exception:
                            pass
                        other_plugins_ms += (time.perf_counter() - _phase_start) * 1000.0
                    # Update statistics plugin and handle outputs (persist only)
                    if stats:
                        _phase_start = time.perf_counter()
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
                            try:
                                self.bus.publish_status(json.dumps(sev).encode("utf-8"))
                            except Exception:
                                pass
                        stats_ms += (time.perf_counter() - _phase_start) * 1000.0
                    # Add relative time channel from core
                    elapsed = time.time() - t0
                    vals["Time_Relative_s"] = elapsed
                    units["Time_Relative_s"] = "s"
                    # Evaluate alarms
                    states, summary, events = ({}, {"any_warning": False, "any_shutdown": False}, [])
                    # Handle control messages
                    _phase_start = time.perf_counter()
                    for raw in self.bus.recv_controls_nonblocking():
                        try:
                            ctrl_msg = json.loads(raw.decode("utf-8"))
                            try:
                                print(f"[CTRL] Received: {ctrl_msg}")
                            except Exception:
                                pass
                            if ctrl_msg.get("type") == "shutdown":
                                self.request_stop()
                            elif ctrl_msg.get("type") == "core_ready_request":
                                self._publish_core_ready(force=True, reason="request")
                            elif ctrl_msg.get("type") == "ui_ready_ack":
                                self._ready_acknowledged = True
                            elif ctrl_msg.get("type") == "stats_snapshot" and stats:
                                getattr(stats, "request_manual_snapshot")(now_ts)
                            elif ctrl_msg.get("type") == "lock_test":
                                self._engine_test_lock()
                            elif ctrl_msg.get("type") == "unlock_test":
                                self._engine_test_unlock()
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
                            elif ctrl_msg.get("type") == "loadbank_command":
                                self._handle_loadbank_command(ctrl_msg)
                            elif ctrl_msg.get("type") == "reload_plugin":
                                pid = str(ctrl_msg.get("plugin", ""))
                                self._stash_session_keys(ctrl_msg)
                                self._reload_plugin(pid)
                            elif ctrl_msg.get("type") == "plugin_inject_fail":
                                self._handle_inject_fail(ctrl_msg)
                            elif ctrl_msg.get("type") == "ccp_test":
                                self._kickoff_ccp_test(ctrl_msg)
                            elif ctrl_msg.get("type") == "sync_plugin_selections":
                                self._sync_all_plugin_selections()
                            elif ctrl_msg.get("type") in ("cycle_play", "cycle_pause", "cycle_seek", "cycle_set_loops", "cycle_set_start_with_test"):
                                self._handle_cycle_command(ctrl_msg)
                        except Exception as e:
                            try:
                                print(f"[WARN] Control handling error: {e}")
                            except Exception:
                                pass
                    controls_ms += (time.perf_counter() - _phase_start) * 1000.0
                    if self.alarm_engine is not None:
                        _phase_start = time.perf_counter()
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
                    if self.alarm_engine is not None:
                        alarms_ms += (time.perf_counter() - _phase_start) * 1000.0
                    # Publish internal alarm/status booleans.
                    vals["iOT_Warning"] = 1.0 if bool(summary.get("any_warning", False)) else 0.0
                    vals["iOT_Alarm"] = 1.0 if bool(summary.get("any_shutdown", False)) else 0.0
                    vals["iOT_AlmSftSdn"] = 1.0 if summary.get("any_soft_shutdown") else 0.0
                    vals["iOT_AlmEmgSdn"] = 1.0 if summary.get("any_hard_shutdown") else 0.0
                    vals["iDG_EngRunStp"] = 1.0 if summary.get("engine_running") else 0.0
                    units["iOT_Warning"] = ""
                    units["iOT_Alarm"] = ""
                    units["iOT_AlmSftSdn"] = ""
                    units["iOT_AlmEmgSdn"] = ""
                    units["iDG_EngRunStp"] = ""
                    # Calculated channels run LAST so all source values (plugins, alarms, iOT) are available
                    if calc is not None:
                        _phase_start = time.perf_counter()
                        try:
                            calc_vals = getattr(calc, "simulate_step")(vals)
                            vals.update(calc_vals)
                            units.update(getattr(calc, "units")())
                        except Exception:
                            pass
                        calc_ms += (time.perf_counter() - _phase_start) * 1000.0
                    _phase_start = time.perf_counter()
                    self._evaluate_do_conditions(vals)
                    do_conditions_ms = (time.perf_counter() - _phase_start) * 1000.0
                    _phase_start = time.perf_counter()
                    self._forward_console_msgs(vals)
                    console_msgs_ms = (time.perf_counter() - _phase_start) * 1000.0
                    outputs_ms = do_conditions_ms + console_msgs_ms
                    _phase_start = time.perf_counter()
                    pub_vals = _strip_debug_keys(vals)
                    pub_units = _strip_debug_keys(units)
                    strip_ms = (time.perf_counter() - _phase_start) * 1000.0
                    _phase_start = time.perf_counter()
                    payload = json.dumps({
                        "ts": time.time(),
                        "values": pub_vals,
                        "units": pub_units,
                        "states": states,
                        "alarm_summary": summary,
                        "alarm_events": events,
                        "recording": bool(self._recording),
                        "source_map": self._source_map,
                        "display_aliases": self._display_aliases,
                    }).encode("utf-8")
                    json_ms = (time.perf_counter() - _phase_start) * 1000.0
                    _phase_start = time.perf_counter()
                    self.bus.publish_telemetry(payload)
                    publish_ms = (time.perf_counter() - _phase_start) * 1000.0
                    self._record_publish_perf(
                        interval_s=interval,
                        payload_len=len(payload),
                        value_count=len(pub_vals),
                        json_ms=json_ms,
                        publish_ms=publish_ms,
                    )
                    try:
                        if self._recording and self._db_writer is not None:
                            _phase_start = time.perf_counter()
                            self._db_writer.append(now_ts, pub_vals, pub_units)
                            db_ms = (time.perf_counter() - _phase_start) * 1000.0
                            row_appended = True
                    except Exception:
                        pass
                    tick_body_ms = (time.perf_counter() - tick_body_start) * 1000.0
                    self._record_core_timing(
                        tick_ms=tick_body_ms,
                        interval_s=interval,
                        ccp_ms=ccp_ms,
                        nidaq_ms=nidaq_ms,
                        other_plugins_ms=other_plugins_ms,
                        stats_ms=stats_ms,
                        controls_ms=controls_ms,
                        alarms_ms=alarms_ms,
                        calc_ms=calc_ms,
                        outputs_ms=outputs_ms,
                        do_conditions_ms=do_conditions_ms,
                        console_msgs_ms=console_msgs_ms,
                        strip_ms=strip_ms,
                        json_ms=json_ms,
                        publish_ms=publish_ms,
                        db_ms=db_ms,
                        row_appended=row_appended,
                    )
                    sleep_s = max(0.0, next_tick_deadline - time.monotonic())
                    if sleep_s > 0.0:
                        time.sleep(sleep_s)
                    now_mono = time.monotonic()
                    if now_mono > next_tick_deadline + interval:
                        next_tick_deadline = now_mono + interval
                    else:
                        next_tick_deadline += interval
                    i += 1
        finally:
            self._shutdown_plugins()
            self.bus.stop()
            # Finalize events sink (JSONL only for now)
            try:
                if self._events_sink is not None:
                    self._events_sink.finalize()
            except Exception:
                pass
            try:
                if self._db_writer is not None:
                    self._db_writer.finalize()
            except Exception:
                pass

    def _kickoff_export(self) -> None:
        kickoff_export(self)

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
            self._do_condition_states[alias] = state
        except Exception:
            pass

    _DO_OPS = {
        ">": float.__gt__,
        ">=": float.__ge__,
        "<": float.__lt__,
        "<=": float.__le__,
        "==": float.__eq__,
        "!=": float.__ne__,
    }

    _do_cond_diag_count: int = 0

    def _write_do_condition_if_changed(self, nidaq: Any, alias: str, state: int) -> None:
        desired = 1 if bool(state) else 0
        if self._do_condition_states.get(alias) == desired:
            return
        nidaq.write_do(alias, desired)
        self._do_condition_states[alias] = desired

    def _evaluate_do_conditions(self, vals: Dict[str, Any]) -> None:
        """Drive DO outputs based on expression conditions each tick."""
        nidaq = self.plugins.get("NI_DAQ") if self._plugin_enabled.get("NI_DAQ") else None
        if nidaq is None:
            return
        try:
            conditions = nidaq.do_conditions()
        except Exception:
            return
        if self._do_cond_diag_count < 3:
            print(f"[DO_COND] evaluating {len(conditions)} conditions")
        for cond in conditions:
            operator = cond.get("operator", "")
            alias = cond.get("alias", "")
            if not alias:
                continue
            try:
                if operator == "TRUE":
                    self._write_do_condition_if_changed(nidaq, alias, 1)
                    continue
                if operator == "FALSE":
                    self._write_do_condition_if_changed(nidaq, alias, 0)
                    continue
                source = cond.get("source", "")
                if source not in vals:
                    if self._do_cond_diag_count < 3:
                        print(f"[DO_COND] SKIP {alias}: source '{source}' not in vals")
                    continue
                src_val = float(vals[source])
                threshold = float(cond["threshold"])
                op_fn = self._DO_OPS.get(operator)
                if op_fn is None:
                    continue
                state = 1 if op_fn(src_val, threshold) else 0
                if self._do_cond_diag_count < 3:
                    print(f"[DO_COND] {alias}: src={source} val={src_val} {operator} {threshold} -> state={state}")
                self._write_do_condition_if_changed(nidaq, alias, state)
            except Exception as exc:
                if self._do_cond_diag_count < 5:
                    print(f"[DO_COND] ERROR {alias}: {exc}")
        self._do_cond_diag_count += 1

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

    def _handle_cycle_command(self, msg: Dict[str, Any]) -> None:
        cycle = self.plugins.get("Cycle") if self._plugin_enabled.get("Cycle", True) else None
        if cycle is None:
            return
        lb = self.plugins.get("LoadBank") if self._plugin_enabled.get("LoadBank", True) else None
        cmd = str(msg.get("type", ""))
        try:
            if cmd == "cycle_play":
                if lb is not None:
                    lb.command_master_load(True)
                    print("[CYCLE->LB] Master Load enabled for cycle")
                cycle.play()
                print("[INFO] Cycle: play")
            elif cmd == "cycle_pause":
                cycle.pause()
                print("[INFO] Cycle: pause")
            elif cmd == "cycle_seek":
                cycle.seek(float(msg.get("time_s", 0.0)))
                print(f"[INFO] Cycle: seek to {msg.get('time_s')}s")
            elif cmd == "cycle_set_loops":
                cycle.set_loops(int(msg.get("loops", 1)))
                print(f"[INFO] Cycle: loops set to {msg.get('loops')}")
            elif cmd == "cycle_set_start_with_test":
                cycle.set_start_with_test(bool(msg.get("enabled", False)))
                print(f"[INFO] Cycle: start_with_test = {msg.get('enabled')}")
        except Exception as e:
            print(f"[WARN] Cycle command failed: {e}")

    def _handle_loadbank_command(self, msg: Dict[str, Any]) -> None:
        if not self._plugin_enabled.get("LoadBank", True):
            try:
                print("[WARN] LoadBank command ignored: plugin disabled")
            except Exception:
                pass
            return
        lb = self.plugins.get("LoadBank")
        if lb is None:
            try:
                print("[WARN] LoadBank command ignored: plugin not present")
            except Exception:
                pass
            return

        action = str(msg.get("action", "")).strip().lower()
        try:
            if action in {"setpoint", "setpoint_kw", "setpoint_pct"}:
                value = float(msg.get("value", 0.0))
                if hasattr(lb, "command_setpoint_kw"):
                    getattr(lb, "command_setpoint_kw")(value)
                else:
                    getattr(lb, "command_setpoint_pct")(value)
            elif action == "fan_power":
                enabled = bool(msg.get("enabled", False))
                if hasattr(lb, "command_fan_power"):
                    getattr(lb, "command_fan_power")(enabled)
            elif action == "take_control":
                enabled = bool(msg.get("enabled", False))
                if hasattr(lb, "command_take_control"):
                    getattr(lb, "command_take_control")(enabled)
            elif action == "master_load":
                enabled = bool(msg.get("enabled", False))
                if hasattr(lb, "command_master_load"):
                    getattr(lb, "command_master_load")(enabled)
            elif action == "control_enable_a":
                if hasattr(lb, "set_control_enable_a"):
                    getattr(lb, "set_control_enable_a")(
                        msg.get("take_control"),
                        msg.get("fan_power"),
                        msg.get("master_load"),
                    )
            else:
                try:
                    print(f"[WARN] Unknown LoadBank command action: {action}")
                except Exception:
                    pass
                return
            try:
                print(f"[INFO] LoadBank command accepted: {action}")
            except Exception:
                pass
        except Exception as e:
            try:
                print(f"[WARN] LoadBank command failed: {e}")
            except Exception:
                pass

    def _sync_all_plugin_selections(self) -> None:
        """Re-read plugins.yaml and start/stop plugins whose enabled state changed."""
        ALWAYS_ON = {"Channel_Manager", "EngineTest"}
        try:
            plugins_cfg_path = (self.configs_dir / "plugins.yaml").resolve()
            plugins_cfg = load_yaml_config(plugins_cfg_path)
            selected_set = {str(x) for x in (plugins_cfg.get("selected_plugins") or [])}
        except Exception:
            plugins_cfg = {}
            selected_set = set()
        if not selected_set:
            return

        prev_sim = self._global_sim_mode
        self._global_sim_mode = str(plugins_cfg.get("data_mode", "")).strip().lower() == "sim"
        mode_changed = (prev_sim != self._global_sim_mode)
        if mode_changed:
            label = "offline (sim)" if self._global_sim_mode else "online (real)"
            print(f"[INFO] Global data mode changed to {label}")

        print(f"[INFO] Syncing plugin selections: {sorted(selected_set)}")
        for pid, plugin in self.plugins.items():
            if pid in ALWAYS_ON:
                self._plugin_enabled[pid] = True
                if mode_changed:
                    self._apply_mode_and_restart(pid, plugin)
                continue
            config_enabled = True
            try:
                config_enabled = bool(plugin.config.get("enabled", True))
            except Exception:
                config_enabled = True
            new_enabled = (pid in selected_set) and config_enabled
            old_enabled = self._plugin_enabled.get(pid, False)
            self._plugin_enabled[pid] = new_enabled

            if new_enabled and (not old_enabled or mode_changed):
                action = "mode change" if old_enabled else "newly enabled"
                print(f"[INFO] Plugin '{pid}' {action}; configuring")
                if pid == "NI_DAQ":
                    self._do_condition_states.clear()
                try:
                    plugin.stop()
                except Exception:
                    pass
                try:
                    plugin.load_config()
                    plugin.mode = "sim" if self._global_sim_mode else "real"
                    if pid == "NI_DAQ":
                        plugin._core_tick_rate_hz = self.settings.recording_rate_hz
                    plugin.configure()
                    status = plugin.validate()
                    if getattr(status, "ok", True):
                        plugin.start()
                        print(f"[INFO] Plugin '{pid}' started via selection sync")
                    else:
                        print(f"[WARN] Plugin '{pid}' validate failed: {getattr(status, 'message', '')}")
                except Exception as e:
                    print(f"[WARN] Plugin '{pid}' start failed: {e}")
            elif not new_enabled and old_enabled:
                print(f"[INFO] Plugin '{pid}' disabled; stopping")
                if pid == "NI_DAQ":
                    self._do_condition_states.clear()
                try:
                    plugin.stop()
                except Exception:
                    pass
        self._refresh_source_map("plugin selection sync")

    def _apply_mode_and_restart(self, pid: str, plugin) -> None:
        """Stop, reload config with global mode override, and restart a plugin."""
        if pid == "NI_DAQ":
            self._do_condition_states.clear()
        try:
            plugin.stop()
        except Exception:
            pass
        try:
            plugin.load_config()
            plugin.mode = "sim" if self._global_sim_mode else "real"
            plugin.configure()
            status = plugin.validate()
            if getattr(status, "ok", True):
                plugin.start()
                self._refresh_source_map(f"mode restart {pid}")
        except Exception as e:
            print(f"[WARN] Mode restart failed for {pid}: {e}")

    def _refresh_plugin_selection(self, plugin_id: str) -> None:
        """Re-read plugins.yaml and update _plugin_enabled for a single plugin."""
        ALWAYS_ON = {"Channel_Manager", "EngineTest"}
        if plugin_id in ALWAYS_ON:
            self._plugin_enabled[plugin_id] = True
            return
        try:
            plugins_cfg_path = (self.configs_dir / "plugins.yaml").resolve()
            plugins_cfg = load_yaml_config(plugins_cfg_path)
            selected_set = {str(x) for x in (plugins_cfg.get("selected_plugins") or [])}
        except Exception:
            selected_set = set()
        p = self.plugins.get(plugin_id)
        config_enabled = True
        if p is not None:
            try:
                config_enabled = bool(p.config.get("enabled", True))
            except Exception:
                config_enabled = True
        in_selection = (plugin_id in selected_set) if selected_set else True
        enabled = in_selection and config_enabled
        prev = self._plugin_enabled.get(plugin_id)
        self._plugin_enabled[plugin_id] = enabled
        if prev != enabled:
            print(f"[INFO] Plugin '{plugin_id}' enabled state: {prev} -> {enabled}")

    @staticmethod
    def _stash_session_keys(ctrl_msg: dict) -> None:
        """Store session access keys from the UI process into this process."""
        import sys
        keys = ctrl_msg.get("session_keys")
        if isinstance(keys, dict) and keys:
            store = getattr(sys, "_matrix_ccp_session_keys", {})
            store.update(keys)
            sys._matrix_ccp_session_keys = store  # type: ignore[attr-defined]

    def _reload_plugin(self, plugin_id: str) -> None:
        if not plugin_id:
            return
        if plugin_id == "Channel_Manager":
            try:
                ch_cfg_path = (self.configs_dir / "channel_manager.yaml").resolve()
                self.channel_cfg = load_yaml_config(ch_cfg_path)
                self._apply_channel_manager_runtime()
                print("[INFO] Reloaded plugin: Channel_Manager")
            except Exception as e:
                print(f"[WARN] Reload failed for Channel_Manager: {e}")
            return
        self._refresh_plugin_selection(plugin_id)
        try:
            p = self.plugins.get(plugin_id)
            if p is None:
                print(f"[WARN] Reload ignored: plugin not found: {plugin_id}")
                return
            if plugin_id == "NI_DAQ":
                self._do_condition_states.clear()
            try:
                p.stop()
            except Exception:
                pass
            if not self._plugin_enabled.get(plugin_id, False):
                print(f"[INFO] Plugin '{plugin_id}' not enabled; stopped")
                self._refresh_source_map(f"reload {plugin_id} disabled")
                return
            try:
                p.load_config()
                p.mode = "sim" if self._global_sim_mode else "real"
            except Exception as e:
                print(f"[WARN] Reload: failed to load config for {plugin_id}: {e}")
            try:
                if plugin_id == "NI_DAQ":
                    p._core_tick_rate_hz = self.settings.recording_rate_hz
                p.configure()
                status = p.validate()
                if not getattr(status, 'ok', True):
                    print(f"[ERROR] Reload validate failed for {plugin_id}: {getattr(status,'message','')}")
                    self._refresh_source_map(f"reload {plugin_id} invalid")
                    return
                p.start()
                self._refresh_source_map(f"reload {plugin_id}")
                print(f"[INFO] Reloaded plugin: {plugin_id}")
            except Exception as e:
                print(f"[WARN] Reload failed for {plugin_id}: {e}")
                self._refresh_source_map(f"reload {plugin_id} failed")
        except Exception:
            pass

    def _apply_channel_manager_runtime(self) -> None:
        # Tick cadence authority: Channel Manager recording_rate_hz -> core tick interval.
        default_interval = float(self.core_cfg.get("tick_interval_s", 0.1))
        interval = default_interval
        try:
            hz = float((self.channel_cfg or {}).get("recording_rate_hz", 0.0))
            if hz > 0.0:
                interval = 1.0 / hz
        except Exception:
            interval = default_interval
        self._tick_interval_s = max(0.001, float(interval))
        try:
            self.settings.recording_rate_hz = 1.0 / self._tick_interval_s
        except Exception:
            pass
        # Propagate new tick rate to NI DAQ snapshot period without full restart.
        try:
            nidaq = self.plugins.get("NI_DAQ")
            if nidaq is not None and self._plugin_enabled.get("NI_DAQ", True):
                new_rate = self.settings.recording_rate_hz
                nidaq._core_tick_rate_hz = new_rate
                nidaq._sim_rate_hz = new_rate
                nidaq._snapshot_period_s = max(0.01, 1.0 / max(1.0, new_rate))
        except Exception:
            pass

        if self.channel_cfg:
            try:
                self.alarm_engine = AlarmEngine(self.channel_cfg)
                self._alarm_tick_logged = False
                chan_items = (self.channel_cfg.get("channels") or [])
                chan_count = len(chan_items)
                print(f"[INFO] AlarmEngine initialized: {chan_count} channel(s)")
                if chan_count:
                    aliases = [str(it.get("alias")) for it in chan_items if isinstance(it, dict) and it.get("alias")]
                    print("[INFO] AlarmEngine channels:", ", ".join(aliases))
                print(f"[INFO] Core tick interval set to {self._tick_interval_s:.6f}s from Channel Manager")
            except Exception as e:
                print(f"[WARN] Failed to initialize AlarmEngine: {e}")
                self.alarm_engine = None
        else:
            self.alarm_engine = None
            print("[INFO] Channel Manager config not found or empty; alarms disabled")

    def _kickoff_ccp_test(self, msg: Dict[str, Any]) -> None:
        run_id = str(msg.get("run_id", ""))
        try:
            import threading
        except Exception:
            return
        if bool(getattr(self, "_ccp_test_running", False)):
            self._publish_ccp_test(run_id, "start", False, "CCP test already running", done=True)
            return

        ccp = self.plugins.get("CCP")
        if ccp is None or not self._plugin_enabled.get("CCP", True):
            self._publish_ccp_test(run_id, "start", False, "CCP plugin is not enabled", done=True)
            return

        def _emit(step: str, ok: bool, detail: str, done: bool = False) -> None:
            self._publish_ccp_test(run_id, step, ok, detail, done=done)

        def _worker() -> None:
            setattr(self, "_ccp_test_running", True)
            restart_after_test = False
            try:
                _emit("start", True, "Starting CCP connection test...")
                try:
                    try:
                        restart_after_test = bool(getattr(ccp, "_worker_thread", None) is not None)
                        if restart_after_test:
                            ccp.stop()
                    except Exception:
                        restart_after_test = False
                    ccp.load_config()
                    if self._global_sim_mode:
                        ccp.mode = "sim"
                    ccp.configure()
                except Exception as e:
                    _emit("load_config", False, f"Failed to load/configure CCP: {e}", done=True)
                    return
                if hasattr(ccp, "run_connection_test"):
                    getattr(ccp, "run_connection_test")(_emit)
                else:
                    _emit("unsupported", False, "CCP plugin does not support connection test", done=True)
            except Exception as e:
                _emit("error", False, f"CCP test exception: {e}", done=True)
            finally:
                try:
                    ccp.stop()
                except Exception:
                    pass
                if restart_after_test:
                    try:
                        ccp.load_config()
                        if self._global_sim_mode:
                            ccp.mode = "sim"
                        ccp.configure()
                        status = ccp.validate()
                        if status.ok:
                            ccp.start()
                        else:
                            _emit("restart", False, f"CCP restart skipped: {status.message}")
                    except Exception as e:
                        _emit("restart", False, f"CCP restart failed: {e}")
                setattr(self, "_ccp_test_running", False)

        try:
            t = threading.Thread(target=_worker, daemon=True)
            t.start()
        except Exception as e:
            self._publish_ccp_test(run_id, "start", False, f"Failed to start CCP test thread: {e}", done=True)

    def _publish_ccp_test(self, run_id: str, step: str, ok: bool, detail: str, done: bool = False) -> None:
        try:
            import json
            payload = json.dumps(
                {
                    "type": "ccp_test",
                    "run_id": run_id,
                    "step": str(step),
                    "ok": bool(ok),
                    "detail": str(detail),
                    "done": bool(done),
                }
            ).encode("utf-8")
            self.bus.publish_status(payload)
        except Exception:
            pass

    def _forward_console_msgs(self, vals: dict) -> None:
        """Extract __console_msgs__ from merged plugin vals and publish to status topic."""
        msgs = vals.pop("__console_msgs__", None)
        if not msgs:
            return
        import json as _json
        for text in msgs:
            try:
                payload = _json.dumps({"type": "plugin_message", "text": str(text)}).encode("utf-8")
                self.bus.publish_status(payload)
            except Exception:
                pass

    def _publish_core_ready(self, force: bool = False, reason: str = "") -> None:
        if self._ready_acknowledged:
            return
        try:
            import json as _json
            import time as _time
            now_mono = _time.monotonic()
            if not force and (now_mono - self._last_ready_publish_mono) < 0.5:
                return
            payload = _json.dumps({
                "type": "core_ready",
                "plugins": sorted(list(self.plugins.keys())),
                "plugin_enabled": dict(self._plugin_enabled),
            }).encode("utf-8")
            self.bus.publish_status(payload)
            self._last_ready_publish_mono = now_mono
            if reason == "request" and not self._ready_request_logged:
                print("[INFO] Published core_ready in response to launcher request.")
                self._ready_request_logged = True
        except Exception:
            pass

    def _start_plugin_runtime(
        self,
        pid: str,
        plugin,
        arm: bool = False,
        continue_on_validate_error: bool = True,
    ):
        if plugin is None:
            return None
        if pid == "NI_DAQ":
            self._do_condition_states.clear()
        try:
            plugin.configure()
            status = plugin.validate()
            if not getattr(status, "ok", True):
                msg = getattr(status, "message", "")
                print(f"[WARN] Plugin '{pid}' is not ready: {msg}")
                if not continue_on_validate_error:
                    return None
            if arm:
                plugin.arm()
            plugin.start()
            return plugin
        except Exception as e:
            print(f"[WARN] Plugin '{pid}' startup failed: {e}")
            return None

    def _shutdown_plugins(self) -> None:
        ordered = (
            "Cycle",
            "EngineTest",
            "Statistics",
            "Calculated_Channels",
            "NI_DAQ",
            "CCP",
            "CAN",
            "LoadBank",
            "Modbus",
            "Vaisala",
            "Omega",
            "Channel_Manager",
        )
        seen = set()
        for pid in list(ordered) + [p for p in self.plugins.keys() if p not in ordered]:
            if pid in seen:
                continue
            seen.add(pid)
            plugin = self.plugins.get(pid)
            if plugin is None or not self._plugin_enabled.get(pid, True):
                continue
            if pid == "NI_DAQ":
                self._do_condition_states.clear()
            try:
                plugin.stop()
            except Exception as e:
                try:
                    print(f"[WARN] Plugin stop failed for {pid}: {e}")
                except Exception:
                    pass
            try:
                plugin.teardown()
            except Exception as e:
                try:
                    print(f"[WARN] Plugin teardown failed for {pid}: {e}")
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
            PluginSpec(id="Omega", cls=OmegaPlugin, config_name="omega.yaml"),
            PluginSpec(id="EngineTest", cls=EngineTestPlugin, config_name="engine_test.yaml"),
            PluginSpec(id="Channel_Manager", cls=ChannelManagerPlugin, config_name="channel_manager.yaml"),
        ]
        for s in specs:
            self.registry.register(s)

    def _engine_test_lock(self) -> None:
        et = self.plugins.get("EngineTest")
        if et is None or not self._plugin_enabled.get("EngineTest", True):
            return
        try:
            st = getattr(et, "lock_session")()
            if not st.ok:
                try:
                    print(f"[WARN] EngineTest lock failed: {st.message}")
                except Exception:
                    pass
        except Exception as e:
            try:
                print(f"[WARN] EngineTest lock error: {e}")
            except Exception:
                pass

    def _engine_test_unlock(self) -> None:
        et = self.plugins.get("EngineTest")
        if et is None or not self._plugin_enabled.get("EngineTest", True):
            return
        try:
            getattr(et, "unlock_session")()
        except Exception as e:
            try:
                print(f"[WARN] EngineTest unlock error: {e}")
            except Exception:
                pass

    def _begin_recording(self) -> None:
        et = self.plugins.get("EngineTest")
        if et is not None and self._plugin_enabled.get("EngineTest", True):
            try:
                et.load_config()
                if self._global_sim_mode:
                    et.mode = "sim"
                et.configure()
                st = et.validate()
                if not st.ok:
                    try:
                        print(f"[ERROR] Cannot start recording: {st.message}")
                    except Exception:
                        pass
                    return
                ph = getattr(et, "phase", lambda: "")()
                if ph != "locked":
                    try:
                        print("[ERROR] Cannot start recording: Lock Test first (EngineTest phase is not locked).")
                    except Exception:
                        pass
                    return
                et.start()
            except Exception as e:
                try:
                    print(f"[ERROR] EngineTest start failed: {e}")
                except Exception:
                    pass
                return
        cycle = self.plugins.get("Cycle") if self._plugin_enabled.get("Cycle", True) else None
        if cycle is not None and getattr(cycle, "start_with_test", False):
            lb = self.plugins.get("LoadBank") if self._plugin_enabled.get("LoadBank", True) else None
            if lb is not None:
                ctrl = getattr(lb, "_control_values_a", [False, False, False])
                if not (bool(ctrl[0]) if len(ctrl) >= 1 else False):
                    print("[ERROR] Cannot start recording: Cycle 'Start with Test' is enabled but "
                          "LoadBank Take Control is not active. Enable it first, then try again.")
                    return
            if lb is not None:
                lb.command_master_load(True)
                print("[CYCLE->LB] Master Load enabled for cycle (Start with Test)")
            cycle.play()
            print("[INFO] Cycle started with test (Start with Test enabled)")
        begin_recording(self)

    def _end_recording(self) -> None:
        et = self.plugins.get("EngineTest")
        if et is not None and self._plugin_enabled.get("EngineTest", True):
            try:
                et.stop()
            except Exception:
                pass
        end_recording(self)

    def _build_storage_settings_from_channel_cfg(self) -> SqliteWriterSettings:
        return build_storage_settings(self.channel_cfg)


