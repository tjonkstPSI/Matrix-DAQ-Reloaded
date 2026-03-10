<!-- Author: T. Onkst | Date: 03092026 -->

## CCP Plugin Specification

### Purpose
Configure and communicate with an ECU using CCP (CAN Calibration Protocol) over NI-XNET/CAN. Import A2L files, handle seed/key unlock via vendor DLL, allow operators to select measurement variables (no per-variable aliases; use A2L names), optionally apply a user-defined prefix for naming, and record aligned to the system recording rate R (≤ 100 Hz). Calibration writes are out-of-scope (read-only).

### Current Implementation Status (Matrix_v2_retry)
- Implemented and validated in-app:
  - Real-mode NI-XNET connect path
  - `GET_SEED` + algorithmic access-key unlock (CAL/DAQ modes)
  - `SHORT_UP` polling over A2L measurement addresses
  - UI config dialog + live plugin reload
  - Runtime diagnostics channels and stage logs
- Not required for current path:
  - Vendor seed/key DLL integration (deprecated for this project)
- Deferred:
  - DAQ/ODT streaming setup path
  - Write/calibration operations

### Scope
- Transport: CAN (classic) via NI-9862 and NI-XNET
- Protocol: CCP (ASAP2/CCP); XCP is out-of-scope initially
- Descriptors: ASAP2 A2L file provided by operator
- Security: Seed/Key via vendor-supplied DLL (path and function configured by operator)

### Unlock and Security
- Unlock flow:
  1) Establish CCP connection to ECU station address
  2) Request seed (ECU-specific resource)
  3) Load operator-specified DLL and function, compute key from seed
  4) Send key; on success, gain access for protected actions (e.g., calibration writes)
- Configuration parameters for DLL:
  - `dll_path` (absolute), `function` name, optional calling convention, argument types, and return type
  - Optional "dry-run" tester with a sample seed to verify DLL invocation
- Safety:
  - Unlock only upon explicit operator action (Unlock button) or when write is requested
  - Automatically relock on Stop Test or plugin teardown

Implementation note:
- Current integration uses algorithmic unlock with ECU access key (`security.access_key` or `CCP_ACCESS_KEY` env var). DLL-based key derivation remains optional/deferred.

### Variable Discovery, Selection, and Naming
- Import one or more A2L files; if multiple ECU variants exist, operator selects the target variant
- Present a searchable table/tree of measurement variables (and optional characteristic parameters for write)
- Checkbox selection builds an enabled list; final channel names are, by default, the A2L names
- Optional naming prefix (operator-provided) is prepended to each selected variable to form the final name
  - Example: prefix `emaster` + A2L `RPM` ⇒ final name `emasterRPM`
  - Prefix format is not restricted; typical convention encodes ECU role (e.g., master/slave)
- Units, conversion (factor/offset), and limits default from A2L; optional unit override allowed
- For write-enabled parameters, require min/max constraints in config

### Acquisition Model
- Default mode: Host-polled at rate R (≤ 100 Hz) using CCP requests, mapped to R grid
- Advanced (TBD): DAQ lists/ODTs configured from A2L for ECU-driven sampling
- Timestamps: ECU responses mapped using last-value-hold within each R interval
- Buffering and retries: bounded retries with backoff for timeouts; surface persistent failures to UI

### Bus and Session Configuration
- Reuse CAN bus defined in CAN plugin or specify here
- Per-session settings:
  - `bus` (e.g., CAN1), `station_address` (ECU), `resource` (unlock resource), `base_id`/message IDs if needed
  - `timeout_ms` for CCP requests, `max_retries`

### Configuration (YAML)
File: `configs/ccp.yaml`

