<!-- Author: T. Onkst | Date: 03092026 -->

# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased] - 03/10/2026

### Config UIs, CCP tuning, UI responsiveness — 03/09/2026
#### Added
- Cycle config dialog: embedded QtCharts staircase plot preview (step/hold matching load bank behavior), BOM-safe CSV parsing, status bar with point count/loops/duration.
- Statistics config dialog: right-click configure for window settings, metrics, trigger configuration.
- Vaisala config dialog: right-click configure for IP, model, polling rate, calibration offsets.
- LoadBank operator control UI panel (dockable widget): Take Control, Fan Power, Load Setpoint, Emergency Stop, live readback metering.
- EngineTest promoted to dedicated plugin (`src/plugins/engine_test.py`) with Lock/Start/Stop lifecycle and metadata validation.
- Pytest framework with unit tests for alarm engine, BCD encoding, calculated expressions, CCP protocol.

#### Changed
- CCP: removed 15 ms hard cap on SHORT_UP poll timeout; now honors configured `io_timeout_s` (default 50 ms). Significantly reduces poll fail rate under real ECU load.
- UI: tightened ZMQ telemetry poll interval from 100 ms to 20 ms (50 Hz) and display refresh from 250 ms to 50 ms (20 Hz) for near-real-time responsiveness.
- Cycle config preview uses `utf-8-sig` encoding to handle BOM in CSV files.

### Structural refactor — 03/09/2026
#### Changed
- Split `src/plugins/ni_daq.py` into the main NI DAQ plugin module plus helpers: `_nidaq_discovery.py` (MAX/template generation), `_nidaq_simulation.py` (sim timestep), `_nidaq_tasks.py` (DAQmx tasks/buffers), and `_nidaq_acquisition.py` (worker and snapshot buffer).
- Split `src/plugins/ccp.py` into the main CCP plugin module plus `_ccp_a2l.py` (A2L parse/decode) and `_ccp_protocol.py` (CCP session and polling).
- Slimmed `src/core/orchestrator.py` by moving recording session and export kickoff logic into `src/core/recording.py` (`begin_recording`, `end_recording`, `kickoff_export`, `build_parquet_settings`, related helpers).
- Promoted Channel Manager and Engine Test from orchestrator-internal stubs to first-class plugins: `src/plugins/channel_manager.py` and `src/plugins/engine_test.py`.

### Added
- Integrated CCP real-mode plugin path with NI-XNET session, connect/seed/unlock, and SHORT_UP polling using A2L-defined measurements.
- Added access-key unlock algorithm support in CCP (no vendor DLL required), with fallback to `CCP_ACCESS_KEY` environment variable.
- Added CCP configuration dialog in UI and wired CCP tile context menu configure/reload workflow.
- Added CCP diagnostics telemetry channels (`CCP/connected`, state/counters/error indicators) and runtime logs for connect/unlock/poll stages.
- Added core tick diagnostics channels: `Core/tick_dt_s`, `Core/tick_jitter_s`, `Core/tick_overrun`.
- Added CAN diagnostics channels for runtime tuning: `CAN/frames_rx`, `CAN/decode_hits`, `CAN/last_decode_age_s`.
- Added CAN configuration dialog with right-click tile integration, DBC import, signal selection/filtering, and YAML persistence.
- Added Modbus configuration dialog with right-click tile integration, channel table editing, live Value test view, and YAML persistence.
- Added multi-device Modbus configuration UI with per-device tabs and independent TCP/RS485 settings.
- Added CCP multi-device configuration support with tabbed UI (`Add Device`/`Remove Device`) for up to two ECMs.
- Added CCP device role selection (`Primary`/`Secondary`) with station-address mapping (`0x0`/`0x1`) persisted in `configs/ccp.yaml` under `devices[*]`.
- Added CAN runtime dependencies to project requirements: `python-can`, `cantools`.
- Added Channel Manager configuration dialog with right-click tile integration, YAML import/export, active-channel alarm table, and save/reload workflow.
- Added Channel Manager enabling condition controls (`Always Enabled`, `Engine Running`, `Engine Run time`, `Test Time`) with engine-speed alias selection and thresholding.
- Added aggregate alarm boolean telemetry channels: `iOT_Warning` and `iOT_Alarm`.
- Added alarm-state row coloring in All Channels Table (`WARN` yellow, tier-2 alarm red) using reusable table color helper.

### Changed
- Updated CCP default alias prefix to reduce cross-plugin alias collisions (e.g., `CCP_` namespace).
- Improved CCP polling stability by using bounded per-tick polling and shorter IO timeouts to avoid UI connection flicker.
- Improved core recording cadence by removing repeated A2L parsing from CCP unit lookup on the tick path.
- Decoupled plugin acquisition from core tick for latest-value snapshot behavior in `NI_DAQ`, `CAN`, `Statistics`, `Calculated_Channels`, and `Modbus`.
- Updated CCP runtime to background polling with cached snapshot reads on tick path and freshness/staleness telemetry.
- Updated CCP runtime to resolve/read from `devices[*]` first (multi-device), with backward compatibility to legacy single-device top-level keys.
- Updated CAN runtime to support real-mode DBC decode with event-driven frame drain and J1939 PGN-aware fallback matching.
- Updated Modbus runtime config resolution to use `devices[*].reads[*]` first, with fallback to legacy top-level `reads`.
- Updated Channel Manager alarm model to support two-tier warning/alarm semantics with per-limit debounce and per-tier actions (`Visible Alert` / `Visible Alert + Shutdown`).
- Updated Channel Manager second-tier naming from `shutdown` to `alarm` in UI/config output while retaining runtime backward compatibility for legacy `shutdown` keys.
- Updated core tick cadence authority so `channel_manager.yaml` `recording_rate_hz` controls core tick/logging cadence.
- Updated Channel Manager reload handling to reapply runtime alarm/tick settings without full app restart.

### Notes
- Deferred CCP optimization backlog (tracked in `docs/plugins/ccp.md`):
  - further reduce `CCP/poll_fail` rate in real mode,
  - reduce stale/freshness warnings while keeping current channel responsiveness,
  - tune adaptive timeout/backoff and add rolling CCP health metrics.

## [0.1.0-alpha.1] - 08/11/2025
### Added
- Documentation scaffold: README, specs, flows, interfaces, test plan, RTM, AI context
- Established architecture decisions and naming/segmentation/export policies
- Defined plugin set and lifecycle, configuration approach, and run folder structure


