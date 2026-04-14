<!-- Author: T. Onkst | Date: 03092026 -->

## Channel Manager Plugin Specification

### Purpose
Configure core logging cadence and segmentation settings, and manage two-tier per-channel alarms with old-system compatible action/enabling-condition behavior.

### Current Implementation Status
- Implemented now:
  - Right-click Configure dialog for `Channel_Manager`.
  - Logging inputs:
    - sample rate (Hz),
    - segment time limit (s),
    - segment size limit (MB),
    - coalesce/keep chunk options.
  - Alarm table for active runtime channel aliases.
  - Two-tier alarm setup:
  - warning tier and alarm tier,
    - low/high thresholds,
    - per-limit latch delays (enter/clear),
    - per-tier action.
  - Enabling conditions:
    - Always Enabled
    - Engine Running
    - Engine Run time
    - Test Time
  - Engine speed source selector filtered to active aliases containing `rpm` or `cSP_Eng`.
  - YAML import/export from the dialog.

### Alarm Semantics
- Two-tier behavior:
  - Tier 1 warning (yellow semantics in UI layer)
  - Tier 2 alarm (red semantics in UI layer)
- Each tier action can be:
  - `Visible Alert`
  - `Visible Alert + Shutdown`
- Aggregated shutdown request is action-driven (a tier must be in active alarm state and configured with `Visible Alert + Shutdown`).
- Per-limit debounce is supported for each of:
  - warning low/high
  - alarm low/high
- Backward compatibility:
  - legacy flat keys (`high_warning`, `high_shutdown`, `enter_delay_s`, `clear_delay_s`, etc.) are still interpreted by alarm runtime.

### Aggregation Outputs
- `alarm_summary.any_warning`
- `alarm_summary.any_shutdown`
- `alarm_summary.any_shutdown_request` (action-driven)
- Channel outputs for operator visibility:
  - `iOT_Warning`
  - `iOT_Alarm`

### Configuration (YAML - current canonical shape)
File: `configs/channel_manager.yaml`

```yaml
enabled: true
recording_rate_hz: 10
storage:
  chunk_duration_s: 1
  segment_time_limit_s: 3600
  segment_size_limit_mb: 100
  coalesce_on_finalize: true
  keep_chunk_files: false
engine_running:
  source_alias: cSP_Eng
  rpm_threshold: 0
channels:
  - alias: iTM_EngRun
    warning:
      low: null
      low_enter_delay_s: 0.2
      low_clear_delay_s: 1.0
      high: 100.0
      high_enter_delay_s: 0.2
      high_clear_delay_s: 1.0
      action: visible_alert
    alarm:
      low: null
      low_enter_delay_s: 0.2
      low_clear_delay_s: 1.0
      high: 150.0
      high_enter_delay_s: 0.2
      high_clear_delay_s: 1.0
      action: visible_alert_shutdown
    enabling_condition: always_enabled
    enable_threshold: 0.0
```

#### Validation Rules
- `recording_rate_hz > 0`
- `storage.segment_time_limit_s > 0`
- `storage.segment_size_limit_mb > 0`
- Channel aliases in table must be unique.
- Alarm thresholds are optional; blank disables that threshold.

### Execution Model
- Core tick cadence is derived from `recording_rate_hz` in Channel Manager.
- Segmentation uses time-or-size policy from `storage` settings.
- Alarm runtime evaluates latest values each tick with enabling-condition gating and per-limit debounce.

### UI Flow
- Right-click Channel Manager tile -> Configure:
  1) Set sample rate and storage segmentation settings.
  2) Select engine speed alias + RPM threshold for engine condition modes.
  3) Add active channels, remove extra rows, and edit alarm table.
  4) Save (writes YAML + reloads Channel Manager runtime behavior).

### Outputs & Metadata
- Recording cadence and storage segmentation settings are captured in run metadata snapshot.
- Alarm transition events continue to flow through alarm event logging (`alarm_events.jsonl`).
- UI integration:
  - All Channels Table row colors follow alarm state (`WARN` yellow, `ALARM` red).
  - Coloring helper is reusable for future table-based displays.

### Error Conditions (Examples)
- Invalid thresholds (e.g., high < low) → validation error
- Non‑numeric channel selected for numeric alarm → validation error

### Test Cases (Channel Manager)
- CM-Rate-001: Set R to various values ≤ 100 Hz; system uses R for storage timeline
- CM-Alarms-Validate-001: Threshold relations and non‑negative latch times enforced
- CM-Alarms-Latch-001: trigger/unlatch timings perform as specified on synthetic data
- CM-Precedence-001: Alarm state overrides Warning in UI and summary
- CM-Aggregation-001: AlarmSummary booleans reflect per‑channel states

