<!-- Author: T. Onkst | Date: 03102026 -->

# Engine Test Data Recorder

Stream, visualize, and record engine test data from NI cDAQ, CAN/CCP, and Modbus devices. Stores data in Parquet with YAML metadata and supports Excel export.

## Overview
- Desktop application for Windows 10/11
- Python 3.x with PySide6 UI
- Modular plugin architecture (NI DAQ, CAN, CCP, Calculated Channels, Cycle, LoadBank, Modbus, Statistics, Vaisala, EngineTest, Channel Manager)
- Live visualization at 1/5/10 Hz; recording up to 100 Hz (per run)
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
- CAN supports DBC-based signal selection/configuration via UI (right-click CAN tile → Configure)
- Modbus supports multi-device UI configuration (TCP/IP and RS485 tabs) in `configs/modbus.yaml` under `devices[*]`

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
- Per-channel high/low warning and shutdown with per-limit latching (trigger/unlatch seconds)
- Warning: UI yellow + log
- Shutdown: asserts E‑stop circuit via calculated channel logic + UI red + log

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
The repository contains a working Core/UI implementation with plugin-based telemetry, recording/export pipeline, CCP real-mode polling with access-key unlock/diagnostics, CAN DBC-driven runtime decoding, and snapshot-buffered acquisition across core plugins.


