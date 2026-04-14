<!-- Author: T. Onkst | Date: 03092026 -->

## CAN Plugin Specification

### Purpose
Decode selected CAN signals from a DBC in real mode, or generate simulated values in sim mode, and expose latest values to the core tick through a snapshot buffer.

### Current Implementation Status
- Implemented now:
  - `python-can` real bus session (`bustype: nixnet` default)
  - DBC parsing with `cantools`
  - Signal selection via CAN Configure dialog (checkbox list + filter)
  - Background snapshot loop (non-blocking core tick reads)
  - Runtime diagnostics channels:
    - `CAN/frames_rx`
    - `CAN/decode_hits`
    - `CAN/last_decode_age_s`
- Implemented decode fallback:
  - Direct arbitration-ID decode first
  - J1939 PGN-based fallback using configured message names/signals

### Runtime Model
- The plugin runs a worker thread that:
  - waits for incoming frames (`recv`),
  - drains queued frames in a short burst,
  - decodes matching configured signals,
  - updates `_snapshot_values`.
- `simulate_step()` returns the cached snapshot immediately.
- Core samples CAN values at recording tick cadence (sample-and-hold behavior).
- Core tick/log cadence is controlled globally by Channel Manager; CAN `recording_rate_hz` is used for plugin-local timing behavior.

### Configuration (Current)
File: `configs/can.yaml`

```yaml
enabled: true
mode: real              # real | sim
recording_rate_hz: 10
session:
  channel: CAN1
  baudrate: 250000
  bustype: nixnet
dbc_path: C:/path/to/file.dbc
signals:
  - alias: cSP_Eng
    message: EEC1_61444
    signal: cSP_Eng
    unit: rpm
    enabled: true
```

Notes:
- Runtime currently uses `session`, `dbc_path`, and `signals`.
- Legacy compatibility keys (`buses`, `databases`) may exist in YAML but are not used by current decode runtime.

### Validation Rules
- `signals` must be a list.
- Enabled signal aliases must be unique within CAN plugin.
- In real mode:
  - `dbc_path` is required and must exist.
  - `python-can` and `cantools` must be importable.

### UI Flow (Current)
- Right-click CAN tile → Configure:
  - Set mode (`real`/`sim`)
  - Set CAN channel + baudrate
  - Choose DBC path
  - Load signals from DBC
  - Filter signals (prefix or wildcard `*`)
  - Check desired signals
  - Save to `configs/can.yaml` and trigger plugin reload

### Outputs
- Data channels: enabled `signals[*].alias`
- Diagnostics channels:
  - `CAN/frames_rx` (`count`)
  - `CAN/decode_hits` (`count`)
  - `CAN/last_decode_age_s` (`s`)

### Deferred / Not Yet Implemented
- NI-XNET XML import path in runtime
- True multi-bus runtime acquisition
- CAN FD-specific handling


