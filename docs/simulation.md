<!-- Author: T. Onkst | Date: 08122025 -->

## Simulation & Test Harness

### Purpose
Provide deterministic, hardware-free data sources for early development and CI. Allow mixing real and simulated devices per plugin.

### Runtime Selection
- Per-plugin `mode: real | sim` in each config YAML (default `real`).
- The orchestrator may override to `sim` if required drivers/devices are unavailable.

### NI DAQ
- Mode: `real` uses NI‑DAQmx (supports NI MAX simulated chassis/devices). `sim` generates synthetic waveforms (sine/step/ramp) per channel config.
- Recommendation: Use your NI MAX simulated cDAQ chassis for development/testing.

### CAN (XNET)
- Mode: `sim` publishes frames from a scripted schedule or random‑walk values per DBC signal (respects units/scaling).
- `real` uses NI‑XNET; if DBs missing or interface absent, auto‑fallback to `sim` (configurable).

### CCP
- Read‑only.
- Mode: `sim` maps selected A2L measurement names to simulated values (shared with CAN if present) and timestamps; no unlock required.

### Modbus
- Mode: `sim` provides an in‑process Modbus server with registers/coils prefilled and evolving over time; respects read/write FCs.
- `real` connects to configured servers; if unreachable, optional fallback to `sim` for specific endpoints.

### LoadBank
- Mode: `sim` implements setpoint/accept/status behavior in‑process; measured load follows setpoint with simple dynamics and fault injection.

### Vaisala
- Mode: `sim` serves mapped measurements via the in‑process Modbus server; supports calibration offsets.

### Statistics & Calculated Channels
- Always compute from whatever sources are active (real or sim).

### Cycle
- Drives LoadBank (real or sim) using the CSV schedule.

### Configuration Example (excerpt)
```yaml
# configs/modbus.yaml
mode: sim

# configs/can.yaml
mode: sim

# configs/ccp.yaml
mode: sim

# configs/ni_daq.yaml
mode: real   # uses NI MAX simulated chassis if physical not present
```

### Tests
- Sim smoke tests cover R=100 Hz with ~200 channels, Excel export, AlarmEvents, and segmentation.
- Per‑plugin tests verify sim modes (connectivity, data shape, timing).


