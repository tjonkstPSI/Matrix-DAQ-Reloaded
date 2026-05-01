# CCP Multi-List DAQ Production Integration

**Author:** T. Onkst
**Date:** 04282026
**Status:** Completed -- Implemented 04/28/2026. See CHANGELOG.md for follow-up hardening (DTO CAN-ID filter, ODT utilization cap, DAQConfigError, UI capacity enforcement).

---

## Overview

Integrate multi-list DAQ streaming into the production CCP plugin (`ccp.py`),
replacing the current single-list implementation. This allows all configured
channels to be distributed across multiple DAQ lists (tiers) on the ECU,
matching the behavior validated by the standalone probe tool.

Additionally, change the `fallback_short_up` default to `false` so DAQ failures
surface as hard errors rather than silently degrading to SHORT_UP polling.

---

## Motivation

The current production `_connect_daq_ctx` configures **one DAQ list** using the
majority tier among selected channels. All channels are packed into that single
list, which overflows once ODT capacity is exceeded (typically 10 ODTs / ~35
channels). Channels that don't fit are silently dropped.

The probe tool (`ccp_daq_probe.py --multi-list`) has validated on v577 that:

- 3 simultaneous DAQ lists (10ms @ 100 Hz, 50ms @ 20 Hz, 100ms @ 10 Hz) work
- Zero unknown-PID frames across all lists
- Dual-unlock + CCP_DAQ_ena write works for v577; skipped cleanly for v661
- PID-based DTO filtering (ignoring CAN ID) reliably separates all lists

---

## Root Cause Summary: Why We Had Issues Before

| Issue | Root Cause | Status |
|---|---|---|
| RC=53 on GET_DAQ_SIZE | `_send_wait_crm` spun until timeout on matched-but-error CRM | Fixed |
| 0 DTOs on v577 | `CCP_DAQ_ena` gate at 0x00147cef defaults to 0 (disabled) | Fixed (dual-unlock) |
| 0 DTOs despite setup OK | NI-XNET reports DTO CAN IDs as 0x00000000 | Fixed (PID-based) |
| All channels packed into 10ms | `_canonical_priority` collapsed all tiers | Fixed |
| DNLOAD rejected as error | Notification code 0x32 treated as hard error | Fixed |
| Wrong seed/key on v577 | Different access key than v661 | User config issue |

All of the above fixes are already in production `ccp.py`. This plan only adds
multi-list support and changes the fallback default.

---

## ECU Compatibility

### What's Universal (Same Across All ECU/A2L Versions)

- CCP protocol commands (GET_DAQ_SIZE, SET_DAQ_PTR, WRITE_DAQ, START_STOP, START_STOP_ALL)
- Seed & Key algorithm (`compute_key_from_seed_algo` with per-ECU access keys)
- A2L SOURCE / QP_BLOB structure for DAQ list metadata
- PID-based DTO decoding (payload byte 0 = PID, bytes 1-7 = data)
- 7 usable bytes per DTO (after PID byte)

### What Varies Per ECU (Handled by Config + A2L Parsing)

| Parameter | Where It Comes From | Notes |
|---|---|---|
| Access key | `security.access_key` per device | Not in A2L (security) |
| CAN IDs (tx/rx) | `session.tx_id`, `session.rx_id` per device | |
| DTO CAN IDs | A2L `CAN_ID_FIXED` in QP_BLOB | Normalized by `normalize_dto_can_id` |
| ODT count per list | A2L `LENGTH` in QP_BLOB | Validated at setup time |
| First PID per list | A2L `FIRST_PID` in QP_BLOB | Unique per list |
| Available tiers | A2L `SOURCE` blocks | Determines which lists exist |
| CCP_DAQ_ena gate | A2L `CHARACTERISTIC` (manual config) | v577 yes, v661 no |
| DAQ ena address/value | `acquisition.daq_ena_address/value` | Only needed if gate exists |

### DLLs vs Access Keys

The old tool uses vendor-provided DLLs that internally implement the seed-key
algorithm and may handle CCP_DAQ_ena writes. Our Python implementation
(`compute_key_from_seed_algo`) reimplements the same rotation logic. The DLLs
are not needed as long as the access key and CCP_DAQ_ena address are configured
correctly per ECU.

---

## Changes Required

### File: `src/plugins/ccp.py`

#### 1. Refactor `_build_daq_plan` → `_build_multi_daq_plan`

**Current behavior (lines 756-825):**
- Groups all entries, picks majority tier, packs into one list
- Returns a flat `List[Dict[str, Any]]` (single plan)
- Overflow channels silently logged

