<!-- Author: T. Onkst | Date: 08122025 -->

## Engine Test Data Recorder — Roadmap (3–6 months)

### Vision
Deliver a Windows desktop app that streams, visualizes, and records engine test data from NI cDAQ, CAN/CCP, Modbus/LoadBank/Vaisala, with crash‑safe storage (Parquet+YAML) and Excel export. Plugin architecture, two‑process Core/UI, simulation harness, and strong reproducibility.

### High‑level milestones (target windows)
- Month 0–1
  - Core/UI skeleton complete (DONE)
  - IPC PUB/SUB, demo telemetry to UI (DONE)
  - Sim sources: Modbus, CAN, CCP (DONE)
  - LoadBank plugin (sim) and model map loader; units resolved from map (DONE)
  - NI DAQ enumeration via NI MAX sim; inventory printed (DONE)
  - Docs pack (specs, plugins, UI spec, AI context) (DONE)
  - Basic validation + alias checks; Modbus schema (DONE)

- Month 1–2
  - NI DAQ: fast AI path with 10×R acquisition and 4th‑order IIR Butterworth decimation
  - Channel Manager: evaluate per‑channel alarms; AlarmSummary booleans; AlarmEvents log; row coloring in UI table
  - Excel exporter: Metadata/Data, AlarmEvents, and per‑stat tabs; workbook split policy confirmed (DONE: post‑run tool — per‑Parquet workbooks; autosize and 2‑decimal display)
  - Continuous core run mode + graceful shutdown; basic run lifecycle logs (DONE)

- Month 2–3
  - LoadBank (real): map‑driven reads/writes, confirm readback; control from UI panel
  - Cycle: step schedule (kW), pause/stop/restart/skip; preview plot; drive LoadBank setpoint (SIM WIRED)
  - CAN (real): XNET integration (v23.3), signal selection/aliasing import path
  - CCP (read‑only real): A2L prefix naming; seed/key DLL unlock flow stubbed (no writes)

- Month 3–4
  - Statistics: rolling/fixed windows; selectable metrics; manual Log Statistics; per‑stat Excel tabs
  - Calculated Channels: restricted Python eval; rolling helpers; boolean latching; dependency ordering
  - UI: dual windows; plots with up to 3 Y axes; alarm drawer; controls panel polish
    - Launch configuration dialog (plugin selection, data root, test cell, data mode, import configs)
    - Data display selection (up to 2), opened as separate windows
    - Console layout compact: vertical plugin list; tiles ordered `Channel_Manager`, `EngineTest`, then others alphabetically
    - Tile color policy: green=healthy/valid, red=error/invalid, grey=disconnected
    - Primary stateful control: Lock Test → Start Test → Stop Test; Close Plugins action resets to launcher
  - Watchdog: driver mode on 9188/9189; loopback fallback; fault behavior (stop/save)

- Month 4–5
  - Validation: JSON Schema coverage for key plugins (post‑implementation)
  - Robust error handling and recovery (device reconnects, segment rollover)
  - Packaging: PyInstaller EXE, prerequisites/driver checks, first install on a target bench
  - Performance tuning at R=100 Hz and ~200 channels (profiling, GC, buffers)

- Month 5–6
  - Full acceptance test pass vs legacy system; parity or better
  - Documentation, operator guide; config migration guide; backup/restore of runs
  - Hardening (logs/rotation, limits, security posture)
  - Release 1.0.0 (beta → GA) with changelog

### Completed (as of today)
- Architecture and docs pack with AI source of truth (`docs/ai_context.yaml`)
- Project skeleton (Core/UI), IPC wiring, demo telemetry to live UI table with units
- Simulated plugins: Modbus, CAN, CCP; LoadBank (model map + kW units); basic orchestrator run
- NI DAQ enumeration via NI‑DAQmx and NI MAX sim
- Alias uniqueness checks (per‑plugin + global), console validation logs
- Modbus JSON Schema validation (defense in depth)
- Core run mode toggle (demo/continuous) and graceful Ctrl+C stop
- Cycle plugin: CSV (Time, kW) schedule → drives LoadBank setpoint; end‑of‑cycle stops issuing commands; edge‑aware final step
- UI status (Connected/Disconnected) and Core‑sourced `Time_Relative_s` channel displayed as a row
- NI DAQ: simulation path emits configured channels (AI voltage with 10× oversample/average to R, AI temp at R, DI/DO states); discovery helper generates structured YAML template with module family categorization
- Channel Manager: per‑channel alarms with explicit debounce (enter_delay_s, clear_delay_s); UI row coloring (yellow/red); AlarmEvents persisted to per‑run JSONL with local timestamps (ts_hms)
- Statistics: snapshot‑based (manual button + optional rising/falling edge trigger), configurable window (seconds or samples), backward/forward capture, metrics selection including p2p; snapshots persisted to per‑run JSONL; UI control wiring in place
- IPC control path for UI → Core (manual statistics snapshot)
- Vaisala (simulation): Ambient Temp/RH/Pressure channels with configurable IP/model placeholders and calibration offsets; wired into telemetry
- Storage: Parquet writer with 1 s chunked append during run; time/size segmentation; coalesce on finalize to single file per segment; units metadata embedded; config snapshotting to `config_snapshot/`
- Tools: `inspect_parquet` (validate Parquet outputs) and `export_excel` (Metadata, Data, AlarmEvents, StatsSnapshots; split policy)
- UI: Start/Stop Recording toggle; Export Workbook control disabled while recording; telemetry flag `recording` drives UI state
- Plugin enable/disable: all except `Channel_Manager` and `EngineTest`; Modbus optional; Calculated Channels implemented with simple symbol mapping

