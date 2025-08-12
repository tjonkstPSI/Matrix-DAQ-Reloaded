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

    def start(self) -> None:
        # Placeholder: load configs, initialize IPC bus, register simulated plugins
        self._register_builtin_specs()
        self.plugins = self.registry.create_all()
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
            # Generate 50 ticks at 10 Hz, publish telemetry merging Modbus and CAN
            import time, json
            can = self.plugins.get("CAN")
            ccp = self.plugins.get("CCP")
            lb = self.plugins.get("LoadBank")
            if can:
                can.configure(); can.validate(); can.start()
            if ccp:
                ccp.configure(); ccp.validate(); ccp.start()
            if lb:
                lb.configure(); lb.validate(); lb.start()
            for _ in range(50):
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
                if lb:
                    # Example: ramp setpoint from 0 to 100% over demo
                    sp = (_ / 49.0) * 100.0
                    getattr(lb, "command_setpoint_pct")(sp)
                    vals.update(getattr(lb, "simulate_step")())
                    units.update(getattr(lb, "units")())
                payload = json.dumps({"ts": time.time(), "values": vals, "units": units}).encode("utf-8")
                self.bus.publish_telemetry(payload)
                time.sleep(0.1)
        finally:
            modbus.stop()
            self.bus.stop()

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
            PluginSpec(id="Cycle", cls=_Stub, config_name="cycle.yaml"),
            PluginSpec(id="LoadBank", cls=LoadBankPlugin, config_name="loadbank.yaml"),
            PluginSpec(id="Modbus", cls=ModbusPlugin, config_name="modbus.yaml"),
            PluginSpec(id="Statistics", cls=_Stub, config_name="statistics.yaml"),
            PluginSpec(id="Vaisala", cls=_Stub, config_name="vaisala.yaml"),
            PluginSpec(id="EngineTest", cls=_Stub, config_name="engine_test.yaml"),
            PluginSpec(id="Channel_Manager", cls=_Stub, config_name="channel_manager.yaml"),
        ]
        for s in specs:
            self.registry.register(s)


