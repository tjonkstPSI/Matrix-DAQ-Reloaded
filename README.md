<!-- Author: T. Onkst | Date: 08112025 -->

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

## Alarms
- Per-channel high/low warning and shutdown with per-limit latching (trigger/unlatch seconds)
- Warning: UI yellow + log
- Shutdown: asserts E‑stop circuit via calculated channel logic + UI red + log

## Prerequisites
- NI-DAQmx and NI-XNET drivers (Windows)
- Python libs: PySide6, numpy, scipy, pandas, pyarrow, pyzmq, pymodbus, openpyxl/xlsxwriter

## Security & Confidentiality
- Local-only operation; external connections disabled by default
- Assume all data is sensitive; keep development in private/local environments

## Versioning & Changelog
- Semantic Versioning (MAJOR.MINOR.PATCH; pre-release allowed)
- Initial version: 0.1.0-alpha.1 (see CHANGELOG.md)

## License / Compliance
- Internal PSI use; comply with driver and dependency licenses

## Status
This repository currently contains documentation scaffolding and specifications. Implementation to follow.


