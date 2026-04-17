<!-- Author: T. Onkst | Date: 03092026 -->

## Engine Test Data Recorder — Roadmap

### Vision
Deliver a Windows desktop app that streams, visualizes, and records engine test data from NI cDAQ, CAN/CCP, Modbus/LoadBank/Vaisala, with crash-safe storage (Parquet+YAML) and Excel export. Plugin architecture, two-process Core/UI, simulation harness, and strong reproducibility.

---

### Completed

#### Architecture and Infrastructure
- Core/UI two-process skeleton with ZeroMQ IPC (PUB/SUB telemetry, REQ/REP control)
- Plugin registry, lifecycle (configure/validate/arm/start/stop/teardown), per-plugin YAML configs
- Alias uniqueness checks (per-plugin + global), console validation logs
- Core run mode toggle (demo/continuous) and graceful Ctrl+C stop
- Core tick diagnostics channels: `Core/tick_dt_s`, `Core/tick_jitter_s`, `Core/tick_overrun`
- Decoupled plugin acquisition from core tick: latest-value snapshot / sample-and-hold model for NI_DAQ, CAN, CCP, Statistics, Calculated_Channels, Modbus
- Data staleness monitoring with configurable warning/stale thresholds per plugin
- Docs pack: specs, flows, interfaces, test plan, RTM, AI context (`docs/ai_context.yaml`)
- Refactored `orchestrator.py` — recording session logic extracted to `src/core/recording.py`
- Pytest framework with unit tests: alarm engine, BCD encoding, calculated expressions, CCP protocol

#### NI DAQ Plugin
- Simulation path: configured channels (AI voltage with 10x oversample/average to R, AI temp at R, DI/DO states)
- Discovery helper: NI MAX enumeration, generates structured YAML template with module family categorization
- Real-mode: per-device fast AI tasks, per-device task isolation, adaptive timeouts with warm-up suppression, larger device buffers, backlog-aware adaptive drain
- Background worker thread with snapshot buffer
- Configuration UI (right-click tile -> Configure)
- Modular codebase: `ni_daq.py` + `_nidaq_discovery.py`, `_nidaq_simulation.py`, `_nidaq_tasks.py`, `_nidaq_acquisition.py`

#### CCP Plugin
- Real-mode path: NI-XNET session, connect/seed/unlock, SHORT_UP polling using A2L-defined measurements
- Algorithmic access-key unlock (no vendor DLL required), with `CCP_ACCESS_KEY` env var fallback
- Background polling with cached snapshot reads on tick path, freshness/staleness telemetry
- Multi-device support: tabbed UI for up to two ECMs (Primary `0x0` / Secondary `0x1`)
- Diagnostics telemetry channels (`CCP/connected`, state/counters/error indicators)
- Configuration UI with A2L channel list, filtering, test connection
- Modular codebase: `ccp.py` + `_ccp_a2l.py`, `_ccp_protocol.py`
- Poll timeout now honors configured `io_timeout_s` (removed 15 ms hard cap)

#### CAN Plugin
- Real-mode DBC decode with event-driven frame drain and J1939 PGN-aware fallback matching
- Background worker with snapshot buffer
- Diagnostics channels: `CAN/frames_rx`, `CAN/decode_hits`, `CAN/last_decode_age_s`
- Configuration UI: DBC import, signal selection/filtering, YAML persistence

#### Modbus Plugin
- Multi-device runtime: `devices[*].reads[*]` with fallback to legacy top-level `reads`
- Multi-device configuration UI: per-device tabs, independent TCP/RS485 settings
- Channel table editing with live Value test view

#### LoadBank Plugin
- Real-mode Modbus TCP client with background worker thread
- Model-map driven status polling and command writing
- BCD and coil-array encoding/decoding for setpoint writes
- Heartbeat coil toggling, rate-limited commands
- Model maps: Simplex 1.5MW and Simplex 700kW (partially bench-validated)
- Configuration UI: Primary/Secondary load bank selection, IP, voltage, phase, test connection
- Standalone debug tool (`src/tools/loadbank_read_debug.py`) with profiles and side-by-side LV comparison
- Operator control UI panel (dockable: Take Control, Fan Power, Load Setpoint, E-Stop, live readback)

#### Calculated Channels Plugin
- Restricted Python AST evaluator with symbol mapping
- Configuration UI: channel mapping editor, expression builder, global update rate
- YAML persistence for import/export

#### Channel Manager
- Two-tier warning/alarm model with per-limit debounce/latch timing and per-tier actions
- Enabling conditions: Always Enabled, Engine Running, Engine Run time, Test Time
- Aggregate alarm booleans: `iOT_Warning`, `iOT_Alarm`
- Alarm-state row coloring in All Channels Table (yellow warning, red alarm)
- AlarmEvents persisted to per-run JSONL with local timestamps
- Configuration UI: sample rate, segmentation, alarm table with channel list
- Core tick cadence controlled by `channel_manager.yaml` `recording_rate_hz`
- Dedicated plugin file (`src/plugins/channel_manager.py`, no longer an orchestrator stub)

#### Statistics Plugin
- Snapshot-based capture (manual button + rising/falling edge trigger)
- Configurable window (seconds or samples), backward/forward capture, metrics selection
- Snapshots persisted to per-run JSONL; UI control wiring in place
- Configuration UI: right-click configure dialog (window settings, metrics, trigger)

