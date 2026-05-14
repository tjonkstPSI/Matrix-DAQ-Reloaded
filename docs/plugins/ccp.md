<!-- Author: T. Onkst | Date: 04292026 -->

## CCP Plugin Specification

### Purpose
Configure and communicate with one or two CCP ECUs over NI-XNET/CAN using A2L-based measurement polling and algorithmic access-key unlock, while exposing latest-value snapshots to the core tick.

### Current Implementation Status (Matrix_v2_retry)
- Implemented and validated in-app:
  - Real-mode NI-XNET connect path
  - `GET_SEED` + algorithmic access-key unlock (CAL/DAQ modes)
  - **SHORT_UP polling as default acquisition mode** with High/Low priority scheduling
  - Configurable target poll rate (Hz) with rate governor and budget estimation
  - Multi-list DAQ/ODT streaming (available via `acquisition_mode: daq`)
  - Per-channel priority assignment: `High Poll` / `Low Poll` (SHORT_UP) or `DAQ 1ms` / `DAQ 10ms` / `DAQ 50ms` / `DAQ 100ms`
  - UI config dialog with channel allocation summary, DAQ tier capacity bars, and target Hz spinner
  - Session-only access key storage (not saved to disk)
  - Console tile health (red/green) based on runtime connection and data flow status
  - Console Messages box shows key lifecycle events (connect, unlock, poll start, errors)
  - Runtime diagnostics channels and stage logs
  - Multi-device config model (`devices[*]`) with up to two ECMs
  - Shared NI-XNET/CAN hardware discovery helper used by CAN and CCP config dialogs
  - CAN interface dropdown per device, populated from discovered hardware and blanked when saved YAML does not match detected ports
  - Primary/Secondary role mapping to station address (`0x0`/`0x1`)
  - Background acquisition worker + latest-value snapshot reads (non-blocking tick path)
- Not required for current path:
  - Vendor seed/key DLL integration (deprecated for this project)
- Deferred:
  - Hybrid DAQ + SHORT_UP within a single device context (concurrent streaming and polling)
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
  - Session-only memory (entered in CCP config dialog, cleared on app exit)
  - Future: API server for automated key retrieval
- DLL-based seed/key path is not used in current runtime.

### A2L Parsing and Value Decode
- `parse_a2l()` in `_ccp_a2l.py` performs two passes over the A2L file:
  1. **Pass 1**: extracts COMPU_METHOD blocks — parses unit string and COEFFS (6 coefficients: a, b, c, d, e, f) from `RAT_FUNC` methods; recognizes `IDENTICAL` as identity conversion.
  2. **Pass 2**: extracts MEASUREMENT and CHARACTERISTIC blocks — links each to its COMPU_METHOD via the reference name, capturing address, data_type, physical limits, and COEFFS.
- Limits parsing correctly skips the first numeric line (Resolution/Accuracy) and captures the second (actual physical limits), including negative lower bounds.
- `A2LChannel` dataclass stores: `name`, `address`, `data_type`, `limits`, `unit`, `coeffs`.
- `decode_value()` converts raw CCP payload bytes to physical values in three stages:
  1. **Type decode**: raw bytes → numeric internal value (signed/unsigned int via `int.from_bytes`, IEEE float via `struct.unpack`). Supports UBYTE, SBYTE, UWORD, SWORD, ULONG, SLONG, FLOAT32_IEEE, FLOAT64_IEEE.
  2. **COEFFS conversion**: applies inverted RAT_FUNC formula (`PHYS = (f*INT - c) / b` for the common linear case; full quadratic solver for rare edge cases). Used when COEFFS are available and non-identity.
  3. **Legacy fallback**: limits-based linear scaling when no COEFFS available (backward compatibility).
- `_apply_rat_func_inv()` handles all three RAT_FUNC variants: pure linear (a=d=e=0), linear-rational (a=d=0), and full quadratic.

