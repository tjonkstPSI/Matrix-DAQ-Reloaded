<!-- Author: T. Onkst | Date: 08122025 -->

## Vaisala Plugin Specification

### Purpose
Specialized Modbus TCP plugin for Vaisala environmental sensors (e.g., temperature, humidity, dew point). Provides a model dropdown (predefined register maps), IP configuration, connection test, continuous polling aligned to the recording rate R (≤ 100 Hz), per-channel aliasing (operator-editable), and optional per-sensor calibration offsets.

### Scope
- Transport: Modbus TCP
- Models: selectable from predefined maps under `configs/vaisala_models/*.yaml` (site-editable)
- Measurements (typical): Temperature (°C), Relative Humidity (%RH), Dew Point (°C), Absolute Humidity (g/m³), Pressure (hPa) — availability depends on model

### Model Maps
- Each model map YAML defines the register addresses, types, scaling, and units for available measurements.
- Example (template; update addresses per site):

```yaml
# configs/vaisala_models/HMP110.yaml
model: "HMP110"
poll_default_hz: 1
reads:
  - alias: "Ambient Temp"
    fc: 4              # Input Register
    address: 30001
    type: float32
    word_order: AB
    byte_order: big
    scaling: { m: 1.0, b: 0.0, unit: "C" }
  - alias: "Relative Humidity"
    fc: 4
    address: 30003
    type: float32
    word_order: AB
    byte_order: big
    scaling: { m: 1.0, b: 0.0, unit: "%RH" }
  - alias: "Dew Point"
    fc: 4
    address: 30005
    type: float32
    word_order: AB
    byte_order: big
    scaling: { m: 1.0, b: 0.0, unit: "C" }
```

### Configuration (YAML)
File: `configs/vaisala.yaml`

```yaml
recording_rate_hz: 100

connection:
  host: "192.168.1.70"
  port: 502
  unit_id: 1
  timeout_ms: 200
  max_retries: 3

model:
  selected: "HMP110"                              # dropdown from configs/vaisala_models/*.yaml
  map_file: "configs/vaisala_models/HMP110.yaml"

polling:
  override_poll_hz: null                           # if set, applies to all reads; else model default per-point

calibration_offsets:                                # optional per-alias additive offsets (site-calibration)
  "Ambient Temp": 0.0
  "Relative Humidity": 0.0
  "Dew Point": 0.0

advanced:
  endianness_override: null                         # { word_order: AB, byte_order: big } to override all

# Optional operator alias overrides and channel enablement
channels:
  - model_alias: "Ambient Temp"                     # as defined in model map
    alias: "Intake Air Temp"                       # operator-defined alias used in UI and outputs
    enabled: true
  - model_alias: "Relative Humidity"
    alias: "Intake RH"
    enabled: true
```

#### Validation Rules
- `model.selected` must exist and `map_file` must be readable and valid
- `connection.*` types valid; retries/timeouts non-negative
- Each read entry has valid `fc`, `address`, `type`, and scaling; aliases unique system-wide
- If `override_poll_hz` set, it must be > 0 and ≤ R

### Acquisition Model
- Establish Modbus TCP connection; on drop, auto-reconnect with bounded backoff
- For each mapped read, poll at `override_poll_hz` if set else the model’s default (or per-point poll_hz)
- Align values to the R grid via last-value-hold; apply `calibration_offsets` after scaling

### UI Flow
- Right-click Vaisala tile → Configure:
  1) Select model (loads map)
  2) Enter IP (host/port), unit-id, timeouts
  3) Optional: set polling override and calibration offsets
  4) Test Connection (single read of first mapped register)
  5) Save → auto-connect and maintain connection
- Runtime: show current values per measurement and connection health (green/red). Context: Configure, Show Error, Reset Error

### Outputs & Metadata
- Records each selected measurement as its own channel (alias and units from map)
- Sidecar YAML includes model name/map path, connection parameters, polling policy, and offsets

### Error Conditions (Examples)
- Connection failure/timeouts → red status; resets with backoff
- Illegal function/address or size/type mismatch → validation/runtime error

### Test Cases (Vaisala)
- VAIS-Models-001: Model dropdown lists maps; loading applies correct addresses/units
- VAIS-ConnTest-001: Test Connection probe read succeeds/fails appropriately
- VAIS-Poll-001: Polls at override/default rates; aligns to R; applies offsets
- VAIS-ErrorUI-001: Connection drop and recover with Show Error/Reset Error behavior


