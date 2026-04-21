<!-- Author: T. Onkst | Date: 03092026 -->

# Engine Test Data Recorder

Stream, visualize, and record engine test data from NI cDAQ, CAN/CCP, and Modbus devices. Stores data in Parquet with YAML metadata and supports Excel export.

## Overview
- Desktop application for Windows 10/11
- Python 3.x with PySide6 UI
- Modular plugin architecture (NI DAQ, CAN, CCP, Calculated Channels, Cycle, LoadBank, Modbus, Statistics, Vaisala, Omega, EngineTest, Channel Manager)
- Live visualization at up to 20 Hz; recording up to 100 Hz (per run)
- Crash-safe, append-only chunked writes with < 1 s worst-case data loss
- Segmentation by time (default 4 h, configurable) or size; suffix “_1, _2, …” only when segmentation occurs
- Excel export with automatic multi-file split “.1, .2, …” when row limit exceeded

## Architecture
- Two-process bundle:
  - Core: acquisition, plugins, writers
  - UI: PySide6-only renderer(s)
  - IPC: ZeroMQ (local-only)
- Plugin lifecycle: configure → validate → arm → start → stop → teardown → status
- Per-plugin YAML configs; per-run config snapshots bundled for reproducibility
- Acquisition model: plugin-side latest-value buffering with core tick sample-and-hold (core reads cached snapshots rather than blocking on plugin I/O)
- Core recording: `src/core/orchestrator.py` coordinates the run loop; recording session setup/teardown and Excel export kickoff live in `src/core/recording.py` (`begin_recording`, `end_recording`, `kickoff_export`, Parquet settings helpers).
- Plugin source layout (selected large plugins are split for maintainability):
  - **NI DAQ:** `src/plugins/ni_daq.py` plus `_nidaq_discovery.py`, `_nidaq_simulation.py`, `_nidaq_tasks.py`, `_nidaq_acquisition.py`
  - **CCP:** `src/plugins/ccp.py` plus `_ccp_a2l.py`, `_ccp_protocol.py`
  - **Channel Manager / Engine Test:** `src/plugins/channel_manager.py` and `src/plugins/engine_test.py` (dedicated plugin modules, not stubs inside the orchestrator)

## Storage and Naming
- Primary storage: Parquet (.parquet) + sidecar YAML (.yaml)
- Base filename: `MMDDYY_HHMMSS_EngineType_TestType`
- Segments: apply `_1, _2, …` only if segmentation occurs
- Excel row split: apply `.1, .2, …` only if row limit exceeded
- Run folder structure (example):
  - `data/*.parquet`
  - `metadata.yaml`
  - `config_snapshot/*.yaml`
  - `logs/*.log`
  - `exports/*.xlsx`

## Configuration
- Canonical format: YAML
- Per-plugin files in `configs/` (e.g., `configs/ni_daq.yaml`, `configs/can.yaml`, …)
- On Start/Stop Test, snapshot active plugin configs to the run folder
- CCP real mode supports access-key unlock without DLLs:
  - `security.access_key` in `configs/ccp.yaml`, or
  - `CCP_ACCESS_KEY` environment variable
- CCP supports placeholder multi-device configuration for dual-ECM benches:
  - `devices[*]` in `configs/ccp.yaml` (up to 2 devices)
  - role dropdown in CCP Configure dialog sets station address:
    - `Primary` -> `0x0`
    - `Secondary` -> `0x1`
  - desk/single-ECM workflow: keep one active CCP device configured
- CAN supports DBC-based signal selection/configuration via UI (right-click CAN tile → Configure)
- Modbus supports multi-device UI configuration (TCP/IP and RS485 tabs) in `configs/modbus.yaml` under `devices[*]`
- Channel Manager supports right-click configuration for:
  - core sample rate (tick/log cadence),
  - segmentation limits (time + size),
  - two-tier warning/alarm setup with per-limit latch delays and actions.

## CCP Diagnostics
- CCP runtime telemetry channels are available in the All-Channels table:
  - `CCP/connected`, `CCP/state_code`, `CCP/connect_attempts`, `CCP/connect_ok`
  - `CCP/unlock_ok`, `CCP/poll_success`, `CCP/poll_fail`
  - `CCP/last_seed_status`, `CCP/last_rc`, `CCP/ctr_mismatch`
- `CCP/state_code` quick reference:
  - `0` stopped, `1` configured, `2` starting
  - `10` connecting, `20` connected, `30` seed received
  - `40` unlocked, `41` unlock skipped, `50` session status set
  - `60` ready for polling, `61` no measurements configured
  - `70` polling
  - `90` connect/unlock error, `91` session error, `92` polling error

