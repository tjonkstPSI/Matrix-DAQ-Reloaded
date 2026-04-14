<!-- Author: T. Onkst | Date: 03092026 -->

## CCP Plugin Specification

### Purpose
Configure and communicate with one or two CCP ECUs over NI-XNET/CAN using A2L-based measurement polling and algorithmic access-key unlock, while exposing latest-value snapshots to the core tick.

### Current Implementation Status (Matrix_v2_retry)
- Implemented and validated in-app:
  - Real-mode NI-XNET connect path
  - `GET_SEED` + algorithmic access-key unlock (CAL/DAQ modes)
  - `SHORT_UP` polling over A2L measurement addresses
  - UI config dialog + live plugin reload
  - Runtime diagnostics channels and stage logs
  - Multi-device config model (`devices[*]`) with up to two ECMs
  - Primary/Secondary role mapping to station address (`0x0`/`0x1`)
  - Background acquisition worker + latest-value snapshot reads (non-blocking tick path)
- Not required for current path:
  - Vendor seed/key DLL integration (deprecated for this project)
- Deferred:
  - DAQ/ODT streaming setup path
  - Write/calibration operations

### Scope
- Transport: CAN (classic) via NI-9862 and NI-XNET
- Protocol: CCP (ASAP2/CCP); XCP is out-of-scope initially
- Descriptors: ASAP2 A2L file provided by operator
- Security: algorithmic seed/key unlock with configured access key or env var

### Unlock and Security
- Unlock flow:
  1) CONNECT to configured station address
  2) `GET_SEED` for configured resource
  3) compute key using access key + seed algorithm
  4) UNLOCK and optional `SET_S_STATUS`
- Access key sources:
  - `security.access_key`
  - env var fallback (`CCP_ACCESS_KEY`, plus compatibility fallbacks)
- DLL-based seed/key path is not used in current runtime.

### Variable Discovery, Selection, and Naming
- CCP Configure dialog loads A2L channels and metadata (address/type/size/limits/unit).
- Operator selects channels by checkbox with prefix/wildcard filter.
- Final alias = `measurements.naming_prefix + measurement.name`.
- Units come from config override when present, else A2L-resolved units.

### Acquisition Model
- Worker thread performs connect/reconnect and polling independently of core tick.
- Core tick samples `_snapshot_values` (latest-value sample-and-hold).
- Per-device polling interval and per-tick fanout are used; auto-tuning increases fanout for channel responsiveness.
- SHORT_UP poll timeout honors configured `io_timeout_s` (default 50 ms, no hard cap). Occasional poll fails under CAN bus contention are normal; failed reads hold the previous good value.
- Freshness telemetry tracks channel age against poll interval (warn at 25%, stale at 100%).

### Bus and Session Configuration
- Per-device session config includes:
  - interface, baudrate, tx/rx IDs, station_address, extended-ID flag
- Security config includes seed/connect/unlock counters and access key settings.

### Configuration (YAML)
File: `configs/ccp.yaml`

```yaml
enabled: true
mode: real
recording_rate_hz: 10
devices:
  - name: CCP Primary
    role: primary
    session:
      interface: CAN1
      baudrate: 250000
      tx_id: "0x0CFF50F9"
      rx_id: "0x0CFF5100"
      station_address: "0x0"
      is_extended: true
    security:
      seed_resource: "0x01"
      seed_ctr: "0x07"
      connect_ctr: "0x19"
      unlock_ctr: "0x08"
      access_key: ""
      seed_endian: big
      sec_type: CAL
    a2l:
      path: C:/path/to/file.a2l
    poll_interval_ms: 100
    measurements:
      naming_prefix: CCP_
      list:
        - name: rpm
          enabled: true
```

Compatibility:
- Top-level `session/security/a2l/measurements` keys remain supported and are mirrored from the first device by UI save.

#### Validation Rules
- Device config must resolve to at least one configured device.
- Enabled aliases must be unique within CCP plugin.
- In real mode, each configured device requires:
  - session interface + tx/rx IDs
  - access key (config or env var)
  - existing A2L path
  - selected measurement names resolvable in A2L with valid addresses

### UI Flow
- Right-click CCP tile → Configure:
  1) Add/remove device tabs (max two)
  2) Set device role (`Primary`/`Secondary`) and session/security fields
  3) Select A2L and load channels
  4) Filter + check measurements
  5) Save and reload plugin
- Test button sends `ccp_test` control request and shows step status in dialog terminal.

### Outputs and Metadata
Additional diagnostics channels:
- `CCP/connected`, `CCP/state_code`, `CCP/connect_attempts`, `CCP/connect_ok`
- `CCP/unlock_ok`, `CCP/poll_success`, `CCP/poll_fail`
- `CCP/last_seed_status`, `CCP/last_rc`, `CCP/ctr_mismatch`
- `CCP/fresh_age_s`, `CCP/fresh_max_channel_age_s`
- `CCP/freshness_state_code`, `CCP/freshness_warn_count`, `CCP/freshness_stale_count`

### Deferred Optimization Backlog
- Poll fail rate significantly reduced after removing 15 ms timeout cap; residual occasional fails are normal CAN bus behavior and do not affect data quality.
- Reduce stale data frequency (`CCP/freshness_state_code` warn/stale transitions), especially for high-priority channels such as `CCP_Vsw`.
- Consider increasing `poll_channels_per_tick` if further fail-rate reduction is needed.
- Add rolling CCP health metrics (for example, success-rate window and consecutive-fail counters) to separate transient noise from sustained degradation.

### Notes
- Current scope is read-only measurement polling.
- Write/calibration flows are intentionally not implemented in this plugin version.