### Variable Discovery, Selection, and Naming
- CCP Configure dialog opens with shallow A2L `MEASUREMENT` name discovery and uses saved config metadata for already-selected channels.
- Operator selects channels by checkbox with prefix/wildcard filter.
- Final alias = `measurements.naming_prefix + measurement.name`.
- The All Channels Table uses a display-only label for CCP Primary/Secondary rows: it removes the configured `naming_prefix` from the visible Alias cell and keeps the full telemetry/recording alias in the row tooltip.
- Units come from config override when present, else A2L-resolved units.
- CAN interfaces are discovered through the shared CAN helper. The dialog attempts `python-can.detect_available_configs(interfaces=["nixnet"])` first and falls back to NI-XNET system probing.
- Saved `session.interface` values are preselected only when they match discovered hardware. If a saved interface is no longer present, the device tab starts blank while the dropdown still lists detected interfaces.
- If no CAN hardware is discovered, interface fields remain blank and the dialog can still be saved/closed. Runtime then reports the plugin disconnected/red and emits a console message.

### Acquisition Model

**Default: SHORT_UP polling (High/Low priority)**

SHORT_UP is the default acquisition mode. Each channel is assigned a priority:
- **Low Poll** (default) — placed in the LOW priority bucket
- **High Poll** — placed in the HIGH priority bucket for time-critical channels

New channels default to **Low Poll**. Users promote important channels to High Poll as needed, rather than demoting unimportant ones. This prevents accidental oversubscription of the HIGH bucket.

The worker thread continuously sends SHORT_UP requests to the ECU and decodes CRM responses. A rate governor targets the configured `target_poll_hz` (default 10 Hz per channel). The weighted round-robin scheduler follows a configurable HIGH:LOW ratio (default 3:1, i.e., `[HIGH, HIGH, HIGH, LOW]`) to distribute reads across priority buckets. The ratio is adjustable via `high_low_ratio` in `ccp.yaml` (clamped 1-20) for super-user tuning.

Throughput depends on CAN bus round-trip time (typically 3-8 ms per SHORT_UP read). At 5 ms avg RTT, the bus can sustain ~200 reads/sec. With 20 channels at 10 Hz target = 200 reads/sec, the system runs at full capacity. The periodic terminal log reports achieved reads/sec, estimated Hz per priority, and budget utilization.

**Opt-in: DAQ streaming (`acquisition_mode: daq`)**

DAQ streaming remains fully functional for users who explicitly set `acquisition_mode: daq`. In DAQ mode, channels are assigned to ECU raster tiers (1ms, 10ms, 50ms, 100ms) and the ECU autonomously streams DTOs. All DAQ infrastructure (multi-list packing, ODT capacity enforcement, PID-based decode, CAN ID filtering) is preserved.

DAQ mode uses DAQ security unlock (`seed_resource: 0x02`, `sec_type: DAQ`), stops all active lists, performs `GET_DAQ_SIZE` per list, writes ODT entries, and starts each list independently. DAQ is subject to ECU firmware ODT limits; channels that exceed capacity trigger a `DAQConfigError`.

**Future: Hybrid mode**

A future `acquisition_mode: hybrid` will allow SHORT_UP and DAQ to run concurrently on the same session -- HIGH/LOW channels polled via SHORT_UP while DAQ-assigned channels stream via DTOs. This requires bench validation that the ECU accepts SHORT_UP commands while DAQ lists are active.

**Parallel Worker Threads (v2.x+):**

When multiple devices are configured, the plugin spawns one daemon worker thread per device context (default behavior). Each thread exclusively owns its device's NixnetSession and CcpProto -- no cross-session CCP I/O from other threads. This allows SHORT_UP blocking waits on different CAN buses to overlap, targeting ~2x combined throughput with two ECUs on separate interfaces.

- Config key: `use_parallel_workers: true` (default). Set to `false` to fall back to sequential single-thread behavior.
- Single-device setups: parallel mode spawns one thread -- functionally identical to sequential.
- Thread safety: per-device values are written to thread-local dicts (`ctx["_local_values"]`) and merged into the global snapshot under `_state_lock` each iteration.
- Clean shutdown: `stop()` signals all threads via a shared Event, then joins each within 2 seconds.
- Connection test: `run_connection_test` rejects while workers are alive (stop the plugin first).

