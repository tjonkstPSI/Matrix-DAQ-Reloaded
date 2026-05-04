<!-- Author: T. Onkst | Date: 03092026 -->

## Statistics Plugin Specification

### Purpose
Compute snapshot statistics on demand (manual button) or automatically on a trigger edge. Metrics include mean, stdev, min, max, and peak-to-peak (p2p). A snapshot operates over a configurable window (seconds or samples) and emits one row per fire. Statistics snapshots are captured as JSONL during recording and included in the automatic Excel export.

### Inputs
- Source channels: any enabled channels from NI DAQ, CAN/CCP, Modbus, etc.
- Selectable statistics: per-channel checkboxes for mean, stdev, min, max; include a "Select All" convenience control
- Window definition: snapshot window length in seconds or samples; min_samples to validate a snapshot
- Capture mode: `backward` (trailing window, immediate) or `forward` (capture next window, delayed)
 - Metrics selection: global `metrics.selected` with per-channel overrides via `channels[*].stats`
- Automatic trigger (optional): define channel, comparator (>, >=, <, <=), threshold, and edge (rising/falling). A snapshot is taken only on the configured edge crossing. Re-arms after optional holdoff.
- Manual logging: UI provides a "Log Statistics" button that emits one line for the selected stats using the current window content

### Outputs
- File naming: statistics snapshots remain in JSONL (`stats_snapshots.jsonl`) and are included as a `StatsSnapshots` sheet in Excel export.
- Columns (wide by default):
  - Time_Relative_s, Time_Absolute_iso8601
  - For each selected channel and metric, a column named `<alias>_<metric>` (e.g., `Oil Pressure_mean`)
- Long format optional: rows with columns [time, channel, metric, value]
 - Excel export (when enabled): one worksheet per statistic. Selected stats each get their own tab (e.g., `mean`, `stdev`, `min`, `max`). Each tab contains Time columns and only the channels that include that statistic. A `Metadata` sheet is also included.
  - Current implementation: Post-run Excel exporter generates `StatsSnapshots` sheet directly from JSONL; per-stat tabs are on the roadmap.

### Configuration (YAML)
File: `configs/statistics.yaml`

```yaml
snapshot:
  window:
    seconds: 5                         # snapshot window length
    samples: null
  # readiness derived from window dimension: if seconds set, require >= seconds; if samples set, require >= samples
  capture_mode: backward               # backward | forward
  notify_on_skip: true                 # notify operator when skipped due to insufficient samples

metrics:
  selected: [mean, stdev, min, max, p2p]

manual_logging:
  enabled: true                        # enables "Log Statistics" button in UI

automatic_logging:
  enabled: false
  trigger:
    channel: "Room Temp"
    comparator: ">"                    # one of: >, >=, <, <=
    threshold: 25
    edge: rising                        # rising | falling
    holdoff_s: 0

output:
  format: wide                         # wide | long (export deferred until recording pipeline ready)
  enable_excel_export: false
  excel_per_stat_sheet: true

channels:
  - alias: "Oil Pressure"              # source channel alias
    stats: [mean, stdev, min, max, p2p] # override; if omitted uses metrics.selected
    enabled: true
  - alias: "RPM"
    stats: [mean]
    enabled: true
```

#### Validation Rules
- `recording_rate_hz` must match session R
- Snapshot window requires at least one of `seconds` or `samples`
- `min_samples` ≥ 1
- All `channels[*].alias` must exist and be numeric
- `stats` subset of {mean, stdev, min, max, p2p}
- Automatic trigger (when enabled): must reference an existing numeric channel; comparator, threshold, and edge valid
 - If `manual_logging.enabled`, UI exposes action; when invoked with fewer than `min_samples`, emit NaN/skip per format rules

### Execution Model
- Receives upstream values/units as non-blocking queued updates, then processes them in a background worker thread.
- Maintains rolling window buffers per configured source channel.
- Core tick/log cadence is controlled by Channel Manager; Statistics processing cadence is plugin-local and decoupled from the core loop.
- Snapshot emission:
  - Manual: when the user presses "Log Statistics", compute the selected stats over the current window and emit exactly one row.
  - Automatic: armed trigger watches for configured edge; on crossing, compute and emit one row; then re-arm after optional holdoff.
- When `min_samples` not met, output NaN (or skip in long format).

### UI Flow
- Right-click Statistics tile → Configure:
  1) Select channels and metrics (checkboxes; Select All)
  2) Choose window mode and parameters
  3) (Optional) define trigger gating condition
  4) Choose output format and Excel option
  5) Save
- Runtime: shows current window size/progress, last emitted timestamp, quick preview values, and a "Log Statistics" button for manual one-shot emission

### Outputs and Metadata
- Sidecar YAML includes: snapshot window, min_samples, trigger config, channel list and metrics
- Files will follow same segmentation and export rules as primary data, using `_Statistics` postfix (when recording pipeline is integrated)
 - Excel export will produce a `Metadata` sheet and one sheet per selected statistic (tabs named by the metric)

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


