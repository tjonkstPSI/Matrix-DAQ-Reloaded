<!-- Author: T. Onkst | Date: 08122025 -->

## Channel Manager Plugin Specification

### Purpose
Configure the recording rate R (≤ 100 Hz per run) and manage per‑channel alarm policies (warning/shutdown) with latching, in the scaled units of each channel. Provide a unified view to enable/disable channels for recording, bulk edit limits, and visualize current alarm state. Alarm outcomes integrate with UI colors and E‑stop logic (via calculated channels).

### Responsibilities
- Set recording rate R for the run (plot update rates are independent)
- List all enabled channels from plugins (NI DAQ, CAN/CCP, Modbus, LoadBank, Calculated, Statistics)
- Configure per‑channel alarms:
  - Limits: high_warning, low_warning, high_shutdown, low_shutdown (any subset)
  - Latching: trigger_after_s, unlatch_after_s (per-limit)
  - Units: reflect post‑scaling units of the channel
  - Enable/disable alarms per channel
- Bulk operations: copy/paste limits, apply templates by category (e.g., Temperature, Pressure)
- Optional: enable/disable a channel for recording (streaming may continue)
- Live status view: per‑channel current value, alarm state (OK/Warning/Shutdown), latched timers

### Alarm Semantics
- Evaluation at the recording rate R
- Warning actions: UI color yellow + log with timestamp
- Shutdown actions: set UI color red + log; downstream E‑stop actuation remains via a separate calculated logic channel, but Channel Manager can expose an aggregated `shutdown_request` boolean (see below)
- Latching:
  - trigger_after_s: condition must be continuously true for this duration before setting the state
  - unlatch_after_s: condition must be continuously false for this duration before clearing the state
- Precedence: Shutdown supersedes Warning for display/aggregation

### Aggregation Outputs (optional)
- `AlarmSummary/warning_active`: boolean, true if any channel has active warning
- `AlarmSummary/shutdown_request`: boolean, true if any channel has active shutdown
- These can be consumed by Calculated Channels/E‑stop logic and recorded if enabled

### Configuration (YAML)
File: `configs/channel_manager.yaml`

```yaml
recording_rate_hz: 100              # R for the run (≤ 100 Hz)

channels:
  - alias: "Oil Pressure"
    units: "kPa"                    # informational; derived from source
    record_enabled: true
    alarms:
      high_warning:  { value: 90,  trigger_after_s: 5,  unlatch_after_s: 10, enabled: true }
      high_shutdown: { value: 95,  trigger_after_s: 1,  unlatch_after_s: 5,  enabled: true }
      low_warning:   null
      low_shutdown:  null

  - alias: "Oil Temperature"
    units: "C"
    record_enabled: true
    alarms:
      high_warning:  { value: 110, trigger_after_s: 5,  unlatch_after_s: 10, enabled: true }
      high_shutdown: { value: 120, trigger_after_s: 1,  unlatch_after_s: 5,  enabled: true }

aggregation:
  emit_summary_channels: true       # expose AlarmSummary booleans

output:
  alarm_events:
    enabled: true                   # record per-event activation/clear with timestamps
    include_in_excel: true          # export an `AlarmEvents` sheet with event rows
```

#### Validation Rules
- `recording_rate_hz` in (0, 100]
- Channel `alias` must exist among enabled sources; units are informational but shown
- For each limit: `value` numeric in channel units; `trigger_after_s, unlatch_after_s ≥ 0`
- High limit > low limit if both specified; shutdown thresholds may not be less strict than warnings

### Execution Model
- Runs at R; evaluates limits for each channel using latest scaled value
- Maintains per‑limit latch timers; computes effective warning/shutdown state per channel
- Aggregates booleans when enabled

### UI Flow
- Right‑click Channel Manager tile → Configure:
  1) Set recording rate R (≤ 100 Hz)
  2) Channel table: alias, units, current value, record_enabled, per‑limit values and latch timings
  3) Bulk edit: paste limits to selection; apply templates by category
  4) Save
- Runtime: same table with live values and colored state; quick toggles for record_enabled

### Outputs & Metadata
- R is stored in run metadata and drives acquisition decimation
- Alarm configurations are included in the config snapshot
 - Optional AlarmSummary booleans can be recorded as channels
 - Alarm events log: each activation and clear is recorded with timestamp(s), channel alias, limit type (high/low, warning/shutdown), value at event, and latch timing info. This event table is saved with run artifacts and included in Excel export as a separate worksheet (e.g., `AlarmEvents`).

### Error Conditions (Examples)
- Invalid thresholds (e.g., high < low) → validation error
- Non‑numeric channel selected for numeric alarm → validation error

### Test Cases (Channel Manager)
- CM-Rate-001: Set R to various values ≤ 100 Hz; system uses R for storage timeline
- CM-Alarms-Validate-001: Threshold relations and non‑negative latch times enforced
- CM-Alarms-Latch-001: trigger/unlatch timings perform as specified on synthetic data
- CM-Precedence-001: Shutdown state overrides Warning in UI and summary
- CM-Aggregation-001: AlarmSummary booleans reflect per‑channel states