**General notes:**
- Worker thread(s) perform connect/reconnect and acquisition independently of core tick.
- Core tick samples `_snapshot_values` (latest-value sample-and-hold).
- SHORT_UP poll timeout honors `short_up_timeout_s` (default 30ms, top-level or per-device in YAML). It has a 5ms minimum and never exceeds `io_timeout_s`. Super users can tune per-device if ECU response times differ across buses. Occasional poll fails under CAN bus contention are normal.
- SHORT_UP CRM handling accepts normal success responses and CCP notification acknowledgments (`0x30`-`0x33`) when the command counter matches. This matches the DAQ/setup command path and prevents notification responses such as `0x32` from being misclassified as timeouts.
- `short_up_debug_misses: true` enables capped diagnostic logging for failed SHORT_UP attempts, including channel name, address, expected counter, and sampled RX payloads. Use for troubleshooting only; disable after validation.
- Freshness telemetry tracks channel age against realistic expected sweep timing.

**Config dialog:**
- The channel table uses plain table cells; double-click the `Tier` cell to select `High Poll` / `Low Poll` / `DAQ 1ms` / `DAQ 10ms` / `DAQ 50ms` / `DAQ 100ms`.
- The "Channel Allocation" section shows a SHORT_UP channel summary (high/low counts) and DAQ tier capacity bars (only visible when DAQ tiers are assigned).
- A "Target Poll Rate" spinner (1-50 Hz) shows estimated per-channel Hz and budget utilization for SHORT_UP channels.
- DAQ tier capacity bars show per-tier ODT usage. Save is blocked if any DAQ tier exceeds the 90% ODT utilization cap.
- The "Show selected channels only" toggle narrows the A2L channel table to checked rows.

### Console Health and Messages

**Tile health (red/green):**

The CCP plugin tile in the main console reflects runtime health, not just config validity:

| State | Tile Color | Condition |
|-------|-----------|-----------|
| Green | `#27ae60` | Connected to ECU and data flowing (`CCP/health_ok=1`, `CCP/conn_ok=1`) |
| Red "Disconnected" | `#c0392b` | Not connected to ECU (`CCP/conn_ok=0`) |
| Red "Error" | `#c0392b` | Connected but no data flowing (`CCP/conn_ok=1`, `CCP/health_ok=0`) — e.g., unlock failed, all polls timing out |
| Grey "Unknown" | `#888888` | Core telemetry not arriving (no connection to orchestrator) |

Health keys are published in `_append_diag_values()` and pass through the orchestrator's `_strip_debug_keys()` filter via the `/health_ok` and `/conn_ok` suffix allowlist.

**Console messages:**

Key CCP lifecycle events are displayed in the console's Messages box (below the plugin tiles). Messages are queued thread-safely from the worker thread, drained during `simulate_step()`, forwarded by the orchestrator to the ZMQ `status` topic, and handled by the console UI.

| Event | Message |
|-------|---------|
| Connection successful | `[CCP] Connected to {name}` |
| Unlock successful | `[CCP] {name}: Unlock OK` |
| Unlock failed | `[CCP] {name}: Unlock failed - {reason}` |
| SHORT_UP polling started | `[CCP] {name}: Polling {n} channels` |
| DAQ streaming started | `[CCP] {name}: DAQ streaming {n} channels` |
| Connection lost | `[CCP] {name}: Connection lost` |
| DAQ setup failed | `[CCP] {name}: DAQ setup failed - {reason}` |
| Missing CAN interface | `[CCP] {name}: No CAN interface configured or available. Open CCP config and select a detected CAN interface.` |