## Runtime Diagnostics
- Core tick observability channels:
  - `Core/tick_dt_s`, `Core/tick_jitter_s`, `Core/tick_overrun`
- CAN runtime observability channels:
  - `CAN/frames_rx`, `CAN/decode_hits`, `CAN/last_decode_age_s`

## Alarms
- Tiered per-channel warning/alarm model with per-limit debounce/latch timing
- Tier 1 (warning): UI yellow + log
- Tier 2 (alarm): UI red + log, optional `Visible Alert + Shutdown` action
- Per-channel **Shutdown Type** (Hard/Soft) determines which internal shutdown boolean is raised
- Enabling conditions: Always Enabled, Engine Running, Engine Run time, Test Time
- Internal boolean channels published each tick:
  - `iOT_Warning` — any warning active
  - `iOT_Alarm` — any shutdown active
  - `iOT_AlmSftSdn` — soft shutdown active (mapped to DO relay for graceful shutdown)
  - `iOT_AlmEmgSdn` — hard/emergency shutdown active (mapped to DO relay for immediate estop)
  - `iDG_EngRunStp` — engine running (RPM > threshold, mapped to DO relay)
- Plugin health indicators: console tiles show Green/Red/Disconnected based on `*/health_ok` or `*/conn_ok` telemetry channels

## Prerequisites
- NI-DAQmx and NI-XNET drivers (Windows)
- Python libs: PySide6, numpy, scipy, pandas, pyarrow, pyzmq, pymodbus, python-can, cantools, openpyxl/xlsxwriter

## Security & Confidentiality
- Local-only operation; external connections disabled by default
- Assume all data is sensitive; keep development in private/local environments

## Versioning & Changelog
- Semantic Versioning (MAJOR.MINOR.PATCH; pre-release allowed)
- Initial version: 0.1.0-alpha.1 (see CHANGELOG.md)

## License / Compliance
- Internal PSI use; comply with driver and dependency licenses

## Status
The repository contains a working Core/UI implementation with plugin-based telemetry, recording/export pipeline (orchestrator plus `recording.py`), CCP real-mode polling with access-key unlock/diagnostics (including placeholder dual-ECM config model), CAN multi-bus DBC-driven runtime decoding, and snapshot-buffered acquisition across core plugins. NI DAQ and CCP are split into focused helper modules under `src/plugins/`; Channel Manager and Engine Test ship as dedicated plugin files rather than orchestrator stubs.

All plugins now have right-click Configure dialogs wired: NI_DAQ, CCP, CAN, Modbus, LoadBank, Calculated_Channels, Channel_Manager, Statistics, Vaisala, Omega, Cycle. The Cycle config includes an embedded QtCharts staircase plot preview. A dockable LoadBank operator control panel provides runtime setpoint, fan, E-Stop, and live readback. UI refresh is tightened to 50 ms (20 Hz) with 20 ms ZMQ polling for near-real-time responsiveness.

Modbus plugin has full TCP transport (real-mode read path). Vaisala and Omega plugins operate via Modbus TCP with auto-reconnect and configurable word order for float decoding. All hardware plugins publish connection health channels (`*/conn_ok`) displayed as Green/Red/Disconnected on console tiles. NI DAQ supports chassis-grouped task creation, DO condition-based output control with a popup editor, and hardware migration for chassis/card swaps. The alarm system supports per-channel shutdown type (hard/soft) with internal boolean channels (`iOT_AlmSftSdn`, `iOT_AlmEmgSdn`, `iDG_EngRunStp`) driving mapped DO relays. Global offline mode enforced by orchestrator overrides all plugin modes. Pytest unit tests cover the alarm engine (including shutdown types), BCD encoding, calculated expressions, and CCP protocol.

CCP A2L parser fully rewritten to extract COMPU_METHOD COEFFS (RAT_FUNC conversion coefficients) and apply proper raw-to-physical conversion for all data types (UWORD, SWORD, ULONG, SLONG, FLOAT32_IEEE, FLOAT64_IEEE, UBYTE, SBYTE). A pymodbus version compatibility shim (`_modbus_compat.py`) handles API parameter changes across pymodbus 3.0–3.10+ for all Modbus-using plugins. Onsite testing validated CCP, NI DAQ, Modbus (ComApp), and Vaisala (HMT330) data accuracy against reference tools.


