<!-- Author: T. Onkst | Date: 03092026 -->

## Modbus Plugin Specification

### Purpose
Provide configurable Modbus channel mapping with a multi-device UI model (TCP/IP and RS485 setup per device), while runtime currently serves snapshot-based simulated values and preserves config compatibility for future real Modbus I/O.

### Current Implementation Status
- Implemented now:
  - Modbus Configure dialog with tabbed multi-device editor
  - Per-device connection settings and channel table
  - YAML persistence to `devices[*]` model
  - Legacy compatibility projection to top-level `reads`, `servers`, `serial_devices`, `connection`
  - Snapshot-buffer runtime (`simulate_step` returns cached values)
  - Runtime read resolution priority:
    1) `devices[*].reads[*]`
    2) fallback `reads[*]`
- Not implemented yet:
  - Real Modbus comms polling path (TCP/RTU read/write transport)
  - Real write execution/audit flow

### Runtime Model (Current)
- Worker thread updates `_snapshot_values` at ~`1 / recording_rate_hz`.
- In current code path, values are simulated from configured read aliases:
  - `Room Temp` and `Humidity` get dynamic demo values
  - Other aliases default to `0.0`
- Core reads snapshots non-blocking.
- Core tick/log cadence is controlled by Channel Manager; Modbus worker cadence remains plugin-local.

### Configuration (Current)
File: `configs/modbus.yaml`

```yaml
enabled: true
mode: sim
recording_rate_hz: 10
devices:
  - name: ComApp
    connection:
      interface_type: TCP/IP      # TCP/IP | RS485
      ip_address: 192.168.10.1
      network_port: 502
      com_port: COM1
      unit_id: 1
      baud_rate: 115200
      serial_type: RTU            # RTU | ASCII
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

### UI Flow (Current)
- Right-click Modbus tile → Configure:
  - Add/remove device tabs
  - Set per-device connection:
    - TCP/IP: IP + network port
    - RS485: COM port, unit id, baud, serial type
    - word order (big/little)
  - Edit channel table columns:
    - Channel Name, Unit, Type, Address, Length, Data Type, Gain, Offset, Value
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

### Deferred / Next Real-Mode Work
- Implement TCP/RS485 transport clients and polling scheduler
- Map per-device connection settings into real readers
- Implement true write path + safeguards + audit logs