Per-poll failures, RTT diagnostics, and freshness warnings remain terminal-only to avoid message flooding.

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
use_parallel_workers: true              # one thread per device (default true); false for sequential fallback
poll_default_priority: low              # "low" (default) or "high" for SHORT_UP; "10ms" etc. for DAQ
target_poll_hz: 10                      # target update rate per SHORT_UP channel (1-50)
high_low_ratio: 3                       # HIGH:LOW polling ratio (default 3:1, super-user adjustable 1-20)
io_timeout_s: 0.05                      # base CCP response timeout; caps short_up_timeout_s
short_up_timeout_s: 0.030               # top-level default SHORT_UP response timeout
short_up_debug_misses: false            # troubleshooting only; logs failed SHORT_UP payload samples
acquisition_mode: short_up              # "short_up" (default) or "daq" (opt-in DAQ streaming)
fallback_short_up: false
acquisition:
  mode: short_up
  fallback_short_up: false
  seed_resource: "0x02"
  sec_type: DAQ
  tier: 100ms                           # default DAQ tier (only used when acquisition_mode: daq)
  prescaler: 1
  max_odt_utilization_pct: 90          # cap per-tier ODT usage (only used in DAQ mode)
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
      access_key: ""                   # enter in config dialog (session-only, not saved to disk)
      seed_endian: big
      sec_type: CAL
    a2l:
      path: C:/path/to/file.a2l
    short_up_timeout_s: 0.030             # optional per-device override; min 5ms, capped by io_timeout_s
    short_up_debug_misses: false          # optional per-device override for SHORT_UP miss diagnostics
    acquisition_mode: short_up
    acquisition:
      mode: short_up
      seed_resource: "0x02"
      sec_type: DAQ
      tier: 100ms
      prescaler: 1
      max_odt_utilization_pct: 90
      # daq_ena_address: "0x00147cef"  # uncomment for v577 ECUs (DAQ mode only)
      # daq_ena_value: 2               # CAN1 - 10ms (v577 only)
    measurements:
      naming_prefix: CCP_
      list:
        - name: rpm
          enabled: true
          priority: high               # promoted to SHORT_UP High Poll
        - name: coolant_temp
          enabled: true
          priority: low                # SHORT_UP Low Poll (default)
