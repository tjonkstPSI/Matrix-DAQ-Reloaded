<!-- Author: T. Onkst | Date: 04212026 -->

## Cycle Plugin Specification

### Purpose
Execute a user-defined load schedule for the load bank using a CSV file with Time and Load columns (Load in kW). Supports play/pause/seek/loops/restart and integrates tightly with the LoadBank plugin via orchestrator setpoint piping. There is no interpolation: new setpoints are applied at the specified times (step behavior).

### CSV Schema
- File format: CSV
- Required columns:
  - Time (seconds)
  - Load (kW)
- Semantics:
  - Time is monotonically non-decreasing; Load is the target setpoint (kW) applied at that time (step behavior)
  - No interpolation; the setpoint changes only at the provided time stamps
  - Cycles typically end at 0kW to ramp down load before completion

### State Machine
The cycle plugin maintains an internal state:

| State | Description |
|-------|-------------|
| `idle` | Not started; waiting for play command |
| `running` | Actively advancing through the schedule; setpoints piped to loadbank |
| `paused` | Timer frozen; last setpoint held on loadbank |
| `complete` | All loops finished; last setpoint held; Play restarts from beginning |

### Runtime Controls
All controls are available via the Cycle Control section in the LoadBank operator panel and routed via IPC to the orchestrator:

- **Play**: start from idle, resume from paused, or restart from complete
- **Pause**: freeze timer at current position; loadbank holds last setpoint
- **Seek**: jump to a specific time (only when paused)
- **Loops**: set total loop count at runtime
- **Start with Test**: when enabled, pressing Record checks LoadBank readiness (Take Control active), then starts the cycle and recording simultaneously

### Integration with LoadBank
- Orchestrator pipes `cycle.current_setpoint_kw()` to `lb.command_setpoint_kw()` **only when the value changes** — a 5-step cycle produces exactly 5 Modbus writes, not one per tick
- Master Load is automatically enabled when cycle plays (via orchestrator `_handle_cycle_command`)
- On pause: last setpoint held, Master Load stays on
- On complete/stop: last setpoint held (no auto-zero); operator uses Emergency Stop / Zero Load to drop load manually
- Rate limiting and step decomposition handled by LoadBank per model map

### Execution Model
- At each orchestrator tick:
  - `simulate_step()` computes current position, setpoint, loop number, and progress
  - If state is `running` and setpoint differs from last sent value, `lb.command_setpoint_kw()` is called
- Loop boundary: when elapsed time exceeds `loop_len * loops_total`, state transitions to `complete`
- Multi-loop: elapsed time wraps via modulo for `loops_total > 1`

### Telemetry Channels
Published every tick by `simulate_step()`:

| Channel | Unit | Description |
|---------|------|-------------|
| `Cycle/state` | — | State code: 0=idle, 1=running, 2=paused, 3=complete |
| `Cycle/position_s` | s | Current position within the active loop |
| `Cycle/setpoint_kw` | kW | Current load setpoint from schedule |
| `Cycle/loop_current` | — | Current loop number (1-based) |
| `Cycle/loop_total` | — | Total configured loops |
| `Cycle/progress_pct` | % | Overall progress across all loops |
| `Cycle/schedule_len_s` | s | Duration of one loop |

### Configuration (YAML)
File: `configs/cycle.yaml`

```yaml
source:
  csv_path: configs/cycles/demo.csv
  columns:
    time: Time
    load: Load
execution:
  loops_total: 1
  start_with_test: false
  inter_loop_dwell_s: 0
```

### UI

#### Configure Dialog (right-click Cycle tile -> Configure)
- **CSV Source**: browse/enter CSV path, column name mapping, embedded QtCharts staircase plot preview with multi-loop visualization
- **Execution**: loops total, start with test checkbox, inter-loop dwell

#### Cycle Control Section (in LoadBank operator panel)
- **Start with Test** checkbox
- **State/Position/Setpoint/Loop** labels updated from telemetry
- **Progress bar** showing overall completion percentage
- **CycleChartWidget**: lightweight QPainter step-line chart with filled area, axis labels, and red vertical position marker
- **Play/Pause** buttons
- **Seek** spinner + Go button (active when paused)
- **Loops** spinner

The `CycleChartWidget` (`src/ui/widgets/cycle_chart.py`) is imported with a `try/except` guard — if the file is missing on a workstation, the chart area is simply omitted and the rest of the panel functions normally.

### Error Handling
- CSV parse/validation errors -> status label in config dialog
- LoadBank comms loss: system policy via calculated-channel estop logic
- Missing `cycle_chart.py` on workstation: graceful degradation, panel loads without chart

### Outputs and Metadata
- Telemetry channels recorded at core tick rate (see table above)
- Boundary and loop events logged to console