#### Cycle Plugin
- CSV schedule (Time, kW) -> drives LoadBank setpoint; end-of-cycle stops issuing commands; edge-aware final step
- Configuration UI with QtCharts staircase plot preview (step/hold visualization matching load bank behavior)
- Handles BOM-encoded CSV files; status bar shows point count, loops, cycle duration

#### Vaisala Plugin
- Simulation mode: Ambient Temp/RH/Pressure channels with configurable IP/model and calibration offsets
- Configuration UI: right-click configure dialog (IP, model, polling rate, calibration offsets)

#### EngineTest Plugin
- Dedicated plugin (`src/plugins/engine_test.py`) with Lock/Start/Stop test lifecycle
- Validates required metadata, exposes diagnostic channels

#### Storage and Export
- Parquet writer: 1 s chunked append, time/size segmentation, coalesce on finalize, units metadata, config snapshotting
- Excel export tool: Metadata, Data, AlarmEvents, StatsSnapshots tabs; split policy; autosize and 2-decimal display
- Parquet inspection tool (`src/tools/inspect_parquet.py`)

#### UI
- Console window with plugin tiles, status indicators, telemetry table
- Launch configuration dialog (plugin selection, data root, test cell, data mode, import configs)
- Start/Stop Recording; Lock / Unlock Test workflow; status bar (Connected, Locked/Unlocked, Recording); Parquet merge followed by automatic Excel export into each run's `data/` folder (no manual export button)
- Plugin enable/disable (all except Channel_Manager and EngineTest)
- Lock dialog for EngineTest metadata
- Right-click Configure wired for: NI_DAQ, CCP, CAN, Modbus, LoadBank, Calculated_Channels, Channel_Manager, Statistics, Vaisala, Cycle
- Tightened UI refresh (50 ms / 20 Hz) and ZMQ telemetry poll (20 ms / 50 Hz) for near-real-time responsiveness

---

### In Progress / Next Up

#### LoadBank Completion
- Bench-validate and fix 700kW BCD write encoding (125 kW commanded -> 300 kW observed)
- Bench-validate 1.5MW address base and AB word order vs LabVIEW

#### UI Features
- Dual display windows (separate data displays opened from console)
- Plot displays with up to 3 Y-axes, cursor readouts, PNG snapshot
- Alarm drawer / global banner (deferred)
- Non-blocking Start Recording (background init to avoid telemetry stall/grey flicker)

---

### Queued

#### High-speed NI subset acquisition (future investigation)
LabVIEW-era tooling historically ran a **parallel high-speed DAQ** beside the main recorder for a few channels (for example pressure pulses above ~500 Hz, accelerometers above ~1 kHz). In this Python app the same need is better met as a **dedicated path**, not by forcing the full `NI_DAQ` channel list and the global Channel Manager tick to those rates.

- **Direction**: optional **separate plugin** (or clearly isolated submodule) that configures a **minimal allowlist** of NI physical channels, runs a **dedicated DAQmx task** at a user-selected high sample rate (module- and chassis-dependent), and writes **sidecar storage** under the same run (e.g. `runs/<id>/data/high_speed/` as Parquet chunks or compact binary + schema metadata), while the existing `NI_DAQ` plugin continues to serve the wide slow list at 1–100 Hz core tick.
- **UI / workflow**: same Lock → Record → Stop session; optional **arm window** or record-tied lifecycle; main telemetry table shows **decimated** or **last-value** views for those channels so the UI does not ingest multi-kHz streams.
- **Sync**: shared run start timestamp and documented alignment rules between slow grid and fast streams in post-processing (and optionally in Metadata sheet).
- **When to split out**: if process isolation, jitter guarantees, or NI guidance require it, the fast reader can become a **separate process** while reusing config patterns and run folder layout from this repo.

#### Hardening
- NI DAQ: shared timebase/start trigger, oversample/decimate pipeline, NI DAQ channel picker UI
- Watchdog: driver mode on 9188/9189 chassis; loopback fallback; fault behavior (stop/save)
- Disk space guardrail: config-driven warning/stop on low disk
- Excel export: per-stat tabs from StatsSnapshots, formatting polish
- Robust error handling: device reconnects, segment rollover

#### Testing
- Performance tuning at R=100 Hz and ~200 channels (profiling, GC, buffers)
- Expand pytest coverage: Parquet writer, plugin lifecycle, IPC round-trip

#### Packaging and Release
- PyInstaller EXE build with driver prerequisite checks
- Full acceptance test pass vs legacy system
- Operator guide and config migration documentation
- Release 1.0.0

---

### Risks and Mitigations
- NI driver bindings (DAQmx/XNET) variance across machines
  - Early smoke tests on target benches; graceful fallbacks; simulation modes
- CCP specifics (access-key nuances per ECM model)
  - Read-only in v1; algorithmic unlock with env var fallback; detailed logs
- Performance at high channel counts
  - Vectorized processing; bounded queues; profiled decimators; optional feature toggles
- LoadBank dual-writer conflicts during LV side-by-side testing
  - Read-only debug mode; disable heartbeat writes; isolate write testing

### Acceptance Checkpoints
- Parity with legacy for data sources and export
- 4-hour run stability; crash-safe; worst-case loss < 1 s
- Excel export correctness; layout (Metadata, Data, per-stat, AlarmEvents)
- UI responsiveness <= 100 ms; dual displays stable

### Target Releases
- 0.2.0: Codebase refactoring, config UIs, EngineTest promotion, documentation sync (done)
- 0.3.0: LoadBank bench validation, dual display windows, plots
- 0.9.0: Watchdog, packaging, acceptance testing, performance tuning
- 1.0.0: Acceptance complete, operator guide, GA release
