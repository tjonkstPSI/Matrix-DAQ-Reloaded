<!-- Author: T. Onkst | Date: 08112025 -->

## NI DAQ Plugin Specification

### Purpose
Configure and acquire data from NI cDAQ modules for analog input (voltage, thermocouple, RTD), digital input/output, and analog output. Provide scaling, naming, alarms, synchronization, oversampling/decimation, and health reporting.

### Channel Discovery, Selection, and Aliases
- Discovery helper (available now):
  - Enumerates cDAQ chassis/modules and AI/DI/DO/AO
  - Categorizes modules by product_type (e.g., 9214→TC, 9217→RTD, 9239→Voltage, 9265→AO current)
  - Generates a structured template at `configs/ni_daq.generated.yaml`
  - Usage:
    ```bash
    py -m src.tools.nidaq_discover
    ```
- Operator copies needed sections into `configs/ni_daq.yaml`, enables channels, sets scaling/sensors, and aliases.
- UI channel picker is planned later; for now, YAML drives selection.

### Supported Channel Types
- Analog Input Voltage (`ai_voltage`)
- Thermocouple (`ai_tc`) with built-in CJC; types: J, K, T, E, N, R, S, B
- RTD (`ai_rtd`); wiring: 2/3/4-wire; typical Pt100
- Digital Input (`di`) lines and ports
- Digital Output (`do`) lines and ports
- Analog Output Voltage (`ao_voltage`, 0–10 V)

### Acquisition Model
- Recording rate R (per run) ≤ 100 Hz
- Fast channels: `ai_voltage` sampled at 10×R and anti‑alias averaged to R (sim and real); DI read at R (on‑demand) for now
- Slow channels: `ai_temp` (TC/RTD) at ≤ R; configure CJC/wires where supported; fallback to voltage if unsupported
- Error handling: invalid/unsupported physical channels are skipped; DAQmx tasks are explicitly closed on stop to avoid resource warnings

### Watchdog (Chassis Connectivity Check)
- Purpose: continuously verify NI cDAQ connectivity and detect host↔device link loss.
- Modes:
  - driver: use NI-DAQmx device-level watchdog on supported network cDAQ (e.g., 9185/9188). The host arms a watchdog task on the device and periodically refreshes it. On refresh lapse (e.g., host crash or link loss), the device transitions to an expired state (sets configured safe states) and the task reports expiration.
  - digital_loopback: toggle a dedicated digital output and verify a wired digital input sees the edges within a timeout (fallback for devices without driver watchdog support).
- Health: if the watchdog expires (driver mode) or N consecutive loopback misses occur (loopback mode), mark NI DAQ plugin red and raise a system health fault; optional action to trigger calculated-channel E-stop.
- Typical defaults:
  - driver: refresh_rate_hz = 2, timeout_ms = 1000 (supported on network cDAQ such as CDAQ‑9188/9189; feature-detect at runtime)
  - loopback: toggle_rate_hz = 2, verify timeout_ms = 250, miss_threshold = 3
  - supported_devices (driver mode): network cDAQ such as NI-9185/NI-9188; PCIe/USB devices typically do not support device-side watchdog

#### Watchdog configuration (schema and validation)
The `watchdog` block in `configs/ni_daq.yaml` is optional. If `enabled: true`, config is validated:

```yaml
watchdog:
  enabled: true
  mode: driver            # driver | digital_loopback
  refresh_rate_hz: 2      # required for driver mode
  timeout_ms: 1000        # required for driver mode
  expir_states: { }       # optional mapping of DO alias -> 0|1 safe state on expiry
```

For digital loopback mode:

```yaml
watchdog:
  enabled: true
  mode: digital_loopback
  do_line: "Dev1/port1/line0"     # required
  di_return: "Dev1/port0/line0"   # required (must be distinct from do_line)
  toggle_rate_hz: 2                # required (>0)
  verify_timeout_ms: 250           # required (>0)
  miss_threshold: 3                # required (>=1)
```

Validation errors are surfaced by `NiDAQPlugin.validate()` with descriptive messages. Runtime watchdog behavior will be implemented when hardware is available.

### Channel Scaling and Metadata
- Per-channel custom scaling applied before alarms (linear M/B and optional polynomial in roadmap)
- Units derived from scaling; used by alarms and UI
- Categories/tags for UI grouping: Pressure, Temperature, Analog, Digital, Facility, Other

### Alarms Integration
- Per-channel high/low warning and shutdown limits with per-limit latching (trigger_after_s, unlatch_after_s)
- Warning → UI yellow + log; Shutdown → triggers E-stop via calculated channel logic + UI red + log

### Configuration (YAML)
File: `configs/ni_daq.yaml` (structured)

```yaml
mode: real  # real | sim
recording_rate_hz: 10

decimation:
  filter: IIR_Butterworth
  cutoff_hz: auto

channels:
  ai_voltage:
    - phys: "Dev1/ai0"
      alias: "qPR_Amb"
      enabled: true
      range_v: { min: 0, max: 10 }
      scaling: { m: 10.0, b: 0.0, unit: "kPa" }
  ai_temp:
    - phys: "Dev1/ai1"
      alias: "qTP_Amb"
      enabled: true
      sensor: { type: "TC", subtype: "K" }
      unit: "C"
  di:
    - phys: "Dev1/port0/line0"
      alias: "qDG_Estop"
      enabled: true
      initial: 1
  do:
    - phys: "Dev1/port1/line0"
      alias: "qDG_FuelPump"
      enabled: true
      initial: 0
  ao: []
```

