<!-- Author: T. Onkst | Date: 04202026 -->

## CAN Plugin Specification

### Purpose
Decode selected CAN signals from one or more DBC-configured buses in real mode, or generate simulated values in sim mode, and expose latest values to the core tick through a snapshot buffer.

### Current Implementation Status
- Implemented now:
  - **Multi-bus support**: `buses[*]` YAML schema with per-bus channel, baudrate, bustype, DBC path, and signals
  - Legacy fallback: if `buses` is empty, falls back to `session` + top-level `signals` + `dbc_path`
  - `python-can` real bus sessions (`bustype: nixnet` default), one per configured bus
  - DBC parsing with `cantools`, one DBC per bus
  - Signal selection via CAN Configure dialog (tabbed per-bus with Add/Remove Bus)
  - Shared NI-XNET/CAN hardware discovery helper used by CAN and CCP config dialogs
  - CAN channel dropdown populated from discovered hardware, with blank default when saved YAML does not match detected ports
  - Background snapshot loop drains frames from all buses (non-blocking core tick reads)
  - Alias picker dialog on double-click in signal table Alias column
  - Alias validation on save (standard naming convention + global uniqueness across buses)
  - Runtime diagnostics channels:
    - `CAN/frames_rx`
    - `CAN/decode_hits`
    - `CAN/last_decode_age_s`
    - `CAN/conn_ok`
  - Console Messages box notification when no CAN hardware/interface is configured or available
- Implemented decode fallback:
  - Direct arbitration-ID decode first
  - J1939 PGN-based fallback using configured message names/signals

### Runtime Model
- The plugin builds a `_BusContext` per configured bus during `configure()`.
- In `start()`, each bus context opens a `python-can` bus handle and loads its DBC.
- Bus contexts with a blank `channel` are skipped in real mode; the plugin remains disconnected and reports the missing hardware/configuration to the console Messages box.
- The snapshot worker thread iterates all bus contexts:
  - waits for incoming frames (`recv`) per bus,
  - drains queued frames in a short burst,
  - decodes matching configured signals,
  - updates `_snapshot_values`.
- `simulate_step()` returns the cached snapshot immediately.
- `simulate_step()` also drains one-time plugin console messages through `__console_msgs__`.
- Core samples CAN values at recording tick cadence (sample-and-hold behavior).
- Core tick/log cadence is controlled globally by Channel Manager.

### Configuration
File: `configs/can.yaml`

```yaml
enabled: true
mode: real              # real | sim
recording_rate_hz: 10
buses:
  - name: CAN Bus 1
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
  - name: CAN Bus 2
    channel: CAN2
    baudrate: 500000
    bustype: nixnet
    dbc_path: C:/path/to/other.dbc
    signals:
      - alias: cTP_OilSump
        message: ET1_65262
        signal: cTP_OilSump
        unit: C
        enabled: true
```

Legacy compatibility: if `buses` is empty or absent, the plugin falls back to `session` + top-level `signals` + `dbc_path` (auto-wrapped into a single bus context).

### Validation Rules
- At least one bus must be configured.
- Enabled signal aliases must be globally unique across all buses.
- In real mode:
  - Each bus must have a `dbc_path` that exists.
  - `python-can` and `cantools` must be importable.
- Config dialog save requires a non-blank CAN channel when hardware discovery returns available channels.
- If no hardware is discovered, the dialog may save a blank channel so the user can exit config; runtime then reports disconnected/red with a console message instead of trapping the user in the dialog.

### UI Flow
- Right-click CAN tile → Configure:
  - **Tabbed per-bus layout** with Add Bus / Remove Bus buttons
  - Per tab:
    - Bus name, CAN channel dropdown, baudrate
    - DBC path (browse + load signals)
    - Signal filter (prefix or wildcard `*`)
    - Signal table with checkbox, message, signal, unit, and alias columns
    - Double-click Alias column to open standard alias picker
  - CAN channel dropdown behavior:
    - Populated from the shared CAN discovery helper using `python-can.detect_available_configs(interfaces=["nixnet"])` first, then NI-XNET system probing as fallback.
    - Saved YAML channel is preselected only when it matches discovered hardware.
    - If saved YAML does not match discovered hardware, the dropdown starts blank but still lists discovered channels.
    - If no hardware is discovered, the dropdown remains blank and the dialog can still be saved/closed.
  - Save writes to `buses[*]` in `configs/can.yaml`; triggers plugin reload

### Outputs
- Data channels: enabled `signals[*].alias` (all buses combined)
- Diagnostics channels:
  - `CAN/frames_rx` (`count`)
  - `CAN/decode_hits` (`count`)
  - `CAN/last_decode_age_s` (`s`)
- Health channel:
  - `CAN/conn_ok` (`bool` as 1.0/0.0) — True when at least one bus opened successfully. Console tile uses this to show Green/Red/Disconnected status.

### Console Messages
- If no configured/discovered CAN channel can be opened in real mode, the plugin queues one message:
  - `[CAN] No CAN hardware/interface configured or available. Open CAN config and select a detected CAN channel.`
- The message is emitted once per configure/start cycle to avoid flooding while leaving `CAN/conn_ok=0` for the red/disconnected console tile.

### Deferred / Not Yet Implemented
- NI-XNET XML import path in runtime
- CAN FD-specific handling
