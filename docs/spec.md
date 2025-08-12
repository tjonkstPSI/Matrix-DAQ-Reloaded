<!-- Author: T. Onkst | Date: 08112025 -->

## Functional Specification

This document summarizes functional and non-functional requirements for the Engine Test Data Recorder.

### Scope
- Record and visualize engine test data from NI cDAQ, CAN/CCP, and Modbus devices
- Store data in Parquet with YAML metadata; support Excel export
- Plugin-based configuration; per-run config snapshots for reproducibility
// Channel discovery & aliasing
- Discover all cDAQ channels; operator selects via checkboxes
- Operator assigns unique alias per enabled channel; alias used for UI and output column names; hardware path retained in metadata

### Key Requirements
- Up to 200 channels; recording rate R ≤ 100 Hz per run
- Fast channels (analog voltage + digital) oversampled 10×R and decimated to R
- Plotting at 1/5/10 Hz; decoupled from recording
- Segmentation by time (default 4 h, configurable) or size; suffix “_1, _2, …” only when segmented
- Default size-based segment limit: 100 MB (user-configurable)
- Excel export with multi-file split “.1, .2, …” if row limit exceeded; includes relative and absolute time columns
- Crash-safe writes with < 1 s worst-case data loss; recovery on restart
- NI cDAQ watchdog: driver-mode on supported network cDAQ (arm/refresh device watchdog), with digital-loopback fallback; mark plugin red on expiration/misses
- Start blocked until all selected plugins are green
- Disk free-space warning at < 5 GB (warn only)

### Plugins
- NI DAQ, CAN, CCP, Calculated Channels, Cycle, LoadBank, Modbus, Statistics, Vaisala, EngineTest, Channel Manager
- CAN: import DBC/XML; multi-bus support; checkbox signal selection and aliasing; R-grid alignment with last-value-hold
- CCP: A2L import, seed/key DLL unlock, checkbox measurement selection using A2L names and optional global naming prefix; optional bounded writes; R-grid alignment
- Modbus: per-server register/channel definitions, polling at ≤ R, scaling, aliases, safeguarded writes, R-grid alignment
- LoadBank: specialized Modbus with model dropdown, IP config, connection test, auto-connect/keep-alive, setpoint/accept and status
- Cycle: CSV (Time s, Load kW) step runner (no interpolation), loops, pause/stop/restart/skip; no Accept required; preview plot; future safety backoff
- Statistics: rolling/fixed windows at rate R, selectable metrics, optional trigger gating; `_Statistics` files mirror primary segmentation/export rules
- Calculated Channels: restricted Python expressions at R, rolling helpers, boolean latching, dependency ordering, NaN guards
- Channel Manager: select recording rate R and configure per-channel alarms (warning/shutdown) with latching and bulk edits; optional summary outputs
- Vaisala: specialized Modbus with model maps, IP config, connection test, polling at ≤ R with calibration offsets
- UI: separate process with Console (plugin tiles) and two Display windows (Table/Plots/Gauges), alarm UX, controls panel; IPC topics defined
- Lifecycle: configure → validate → arm → start → stop → teardown → status
- Per-plugin YAML configs; INI import/export optional (compat)

### Alarms
- Units reflect post-scaling
- High/low warning and shutdown; per-limit latching (trigger/unlatch seconds)
- Warning: UI yellow + log; Shutdown: assert E‑stop via calculated logic + UI red + log

### UI
- Two-process bundle (Core + UI via ZeroMQ IPC)
- Displays: All-Channels Table, Plots, Dials/Gauges; two windows supported
- Operator controls: Load bank (fan policy always on, setpoint, accept), select analog-out channels
- Plugin tiles: right-click for Configure, Show Error, Reset Error

### Config & Run Artifacts
- Canonical YAML per plugin under `configs/`
- On Start/Stop Test: snapshot active plugin configs under `config_snapshot/` in the run folder
- Run folder structure name: `MMDDYY_HHMMSS_EngineType_TestType`

### Open Questions (TBD)
- cDAQ channel inventory, ranges, accuracies, triggers
- CCP unlock sequence details, retries, timeouts
- Modbus register maps, rates, safeguards
- Final UI templates and layouts
- Default segmentation size limit (MB)