### In progress / next up
- NI DAQ real path hardening (fast AI, DI on-demand, errors/retries, teardown) — ongoing
- Excel export per-stat tabs (from StatsSnapshots), formatting polish
- Optional disk space guardrail; config-driven export options
- Channel Manager: optional AlarmSummary channels (deferred); global banner and alarm drawer (deferred)
- NI DAQ: real read path hardening (fast AI 10×R average to R; AI temperature at R; DI on‑demand at R; explicit task teardown; backlog‑aware adaptive drain; larger input buffers; per‑task isolation); UI channel picker (later)
- LoadBank real control path (model map → reads/writes)
- Plugin enable/disable: add `enabled: true|false` at root of each plugin YAML; orchestrator skips disabled plugins (config/validate/start/run/aliases)

#### Continuous improvements (queued)
- Core: Non-blocking Start Recording to avoid brief telemetry stall/"Disconnected" flicker in UI
  - Keep main tick publishing while run folder, sinks, and Parquet writer initialize (background thread)
  - Optionally publish this tick first, then process control messages
  - UI: small grace window (e.g., 2 s) during transition to suppress transient grey tiles

### To‑do (detailed)
- NI DAQ
  - Implement task creation for AI/DI/DO/AO (start with AI fast path) (DONE: per-device fast AI tasks)
  - Shared timebase/start trigger; oversample 10×R; 4th‑order IIR Butterworth; decimate to R
  - Real read path robustness (DONE): per-device task isolation, adaptive timeouts with warm-up suppression, larger device buffers; backlog-aware adaptive drain to prevent -200279; health telemetry channels optional
  - Future (optional): per-device fast‑AI acquisition thread reading DAQmx continuously into a queue; Core tick consumes/decimates latest n samples
  - Crash‑safe buffer; chunked write; watchdog (driver mode on 9188/9189; loopback fallback)
- CAN/CCP
  - XNET integration (v23.3): database import, multi‑bus, signal selection; timestamps align to R
  - CCP read‑only: A2L prefix naming; seed/key DLL unlock stub; poll at R
- Modbus/LoadBank/Vaisala
  - Real Modbus clients; read/write with retry/backoff; register/coil maps
  - LoadBank real control; confirm readback; model map setpoint/accept UI
  - Vaisala model maps; calibration offsets; alignment to R
- Channel Manager & Alarms
  - Per‑channel limits + latching evaluation; AlarmSummary booleans; AlarmEvents
  - UI row coloring; global banner; alarm drawer with events
- Storage/Export
  - Parquet writer (append‑only chunks, segmentation time/size, rollover naming)
  - Excel exporter (Metadata, Data, AlarmEvents, per‑stat tabs); split policy
- Calculated & Statistics
  - Expression engine (restricted eval); rolling helpers; latching; dependency ordering
  - Stats rolling/fixed windows; per‑stat selection; manual Log Statistics
- UI polish
  - Plots: 3 Y axes; cursor readouts; PNG snapshot
  - Controls: LoadBank panel; AO entries with validation
- Packaging & Ops
  - PyInstaller build; driver checks; versioning; changelog; operator guide
  - Test harness CI with sims; performance profiling runs

### Risks and mitigations
- NI driver bindings (DAQmx/XNET) variance across machines
  - Early smoke tests on target benches; graceful fallbacks; simulation modes
- CCP specifics (seed/key DLL nuances)
  - Read‑only in v1; unlock only when needed for reads; detailed logs
- Performance at high channel counts
  - Vectorized processing; bounded queues; profiled decimators; optional feature toggles

### Acceptance checkpoints
- Parity with legacy for data sources and export
- 4‑hour run stability; crash‑safe; worst‑case loss < 1 s
- Excel export correctness; layout (Metadata, Data, per‑stat, AlarmEvents)
- UI responsiveness ≤ 250 ms; dual displays stable

### Target releases
- 0.2.0 (Month 2): NI DAQ fast path; Channel Manager alarms; Excel v1; continuous run
- 0.5.0 (Month 4): Real LoadBank/Cycle; CAN/CCP real reads; UI plots/dials; watchdog
- 0.9.0 (Month 5): Validation schemas, export polish, packaging, perf tuning
- 1.0.0 (Month 6): Acceptance complete, docs, operator guide, release