#### Validation Rules
- `id` unique across channels
- `device` must exist and match type/module capabilities
- `alias` required for enabled channels; must be unique among enabled channels; allowed charset and length enforced
- `recording_rate_hz * oversample_factor_fast` within module limits; route compatibility for shared clocks/triggers
- Thermocouple: valid `tc_type`; RTD: valid `rtd_type` and `wiring`
- Digital outputs specify `default_state`; analog outputs specify range
- Watchdog:
  - mode in {driver, digital_loopback}
  - driver: device supports DAQmx watchdog; refresh_rate_hz > 0; timeout_ms > 0; expir_states channels exist if provided
  - digital_loopback: `do_line` and `di_return` exist and are distinct; toggle_rate_hz > 0; verify_timeout_ms > 0; miss_threshold ≥ 1
- Alarms values in target units; latching durations ≥ 0

### Task Grouping Strategy
- Group by rate class and physical device:
  - Fast AI/DI at 10×R in one or more tasks (as required by module grouping)
  - Slow AI (TC/RTD) at ≤ R in separate tasks
- Share timing: route master SampleClock/StartTrigger from first fast AI task to others where supported

### Read robustness and health (real path)
- Per-device fast AI tasks: channels are grouped by physical device and read per task to avoid cross-device routing constraints.
- Adaptive read timeouts with warm-up:
  - For fast AI reads per device: timeout_fast = max(n/fast_rate + margin, 2.5/recording_rate_hz), where n=oversample factor (default 10).
  - For AI-Temp: timeout_temp = max(1/recording_rate_hz + margin, 2.5/recording_rate_hz).
  - For DI: timeout_di = max(1/recording_rate_hz + margin, 2.0/recording_rate_hz).
  - A warm-up window of approximately n/fast_rate + 0.05 s is honored at task start; read failures during warm-up do not increment health counters.
- Per-task isolation: each device task read is wrapped in its own try/except so a single device timeout does not prevent other devices from updating.
- Failure accounting: `consec_failures` increments only when no inputs (fast AI, AI-Temp, DI) succeed during a tick; any successful read resets the counter and updates `last_good_read_ts`.
- Buffering: fast AI tasks configure `samps_per_chan ≈ 2×fast_rate` to increase on-device buffering headroom.
- Exposed health telemetry (optional, via config `health.expose_status_channels: true`):
  - `NI_DAQ/health_ok` (0/1), `NI_DAQ/consec_failures`, `NI_DAQ/last_good_read_age_s`, `NI_DAQ/task_fast_alive`.

#### Failure injection tool (for testing)
Use the control tool to inject read failures and verify health behavior:

```bash
py -m src.tools.inject_control --plugin NI_DAQ --mode read_error --count 3
```

Tune sensitivity in `configs/ni_daq.yaml`:

```yaml
health: { poll_hz: 2, read_fail_warn_threshold: 10, read_fail_fault_threshold: 30, expose_status_channels: true }
acquisition: { read_timeout_margin_s: 0.15 }
```

### UI Flow
- Right-click NI DAQ tile → Configure: module discovery, channel list, add/edit channel, scaling, alarms, enable/disable
- Watchdog subtab:
  - Mode selector: driver or digital loopback
  - Driver mode: set refresh_rate_hz, timeout_ms, optional expir_states; Test Watchdog (arm/refresh, simulate lapse)
  - Loopback mode: select DO/DI lines, set toggle rate and thresholds; Test Watchdog (start/stop) with live status
- Validation on save; errors surfaced inline; tile status turns green/red accordingly
- Show Error / Reset Error available from context menu

### Outputs and Metadata
- Channel metadata recorded in sidecar YAML (alias, id, units, scaling, category, hardware `device` path)
- Recorded values at R on the canonical grid; fast-channel decimated values
- DO/AO commanded values may be recorded at R for traceability
- Watchdog status channel (boolean/enum: OK/EXPIRED/FAULT) recorded at R for traceability (optional)

### Error Conditions (Examples)
- -200077: Requested sample rate not supported → suggest lower R or change oversample factor
- -89120: Cannot route specified signal → adjust sync settings or regroup tasks
- Device removed or module missing → red status; Show Error with DAQmx code

### Test Cases (NI_DAQ)
- NI_DAQ-Discovery-001: Discover hardware, present selectable checkbox list; selection persistence
- NI_DAQ-Alias-Unique-001: Enforce alias uniqueness and charset; alias used in UI and output column names
- NI_DAQ-Config-001: Load/save YAML config; validation passes for supported rates and modules
- NI_DAQ-Acq-001: Fast AI at 10×R with decimation to R; verify filter response
- NI_DAQ-DI-001: DI sampled at 10×R; decimated to R with last-sample-hold
- NI_DAQ-TC-RTD-001: TC/RTD at ≤ R aligned to grid; units correct
- NI_DAQ-Sync-001: Shared SampleClock/StartTrigger across tasks; phase alignment checked
- NI_DAQ-Alarms-001: Alarms evaluated in scaled units; latching behavior matches config
- NI_DAQ-ErrorUI-001: Simulate DAQmx error; Show Error/Reset Error behavior
- NI_DAQ-Watchdog-Driver-001: On supported network cDAQ, arming and refreshing driver watchdog works; simulated refresh lapse marks EXPIRED and sets expir_states
- NI_DAQ-Watchdog-Loopback-001: Loopback edges verified; N misses cause FAULT; UI reflects status


