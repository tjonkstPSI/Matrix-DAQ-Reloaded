<!-- Author: T. Onkst | Date: 08122025 -->

## UI Specification

### Overview
Desktop UI (PySide6) runs as a separate process from Core. It subscribes to live telemetry at 1/5/10 Hz and provides:
- Console with plugin tiles (status, context menu)
- Two concurrent display windows (dual‑monitor): All‑Channels Table, Plots, Dials/Gauges
- Control panel for LoadBank and selected AO channels
- Alarm visuals including per‑row coloring and a global banner with quick access to AlarmEvents

### Windows and Navigation
- Launcher opens Console window and one Display window; user can open a second Display window
- Display window content is selected from templates: All‑Channels Table, Plots, Dials/Gauges
- Layouts can be saved/loaded (TBD format); last layout restored per user

### Console (Plugins View)
- Grid/list of selected plugin tiles with health color:
  - Green = OK, Red = Error; Gray = Disabled/Not selected
- Right‑click menu on a tile:
  - Configure
  - Show Error (modal with timestamped last error and details)
  - Reset Error (non‑blocking retry)
- Indicators: last update age, simple throughput/counters per plugin (optional)

### Displays

#### All‑Channels Table
- Rows = enabled channels (physical + calculated + statistics + special like AlarmSummary)
- Grouping and filters: by Category (Pressure, Temperature, Analog, Digital, ECM, Facility, Other), by Source (DAQ/CAN/CCP/Modbus/Vaisala/LoadBank/Calc/Stats)
- Columns:
  - Alias (sortable), Value (latest), Units, Category, Source, Alarm State, Sample Age, Recording Enabled
  - Optional: Min/Max (session), Mean (session), Notes (from config)
- Alarm coloring: row background/yield per current state
  - OK: default; Warning: yellow; Shutdown: red
- Update rate: selectable 1/5/10 Hz

#### Plots
- Multi‑trace time plots; user selects channels (compatible units recommended but not enforced)
- Axes: up to 3 Y axes supported (Y1 left, Y2 right, Y3 overlay). User can assign each channel to a specific Y axis. Each axis has independent auto/manual range and unit label. Legend indicates axis mapping; optional color band per axis label.
- Time window presets: 10 s, 60 s, 5 min, custom
- Decimation: UI receives downsampled frames from Core at UI rate; no gaps fill
- Interactions: zoom/pan, show value under cursor, export snapshot (PNG)

#### Dials/Gauges
- Radial or numeric gauges for selected channels
- Show units and thresholds if configured (Warning/Shutdown ticks)
- Update at 5–10 Hz (configurable); rate‑limited to avoid CPU spikes

### Controls Panel
- LoadBank: setpoint (kW) entry and "Accept" button when applicable; current measured load and ready/fault indicators
- Analog Out: user‑selectable AO channels (0–10 V) with validation

### Alarm UX
- Global banner (top/bottom): shows summary (count warning/shutdown) and blinks when a new shutdown occurs
- Clicking banner opens an Alarm drawer with:
  - Current active alarms (channel, limit, value, since)
  - Recent events table (same data saved for Excel `AlarmEvents`)
- Per‑row alarm colors in All‑Channels Table, with latching tooltips (trigger/unlatch timers)

### Performance & Rendering Targets
- UI update frame budgets:
  - Table (1–2k rows): ≤ 50 ms per refresh @ 5 Hz on target workstation
  - Plots (≤ 8 traces): ≤ 30 ms per refresh @ 10 Hz with decimation
- Memory: rolling buffers limited to selected time window; avoid storing entire run in UI process

### Accessibility & Theming
- Font scaling presets (90/100/110/125%)
- Light/Dark themes; alarm colors remain distinct in both

### Screenshots & Assets
- Reference screenshots (added):
  - Console: `docs/assets/ui/console.png`
  - All‑Channels Table: `docs/assets/ui/all_channels_table.png`
  - Plots/Gauges: `docs/assets/ui/plots_gauges.png`
- If available later, add: `alarm_drawer.png` and link here

### IPC Topics (Core ↔ UI)
- Telemetry: `telemetry/frame` (downsampled at UI rate); includes channel alias → {value, units, age_ms, state}
- Alarms: `alarms/active`, `alarms/events`
- Controls: `control/loadbank`, `control/ao`
- Plugin health: `status/plugins`

### Open Items (TBD)
- Final column set for All‑Channels Table
- Saved layout format and persistence location
- Plot trace limits (max channels per plot)

