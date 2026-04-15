<!-- Author: T. Onkst | Date: 03092026 -->

## NI DAQ Plugin Specification

### Purpose
Acquire NI cDAQ data (AI voltage, AI temp, DI, DO, AO) with robust real-mode DAQmx task handling and decoupled snapshot publishing to the core tick.

### Current Implementation Status
- Implemented now:
  - Real and sim modes
  - Structured channel sections (`ai_voltage`, `ai_temp`, `di`, `do`, `ao`)
  - Hardware inventory enumeration in real mode
  - Fast AI oversample path (10x recording rate) with per-device task grouping
  - Background snapshot worker for non-blocking core reads
  - Optional threaded fast-AI reader mode (`acquisition.threaded_fast_ai`)
  - Health monitoring worker and optional health telemetry append
  - Watchdog config validation (driver / digital_loopback schema checks)
- Not fully implemented yet:
  - Full runtime watchdog actuation behavior (validation is present; real behavior remains staged)

### Runtime Model
- In real mode:
  - DAQ tasks are created at `start()`
  - Fast AI channels are grouped per physical device
  - A snapshot thread continuously calls `_read_real()` and updates latest values
- `simulate_step()` returns cached snapshot in real mode.
- Core tick/logging cadence is controlled by Channel Manager (`configs/channel_manager.yaml` `recording_rate_hz`); NI_DAQ worker timing remains plugin-local and non-blocking to the core loop.
- In sim mode, signals are generated locally:
  - AI voltage: oversampled synthetic waveform + scaling
  - AI temp: synthetic engineering values
  - DI defaults from channel initial states
  - DO/AO reflect current state maps

### Channel Configuration Model
File: `configs/ni_daq.yaml`

```yaml
mode: real
recording_rate_hz: 10
channels:
  ai_voltage:
    - phys: Dev1/ai0
      alias: qPR_Amb
      enabled: true
      range_v: { min: 0, max: 10 }
      scaling:
        type: linear
        gain: 10.0
        offset: 0.0
        unit: kPa
  ai_temp:
    - phys: Dev1/ai1
      alias: qTP_Amb
      enabled: true
      sensor: { type: TC, subtype: K }
      unit: C
  di: []
  do: []
  ao: []
acquisition:
  read_timeout_margin_s: 0.15
  threaded_fast_ai: false
health:
  poll_hz: 2
  read_fail_warn_threshold: 10
  read_fail_fault_threshold: 30
  expose_status_channels: false
watchdog:
  enabled: false
```

### Scaling System

Voltage channels support three scaling types persisted in `scaling`:

| Type | Keys | Behavior |
|------|------|----------|
| `none` | `unit` | Raw voltage passed through |
| `linear` | `gain`, `offset`, `unit` | `scaled = raw * gain + offset` |
| `table` | `points`, `unit`, `extrapolate` | Piecewise linear interpolation between `[raw, scaled]` pairs; clamp outside range by default, or linearly extrapolate when `extrapolate: true` |

Temperature channels (RTD/TC) support unit selection (`C`, `F`, `K`); NI-DAQmx reads in Celsius and the plugin converts using well-known formulas.

Scaling is applied at the plugin level before values are published to the orchestrator. Both the real acquisition path (`_nidaq_acquisition.py`) and the simulation path (`_nidaq_simulation.py`) call the shared `apply_scaling()` / `convert_temp_unit()` helpers in `_nidaq_scaling.py`.

**Scale Library**: Premade scales are stored in `configs/scale_library.yaml` and can be imported into the scaling editor dialog.

### Constrained Alias System

All NI DAQ channel aliases (AI, DI, DO, AO) must match a constrained naming convention enforced by regex validation. The pattern requires:

- A prefix character from `[qcemixypvl]` (or `[eiyx]` for freeform aliases)
- A two-letter measurement-type code (e.g., `TP`, `PR`, `FL`, `VL`)
- An underscore separator
- One or more three-letter subsystem/location codes (e.g., `Eng`, `Oil`, `Amb`)

Aliases are selected via the `AliasPickerDialog` which offers:
- A searchable library loaded from `configs/alias_library.yaml`
- A custom-entry tab with live regex validation

Alias validation is also enforced on config save; invalid aliases on enabled channels block the save with a diagnostic message.

### Validation Rules (Current)
- In real mode, NI-DAQmx Python package must be available.
- Enabled aliases must be unique within NI_DAQ plugin.
- Enabled aliases must match the constrained naming convention regex.
- Real-mode inventory check compares configured physical channels to discovered hardware.
- Watchdog block is validated when enabled:
  - mode `driver` or `digital_loopback`
  - required keys and numeric ranges validated by mode
  - `expir_states` (if used) must reference configured DO aliases

### Discovery Helper
- Tool available: `py -m src.tools.nidaq_discover`
- Generates `configs/ni_daq.generated.yaml` template from discovered devices/channels.

### Health and Diagnostics
- Internal health state tracks:
  - last good read time
  - consecutive read failures
  - health status/error text
- When `health.expose_status_channels: true`, plugin appends:
  - `NI_DAQ/health_ok`
  - `NI_DAQ/consec_failures`
  - `NI_DAQ/last_good_read_age_s`
  - `NI_DAQ/task_fast_alive`

### Notes on Robustness
- Per-device fast AI tasks isolate failures to one device path.
- Adaptive timeout and buffer sizing are used in real read path to reduce backlog/timeout issues.
- Snapshot model prevents DAQ read timing from stalling core tick cadence (sample-and-hold at publish/record tick).


