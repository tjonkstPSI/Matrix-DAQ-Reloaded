<!-- Author: T. Onkst | Date: 08112025 -->

## CCP Plugin Specification

### Purpose
Configure and communicate with an ECU using CCP (CAN Calibration Protocol) over NI-XNET/CAN. Import A2L files, handle seed/key unlock via vendor DLL, allow operators to select measurement variables (no per-variable aliases; use A2L names), optionally apply a user-defined prefix for naming, and record aligned to the system recording rate R (≤ 100 Hz). Calibration writes are out-of-scope (read-only).

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
  bus: "CAN1"                           # link to a CAN interface
  station_address: 0x01                 # ECU station address
  timeout_ms: 50
  max_retries: 3

security:
  dll_path: "C:/Vendor/seedkey.dll"
  function: "calc_key"                  # function(seed_bytes) -> key_bytes
  calling_convention: "cdecl"           # or stdcall
  test_seed_hex: "0011223344556677"     # optional dry-run test vector

a2l:
  files:
    - path: "C:/Configs/ccp/engine.a2l"
  variant: null                         # optional variant selection

measurements:
  naming_prefix: "emaster"              # optional; if omitted, final names are A2L names
  list:
    - name: "RPM"                        # from A2L; final = prefix+name or name
      unit_override: null
      enabled: true
    - name: "Oil_Temperature"
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