**New behavior:**
- Groups entries by their assigned tier (from `priority` / `poll_tier` field)
- For each tier that has channels AND exists in the A2L DAQ lists:
  - Pack entries into ODTs for that list
  - Validate against A2L ODT capacity
  - If a tier overflows, raise a specific error (not silent)
- Returns a `List[DaqListPlan]` where each plan contains:
  - `tier`: str (e.g. "10ms")
  - `list_num`: int (from A2L)
  - `event_ch`: int (raster from A2L)
  - `first_pid`: int (from A2L, confirmed by GET_DAQ_SIZE)
  - `cmd_dto`: int (CAN ID for GET_DAQ_SIZE command)
  - `entries`: List[Dict] (packed with odt/offset)
  - `last_odt`: int
  - `n_channels`: int
- Tiers with 0 channels are skipped
- If a channel's tier has no matching A2L DAQ list, raise an error:
  `"Channel X assigned to tier 1ms but ECU has no 1ms DAQ list (available: 10ms, 50ms, 100ms)"`

**Overflow error format:**
```
DAQ 10ms list needs 17 ODTs but ECU allows 10 -- move 15 channels to 50ms or 100ms tier
```

#### 2. Update `_connect_daq_ctx` to Loop Over Plans

**Current behavior (lines 827-1046):**
- Calls `_build_daq_plan` (single plan)
- Configures one list: STOP → GET_DAQ_SIZE → SET_DAQ_PTR/WRITE_DAQ → START → START_STOP_ALL
- Builds one PID map entry set

**New behavior:**
- Calls `_build_multi_daq_plan` (multiple plans)
- For each plan in tier_order (fastest first):
  1. STOP (fire-and-forget, continue on error)
  2. GET_DAQ_SIZE (validate ODT capacity, capture ecu_first_pid)
  3. SET_DAQ_PTR + WRITE_DAQ for all entries in this list
  4. START (list_num, last_odt, event_ch, prescaler)
  5. Print per-list setup summary
- After all lists configured: START_STOP_ALL (mode=1)
- Build unified PID map spanning all lists
- Populate `ctx["daq_active_lists"]` with one entry per configured list
- Verbose print for each phase (matching probe output style)

**Sequence diagram:**
```
CONNECT
├─ [if daq_ena_address configured]
│  ├─ CAL GET_SEED + UNLOCK
│  ├─ SET_S_STATUS
│  └─ SET_MTA + DNLOAD (write CCP_DAQ_ena)
├─ DAQ GET_SEED + UNLOCK
├─ [if set_s_status and not already sent]
│  └─ SET_S_STATUS
│
├─ For each tier (10ms, 50ms, 100ms):
│  ├─ STOP (list_num) [fire-and-forget]
│  ├─ GET_DAQ_SIZE (list_num, cmd_dto)
│  ├─ For each entry:
│  │  ├─ SET_DAQ_PTR (list_num, odt, element)
│  │  └─ WRITE_DAQ (size, address, extension)
│  └─ START (list_num, last_odt, event_ch, prescaler)
│
└─ START_STOP_ALL (mode=1)
```

#### 3. No Changes to `_poll_daq_ctx`

Already uses `only_id=None` and PID-based matching via `ctx["daq_pid_map"]`.
The PID map will simply contain more entries from multiple lists. PIDs are
guaranteed unique across lists by the A2L (each list has non-overlapping
FIRST_PID + ODT ranges).

#### 4. No Changes to `_stop_daq_ctx`

Already loops over `ctx["daq_active_lists"]` and sends STOP for each. The
existing START_STOP_ALL(mode=0) call can be added for completeness.

#### 5. Change `fallback_short_up` Default to `false`

**Current:**
- `fallback_short_up` defaults to `True` (line 158, 197, 281)
- DAQ failure silently falls back to SHORT_UP polling

**New:**
- `fallback_short_up` defaults to `False`
- DAQ failure raises a hard error, plugin enters error state
- Error message is specific and actionable
- `fallback_short_up: true` remains available as YAML-only super user config
  (not exposed in UI dialog)

#### 6. Improve Error Messages

All DAQ errors should be specific and point the user to what to fix:

