<!-- Author: T. Onkst | Date: 08112025 -->

## Test Plan

### Acceptance Criteria
- Feature parity with prior system or better
- All FR/NFR mapped to tests (see RTM)
- 4 h sustained run with segmentation; forced-crash loss bounded by configured SQLite commit interval
- Excel export correctness including row-limit split

### Test Areas
1. Configuration
   - Per-plugin YAML load/save; schema validation
   - NI DAQ discovery lists all available channels; checkbox selection persists
   - Alias requirements: uniqueness, charset, length; alias used in UI and output columns
   - Snapshot on Start/Stop Test
2. Acquisition & Timing
   - R selection (≤ 100 Hz)
   - Fast channels 10×R oversample and decimation
   - Alignment of TC/RTD/CAN/CCP/Modbus to R grid
   - CAN alignment uses last-value-hold per R interval
   - CCP uses poll at R with last-value-hold alignment
   - NI DAQ Watchdog: DO toggles at configured rate; DI return verified within timeout; fault on N consecutive misses
3. Segmentation & Naming
   - Time-based (default 4 h) and size-based segmentation
   - Default size-based limit = 100 MB; override respected
   - `_1, _2, …` only when segmented; examples validated
4. Crash Safety
   - SQLite WAL recovery after simulated crash; data loss bounded by commit interval
5. Excel Export
   - Metadata + Data sheets; time columns (relative, absolute)
   - Row-limit split with `.1, .2, …` naming
6. UI & IPC
   - Two windows; update rates 1/5/10 Hz
   - Plugin context menu: Configure, Show Error, Reset Error
   - CAN UI: database import, tree selection, alias assignment, bus bitrate configuration
   - Modbus UI: servers, reads (FC/type/endianness/scaling/poll_hz), writes (limits/readback)
   - LoadBank UI: model dropdown from maps, IP config, Test Connection, auto-connect/keep-alive
   - Cycle UI: CSV selection, loop/dwell/restart/skip options, Accept gating controls/status
   - Displays: All‑Channels Table (alarm colors, performance), Plots (time window, decimation), Dials/Gauges (threshold ticks)
   - Alarm drawer and global banner behavior; AlarmEvents table matches export
   - CCP UI: A2L import, seed/key DLL config + dry-run, variable selection with aliases, optional write dialog
7. Alarms
   - Latching behavior and UI colors (yellow/red)
   - Shutdown action via calculated channel logic
8. Disk Guardrail
   - Warn-only at < 5 GB; does not block start
9. Statistics Plugin
   - Emits mean/stdev/min/max at rate R; rolling/fixed window config; optional trigger gating; `_Statistics` file rules
10. Calculated Channels
   - Expression validation (allowed ops/functions), dependency ordering, rolling helpers, boolean latching, NaN guards
11. Channel Manager
   - Set R ≤ 100 Hz; per-channel alarms with latching; bulk edit templates; summary outputs
12. Modbus
   - Server connect/retry, poll at configured rates, scaling correctness, write limit/readback
13. LoadBank
   - Model loading, connection test, auto-connect, setpoint/accept behavior, status polling, error UI
14. Cycle
   - CSV parse/validate (Time s, Load kW), step behavior (no interpolation), loops/pause/stop/restart/skip, preview plot