```

Compatibility:
- Top-level `session/security/a2l/measurements` keys remain supported and are mirrored from the first device by UI save.

#### Validation Rules
- Device config must resolve to at least one configured device.
- Enabled aliases must be unique within CCP plugin.
- In real mode, each configured device with a non-blank interface requires:
  - tx/rx IDs
  - access key (config or env var)
  - existing A2L path
  - selected measurement names resolvable in A2L with valid addresses
- Config dialog save requires a non-blank CAN interface when hardware discovery returns available interfaces.
- If no CAN hardware is discovered, the dialog may save blank interfaces so the user can exit config; runtime then reports disconnected/red with a one-time console message.

### UI Flow
- Right-click CCP tile → Configure:
  1) Add/remove device tabs (max two)
  2) Set device role (`Primary`/`Secondary`) and session/security fields, including CAN interface dropdown
  3) Select A2L and load channels
  4) Filter + check measurements and assign each selected channel to a DAQ tier (double-click to cycle: `10ms`/`50ms`/`100ms`/`1ms`/`High`/`Low`)
  5) Monitor per-tier capacity bars in the "DAQ Tier Capacity" section
  6) Save (blocked if any tier exceeds capacity) and reload plugin
- CAN interface dropdown behavior:
  - Populated from the same shared discovery helper used by the CAN plugin.
  - Each device tab can select from the discovered CAN interfaces.
  - Saved YAML interface is preselected only if it matches discovered hardware.
  - If saved YAML does not match discovered hardware, the dropdown starts blank but still lists discovered interfaces.
  - If no hardware is discovered, the dropdown remains blank and the dialog can still be saved/closed.
- Test button sends `ccp_test` control request and shows step status in dialog terminal.

### Outputs and Metadata
Additional diagnostics channels:
- `CCP/conn_ok`, `CCP/health_ok` (tile health — pass through to telemetry for console display)
- `CCP/connected`, `CCP/state_code`, `CCP/connect_attempts`, `CCP/connect_ok`
- `CCP/unlock_ok`, `CCP/poll_success`, `CCP/poll_fail`
- `CCP/last_seed_status`, `CCP/last_rc`, `CCP/ctr_mismatch`
- `CCP/fresh_age_s`, `CCP/fresh_max_channel_age_s`
- `CCP/freshness_state_code`, `CCP/freshness_warn_count`, `CCP/freshness_stale_count`
- `CCP/bus_load_pct`, `CCP/poll_rtt_avg_ms`
- `CCP/high_priority_budget_pct`, `CCP/high_priority_over_budget`
- `CCP/short_up_rtt_last_ms`, `CCP/short_up_rtt_min_ms`, `CCP/short_up_rtt_max_ms`
- `CCP/short_up_timeout_count`, `CCP/crm_error_count`
- `CCP/poll_selected_count`, `CCP/poll_loop_ms`
- `CCP/attempted_reads_per_sec`, `CCP/successful_reads_per_sec`, `CCP/estimated_sweep_s`
- `CCP/rx_read_calls`, `CCP/rx_empty_reads`, `CCP/rx_read_calls_per_response`, `CCP/rx_predrain_ms`, `CCP/rx_mode_code`
- `CCP/daq_enabled`, `CCP/daq_running`, `CCP/daq_setup_ok`, `CCP/daq_fallback_active`
- `CCP/daq_dto_count`, `CCP/daq_dto_rate_hz`, `CCP/daq_odt_count`, `CCP/daq_active_list_count`, `CCP/daq_decode_errors`
- `CCP/daq_last_pid`, `CCP/daq_last_dto_id`

### DAQ Streaming Notes
- DAQ streaming is enabled for **all** configured devices with `acquisition_mode: daq`.
- **Multi-list DAQ**: channels are distributed across multiple DAQ lists based on their assigned tier (10ms, 50ms, 100ms, etc.). Each tier's channels are independently packed into their respective ECU DAQ list. All lists stream concurrently.
- **ODT capacity and utilization cap**: Each DAQ list has an independent ODT capacity reported by `GET_DAQ_SIZE`. The plugin enforces a **90% ODT utilization cap** by default (`max_odt_utilization_pct`, configurable in `acquisition` block). Running at 100% caused unreliable data on v577/v661 ECUs because the ECU has no scheduling headroom. Enforcement is layered:
  - **UI config dialog**: progress bars turn red and display "OVER 90% LIMIT" when a tier exceeds the cap. A bold warning appears below the bars. Save is blocked until all tiers are within the limit.
  - **Runtime**: `_build_multi_daq_plan` raises `DAQConfigError` (which bypasses SHORT_UP fallback) naming the overflow channels and suggesting redistribution.
  - **Hard overflow**: if a tier needs more ODTs than the ECU physically supports, the plugin raises immediately regardless of utilization cap.
- A PID lookup table maps each PID integer to its plan entries across all active lists for O(1) decode.
- Only 1-, 2-, and 4-byte measurements are packed. Unsupported sizes are rejected.
- The plugin sends `START_STOP stop` for each active list before setup and during cleanup, followed by `START_STOP_ALL` (CCP 0x08, mode=0).
- After per-list `START_STOP start`, the plugin sends `START_STOP_ALL` (CCP 0x08, mode=1) to ensure ECUs that require it begin streaming. Failure of `START_STOP_ALL` is non-fatal (logged only) since some ECUs do not require it.

### Fallback Behavior

The plugin distinguishes between **configuration errors** and **communication errors**:

- **Configuration errors** (`DAQConfigError`): ODT capacity exceeded, tier over utilization cap, channel assigned to a non-existent DAQ list, or no enabled channels. These **always raise** regardless of `fallback_short_up` -- the user must fix the configuration. The UI blocks save when tiers exceed the cap, so these should only occur if the YAML is hand-edited.
- **Communication errors** (`RuntimeError`): ECU unlock rejected, GET_DAQ_SIZE failed, timeout, no seed response, etc. These respect the `fallback_short_up` setting.

**Fallback rules:**
- **Default**: `fallback_short_up: false`. If a communication error occurs during DAQ setup, the plugin enters an error state with a specific, actionable error message (e.g., "DAQ unlock rejected (rc=53) -- verify access_key and sec_type"). No data flows until the user fixes the issue. This prevents silent data quality degradation.
- **Super user override**: Set `fallback_short_up: true` in `ccp.yaml` (YAML only, not exposed in UI). When enabled and a communication error occurs, the plugin falls back to SHORT_UP polling and prints a persistent warning every ~1 second including the estimated sample rate (e.g., `WARNING: SHORT_UP fallback active -- estimated sample rate: ~2.3 Hz (45 channels)`).
- DAQ and SHORT_UP never run simultaneously for the same device.

### ECU-Specific DAQ Initialization

Different ECU firmware versions may require different initialization sequences before DAQ streaming activates. The plugin handles this automatically based on config.

#### CCP_DAQ_ena Gate (v577)

The v577 ECU has a calibration parameter `CCP_DAQ_ena` (CHARACTERISTIC in the A2L, address `0x00147cef`) that acts as a software gate for DAQ streaming:

| Value | Mode |
|-------|------|
| 0 | Disabled (default) |
| 1 | CAN1 - 5ms |
| 2 | CAN1 - 10ms |
| 3 | CAN2 - 5ms |
| 4 | CAN2 - 10ms |
| 5 | CAN3 - 5ms |
| 6 | CAN3 - 10ms |

If this parameter is `0`, the ECU will accept all DAQ setup commands (GET_DAQ_SIZE, SET_DAQ_PTR, WRITE_DAQ, START) without error, but will never transmit DTO frames. The old LabVIEW tool writes this parameter during its DAQ initialization.

**Writing requires CAL privilege** (resource 0x01), not DAQ privilege (resource 0x02). The plugin performs a dual-unlock when `daq_ena_address` is configured:

1. CONNECT
2. CAL unlock (resource=0x01, sec_type=CAL from top-level `security` block)
3. SET_S_STATUS (0x83 = RUN|DAQ|CAL) -- must come before DNLOAD
4. SET_MTA + DNLOAD to write the `daq_ena_value` to `daq_ena_address`
5. DAQ unlock (resource=0x02, sec_type=DAQ from `acquisition` block)
6. DAQ list setup (STOP, GET_DAQ_SIZE, SET_DAQ_PTR, WRITE_DAQ, START, START_STOP_ALL)

The DNLOAD may return CCP notification code `0x32` ("DAQ list init request"), which is an acknowledgment with a request to proceed with DAQ list initialization -- not an error.

**Config example (ccp.yaml acquisition block):**
```yaml
acquisition:
  daq_ena_address: '0x00147cef'
  daq_ena_value: 2  # CAN1 - 10ms
