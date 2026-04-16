<!-- Author: T. Onkst | Date: 03092026 -->

## NI DAQ Plugin Specification

### Purpose
Acquire NI cDAQ data (AI voltage, AI temp, DI, DO, AO) with robust real-mode DAQmx task handling and decoupled snapshot publishing to the core tick.

### Current Implementation Status
- Implemented now:
  - Real and sim modes
  - Structured channel sections (`ai_voltage`, `ai_temp`, `di`, `do`, `ao`)
  - Hardware inventory enumeration in real mode
  - Configurable oversample for voltage/current channels with IIR Butterworth decimation filter or legacy averaging
  - Tick rate alignment: NI DAQ snapshot period inherits from core tick rate (`channel_manager.yaml` `recording_rate_hz`)
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
- **Tick rate alignment**: NI DAQ snapshot period inherits from core tick rate (`channel_manager.yaml` `recording_rate_hz`). The `recording_rate_hz` field in `ni_daq.yaml` is deprecated (`auto` inherits from core); if set to a numeric value that differs from the core rate, a warning is logged.
- In sim mode, signals are generated locally:
  - AI voltage: oversampled synthetic waveform + scaling
  - AI temp: synthetic engineering values
  - DI defaults from channel initial states
  - DO/AO reflect current state maps

### Channel Configuration Model
File: `configs/ni_daq.yaml`

```yaml
mode: real
recording_rate_hz: auto  # inherits from channel_manager.yaml; numeric value overrides with warning
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
  oversample:
    factor: 10              # hardware samples at R * factor for applicable channels
    applies_to: voltage      # "voltage" (default) | "all"
    filter: butterworth      # "butterworth" (default) | "average" | "none"
    butterworth_order: 4     # filter order (default 4, power users only)
  read_timeout_margin_s: 0.15
  threaded_fast_ai: true
health:
  poll_hz: 2
  read_fail_warn_threshold: 10
  read_fail_fault_threshold: 30
  expose_status_channels: false
watchdog:
  enabled: false
```

### Oversample and Decimation Filter

Voltage (and optionally current) channels are oversampled at `R * factor` where `R` is the core tick rate and `factor` is the configurable oversample factor (default 10). Temperature, DI, DO, and AO channels are read at `R` directly (temperature modules have hardware anti-aliasing built in).

| Filter Mode | Behavior | Default |
|-------------|----------|---------|
| `butterworth` | 4th-order IIR Butterworth low-pass (cutoff = R/2) applied per-sample in the fast reader thread; final filtered+scaled value written to shared dict under brief lock. Eliminates deques for voltage channels. | **Yes** |
| `average` | Legacy deque-based averaging of the last `factor` raw samples, computed at read time. | No |
| `none` | Raw samples buffered in deques; no anti-aliasing. | No |

The `IIRFilter` class in `_nidaq_scaling.py` uses SOS (second-order sections) via `scipy.signal.butter` for numerical stability. Coefficients are computed once at task creation; per-sample cost is ~8 multiply-adds per filter order. Falls back to passthrough if scipy is unavailable.

**Data pipeline (butterworth mode):**
1. `task.read()` -> Python lists (no lock)
2. `IIRFilter.process_batch()` + `apply_scaling()` per channel (thread-local, no lock)
3. Brief lock: `shared_values[alias] = scaled`
4. Snapshot worker copies `shared_values` -> `_snapshot_values`

This reduces the data copy chain from 6 stages to 4 and drops lock hold time from O(aliases * deque_size) to O(1) per alias.

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

### Hardware Migration
When chassis are swapped or cards are moved to a different slot, `ni_daq.yaml` channel configuration (aliases, scaling, sensor settings) would be lost because `phys` strings reference the old device names. The migration system preserves that work.

**How it works**
1. On every NI DAQ config-dialog open, `inventory_matches_config()` compares the set of `phys` channels in `ni_daq.yaml` against the live NI-DAQmx inventory.
2. If they diverge, `compute_hardware_diff()` classifies each old device by:
   - **Capability**: `ai` / `digital` / `ao` (inferred from which channel categories the device has entries in: `ai_voltage`/`ai_temp` → `ai`, `di`/`do` → `digital`, `ao` → `ao`).
   - **AI sub-type**: `voltage` or `temp` (inferred from `ai_voltage` vs `ai_temp` on the old side; inferred from product-number pattern on the new side — NI 9210/9211/9212/9213/9214/9216/9217/9219/9226/9235/9236/9237 are classified as TC/RTD/bridge).
   - **Channel count**: preserved for each missing device.
3. `HardwareMigrationDialog` (`src/ui/widgets/nidaq_migration_dialog.py`) presents old modules that need remapping with a "Map To" dropdown:
   - **Chassis are excluded** (cDAQ-9178/9189 etc. have no I/O and can't inherit a module config).
   - Only new modules with **matching capability** and **channel count ≥ old** are offered.
   - Within AI, **sub-type match first** (voltage→voltage, TC→TC), then other compatible modules after a separator.
   - Matching **product type** listed above other matches; each option label shows `DeviceName (ProductType, Nch)`.
4. `apply_migration()` rewrites `phys` strings based on confirmed mappings, preserving the full channel sub-tree (alias, scaling, sensor, enabled flag). Unmapped new devices get default entries.
5. If no mappable old modules exist (e.g., totally new chassis), the system falls back to the "Regenerate default config?" prompt.

**device_map persistence**
`ni_daq.yaml` now includes a top-level `device_map` block that maps device name → product type:

```yaml
device_map:
  AGENTMod1: "NI 9215"
  AGENTMod2: "NI 9239"
  MATRIXMod1: "NI 9214"
```

This is written automatically on every save and regeneration. It enables the migration dialog to auto-suggest exact product-type matches even after the old hardware has been disconnected (when DAQmx can no longer report its product type directly).

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
- ZMQ PUB/SUB high-water marks are bounded (HWM=10) to limit memory use on laggy subscribers.
- Temperature unit map is cached at `start()` to avoid per-tick dict construction.
- Table scaling points are pre-sorted at config load for O(n) interpolation without runtime sort overhead.


