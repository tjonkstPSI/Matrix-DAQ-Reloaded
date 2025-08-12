<!-- Author: T. Onkst | Date: 08112025 -->

## Statistics Plugin Specification

### Purpose
Compute selectable summary statistics (mean, stdev, min, max) for selected channels at the recording rate R (≤ 100 Hz), using fixed or rolling windows. Emission can be automatic (trigger/gated) or manual on-demand via a "Log Statistics" button that records a single sample based on the current window content. Write results to separate Parquet + YAML (and optional Excel) with the `_Statistics` postfix and identical segmentation/split rules as primary data.

### Inputs
- Source channels: any enabled channels from NI DAQ, CAN/CCP, Modbus, etc.
- Selectable statistics: per-channel checkboxes for mean, stdev, min, max; include a "Select All" convenience control
- Window definitions:
  - rolling: window length in seconds or samples
  - fixed: emit interval in seconds or samples (resets each interval)
- Triggers (optional): boolean/gated condition built from existing channels/expressions (for automatic emission)
- Manual logging: UI provides a "Log Statistics" button that emits one line for the selected stats using the current window content

### Outputs
- File naming: `<base>_Statistics[ _segNN ].parquet` (+ sidecar YAML); Excel split `.n` applies when exported
- Columns (wide by default):
  - Time_Relative_s, Time_Absolute_iso8601
  - For each selected channel and metric, a column named `<alias>_<metric>` (e.g., `Oil Pressure_mean`)
- Long format optional: rows with columns [time, channel, metric, value]
 - Excel export (when enabled): one worksheet per statistic. Selected stats each get their own tab (e.g., `mean`, `stdev`, `min`, `max`). Each tab contains Time columns and only the channels that include that statistic. A `Metadata` sheet is also included.

### Configuration (YAML)
File: `configs/statistics.yaml`

```yaml
recording_rate_hz: 100                 # matches recording rate R

output:
  format: wide                         # wide | long
  enable_excel_export: false
  excel_per_stat_sheet: true           # when exporting to Excel, create one tab per selected stat

windows:
  mode: rolling                        # rolling | fixed
  size:
    seconds: 5                         # rolling window length; ignored for fixed
    samples: null
  emit_interval:
    seconds: null                      # for fixed mode (e.g., every 10 s)
    samples: null
  min_samples: 3                       # minimum samples to produce stats

triggers:                              # optional gating (nice-to-have)
  enabled: false
  condition: "(IntakeAirTemp < 60)"     # expression over existing channels
  behavior: emit_only_when_true        # emit_only_when_true | emit_on_rising_edge
  holdoff_s: 0

manual_logging:
  enabled: true                        # enables "Log Statistics" button in UI
  include_timestamp: true              # include current time columns

channels:
  - alias: "Oil Pressure"              # source channel alias
    stats: [mean, stdev, min, max]
    enabled: true
  - alias: "RPM"
    stats: [mean]
    enabled: true
```

#### Validation Rules
- `recording_rate_hz` must match session R
- `windows.mode` in {rolling, fixed}
- rolling: `size.seconds|samples` must specify at least one; fixed: `emit_interval.seconds|samples` required
- `min_samples` ≥ 1
- All `channels[*].alias` must exist and be numeric
- `stats` subset of {mean, stdev, min, max}
- Trigger expression (when enabled) must parse and reference existing channels
 - If `manual_logging.enabled`, UI exposes action; when invoked with fewer than `min_samples`, emit NaN/skip per format rules

### Execution Model
- Runs at R; maintains per-channel state for rolling or fixed intervals
- rolling: update window each tick; emit each tick
- fixed: accumulate until interval boundary, then emit and reset
- When `min_samples` not met, output NaN (or skip in long format)
- Trigger gating:
  - emit_only_when_true: suppress emission when condition false
  - emit_on_rising_edge: emit one sample at rising edge; optional holdoff
 - Manual logging: when the user presses "Log Statistics", compute the selected stats from the current window content and emit exactly one row immediately (does not reset rolling/fixed state); respects `min_samples` policy

### UI Flow
- Right-click Statistics tile → Configure:
  1) Select channels and metrics (checkboxes; Select All)
  2) Choose window mode and parameters
  3) (Optional) define trigger gating condition
  4) Choose output format and Excel option
  5) Save
- Runtime: shows current window size/progress, last emitted timestamp, quick preview values, and a "Log Statistics" button for manual one-shot emission

### Outputs and Metadata
- Sidecar YAML includes: window mode/size, min_samples, trigger config, channel list and metrics
- Files follow same segmentation and export rules as primary data, using `_Statistics` postfix
 - Excel export produces a `Metadata` sheet and one sheet per selected statistic (tabs named by the metric)

### Error Conditions (Examples)
- Non-numeric channel selected → validation error
- Window parameters inconsistent with R → validation warning (rounding applied)
- Trigger references missing channel → validation error

### Test Cases (Statistics)
- STATS-Rolling-001: 5 s rolling window; values match expected for synthetic data
- STATS-Fixed-001: 10 s fixed intervals; emission at boundaries with reset
- STATS-MinSamples-001: Outputs NaN/skip until minimum samples reached
- STATS-Trigger-EmitOnly-001: Emission suppressed while condition false
- STATS-Trigger-RisingEdge-001: Single-sample emissions at rising edges
- STATS-Format-001: Wide vs long formatting; column naming `<alias>_<metric>`
- STATS-FileRules-001: `_Statistics` postfix, segmentation, and Excel split behavior
 - STATS-Selectable-001: Per-channel metric checkboxes respected; Select All applies all metrics
 - STATS-Manual-001: "Log Statistics" emits a single row based on current window content without resetting state
 - STATS-Excel-Tabs-001: Excel export contains `Metadata` plus one tab per selected statistic with appropriate columns