```

#### v661 ECU (no gate)

The v661 ECU has no `CCP_DAQ_ena` parameter. DAQ works with only a DAQ unlock. When `daq_ena_address` is not configured, the dual-unlock and DNLOAD are skipped entirely.

#### How to identify if an ECU needs the gate

Search the A2L for a CHARACTERISTIC named `CCP_DAQ_ena`. If present, note its address and configure `daq_ena_address` and `daq_ena_value` in the acquisition block. The value depends on which CAN bus the ECU is connected to and the desired base raster.

#### CCP Notification Codes

CCP return codes 0x30-0x33 are **notification codes**, not errors:
- 0x30 = Cold Start Request
- 0x31 = CAL Data Init Request
- 0x32 = DAQ List Init Request
- 0x33 = Code Update Request

The plugin treats these as ACK + warning: the command succeeded, and the ECU is requesting an additional action. Notifications are logged but do not abort the setup sequence.

### NI-XNET Session Modes and DTO Filtering

**DAQ mode** uses `FrameInStreamSession` (`force_stream_rx=True`), which receives all CAN frames on the bus regardless of CAN ID. **SHORT_UP mode** uses `FrameInQueuedSession` (or stream fallback), filtered to the CRM response ID (`rx_id`).

**DTO CAN ID filter:** The plugin receives all frames (`only_id=None`) but pre-filters every frame by CAN ID before PID matching. During DAQ setup, the plugin builds a set of expected DTO CAN IDs from all active DAQ lists plus `0x0` (to handle NI-XNET stream sessions that may report DTO arbitration IDs as `0x00000000`). Any frame whose CAN ID is not in this set is silently discarded and counted as `filtered_out` in the diagnostic log.

**Why this matters:** Without CAN ID filtering, unrelated CAN bus traffic (other ECUs, J1939 broadcasts, etc.) whose first data byte happens to match a PID in the map would be decoded as DTOs, corrupting channel values. This was observed in production when a secondary ECM was incorrectly assigned to the same CAN interface as the primary -- the secondary's broadcast frames overwrote DAQ-decoded values with garbage.

**PID-based decode (after CAN ID filter):** Each frame that passes the CAN ID filter is identified by its PID byte (first byte of payload). PIDs are unique per DAQ list and non-overlapping across lists (e.g., 10ms list: PIDs 10-19, 50ms list: PIDs 20-29).

**Periodic DAQ poll log** reports:
- `raw` -- total frames received from the NI-XNET stream
- `filtered_out` -- frames rejected by CAN ID (other bus traffic)
- `decoded` -- frames successfully matched and decoded via PID
- `pid_miss` -- frames with correct CAN ID but unrecognized PID

`SHORT_UP` polling is unaffected -- it uses queued sessions filtered to the CRM ID.

### ECU DAQ List Layout (typical v661/v577 A2L)
| Tier  | List # | Max ODTs | First PID | PID Range | Raster |
|-------|--------|----------|-----------|-----------|--------|
| 1ms   | 0      | 2        | 0         | 0-1       | 3      |
| 10ms  | 1      | 10       | 10        | 10-19     | 2      |
| 50ms  | 2      | 10       | 20        | 20-29     | 1      |
| 100ms | 3      | 10       | 30        | 30-39     | 0      |

All lists share DTO CAN ID `0x0CFF5200`; PID ranges are non-overlapping.

### Throughput Diagnostics
For a 50-channel `SHORT_UP` setup, the practical target is `CCP/successful_reads_per_sec > 50` and `CCP/estimated_sweep_s < 1.0`.

Interpretation guide:
- `successful_reads_per_sec` below the channel count means the plugin cannot refresh every selected channel once per second with the current transport behavior.
- High `short_up_timeout_count` growth means reads are spending time in the full response timeout instead of returning promptly.
- Low RTT but low `attempted_reads_per_sec` points toward scheduler/fanout limits.
- High `rx_read_calls_per_response` or `rx_empty_reads` points toward NI-XNET receive-loop overhead or filtering behavior.
- `rx_mode_code` is `1` for queued RX sessions and `2` for stream fallback.
- The CCP config dialog `Test CCP Connection/Poll` action now runs a short throughput probe after the single-channel read and reports attempted reads/sec, successful reads/sec, timeout rate, average/p95 RTT, and estimated sweep time.

### Troubleshooting and Lessons Learned

This section documents issues encountered during production testing and their resolutions.

#### Incorrect channel values with multi-ODT DAQ lists

**Symptom:** Some channels (e.g., IAT, HM_RAM_seconds) display wrong values. Values are correct when only 1-2 channels are active, but break when 3+ channels are packed into the same DAQ tier (requiring multiple ODTs).

**Root cause:** The NI-XNET `FrameInStreamSession` receives **all** CAN bus traffic, not just DTO frames. Before CAN ID filtering was added, any frame on the bus whose first data byte happened to match a PID would be incorrectly decoded as a DTO. With more ODTs (more PIDs), the probability of a random frame matching a PID increased, causing frequent value corruption.

**Fix:** DTO CAN ID pre-filtering (see "NI-XNET Session Modes and DTO Filtering" above). The plugin now only attempts PID decode on frames with expected DTO CAN IDs.

**Related issue:** A secondary ECM configured on the wrong CAN interface (CAN1 instead of CAN2) caused its broadcast frames to appear on the primary bus, greatly amplifying the cross-traffic problem. Always verify each device's `session.interface` matches its physical CAN connection.

#### ODT capacity saturation causes unreliable data

**Symptom:** With large channel lists (45+ channels), some tiers are packed to 100% ODT capacity. Data appears correct for most channels but a few periodically show garbage values.

**Root cause:** When an ECU's DAQ list uses all available ODTs, the ECU has no scheduling headroom. DTO frames may be delayed, reordered, or dropped, leading to partial or stale data being decoded.

**Fix:** The `max_odt_utilization_pct` cap (default 90%) prevents packing a tier beyond a safe threshold. The UI config dialog enforces this visually (red bars and blocked save) and the runtime raises `DAQConfigError` if exceeded. If a tier is near capacity, move some channels to a different tier.

#### DAQ setup error silently caught by SHORT_UP fallback

**Symptom:** An ODT overflow or configuration error occurs during DAQ setup, but the plugin silently falls back to SHORT_UP polling instead of alerting the user. Data streams at degraded quality without obvious indication.

**Root cause:** Configuration errors were raised as `RuntimeError`, which the `fallback_short_up` mechanism caught like any communication error.

**Fix:** Configuration-class errors now use `DAQConfigError` (a subclass of `RuntimeError`) which is specifically caught and re-raised before the fallback logic. Configuration problems always produce a hard error with an actionable message, even when `fallback_short_up: true`.

#### CCP notification codes misinterpreted as errors

**Symptom:** DAQ setup aborts with an error on return code `0x32` after writing `CCP_DAQ_ena`.

**Root cause:** CCP return codes 0x30-0x33 are notification codes (see "CCP Notification Codes" above), not errors. The ECU is acknowledging the command and requesting an additional action.

**Fix:** The plugin treats 0x30-0x33 as ACK + warning and continues the setup sequence.

#### SHORT_UP notification CRM treated as timeout

**Symptom:** Some SHORT_UP-polled measurements, observed with v577 TIPAdapt channels, remain `NaN` even though other CCP channels update. Diagnostic logging shows frames like `FF 32 <ctr> ...` arriving during the request window, but the poll attempt ends as `short_up_timeout`.

**Root cause:** The SHORT_UP path had local CRM matching logic that only accepted `rc=0x00`. Notification acknowledgments such as `0x32` were ignored even when the command counter matched. The rest of the plugin already accepted these notification codes through `_crm_match()`.

**Fix:** SHORT_UP polling now uses the same notification-aware `_crm_match()` helper as setup/DAQ commands. Responses with `0x30`-`0x33` and a matching command counter are accepted and decoded from the normal SHORT_UP payload location.

**Diagnostics:** Set `short_up_debug_misses: true` to log a capped set of failed SHORT_UP attempts with channel, address, expected counter, and sampled RX payloads. Disable after troubleshooting to avoid noisy logs.

#### Quick diagnostic checklist

1. **All channels wrong:** Check `session.interface` matches physical CAN port. Check `access_key` is correct.
2. **Some channels wrong, more break as list grows:** Check periodic log for `filtered_out` count. High `filtered_out` suggests other devices on the bus -- verify CAN interface assignments.
3. **Data streams but some tiers are erratic:** Check tier capacity in config dialog. Move channels if any tier exceeds 90%.
4. **DAQ setup fails with unlock error:** Verify `access_key`, `sec_type`, and `seed_resource` match the ECU version. v577 needs CAL unlock for `CCP_DAQ_ena`; v661 does not.
5. **DTOs received but all zeros:** For v577, ensure `daq_ena_address` and `daq_ena_value` are configured. Without the DAQ gate enabled, the ECU accepts all setup commands but never transmits.
6. **SHORT_UP channels stay NaN but logs show `FF 32 <ctr>` frames:** Verify the SHORT_UP notification matcher is active and disable `short_up_debug_misses` after confirming values are updating.

### Deferred Optimization Backlog
- **Hybrid DAQ + SHORT_UP**: within a single device context, stream channels that fit in DAQ lists while simultaneously SHORT_UP-polling overflow channels. Currently, fallback is all-or-nothing per device.
- Timeout rate reduced to ~0% after parallel workers, queued RX fix, non-blocking predrain, and 30ms default timeout. Residual occasional fails are normal CAN bus behavior and do not affect data quality.
- Reduce stale data frequency (`CCP/freshness_state_code` warn/stale transitions), especially for high-priority channels.
- Add rolling CCP health metrics (for example, success-rate window and consecutive-fail counters) to separate transient noise from sustained degradation.
- **Cold-start grace period**: some ECU measurements (e.g., adaptive/computed values like TIPAdapt) may require a longer initial timeout on first SHORT_UP access after ECU power cycle. A future enhancement could temporarily extend the timeout for channels that have never succeeded.

### Notes
- Current scope is read-only measurement polling.
- Write/calibration flows are intentionally not implemented in this plugin version.
- COMPU_METHOD types `TAB_VERB`, `TAB_INTP`, `TAB_NOINTP`, and `FORM` are not currently parsed; channels using these methods will have `coeffs=None` and fall back to limits-based scaling or raw values. In practice, verb/table methods apply to enum/status channels where raw integer values are meaningful.


