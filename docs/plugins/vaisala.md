<!-- Author: T. Onkst | Date: 04202026 -->

## Vaisala Plugin Specification

### Purpose
Provide environmental measurements from Vaisala HMT330 and Indigo510 humidity transmitters via Modbus TCP. Reads float32 holding registers for up to 13 measurement channels with pressure compensation writes and filtering control.

### Scope
- Transport: Modbus TCP (`pymodbus`).
- Models: HMT330 (unit_id=1), Indigo510 (unit_id=241) — auto-assigned from model dropdown.
- Measurements (13 available): RH, T, Td, Td/f, a, x, Tw, H2Ov, pw, pws, H, dT, H2Ow. User selects which to enable via checkboxes.
- Sim/real mode is controlled globally via the launch dialog Offline Mode checkbox, not per-plugin.

### Hardcoded Register Map
All 13 measurement channels have fixed register addresses, units, and sim parameters in the `REGISTER_MAP` constant in `src/plugins/vaisala.py`. Two bulk register reads per poll cycle cover all enabled channels.

### Configuration (YAML)
File: `configs/vaisala.yaml`

```yaml
mode: real               # overridden to 'sim' when global Offline Mode is on

connection:
  host: 192.168.1.50
  port: 502

model:
  selected: "HMT330"     # HMT330 | Indigo510

polling:
  poll_rate_hz: 1
  timeout_s: 2.0

pressure:
  mode: fixed             # fixed | dynamic
  fixed_hpa: 1013.25
  dynamic:
    source_channel: ""
    source_unit: ""
    gain: 1.0
    offset: 0.0

filtering:
  mode: none              # none | standard | extended

channels:
  - id: RH
    alias: "xHM_Amb"
    enabled: true
  - id: T
    alias: "xTP_Amb"
    enabled: true
```

### Parameter Writes
- **Pressure compensation**: fixed hPa value or dynamic from any telemetry channel (with gain/offset) written to temporary register 771-772 every poll cycle.
- **Filtering mode**: None/Standard/Extended written to flag registers 1281/1282 every poll cycle.
- Orchestrator feeds merged telemetry via `update_telemetry(vals)` for dynamic pressure source resolution.

### UI Flow
- Right-click Vaisala tile → Configure:
  1) Select model (HMT330 / Indigo510) — unit ID auto-assigned, hidden from user.
  2) Enter host/port connection settings.
  3) Enable/disable channels via checkboxes; double-click Alias column to open shared `AliasPickerDialog`.
  4) Configure pressure compensation (Fixed / Dynamic with source channel, gain, offset).
  5) Configure filtering (None / Standard / Extended).
  6) Save → writes YAML + reloads plugin.
- Poll rate and timeout remain in YAML for super-users (not in config dialog).

### Outputs
- Data channels: all enabled channel aliases
- Health channel: `Vaisala/conn_ok` (bool as 1.0/0.0) — True when Modbus connection is active. Console tile uses this for Green/Red/Disconnected status.

### Runtime
- Threaded poll loop with configurable poll rate and auto-reconnect on connection loss.
- Sim mode generates sine-wave values per channel with independent phase/amplitude.
- Real mode reads float32 from holding registers, writes pressure/filtering parameters, and handles connection errors with sample-and-hold.

### Error Conditions
- Connection failure/timeouts → `conn_ok=False`; red status on console; auto-reconnect on next poll.
- Illegal function/address → runtime error logged; sample-and-hold preserves last good values.


