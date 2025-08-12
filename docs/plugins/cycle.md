<!-- Author: T. Onkst | Date: 08112025 -->

## Cycle Plugin Specification

### Purpose
Execute a user-defined load schedule for the load bank using a CSV file with Time and Load columns (Load in kW). Supports pause/stop/restart/skip and looping (default 1 total pass). There is no interpolation: new setpoints are applied at the specified times. Integrates tightly with the LoadBank plugin and drives its setpoint directly in kW.

### CSV Schema
- File format: CSV
- Required columns:
  - Time (seconds)
  - Load (kW)
- Semantics:
  - Time is monotonically non-decreasing; Load is the target setpoint (kW) applied at that time (step behavior)
  - No interpolation; the setpoint changes only at the provided time stamps

### Looping & Controls
- loops_total: default 1 (no infinite mode). If > 1, repeats the cycle that many times.
- inter_loop_dwell_s: default 0; no safe-state enforced between loops
- Controls during run:
  - Pause: hold current setpoint; timer paused
  - Stop: abort cycle; LoadBank remains under app control; recording can continue until user stops test
  - Restart: resume from start of current loop or from beginning (configurable)
  - Skip: jump to next step/row

### Integration with LoadBank
- Issues setpoint commands to the LoadBank plugin in kW (direct pass-through)
- No Accept required when running a cycle; step changes apply automatically
- Rate limiting and readback confirmation handled by LoadBank per model map (if supported)

### Execution Model
- Scheduler runs at the system recording rate R (≤ 100 Hz)
- At each tick:
  - Determine current cycle row based on elapsed time within the loop (step schedule)
  - If at a new step boundary, update the target setpoint to the row's Load (kW)
  - Send setpoint to LoadBank (respecting model rate limit if configured)
- Boundary markers are logged at each CSV row transition and loop boundary

### Configuration (YAML)
File: `configs/cycle.yaml`

```yaml
recording_rate_hz: 100

source:
  csv_path: "C:/Configs/cycles/standard_breakin.csv"
  columns: { time: "Time", load: "Load" }   # Load is in kW

execution:
  loops_total: 1
  inter_loop_dwell_s: 0
  restart_policy: "restart_loop"     # restart_loop | resume_step
  skip_behavior: "next_row"          # next_row | next_change
  interpolation: "none"              # no interpolation; step schedule

integration:
  loadbank:
    accept_required: false            # ignored for cycle; steps apply automatically
    units: "kW"                       # cycle drives LoadBank setpoint in kW
    smoothing:
      ramp_limit_pct_per_s: null      # not applicable to step schedule; reserved for future

optional_safety:                      # nice-to-have future feature (disabled by default)
  enabled: false
  watch_channel: "IntakeAirTemp"      # channel name to monitor
  limit_high: 60.0                    # unit as per channel (e.g., C)
  backoff_kw: 10.0                    # reduce setpoint by this kW when exceeded
  cooloff_s: 30                       # hold reduced load for this many seconds before reevaluating

ui:
  show_status_table: true
  show_current_step_details: true
```

#### Validation Rules
- CSV readable; required columns present; Time non-decreasing; Load kW within [0, +∞) and reasonable for site limits
- loops_total ≥ 1; inter_loop_dwell_s ≥ 0
- If optional_safety.enabled, ensure watch_channel exists; limits and backoff are non-negative

### UI Flow
- Right-click Cycle tile → Configure:
  1) Select CSV
  2) Set loops_total, dwell, restart/skip behavior
  3) (Optional future) configure safety backoff watcher
  4) Save; preview plot shown (x: Time s, y: Load kW) for visual confirmation
- Runtime display: current loop/index, current/next step, time remaining, setpoint, accept status

### Error Handling
- CSV parse/validation errors → red status; must be corrected before run
- LoadBank comms loss: follow system policy → trigger E‑stop via calculated-channel logic; cycle aborts
- If operator does not accept when required, cycle remains paused at boundary

### Outputs and Metadata
- Record boundary and loop events in per-run logs
- Optionally record a “Cycle Target (kW)” channel at rate R for traceability

### Test Cases (Cycle)
- CYC-CSV-001: Load CSV with Time/Load (kW); validation of monotonic time and plausible ranges
- CYC-Loops-001: loops_total=3 executes three passes; boundary logs correct
- CYC-Step-Apply-001: Setpoints change at exact time stamps (no interpolation); LoadBank receives kW setpoints
- CYC-Pause-Stop-001: Pause holds current setpoint; Stop aborts cycle
- CYC-Restart-Skip-001: Restart behavior per policy; Skip jumps to next row/change
- CYC-Plot-Preview-001: After CSV import, plot preview renders Time vs Load kW correctly
- CYC-TargetChannel-001: Target setpoint (kW) channel aligns to R and matches step schedule
- CYC-Safety-Backoff-001: (Future) With optional_safety enabled and limit exceeded, setpoint is backed off by configured kW and cooloff applied

