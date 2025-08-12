<!-- Author: T. Onkst | Date: 08112025 -->

## LoadBank Plugin Specification

### Purpose
Specialized Modbus TCP control/monitor plugin for load banks from multiple suppliers. Operators select a loadbank model from a dropdown (different register maps), configure IP settings, test the connection in the config UI, and upon exiting configuration the plugin auto-connects and maintains a constant connection throughout the test session.

### Scope
- Transport: Modbus TCP
- Model support: multiple suppliers/models via model map files
- Functionality:
  - Configure host, port, unit-id
  - Select loadbank model (predefined register map)
  - Test connection in configuration UI
  - Auto-connect and keep-alive after configuration
  - Provide control channels used by UI and Cycle plugin (setpoint/accept)
  - Provide status/measurement channels for monitoring and recording (aligned to R)

### Model Maps
- Each supported loadbank model has a model map YAML in `configs/loadbanks/<model>.yaml`
- Map defines addresses, types, scaling, limits for:
  - Commands: load setpoint write, accept/apply, optional mode bits
  - Status: measured load, status words, fault bits, ready, comms health
- Example model map (YAML):

```yaml
# configs/loadbanks/Acme-LB100.yaml
model: "Acme-LB100"
commands:
  setpoint:
    fc: 6                 # write single holding register
    address: 40100
    type: uint16
    ui_unit: "%"         # UI percent 0..100 → register via scaling
    scaling: { m: 10.0, b: 0.0 }  # 1% → +10 register units
    min: 0
    max: 100
    confirm_readback: true
  accept:
    fc: 5                 # write single coil
    address: 1
status:
  measured_load:
    fc: 3                 # read holding register
    address: 41000
    type: uint16
    scaling: { m: 0.1, b: 0.0, unit: "%" }
    poll_hz: 2
  faults_word:
    fc: 3
    address: 41010
    type: uint16
    poll_hz: 1
  ready_bit:
    fc: 2                 # read discrete input
    address: 10
    poll_hz: 2
```

### Configuration (YAML)
File: `configs/loadbank.yaml`

```yaml
recording_rate_hz: 100

connection:
  host: "192.168.1.60"
  port: 502
  unit_id: 1
  timeout_ms: 200
  max_retries: 3

model:
  selected: "Acme-LB100"                  # dropdown of available models (from configs/loadbanks/*.yaml)
  map_file: "configs/loadbanks/Acme-LB100.yaml"

polling:
  default_status_poll_hz: 2               # used when model map does not specify

safety:
  setpoint_limits_percent: { min: 0, max: 100 }
  require_accept_confirmation: true
  rate_limit_setpoint_hz: 1

expose_channels:
  measured_load_alias: "LB Measured Load"
  ready_alias: "LB Ready"
  faults_alias: "LB Faults"
  setpoint_alias: "LB Setpoint"          # command echo channel recorded at R
  accept_alias: "LB Accept"              # command event channel recorded at R
```

#### Validation Rules
- `model.selected` must correspond to an existing model map; `map_file` readable and schema-valid
- `connection.host`/`port`/`unit_id` types valid; timeouts/retries non-negative
- Model map `commands.setpoint` must define min/max; enforce against `safety.setpoint_limits_percent` if present
- Aliases required and unique across enabled names

### UI Flow
- Right-click LoadBank tile → Configure:
  1) Pick `model.selected` from dropdown (populated from `configs/loadbanks/*.yaml`)
  2) Set IP config: host, port, unit-id, timeouts
  3) Test Connection button: attempts connect, performs a lightweight read (e.g., status or identity)
  4) Save
- On exiting configuration: plugin auto-connects and maintains connection; auto-reconnect with backoff if dropped
- Context actions: Show Error, Reset Error (disconnect/reconnect), Configure

### Acquisition & Control
- Status reads: polled at per-point `poll_hz` from the model map, or `polling.default_status_poll_hz` if unspecified; aligned to R grid using last-value-hold
- Commands:
  - Setpoint: percent 0..100 entered by UI/Cycle; scaled to register; rate-limited; optional confirm_readback
  - Accept: momentary coil write; optionally confirm by reading coil or status
- Command echoes: record last commanded setpoint and accept events at R for traceability

### Integration with Cycle Plugin
- Cycle issues setpoint targets over time; LoadBank applies setpoint and, if configured, requires operator “Accept”
- Pause/stop/restart/skip from Cycle are forwarded as needed; comms loss triggers E-stop via calculated channel logic (outside of LoadBank)

### Outputs and Metadata
- Metadata includes: model name, map file path, connection details (host, port, unit-id), command and status mapping
- Recorded channels include: measured load, readiness, faults word/flags, command echoes

### Error Conditions (Examples)
- Connection failure/timeouts → UI red; Show Error details; auto-retry with backoff
- Illegal function/address per selected model map → validation error
- Confirm readback mismatch on setpoint → warning, retry or surface to UI

### Test Cases (LoadBank)
- LB-Models-001: Model dropdown lists all maps; selection loads correct addresses/types
- LB-ConnTest-001: Test Connection succeeds/fails appropriately (mock server)
- LB-AutoConnect-001: After saving, auto-connect and keep-alive; auto-reconnect on drop
- LB-Setpoint-001: Apply setpoint within limits; scaling correct; confirm readback
- LB-Accept-001: Accept command writes coil; event recorded
- LB-Status-001: Status points polled at configured rates; aligned to R grid
- LB-ErrorUI-001: Show Error/Reset Error behavior on simulated faults