```yaml
recording_rate_hz: 100                 # R (per run); from Channel Manager

session:
  interface: "CAN1"
  baudrate: 250000
  tx_id: 0x0CFF50F9
  rx_id: 0x0CFF5100
  station_address: 0x0
  is_extended: true

security:
  seed_resource: 0x01
  seed_ctr: 0x07
  connect_ctr: 0x19
  unlock_ctr: 0x08
  access_key: ""                         # optional if CCP_ACCESS_KEY env var is set
  seed_endian: "big"                     # big | little | reverse
  sec_type: "CAL"                        # CAL | DAQ
  unlock_pad: 0x55
  force_unlock: true
  set_s_status: true
  s_status: 0x83

a2l:
  path: "C:/Configs/ccp/engine.a2l"

poll_interval_ms: 100
poll_channels_per_tick: 1
io_timeout_s: 0.05
poll_endian: big
mta_addr_endian: big
addr_ext_high: false
reconnect_interval_s: 2.0

measurements:
  naming_prefix: "CCP_"
  list:
    - name: "RPM"
      unit_override: null
      enabled: true
    - name: "Vbat"
      enabled: true

writes: []                                # read-only scope; no calibration writes
```

#### Validation Rules
- A2L file(s) must parse; each `measurements.list[*].name` and `writes[*].name` must exist in A2L
- Final channel names are computed as `prefix + A2L name` (or just A2L name if no prefix); must be unique across all enabled names in the system
- `station_address` within valid range; bus must exist
- Seed/Key DLL must be loadable; function callable with provided signatures (validated via dry-run when `test_seed_hex` set)
 - No write validation required (read-only)

### UI Flow
- Right-click CCP tile → Configure:
  1) Add/choose A2L file(s) and variant
  2) Configure bus, station address, timeouts
  3) Configure seed/key DLL and test unlock (dry-run with sample seed)
  4) Browse/select measurement variables (checkboxes); set an optional naming prefix
  5) Optionally enable write parameters; set limits
  6) Save
- At runtime: Unlock button; Write dialog requires confirmation and range validation
- Errors: Show Error displays last CCP/XNET error; Reset Error restarts session

### Outputs and Metadata
- For each measurement: record alias, original A2L name, unit, conversion, address/ODT info (if available), and ECU variant
- For writes: log write attempts (value, time, result) to per-run logs; optionally include a write audit CSV (TBD)

Additional diagnostics channels:
- `CCP/connected`, `CCP/state_code`, `CCP/connect_attempts`, `CCP/connect_ok`
- `CCP/unlock_ok`, `CCP/poll_success`, `CCP/poll_fail`
- `CCP/last_seed_status`, `CCP/last_rc`, `CCP/ctr_mismatch`

### Deferred Optimization Backlog
- Reduce `CCP/poll_fail` rate under real ECU load while preserving current responsiveness.
- Reduce stale data frequency (`CCP/freshness_state_code` warn/stale transitions), especially for high-priority channels such as `CCP_Vsw`.
- Add adaptive timeout and/or bounded backoff tuning for SHORT_UP to improve stability during bus jitter.
- Add rolling CCP health metrics (for example, success-rate window and consecutive-fail counters) to separate transient noise from sustained degradation.

### Error Conditions (Examples)
- Unlock failure (key rejected) → UI error; retry or inspect DLL
- Timeout on CCP read → retry up to `max_retries`; if persistent, mark variable stale and log
- A2L mismatch (variable not found) → validation error

### Test Cases (CCP)
- CCP-A2L-Import-001: Import A2L; list measurements; variant selection works
- CCP-SeedKey-001: Load DLL; dry-run with `test_seed_hex`; unlock flow simulated
- CCP-Prefix-Naming-001: With naming_prefix set, final names are `prefix + A2L name`; without prefix, final names are A2L names
- CCP-Read-Poll-001: Poll measurements at rate R; align to R grid via last-value-hold
- CCP-Write-Range-001: Attempt write within/outside limits; outside rejected; within succeeds (sim)
- CCP-ErrorUI-001: Simulate timeout/unlock failure; Show Error/Reset Error behavior


