<!-- Author: T. Onkst | Date: 08112025 -->

## Interfaces

### Hardware & Drivers
- NI cDAQ (NI-9214, NI-9239, NI-9375, NI-9862)
- NI-DAQmx, NI-XNET
- Modbus TCP (Vaisala, Load Bank)

### Data I/O
- Recording grid at rate R (≤ 100 Hz); fast channels oversampled 10×R then decimated
- Parquet + YAML metadata; segmentation by time/size
- Excel export: Metadata, Data sheets; row-limit split with `.1, .2, …`

### IPC (Core ↔ UI)
- Transport: ZeroMQ (local-only)
- Telemetry: PUB/SUB topics (e.g., telemetry/*)
- Control: REQ/REP (configure, commands)
- Payloads: JSON or MessagePack

### Configuration Files (YAML)
- Per-plugin under `configs/` (e.g., `configs/ni_daq.yaml`, `configs/can.yaml`, …)
- Snapshot copies under `config_snapshot/` within each run folder

### Excel Columns (Data sheet)
- Time_Relative_s, Time_Absolute_iso8601
- One column per recorded channel (post-scaling units)


