<!-- Author: T. Onkst | Date: 03092026 -->

## Omega Weather Station Plugin Specification

### Purpose
Provide ambient weather measurements (barometric pressure, temperature, relative humidity) from an Omega weather station via Modbus TCP. The device exposes a fixed register layout, so the plugin ships with hard-coded channel map; users only need to supply the IP/port and may optionally rename the published aliases.

### Scope
- Transport: Modbus TCP (`pymodbus`).
- Channels (3, fixed): `temp` (addr 8), `barometric` (addr 10), `humidity` (addr 12). Each is a two-register big-endian float32.
- Error handling: sentinel NaN codes `0x7F800000`, `0x7F800001`, `0x7F800002`, `0x7F800003` are decoded as Python `float('nan')` and reported as NaN in telemetry.
- Sim/real mode is controlled globally via the launch dialog Offline Mode checkbox, not per-plugin.

### Fixed Channel Map
| Name        | Register | Default Alias | Unit |
|-------------|----------|---------------|------|
| temperature | 8        | `xTP_Amb`     | C    |
| barometric  | 10       | `xPR_Amb`     | kPa  |
| humidity    | 12       | `xHM_Amb`     | Pct  |

### Configuration (YAML)
File: `configs/omega.yaml`

```yaml
mode: real             # overridden to 'sim' when global Offline Mode is on
connection:
  host: 192.168.1.100
  port: 502
  timeout_ms: 1000
  poll_rate_hz: 1
channels:              # optional — overrides default aliases
  temp:       { alias: "xTP_Amb" }
  barometric: { alias: "xPR_Amb" }
  humidity:   { alias: "xHM_Amb" }
```

Blank or missing alias entries fall back to the defaults in the `CHANNEL_MAP` constant in `src/plugins/omega.py`.

### Config Dialog (`omega_config.py`)
- **Connection**: host (IP), port spin box.
- **Channels table**: columns ID / Unit / Alias. The Alias cell is editable; double-clicking opens the shared `AliasPickerDialog` (standard channels from JSON + custom entry with regex validation).
- Blank aliases on save trigger a warning. Poll rate and timeout remain in YAML for super-users.

### Runtime
- Threaded poll loop reads all three registers in one `read_holding_registers(0, 6)` call, decodes float32 big-endian, applies NaN sentinel handling, and publishes to the orchestrator on each tick via cached latest values.
- Automatic reconnect on socket/Modbus errors.
- Sim mode generates sine-wave values per channel (independent phase and amplitude per measurement).
