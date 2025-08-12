<!-- Author: T. Onkst | Date: 08112025 -->

## NI DAQ Plugin Specification

### Purpose
Configure and acquire data from NI cDAQ modules for analog input (voltage, thermocouple, RTD), digital input/output, and analog output. Provide scaling, naming, alarms, synchronization, oversampling/decimation, and health reporting.

### Channel Discovery, Selection, and Aliases
- On Configure, discover all connected cDAQ chassis/modules and enumerate available physical channels.
- UI presents a tree/list with checkbox selection per channel.
- For each selected channel, the operator assigns a required display/recording name called "alias". The alias:
  - Is used in UI displays and as the column name in Parquet/Excel output
  - Must be unique among enabled channels
  - Accepts letters, numbers, underscore, dash, and space; length ≤ 64
  - Defaults to the internal `id` if left blank (validation will prompt to confirm)
- The hardware path (e.g., `cDAQ9185-1/ai0`) is always stored in metadata alongside the alias for traceability.

### Supported Channel Types
- Analog Input Voltage (`ai_voltage`)
- Thermocouple (`ai_tc`) with built-in CJC; types: J, K, T, E, N, R, S, B
- RTD (`ai_rtd`); wiring: 2/3/4-wire; typical Pt100
- Digital Input (`di`) lines and ports
- Digital Output (`do`) lines and ports
- Analog Output Voltage (`ao_voltage`, 0–10 V)

### Acquisition Model
- Recording rate R (per run) ≤ 100 Hz
- Fast channels: `ai_voltage` and `di` sampled in hardware at 10×R, anti-aliased, decimated to R for storage/UI
  - AI decimation: low-pass FIR/IIR; cutoff < R/2
  - DI decimation: last-sample-hold at decimation boundary; optional edge count sub-feature (TBD)
- Slow channels: `ai_tc`, `ai_rtd` sampled at ≤ R and aligned to the R grid
- Synchronization: share DAQmx SampleClock and Start Trigger among compatible tasks; align all DAQ tasks to the same timebase
- Buffering: continuous acquisition with ring buffers sized for ≥ 5 s (or ≥ 10× chunk duration); write chunks at 1 s intervals
- Error handling: device/module removal, rate not supported, routing conflicts → surface in UI (Show Error), allow Reset Error to retry init; logs include DAQmx error codes

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

### Channel Scaling and Metadata
- Per-channel custom scaling applied before alarms (linear M/B and optional polynomial in roadmap)
- Units derived from scaling; used by alarms and UI
- Categories/tags for UI grouping: Pressure, Temperature, Analog, Digital, Facility, Other

### Alarms Integration
- Per-channel high/low warning and shutdown limits with per-limit latching (trigger_after_s, unlatch_after_s)
- Warning → UI yellow + log; Shutdown → triggers E-stop via calculated channel logic + UI red + log

### Configuration (YAML)
File: `configs/ni_daq.yaml`

```yaml
recording_rate_hz: 100            # R (per run); may be overridden by Channel Manager
oversample_factor_fast: 10        # hardware oversample factor for ai_voltage and di
  decimation:
  filter: IIR_Butterworth         # default: 4th-order IIR Butterworth
  cutoff_hz: auto                 # auto = < R/2 (design 4th-order)
sync:
  share_sample_clock: true
  share_start_trigger: true

watchdog:
  mode: driver                        # driver | digital_loopback
  enabled: true
  driver:
    refresh_rate_hz: 2
    timeout_ms: 1000                  # device-side expiration window
    expir_states:                     # optional safe states to apply on expiration (device-supported DO/AO)
      do_lines: []                    # e.g., ["cDAQ9185-1/port1/line0:low", "cDAQ9185-1/port1/line1:low"]
      ao_lines: []                    # e.g., ["cDAQ9185-1/ao0:0.0V"]
  digital_loopback:
    do_line: "cDAQ9185-1/port1/line7"
    di_return: "cDAQ9185-1/port0/line7"
    toggle_rate_hz: 2
    verify_timeout_ms: 250
    miss_threshold: 3

chassis:
  - alias: "cDAQ_A"
    device: "cDAQ9185-1"         # DAQmx device name

channels:
  - id: "P_oil"
    alias: "Oil Pressure"          # Display/recording name (required for enabled channels)
    device: "cDAQ9185-1/ai0"
    type: ai_voltage
    terminal_config: Differential  # Differential | RSE | NRSE
    range_v: { min: 0, max: 10 }
    scaling: { type: linear, unit: kPa, m: 10.0, b: 0.0 }  # 0–10 V → 0–100 kPa
    category: Pressure
    enabled: true
    alarms:
      high_warning:  { value: 90,  trigger_after_s: 5,  unlatch_after_s: 10 }
      high_shutdown: { value: 95,  trigger_after_s: 1,  unlatch_after_s: 5 }
      low_warning:   null
      low_shutdown:  null

  - id: "T_exh1"
    alias: "Exhaust Temp 1"
    device: "cDAQ9185-1/ai1"
    type: ai_tc
    tc_type: K
    cjc: built_in
    unit: C
    range_c: { min: 0, max: 1200 }
    category: Temperature
    enabled: true

  - id: "RTD1"
    alias: "RTD Sensor 1"
    device: "cDAQ9185-1/ai2"
    type: ai_rtd
    rtd_type: Pt100
    wiring: 3wire                  # 2wire | 3wire | 4wire
    unit: C
    category: Temperature
    enabled: true

  - id: "DI_0"
    alias: "DI Line 0"
    device: "cDAQ9185-1/port0/line0"
    type: di
    category: Digital
    enabled: true

  - id: "DO_SHUTDOWN"
    alias: "Shutdown DO"
    device: "cDAQ9185-1/port1/line0"
    type: do
    default_state: low
    category: Digital
    enabled: true

  - id: "AO_FAN"
    alias: "Fan Command"
    device: "cDAQ9185-1/ao0"
    type: ao_voltage
    range_v: { min: 0, max: 10 }
    unit: V
    category: Analog
    enabled: true
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


