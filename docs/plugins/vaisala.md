<!-- Author: T. Onkst | Date: 08122025 -->

## Vaisala Plugin Specification

### Purpose
Provide ambient environmental measurements from Vaisala sensors. Current implementation is simulation-only (no I/O), producing temperature, relative humidity, and pressure for UI/telemetry. The real Modbus TCP path (with model register maps, polling, and connection testing) will be added later.

### Scope
- Transport: Modbus TCP (planned). Simulation implemented now.
- Models: selectable via `model.selected`; map files reserved for future real integration
- Measurements (sim default): Ambient Temperature (°C), Ambient RH (%RH), Ambient Pressure (kPa)

### Model Maps (future)
- Each model map YAML will define register addresses, types, scaling, and units. Integration TBD.

### Configuration (YAML)
File: `configs/vaisala.yaml`

```yaml
mode: sim

connection:
  host: 127.0.0.1
  port: 502
  unit_id: 1

model:
  selected: "HMT330"      # informational in sim; reserved for future real path
  map_file: null           # reserved for future register map yaml

polling:
  override_poll_hz: null   # reserved for real path

calibration_offsets:
  "Ambient Temp": 0.0
  "Ambient RH": 0.0
  "Ambient Pressure": 0.0

channels:
  - alias: "Ambient Temp"
    unit: "C"
  - alias: "Ambient RH"
    unit: "%RH"
  - alias: "Ambient Pressure"
    unit: "kPa"
```

#### Validation Rules
- `model.selected` must exist and `map_file` must be readable and valid
- `connection.*` types valid; retries/timeouts non-negative
- Each read entry has valid `fc`, `address`, `type`, and scaling; aliases unique system-wide
- If `override_poll_hz` set, it must be > 0 and ≤ R

### Acquisition Model
- Simulation: generates slow-varying sine/cosine signals for temp/RH/pressure
- Optional `calibration_offsets` are applied after generation (additive per alias)
- Real Modbus path: TBD; will use model maps and polling aligned to R

### UI Flow
- Right-click Vaisala tile → Configure:
  1) Select model (loads map)
  2) Enter IP (host/port), unit-id, timeouts
  3) Optional: set polling override and calibration offsets
  4) Test Connection (single read of first mapped register)
  5) Save → auto-connect and maintain connection
- Runtime: show current values per measurement and connection health (green/red). Context: Configure, Show Error, Reset Error

### Outputs & Metadata
- Records ambient measurements as channels with configured aliases and units (sim)
- Sidecar YAML includes connection and model blocks; map_file reserved for future

### Error Conditions (Examples)
- Connection failure/timeouts → red status; resets with backoff
- Illegal function/address or size/type mismatch → validation/runtime error

### Test Cases (Vaisala)
- VAIS-Models-001: Model dropdown lists maps; loading applies correct addresses/units
- VAIS-ConnTest-001: Test Connection probe read succeeds/fails appropriately
- VAIS-Poll-001: Polls at override/default rates; aligns to R; applies offsets
- VAIS-ErrorUI-001: Connection drop and recover with Show Error/Reset Error behavior


