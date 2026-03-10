<!-- Author: T. Onkst | Date: 03092026 -->

# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased] - 03/10/2026
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
- Added CAN runtime dependencies to project requirements: `python-can`, `cantools`.

### Changed
- Updated CCP default alias prefix to reduce cross-plugin alias collisions (e.g., `CCP_` namespace).
- Improved CCP polling stability by using bounded per-tick polling and shorter IO timeouts to avoid UI connection flicker.
- Improved core recording cadence by removing repeated A2L parsing from CCP unit lookup on the tick path.
- Decoupled plugin acquisition from core tick for latest-value snapshot behavior in `NI_DAQ`, `CAN`, `Statistics`, `Calculated_Channels`, and `Modbus`.
- Updated CCP runtime to background polling with cached snapshot reads on tick path and freshness/staleness telemetry.
- Updated CAN runtime to support real-mode DBC decode with event-driven frame drain and J1939 PGN-aware fallback matching.
- Updated Modbus runtime config resolution to use `devices[*].reads[*]` first, with fallback to legacy top-level `reads`.

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