| Error Scenario | Message Format |
|---|---|
| ODT overflow | `"DAQ {tier} needs {n} ODTs, ECU allows {max} -- reduce channels in {tier} tier or redistribute to other tiers"` |
| No A2L list for tier | `"Channel '{name}' assigned to {tier} but ECU has no {tier} DAQ list (available: {list})"` |
| Unlock rejected | `"DAQ unlock rejected (rc={rc}) -- verify security.access_key for this ECU"` |
| No seed response | `"No DAQ GET_SEED response -- check CAN wiring, interface config, or ECU power"` |
| GET_DAQ_SIZE fail | `"GET_DAQ_SIZE failed for list {n} (rc={rc}) -- ECU may not support this DAQ list"` |
| CCP_DAQ_ena write fail | `"CCP_DAQ_ena write rejected (rc={rc}) -- verify daq_ena_address/value in config"` |

#### 7. SHORT_UP Fallback Warning (When Enabled)

When `fallback_short_up: true` is set and DAQ fails:

- On trigger (once):
  ```
  [CCP:Primary] WARNING: DAQ mode failed ({reason}), running SHORT_UP fallback
  ```

- Periodic (every 5s, in the SHORT_UP poll throughput log):
  ```
  [CCP:Primary] WARNING: SHORT_UP fallback active -- estimated sample rate: ~2.3 Hz (45 channels)
  ```

The estimated rate comes from the existing `successful_reads_per_sec` divided by
channel count (already tracked as `estimated_sweep_s`). We convert sweep time to
an intuitive per-channel update rate:
`rate = 1.0 / estimated_sweep_s` if sweep > 0.

---

## Files Modified

| File | Change |
|---|---|
| `src/plugins/ccp.py` | Refactor `_build_daq_plan`, update `_connect_daq_ctx`, change fallback default, add error messages, add fallback warning |
| `docs/plugins/ccp.md` | Update DAQ streaming section for multi-list, document fallback behavior change |
| `CHANGELOG.md` | New entry for multi-list DAQ and fallback change |

---

## Files NOT Modified

| File | Reason |
|---|---|
| `src/plugins/_ccp_a2l.py` | Already parses multi-list metadata correctly |
| `src/plugins/_ccp_protocol.py` | Already has all needed command builders |
| `src/ui/widgets/ccp_config.py` | No UI changes needed; tier capacity bars already work per-tier |
| `configs/ccp.yaml` | Per-channel `priority` field already defines tiers; no schema change |

---

## Implementation Order

### Step 1: Refactor `_build_daq_plan` → `_build_multi_daq_plan`
- Add `DaqListPlan` dataclass or typed dict
- Group entries by tier
- Pack each tier independently with ODT/offset tracking
- Return list of plans
- Validate capacity per list (error if overflow)

### Step 2: Update `_connect_daq_ctx` for Multi-List Loop
- Replace single-list setup with loop over plans
- Configure each list sequentially (STOP, GET_DAQ_SIZE, SET_DAQ_PTR/WRITE_DAQ, START)
- START_STOP_ALL at the end
- Build unified PID map from all lists
- Populate `daq_active_lists` with all list metadata

### Step 3: Change Fallback Default and Add Warnings
- Set `fallback_short_up` default to `False` in all three locations
- Update error message in DAQ setup exception handler to be specific
- Add periodic SHORT_UP fallback warning with estimated sample rate

### Step 4: Update `_stop_daq_ctx`
- Add START_STOP_ALL(mode=0) after per-list STOP commands

### Step 5: Update Documentation
- `docs/plugins/ccp.md`: multi-list behavior, fallback change, error messages
- `CHANGELOG.md`: new entry

---

## Testing Plan

1. **v577 ECU, 3 tiers** -- same channel distribution as probe test
   - Expect: 3 active lists, correct rates, zero decode errors
2. **v577 ECU, single tier** -- all channels on 10ms
   - Expect: 1 active list, same as current behavior
3. **v661 ECU** -- confirm no-gate path still works (no daq_ena_address)
   - Expect: DAQ unlock only, no CAL unlock or DNLOAD
4. **Overflow test** -- assign too many channels to one tier
   - Expect: specific error message, plugin does NOT fall back silently
5. **Fallback test** -- set `fallback_short_up: true`, force DAQ failure
   - Expect: WARNING printed with estimated rate, data still flows via SHORT_UP

---

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Multi-list breaks v661 | v661 has no CCP_DAQ_ena; dual-unlock path already skips when not configured |
| New A2L version missing SOURCE blocks | `parse_a2l_daq_lists` returns empty dict; error message tells user no DAQ lists found |
| PID collisions across lists | Impossible per CCP spec; each list has non-overlapping FIRST_PID ranges in A2L |
| START_STOP_ALL not supported by ECU | Already handled with try/except (skip on error) |
| Users surprised by hard error (no fallback) | Error messages are specific and actionable; super user override documented |
