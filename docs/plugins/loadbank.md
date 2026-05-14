<!-- Author: T. Onkst | Date: 04212026 -->

## LoadBank Plugin Specification

### Purpose
Specialized Modbus TCP control/monitor plugin for load banks from multiple suppliers. Operators select a loadbank model from a dropdown (different register maps), configure IP settings, and upon exiting configuration the plugin auto-connects and maintains a constant connection throughout the test session. Supports both manual operator control and automated cycle-driven setpoints.

### Scope
- Transport: Modbus TCP (pymodbus with `_modbus_compat.py` version shim)
- Model support: multiple suppliers/models via model map files
- Functionality:
  - Configure host, port, unit-id
  - Select a primary loadbank model from predefined register maps
  - Auto-connect and keep-alive after configuration
  - Provide control channels used by UI and Cycle plugin (setpoint in kW)
  - Provide status/measurement channels for monitoring and recording
  - Recording telemetry: `lDG_Fan`, `lPO_LdbAct`, `lPO_LdbStp`, `lCT_Ldb1/2/3`, `lVO_Ldb1/2/3`

### Model Maps
- Each supported loadbank model has a model map YAML in `configs/loadbanks/<model>.yaml`
- Currently supported: `Simplex-1.5MW.yaml` (A-side), `Simplex-750kW.yaml` (B-side), `Simplex-700kW.yaml`
- Map defines:
  - `address_base`: `0` or `1` (vendor documentation offset; plugin adjusts to 0-based wire addresses)
  - `commands.setpoint`: coil array (FC15) step-based load control with `steps_kw` array, or BCD register load request (FC16)
  - `commands.control_enable_a`: static control coils for Take Control/Fan Power/Master Load, or Fan Power/Apply Load when heartbeat is separate
  - `indicators`: coil reads (FC1) for fan status, control available, normal operation, etc.
  - `status`: register reads (FC3/FC4) for metering — voltage, current, power, frequency as float32 with configurable `word_order` (`AB` or `BA`)
  - `heartbeat`: optional periodic coil toggle for keepalive

#### Float32 Word Order
Metering registers (voltage, current, power, frequency) are decoded as IEEE 754 float32 from two consecutive 16-bit Modbus registers. The `word_order` field controls register pairing:
- `AB`: high word first (register N = MSW, N+1 = LSW)
- `BA`: low word first (register N = LSW, N+1 = MSW) — **used by Simplex loadbanks**

#### Step-Based Setpoint (Simplex)
Load is applied via a coil array where each coil represents a load step (e.g., 300kW, 200kW, 150kW, 50kW, 25kW, 25kW for 750kW). The plugin uses a greedy descending algorithm to select the optimal combination of steps for a given kW target:
1. Sort steps descending
2. For each step, include if remaining target >= step value
3. Write coil array via FC15 (multiple coils)

#### BCD Setpoint (Simplex 700kW)
The Simplex 700kW does not expose individual kW step coils. Matrix writes the requested system load as a BCD Double value to the documented load request register, then uses the load bank controller's Apply Load coil. The model map keeps vendor addresses one-based:
- YAML address `2625` becomes wire address `2624`
- `type: bcd_double` encodes the decimal kW request into two 16-bit registers
- `word_order: BA` matches the LabVIEW write behavior unless hardware testing proves otherwise

#### Heartbeat Control (Simplex 700kW)
The 700kW first control coil is a heartbeat, not a static Take Control bit. For heartbeat-capable maps:
- The operator's first control button enables or disables the Matrix remote-control session.
- While enabled, the plugin toggles the heartbeat coil at the map's configured interval.
- Fan/Control Power and Apply Load remain static control coils.
- The UI displays heartbeat readback when the map exposes a heartbeat status channel.

### Configuration (YAML)
File: `configs/loadbank.yaml`

```yaml
enabled: true
mode: real
load_banks:
  primary:
    model: Simplex 1.5MW
    map_file: configs/loadbanks/Simplex-1.5MW.yaml
    ip_address: 192.168.100.1
    port: 502
    unit_id: 1
    enabled: true
  secondary:
    model: None
    enabled: false
safety:
  setpoint_limits_percent:
    min: 0
    max: 1500
  rate_limit_setpoint_hz: 1
expose_channels:
  measured_load_alias: lPO_LdbAct
  setpoint_alias: lPO_LdbStp
  fan_alias: lDG_Fan
  voltage_ab_alias: lVO_Ldb1
  voltage_bc_alias: lVO_Ldb2
  voltage_ca_alias: lVO_Ldb3
  current_l1_alias: lCT_Ldb1
  current_l2_alias: lCT_Ldb2
  current_l3_alias: lCT_Ldb3
  frequency_alias: LB Frequency
```

### Operator Control Workflow
All controls are user-driven; nothing activates automatically at startup:

1. **Take Control / Enable Remote Control**: user toggles in UI -> writes the Take Control coil on Saturn-style maps, or starts the heartbeat loop on heartbeat-capable maps
2. **Fan Power**: user toggles in UI -> writes fan coil (requires Take Control active; hardware-enforced). Fan is shared between A/B sides — app will not turn off a fan that's already on.
3. **Set Load + Apply Load**: user enters kW value and clicks Apply Load -> sends `master_load(True)` + `setpoint_kw(value)`. Master Load/Apply Load enables the hardware load switch; setpoint writes either the step coil array or the BCD system-load request, depending on the map.
4. **Emergency Stop / Zero Load**: sends `setpoint_kw(0)` + `master_load(False)` — immediately drops all load.

Initial state: `_control_values_a = [False, False, False]` (Take Control or heartbeat session off, Fan off, Master/Apply Load off).

### UI
- **Configuration dialog** (right-click tile -> Configure): primary/secondary model dropdowns (populated from `configs/loadbanks/*.yaml` plus "None"), IP/port/unit-id fields
- **Operator panel** (console panel button): Take Control or Enable Remote Control toggle, Fan Power toggle, Load Setpoint spinner + Apply Load button, Emergency Stop button, live metering readback (voltage, current, power, frequency, fan status, heartbeat when available), Cycle Control section (see Cycle plugin docs)

### Deferred Dual-Loadbank Distribution
The configuration schema can store a primary and secondary loadbank, but current runtime control targets the configured primary loadbank. Matrix-managed load distribution, such as filling a 1.5MW primary before applying the remainder to a 750kW secondary, is intentionally deferred until the operating requirements are better defined. The Simplex 700kW remains a single Matrix-controlled target because any daisy-chained downstream loadbanks are managed internally by the Simplex controller.

### Integration with Cycle Plugin
- When the Cycle plugin plays, the orchestrator enables Master Load automatically and begins piping setpoints
- Setpoint commands are sent only when the value changes (change-detection, not every tick)
- On cycle pause: last setpoint held, Master Load stays on
- On cycle complete: last setpoint held (cycles typically end at 0kW); operator uses Emergency Stop to drop load
- On cycle restart (Play from complete): resets to beginning and runs again

### Outputs and Metadata
- Metadata includes: model name, map file path, connection details (host, port, unit-id)
- Recorded channels: `lDG_Fan` (fan boolean), `lPO_LdbAct` (actual power kW), `lPO_LdbStp` (setpoint kW), `lCT_Ldb1/2/3` (phase currents A), `lVO_Ldb1/2/3` (phase voltages V), plus frequency and indicator channels

### Error Conditions
- Connection failure/timeouts -> auto-retry with backoff
- Metering zeros after power cycle -> hardware issue; requires loadbank reboot
- Float32 garbage values -> check `word_order` in model map YAML (AB vs BA)
