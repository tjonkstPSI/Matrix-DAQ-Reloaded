<!-- Author: T. Onkst | Date: 04202026 -->

## Modbus Plugin Specification

### Purpose
Provide configurable Modbus channel mapping with a multi-device UI model (TCP/IP and RS485 setup per device), with real Modbus TCP transport for reading holding/input registers and snapshot-based simulated values for offline development.

### Current Implementation Status
- Implemented now:
  - **Real Modbus TCP transport**: `_ServerConnection` class manages per-server `ModbusTcpClient` connections with configurable host, port, unit_id, timeout, and retries. `_read_all_servers()` polls all configured servers, `_decode_registers()` converts raw register data to float/int values based on type and byte/word order.
  - Modbus Configure dialog with tabbed multi-device editor
  - Per-device connection settings and channel table
  - YAML persistence to `devices[*]` model with `servers` block for real transport
  - Legacy compatibility projection to top-level `reads`, `servers`, `serial_devices`, `connection`
  - Snapshot-buffer runtime (sim and real modes)
  - Runtime read resolution priority:
    1) `devices[*].reads[*]`
    2) fallback `reads[*]`
  - Connection health channel: `Modbus/conn_ok`
  - Double-click Alias column opens standard alias picker
  - Global sim/real mode controlled by launch dialog Offline Mode checkbox
- Not implemented yet:
  - RS485/RTU transport (TCP only)
  - Real write execution/audit flow

### Runtime Model
- Worker thread updates `_snapshot_values` at ~`1 / recording_rate_hz`.
- In **real** mode: `start()` connects to all configured servers; `_snapshot_loop()` calls `_read_all_servers()` which polls each server, decodes registers, applies gain/offset scaling, and updates `_conn_ok` based on connection success. `stop()` disconnects all server connections.
- In **sim** mode: all read aliases get phase-shifted sine waveforms; `_conn_ok` is always True.
- Core reads snapshots non-blocking.
- Core tick/log cadence is controlled by Channel Manager; Modbus worker cadence remains plugin-local.
- Mode is enforced by the orchestrator's global `data_mode` setting (overrides YAML `mode` field).

### Configuration
File: `configs/modbus.yaml`

```yaml
enabled: true
mode: real
recording_rate_hz: 10
servers:
  - host: 192.168.10.1
    port: 502
    unit_id: 1
    timeout: 2.0
    retries: 2
devices:
  - name: ComApp
    connection:
      interface_type: TCP/IP
      ip_address: 192.168.10.1
      network_port: 502
      unit_id: 1
      word_order: big             # big | little
    reads:
      - alias: Room Temp
        fc: 4
        address: 30001
        length: 2
        type: float32
        scaling: { m: 1.0, b: 0.0, unit: C }
        enabled: true
```

Compatibility keys may also be present:
- `reads`, `servers`, `serial_devices`, `connection`

### UI Flow
- Right-click Modbus tile → Configure:
  - Add/remove device tabs
  - Set per-device connection:
    - TCP/IP: IP + network port
    - RS485: COM port, unit id, baud, serial type
    - word order (big/little)
  - Edit channel table columns:
    - Alias, Unit, Type, Address, Length, Data Type, Gain, Offset, Value
  - Double-click the Alias column to open the standard alias picker
  - Alias validation on save (standard naming convention enforced)
  - Test button:
    - saves + reloads plugin
    - fills Value column from live telemetry stream

### Validation Rules
- `reads` and `writes` must be lists after resolution.
- Alias uniqueness enforced within plugin.
- JSON schema validation applied via `configs/schemas/modbus.schema.json` when available.

### Data/Type Mapping in UI
- Type column maps to function code:
  - Coil -> `fc=1`
  - Discrete Input -> `fc=2`
  - Holding Register -> `fc=3`
  - Input Register -> `fc=4`
- Data Type + Length maps to stored `type`:
  - Unsigned -> `uint16`/`uint32`
  - Signed -> `int16`/`int32`
  - Float -> `float32`/`float64`

### Outputs
- Data channels: all enabled read aliases
- Health channel: `Modbus/conn_ok` (bool as 1.0/0.0) — True when at least one server connection is active. Console tile uses this for Green/Red/Disconnected status.

### Pymodbus Compatibility
- All Modbus read/write calls use `_modbus_compat.uid_kwargs()` for version-independent unit/slave/device_id parameter handling (pymodbus 3.0–3.10+).

### Configuration Notes
- Register `address` values in YAML use 0-based Modbus PDU addressing (not 1-based as some legacy tools display).
- `length` refers to the number of 16-bit registers to read (not bytes). A single uint16/int16 value uses `length: 1`; a float32 uses `length: 2`.
- `type` should match the actual register width: `uint16`/`int16` for single-register values, `float32` for two-register IEEE 754 floats.

### Deferred / Next Work
- Implement RS485/RTU transport
- Implement true write path + safeguards + audit logs


