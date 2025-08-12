<!-- Author: T. Onkst | Date: 08112025 -->

## CAN Plugin Specification

### Purpose
Acquire CAN signals via NI-XNET using user-provided databases (DBC or NI-XNET XML). Allow operators to select signals with checkboxes, assign unique aliases, configure bus speeds, and record aligned to the system recording rate R (≤ 100 Hz).

### Scope
- Protocol: CAN 2.0A/B (classic). CAN FD: TBD (future option)
- Hardware: NI-9862 via NI-XNET
- Databases: DBC (.dbc) and NI-XNET database XML (.xml). Import and selection; export of active selection to YAML.

### Signal Discovery, Selection, and Aliases
- Import one or more databases. Present a hierarchical view (Cluster → ECU/Message → Signals) with checkboxes.
- Selection produces a flat list of enabled signals.
- Each enabled signal must have an alias (display/recording name):
  - Alias is used in UI displays and as the column name in Parquet/Excel
  - Must be unique among all enabled signals and other channel namespaces
  - Allowed: letters, numbers, underscore, dash, space; length ≤ 64
  - Default: generated from `Cluster.Message.Signal` if left blank (validation prompts to confirm)
- Store original database identifiers and scaling (factor/offset, units) in metadata.

### Bus Configuration
- Support multiple buses simultaneously (e.g., CAN1, CAN2…)
- Per-bus settings:
  - `interface` (e.g., `CAN1`), `device` (optional NI-XNET device alias)
  - `bitrate_bps` (125000, 250000, 500000, 1000000); precise timing segments: TBD/auto
  - `termination` (true/false metadata only; physical switch managed externally)
  - Optional acceptance filters by ID/mask (TBD)

### Acquisition Model
- NI-XNET read sessions configured by selected signals per bus.
- Hardware timestamps from XNET are used; values are mapped onto the recording grid at R using last-value-hold within each R interval.
- Drift handling: align to canonical R grid with periodic correction (parameters TBD); no resampling beyond last-hold by default.
- Buffering sized to avoid overruns at expected bus load; detect and log overrun conditions.

### Configuration (YAML)
File: `configs/can.yaml`

```yaml
recording_rate_hz: 100         # R (per run); provided by Channel Manager

buses:
  - name: "CAN1"
    interface: "CAN1"          # NI-XNET interface alias
    bitrate_bps: 250000
    termination: false          # informational; physical termination handled externally

  - name: "CAN2"
    interface: "CAN2"
    bitrate_bps: 250000
    termination: false

databases:
  - path: "C:/Configs/can/engine.dbc"
    mount_name: "ENGINE"
  - path: "C:/Configs/can/facility.xml"   # NI-XNET XML
    mount_name: "FACILITY"

signals:
  - cluster: "ENGINE"
    message: "ECM_EngineData"
    signal: "EngineSpeed"
    bus: "CAN1"
    alias: "RPM"
    units_override: null        # optional, else from DB
    enabled: true

  - cluster: "ENGINE"
    message: "ECM_EngineData"
    signal: "OilPressure"
    bus: "CAN1"
    alias: "Oil Pressure"
    enabled: true

  - cluster: "FACILITY"
    message: "FacilityData"
    signal: "RoomTemp"
    bus: "CAN2"
    alias: "Room Temp"
    enabled: true
```

#### Validation Rules
- Each `signals[*]` entry resolves to an element present in the mounted database(s).
- `alias` required for enabled signals; must be unique across enabled signals and not collide with other plugin channel aliases.
- Each signal is mapped to an existing `bus` in `buses`.
- `bitrate_bps` supported by NI-XNET.
- Databases must be readable; DBCs may be internally converted to an XNET database (implementation detail).

### UI Flow
- Right-click CAN tile → Configure:
  1) Add/remove databases (DBC/XML), 2) Choose bus bitrates, 3) Browse/select signals via tree with checkboxes, 4) Assign aliases, 5) Save
- Show Error: display active XNET error; Reset Error: close/reopen sessions.

### Outputs and Metadata
- For each recorded signal, metadata includes: cluster/message/signal, alias, units, factor/offset, bit length, byte order, arbitration ID, bus, database file path.
- Values are emitted on the R grid using last-value-hold (or null if never received; policy TBD).

### Error Conditions (Examples)
- Bus-off or bus heavy load → session error; surface in UI; allow Reset Error.
- Database mismatch (signal not found) → validation error.
- Overrun detected → log warning; advise reducing selection or increasing buffers.

### Test Cases (CAN)
- CAN-DB-Import-001: Import DBC/XML; databases mount and are referenceable.
- CAN-Signal-Select-001: Tree selection produces enabled list; persists to YAML.
- CAN-Alias-Unique-001: Enforce alias uniqueness and charset; alias used in UI and outputs.
- CAN-MultiBus-001: Configure two buses with distinct bitrates; acquire simultaneously.
- CAN-Align-001: Hardware timestamps aligned to R grid with last-value-hold.
- CAN-Overrun-001: Simulate high bus load; verify detection and logging.


