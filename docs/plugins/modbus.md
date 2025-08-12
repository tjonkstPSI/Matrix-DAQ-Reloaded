<!-- Author: T. Onkst | Date: 08112025 -->

## Modbus Plugin Specification

### Purpose
Configure custom Modbus TCP read/write channels beyond the dedicated LoadBank and Vaisala plugins. Operators define servers, register maps, polling rates, data types, scaling, aliases, and safeguarded write commands. Values are aligned to the recording rate R (≤ 100 Hz).

### Scope
- Transport: Modbus TCP (primary). Modbus RTU/Serial: TBD (future option)
- Servers: One or more Modbus TCP servers (host, port, unit-id)
- Reads: Input Registers (FC4), Holding Registers (FC3), Discrete Inputs (FC2), Coils (FC1)
- Writes: Holding Registers (FC6/FC16), Coils (FC5/FC15) with confirmation readback and limits

### Channel Definition and Aliases
- No auto-discovery; operator defines channels via configuration
- Each enabled channel requires an alias used in UI and output column names (unique across enabled names)
- Channels grouped by server; reads may have individual poll rates ≤ R (defaults typically 1–10 Hz)

### Data Types and Endianness
- Supported types for register-backed values:
  - uint16, int16
  - uint32, int32 (2 registers)
  - float32 (IEEE 754, 2 registers)
  - float64 (4 registers)
- Word/byte order options for multi-register values:
  - word_order: AB | BA | ABCD | BADC | CDAB | DCBA (implementation supports 32/64-bit variants)
  - byte_order: big | little (within each 16-bit register)

### Acquisition Model
- Poll each configured read channel at its poll_hz (≤ R). Aggregate to the canonical R grid using last-value-hold per R interval.
- Retry with backoff on timeouts; escalate to UI error if persistent; auto-reconnect with bounded attempts.
- Buffering sized for at least a few R intervals to absorb network jitter.

### Writes and Safeguards
- Writes are issued by operator/UI or by other plugins (e.g., Cycle/LoadBank) through a controlled API
- Each write definition includes:
  - Register/coil address, data type/bit (if applicable), min/max bounds for numeric values
  - Optional rate_limit_hz (max command frequency)
  - Optional confirm_readback (post-write verify via read)
  - Optional latching: require explicit reset before next write

### Configuration (YAML)
File: `configs/modbus.yaml`

```yaml
recording_rate_hz: 100                    # R (per run); provided by Channel Manager

servers:
  - name: "FACILITY_MB"
    host: "192.168.1.50"
    port: 502
    unit_id: 1
    timeout_ms: 200
    max_retries: 3

reads:
  - alias: "Room Temp"
    server: "FACILITY_MB"
    fc: 3                                 # 3=Holding, 4=Input, 1=Coil, 2=Discrete
    address: 40001                        # normalized to 0-based internally
    type: float32
    word_order: AB
    byte_order: big
    scaling: { m: 1.0, b: 0.0, unit: "C" }
    poll_hz: 2                            # ≤ R
    enabled: true

  - alias: "Humidity"
    server: "FACILITY_MB"
    fc: 4
    address: 30010
    type: uint16
    byte_order: big
    scaling: { m: 0.1, b: 0.0, unit: "%RH" }
    poll_hz: 1
    enabled: true

writes:
  - alias: "Fan Accept"
    server: "FACILITY_MB"
    fc: 5                                 # coil write
    address: 1
    type: coil
    rate_limit_hz: 1
    confirm_readback: true
    enabled: true

  - alias: "AO Setpoint"
    server: "FACILITY_MB"
    fc: 6                                 # single register write
    address: 40100
    type: uint16
    scaling: { m: 10.0, b: 0.0, unit: "%" }  # UI % → register value
    min: 0
    max: 100
    confirm_readback: true
    enabled: false
```

Notes:
- For multi-register types, word_order defaults to AB (high→low). Alternate orders supported as configured.
- Addressing policy: internal implementation uses 0-based offsets; YAML accepts human-friendly addresses but normalizes on load.

#### Validation Rules
- alias required and unique among enabled names system-wide
- server references an entry in servers; host reachability deferred to runtime
- fc allowed values per read/write section; type matches FC and size
- poll_hz ≤ R; positive; missing defaults to 1 Hz
- min/max required for numeric writes; value range enforced
- rate_limit_hz ≥ 0; confirm_readback optional

### UI Flow
- Right-click Modbus tile → Configure:
  1) Manage servers (host/port/unit-id, timeout, retries)
  2) Define reads (FC, address, type, endianness, scaling, poll_hz, alias)
  3) Define writes (FC, address, type, scaling, min/max, rate limiting, confirm)
  4) Save and validate
- Show Error: display last comms error; Reset Error: reconnect client

### Outputs and Metadata
- Metadata per channel includes: server, FC, address, type, endianness, scaling, units, poll rate
- Values aligned to R grid with last-value-hold per interval; null if never received since start
- Write audit: per-run log entries for write commands with parameters, result, and optional readback value

### Error Conditions (Examples)
- Connection timeout or refused → UI error; auto-reconnect retries with backoff
- Illegal function/address → validation/runtime error; suggest map correction
- Read/write data size mismatch → validation error

### Test Cases (Modbus)
- MB-Server-001: Add server config; handle connect/disconnect; retries on failure
- MB-Read-001: Define reads of different FC/types; poll at specified rates; scale and align to R grid
- MB-Endian-001: Validate word/byte order handling for 32-bit/64-bit types
- MB-Write-001: Write coil/register with min/max and readback confirmation; rate limiting respected
- MB-ErrorUI-001: Simulate connection loss; Show Error/Reset Error behavior


