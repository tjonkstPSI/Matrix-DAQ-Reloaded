<!-- Author: T. Onkst | Date: 03092026 -->

# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased] - 03/09/2026

### CCP SHORT_UP throughput optimization — 05/01/2026
#### Added
- **Parallel worker threads**: one daemon thread per device context for concurrent SHORT_UP polling. Config key `use_parallel_workers: true` (default). Per-device values written to thread-local dicts and merged into global snapshot under `_state_lock` each iteration. Clean shutdown joins all threads within 2 seconds.
- **SHORT_UP timing diagnostics**: detailed per-attempt instrumentation (`predrain_ms`, `send_ms`, `recv_loop_ms`, `total_ms`, `cap_ms`, `slop_ms`, `outcome`) stored in `ctx["_last_sup_timing"]`. Rolling `_timing_window` deque (200 samples) drives periodic median/P95/timeout-rate summaries. Connect-time banner prints resolved config. 7 new `CCP/sup_*` diagnostic telemetry keys (stripped by orchestrator).
- **`debug_timing` config flag**: enables verbose per-attempt logging for anomalous SHORT_UP attempts (env var `CCP_DEBUG_TIMING=1` also accepted).
- **`short_up_timeout_s` per-device config**: super-user adjustable SHORT_UP response timeout per device in YAML (clamped 5-50ms, never exceeds `io_timeout_s`).
#### Changed
- **Queued RX session fix**: `FrameInQueuedSession` in `_ccp_protocol.py` now uses per-interface unique cluster and frame names (`CCP_Net_{interface}`, `CCP_Rx_{interface}`), fixing NI-XNET `0xBFF63133` name collision that forced all multi-device setups into stream fallback.
- **Non-blocking predrain**: `NixnetSession.recv()` now supports `timeout_s=0` for truly non-blocking reads. Predrain call changed from 1ms blocking to non-blocking, eliminating ~10ms driver-level wait overhead per SHORT_UP attempt with queued RX sessions.
- **Default `short_up_timeout_s` raised from 15ms to 30ms**: matches observed ECU response times (P95 ~28-30ms). Eliminates timeout waste for channels that respond in 20-26ms.
#### Performance
- Combined throughput: ~30 reads/sec → **100+ reads/sec** (~3.3x improvement).
- Predrain overhead: 10-12ms → **<0.1ms**.
- Timeout rate: ~35% → **0%**.
- All TIPAdapt channels (previously permanent NaN) now streaming after ECU address warm-up at 30ms timeout.

### Main Test Monitor Display — 04/29/2026
#### Added
- **Main Test Monitor Display**: New composite display window optimized for 1080p screens, featuring a live rolling plot, AO controls, configurable watch table, standard test info panel, and alarm/warning message terminal.
- **Live rolling plot** (`_PlotPanel`): Powered by `matplotlib` (`FigureCanvasQTAgg`). Supports up to 4 independent Y axes via `twinx()` with offset right-side spines. Users select channels to plot, assign each to a Y axis (1-4), and pick line colors via a configuration dialog. X axis is a rolling time window with 5 presets (10s, 30s, 60s, 2.5min, 5min). Data stored in per-channel ring buffers. Redraws throttled to 10 Hz for smooth performance.
- **Standard info table** (`_StandardInfoPanel`): Always displays Engine Speed, Power (live telemetry with alias fallback: `cSP_Eng`/`emasterrpm`/`eslaverpm` for speed, `xPO_GenAvg`/`lPO_LdbAct` for power), plus Engine Type, Engine Serial, Operator, and Test Type from `engine_test.yaml` lock metadata. Speed and Power rows reflect alarm/warning coloring.
- **User watch table** (`_WatchPanel`): User selects from all enabled telemetry channels via a checkbox picker. Live values update with alarm/warning row coloring via `apply_alarm_state_to_row()`.
- **AO control panel**: Reuses `_AOPanel` from `channels_table.py` — same spin boxes, Set buttons, confirmation dialogs, and IPC write-back.
- **Alarm terminal** (`_AlarmTerminal`): Read-only scrolling log (dark theme, monospace) that displays timestamped alarm/warning transitions from the telemetry `alarm_events` payload. Shows WARNING, ALARM, and CLEARED messages. Capped at 500 lines.
- **Configuration persistence**: Plot channels (alias, Y axis, color), time window, and watch channel selections saved to `configs/test_monitor_display.yaml` and restored on next launch.
- **Display registration**: Registered as `"MainTestMonitor"` in the console display factory. Available in the Launch Configuration dialog alongside AllChannelsTable. Both displays can run simultaneously (one per monitor).
- **matplotlib dependency**: Added `matplotlib>=3.8.0` to `requirements.txt` (replaced `pyqtgraph` which had rendering/compatibility issues in the target environment).

### AO cache invalidation and scaling support — 04/29/2026
#### Added
- **AO scaling (linear & table)**: Analog output channels now support the same `scaling` configuration as AI voltage channels. Users can configure gain/offset or table-point scaling so the UI accepts engineering units (e.g., valve position %) while the plugin writes the corresponding raw voltage to hardware.
- **`inverse_scaling()`**: New function in `_nidaq_scaling.py` that inverts `apply_scaling()` — linear: `(eng - offset) / gain`, table: interpolation with swapped axes. Used by `write_ao()` to convert engineering-unit commands to raw voltage.
- **AO config dialog scaling column**: The NI DAQ config dialog's Analog Output tab now includes a "Scaling" column. Double-clicking opens the same `ScalingEditorDialog` used for AI voltage channels. Full scaling dicts (type, gain, offset, unit, points, extrapolate) are persisted to YAML. Existing `range_v` values are preserved on save instead of being overwritten with defaults.
#### Changed
- **AO panel uses engineering ranges**: When scaling is configured, the AO panel spin box min/max reflect engineering-unit limits (computed via `apply_scaling` on the raw `range_v`). Readback telemetry values are displayed in engineering units.
- **AO cache invalidation**: Closing the NI DAQ config dialog with OK now invalidates the AO metadata cache in the console, so added/removed/changed AO channels are reflected in the All Channels Table without restarting.
- **`write_ao()` applies inverse scaling**: The NI DAQ plugin stores raw voltage in `_ao_states` after applying `inverse_scaling()` on the engineering-unit value received from the UI. Forward scaling is applied at all readback merge points so telemetry always reports engineering units.

### Analog Outputs panel in All Channels Table — 04/29/2026
#### Added
- **Analog Outputs panel**: New `_AOPanel` in the All Channels Table displays enabled AO channels at the top of the display with per-channel `QDoubleSpinBox` controls (min/max clamped to `range_v` from NI DAQ config), unit labels, and Set buttons for writing values to hardware.
- **AO write confirmation**: Clicking "Set" shows a confirmation dialog ("Set {alias} to {value} {unit}?") before sending the `ao_write` IPC message to the orchestrator.
- **Readback from telemetry**: AO spin boxes update from live telemetry values, but only when the spin box does not have focus (prevents fighting user input mid-edit).
- **AO metadata caching**: Console reads AO channel metadata (alias, unit, range) from `ni_daq.yaml` once and caches it; `invalidate_ao_cache()` available for future reload wiring.
- **AO alias exclusion**: AO channel aliases are excluded from the normal read-only category panels to avoid duplicate display.

### Calculated Channels overhaul — 04/29/2026
#### Changed
- **Block-based expression model**: Replaced single-expression-per-row (`alias + expr`) with multiline calculation blocks (`name + body + symbols + outputs`). Each block contains sequential `var = expr` assignment lines evaluated top-to-bottom; later lines can reference earlier intermediates. Only explicitly declared outputs are published as telemetry channels.
- **`SafeExprEvaluator.evaluate_block()`**: New method that processes multiline bodies line-by-line, building a scope dict so intermediate variables propagate forward. No `eval()` or `exec()` — each RHS is still parsed via `ast.parse(mode="eval")`.
- **`CalcBlock` dataclass**: Replaces `CalcItem`. Fields: `name`, `body`, `symbols`, `outputs` (list of `{var, alias, unit}`), `enabled`.
- **Auto-migration**: Legacy `expr`-format configs are auto-converted on load (`body = "result = {expr}"`, single output). No manual migration step needed.
- **Config dialog redesign**: Replaced flat calculation table with a list+detail layout — left panel shows block list with checkboxes (Add/Remove/Duplicate), right panel shows block editor with name field, enable checkbox, symbol table, multiline `QPlainTextEdit` body editor (monospace), and exposed outputs table.
- **Recipe import/export**: Export Recipe saves the selected block as a JSON file; Import Recipe loads a JSON file as a new block. JSON schema matches the YAML block schema 1:1 for future server API integration.
- **YAML migration**: Consolidated 4 separate estop-related calculation rows into a single "Estop Logic" block in `calculated_channels.yaml`.
- **Validation**: Both plugin and dialog validate body line format (`var = expr`), output variable existence in body, alias uniqueness across blocks, and symbol identifier validity.
#### Added
- **`prev(variable, steps)`**: New built-in function for accessing previous evaluation cycle values. Enables delta calculations, running totals, timers, and exponential moving averages. Per-block rolling history buffer stores up to 10 cycles; returns `0.0` when no history exists. First argument must be a variable name, second is the number of steps back.
- **`dt` built-in variable**: Automatically injected into every block — contains elapsed time in seconds since the last evaluation cycle. Combined with `prev()`, enables time-based integration and rate-of-change patterns.
- **`BlockHistory` class**: Per-block rolling history buffer backed by a `deque(maxlen=10)`. Pushes scope snapshots after each evaluation; `prev()` queries into the ring.
- **User guide**: `docs/guides/calculated_channels_help.md` — comprehensive reference covering dialog walkthrough, expression syntax, all operators/functions, `prev()`/`dt` usage, worked examples (unit conversion, estop logic, RPM delta, fuel integration), recipe import/export, and troubleshooting.

### CCP Console UI cleanup — 04/29/2026
#### Added
- **CCP tile health indicators**: CCP plugin tile in the console now reflects runtime health. Green when connected and data flowing, red "Disconnected" when ECU is unreachable, red "Error" when connected but data flow is broken (e.g., unlock failed, all polls timing out). Implemented via `CCP/conn_ok` and `CCP/health_ok` keys published from `_append_diag_values()`.
- **CCP console messages**: Key lifecycle events now display in the console Messages box — connection success, unlock OK/failed, polling started, DAQ streaming started, connection lost, and DAQ setup failures. Messages are minimal to avoid flooding; per-poll diagnostics remain terminal-only.
- **BasePlugin console messaging**: `_console_msg()` and `_drain_console_msgs()` added to `BasePlugin` (`base.py`) as a thread-safe queuing mechanism. Any plugin can now send messages to the console Messages box. Messages are drained during `simulate_step()` via a `__console_msgs__` key, forwarded by the orchestrator to the ZMQ `status` topic as `plugin_message` type, and handled by `ConsoleWindow._handle_status_msg`.
#### Changed
- **Default priority flipped to LOW**: `poll_default_priority` changed from `high` to `low` across plugin, UI, and YAML config. New channels start as Low Poll; users promote important channels to High Poll as needed.
- **Configurable HIGH:LOW ratio**: `high_low_ratio` (default 3, range 1-20) added to `ccp.yaml` for super-user tuning of the weighted round-robin scheduler. Exposed in the poll config terminal log.

### CCP SHORT_UP default + poll engine overhaul — 04/28/2026
#### Changed
- **Default acquisition mode**: Changed from `daq` to `short_up`. SHORT_UP polling with HIGH/LOW priority is now the primary acquisition path, matching the behavior of the legacy LabVIEW tool.
- **Priority namespace**: `"high"` and `"low"` are now first-class SHORT_UP priority values, no longer aliased to DAQ tiers (`10ms`/`100ms`). Added `is_daq_tier()` helper in `_ccp_a2l.py`.
- **Poll engine**: Replaced fixed `poll_interval_ms` timer with continuous polling governed by `target_poll_hz` (default 10 Hz per channel). Removed hard cap of 6 on `poll_channels_per_tick`.
- **Throughput reporting**: Periodic terminal log shows `reads/sec`, estimated Hz per HIGH/LOW priority, and budget utilization %.
- **UI tier dropdown**: Options changed from `10ms/50ms/100ms/1ms` to `High Poll / Low Poll / DAQ 1ms / DAQ 10ms / DAQ 50ms / DAQ 100ms`.
- **UI channel allocation**: Capacity section shows SHORT_UP channel summary + DAQ tier bars (DAQ bars hidden when no DAQ channels assigned).
- **UI target Hz**: Added `QSpinBox` for `target_poll_hz` (1-50) with live budget estimation display.
- **Default priority**: Changed from `100ms` to `high` (High Poll) across plugin, UI, and YAML config.
#### Preserved
- All DAQ code (`_connect_daq_ctx`, `_build_multi_daq_plan`, `_poll_daq_ctx`, `DAQConfigError`, ODT capacity checking) remains fully functional via `acquisition_mode: daq`.

### CCP documentation review — 04/28/2026
#### Changed
- **`docs/plugins/ccp.md`**: Rewrote "NI-XNET DTO CAN ID Reporting" → "NI-XNET Session Modes and DTO Filtering" to document the CAN ID pre-filter, `filtered_out`/`pid_miss` diagnostic counters, and why unfiltered stream mode caused cross-traffic corruption.
- **`docs/plugins/ccp.md`**: Expanded "Fallback Behavior" to document `DAQConfigError` vs `RuntimeError` distinction — configuration errors always raise, communication errors respect `fallback_short_up`.
- **`docs/plugins/ccp.md`**: Expanded "DAQ Streaming Notes" ODT cap paragraph with three-layer enforcement (UI red bars/blocked save, runtime `DAQConfigError`, hard overflow).
- **`docs/plugins/ccp.md`**: Fixed YAML examples — `fallback_short_up` now shows `false` (actual default), added `max_odt_utilization_pct` at device level, added `daq_ena_address`/`daq_ena_value` as commented-out v577 examples, added `CCP_ACCESS_KEY` env var hint.
- **`docs/plugins/ccp.md`**: Added "Troubleshooting and Lessons Learned" section covering cross-traffic corruption, ODT saturation, silent fallback bypass, notification codes, and a quick diagnostic checklist.
- **`docs/plans/ccp_multi_list_daq_production.md`**: Status changed from "Draft - Pending Review" to "Completed" with reference to follow-up hardening.

### CCP DAQ data-integrity hardening — 04/28/2026
#### Added
- **DTO CAN-ID filter**: `_poll_daq_ctx` now pre-filters received frames by expected DTO CAN IDs (built from active DAQ lists + `0x0` fallback for NI-XNET). All other bus traffic is rejected before PID matching, preventing cross-traffic from corrupting channel values.
- **`DAQConfigError` exception**: new subclass of `RuntimeError` for configuration problems (ODT cap exceeded, no valid tiers, missing channels). Always re-raised immediately, bypassing `fallback_short_up` — ensures misconfigurations always produce a hard error with an actionable message.
- **ODT utilization cap**: `_build_multi_daq_plan` enforces a configurable `max_odt_utilization_pct` (default 90%). If a tier exceeds the cap, a `DAQConfigError` names the overflow channels and tells the user to redistribute. Running at 100% caused unreliable data on v577/v661 ECUs.
- **UI capacity enforcement**: Config dialog shows red progress bars and "OVER 90% LIMIT" text when a tier exceeds the cap. Save is blocked with a descriptive error message. Constant `_MAX_ODT_UTILIZATION_PCT = 90` matches the runtime default.
- **Improved DAQ poll diagnostics**: periodic log now shows `raw` (total frames), `filtered_out` (rejected by CAN ID), `decoded` (successful DTOs), and `pid_miss` (unrecognized PIDs). Startup prints accepted DTO CAN IDs and PID map.
#### Changed
- **DAQ plan log includes utilization %**: e.g., `10ms = 15 channels in 3/10 ODTs (30%, cap=90%)`.

### CCP multi-list DAQ production integration — 04/28/2026
#### Added
- **Multi-list DAQ setup loop**: `_connect_daq_ctx` now configures multiple DAQ lists sequentially (one per tier with channels), starts each, then sends `START_STOP_ALL`. Proven sequence matches the validated probe tool.
- **Specific actionable DAQ error messages**: every failure scenario (ODT overflow, unlock rejected, no seed response, GET_DAQ_SIZE failure, CCP_DAQ_ena write failure) now produces a message telling the user what to fix.
- **SHORT_UP fallback warning with estimated rate**: when `fallback_short_up: true` is set and DAQ fails, the plugin prints a persistent per-second warning including estimated sample rate (e.g., `~2.3 Hz (45 channels)`).
- **START_STOP_ALL stop on cleanup**: `_stop_daq_ctx` now sends `START_STOP_ALL(mode=0)` after stopping individual lists.
#### Changed
- **`_build_daq_plan` replaced by `_build_multi_daq_plan`**: channels grouped by assigned tier, each tier packed independently with its own ODT/offset tracking. Raises on overflow instead of silently dropping channels.
- **`fallback_short_up` default changed to `false`**: DAQ failure now stops the device with a clear error by default. SHORT_UP fallback is a YAML-only super user option, not exposed in the UI.
- **DAQ unlock rejection now raises**: if the ECU rejects the unlock with a non-zero RC, the plugin immediately raises instead of continuing with no data.

### CCP DAQ robustness and ECU compatibility — 04/27/2026
#### Added
- **CCP notification code handling**: CRM return codes 0x30-0x33 are now treated as ACK + warning (not errors). The plugin logs the notification and continues.
- **Dual-unlock sequence**: when `daq_ena_address` is configured, the plugin performs a CAL unlock (resource 0x01) before the DAQ unlock (resource 0x02), enabling calibration writes needed by certain ECUs.
- **CCP_DAQ_ena support**: v577 ECUs have a calibration gate (`CCP_DAQ_ena`) that must be written via SET_MTA + DNLOAD to enable DAQ streaming. Configured via `acquisition.daq_ena_address` and `acquisition.daq_ena_value` in ccp.yaml.
- **SET_MTA and DNLOAD commands**: added `build_set_mta` (0x02) and `build_dnload` (0x03) to the CCP protocol layer.
- **START_STOP_ALL command**: added `build_start_stop_all` (0x08) sent after per-list START to support ECUs that require it.
- **Verbose DAQ setup logging**: every CRM during DAQ init is now printed (CAL unlock, SET_S_STATUS, SET_MTA, DNLOAD, DAQ unlock, GET_DAQ_SIZE, START, START_STOP_ALL).
- **Enhanced bus sniff**: when 0 DTOs are received, the bus sniff now prints sample payloads and extended-ID flags for the top CAN IDs.
#### Changed
- **DAQ tier derived from channels**: the active DAQ list tier is now determined by the majority tier among selected channels, not a hardcoded config value.
- **PID-based DTO filtering**: DTO frames are matched by PID byte (first payload byte) instead of CAN arbitration ID, because NI-XNET stream sessions may report DTO IDs as 0x00000000.
- **SET_S_STATUS ordering**: SET_S_STATUS is now sent before DNLOAD (required by some ECUs to accept calibration writes).

### CCP multi-list DAQ packing and per-tier capacity enforcement — 04/27/2026
#### Added
- **Multi-list DAQ streaming**: channels are grouped by assigned tier and packed into separate ECU DAQ lists (1ms/10ms/50ms/100ms). Multiple lists stream concurrently per ECU.
- **Multi-device DAQ**: DAQ streaming now works for all configured devices (primary and secondary), each independently using its own A2L's DAQ lists on its own CAN bus.
- **PID lookup table**: a unified `daq_pid_map` maps each PID to its plan entries across all active lists for efficient O(1) DTO decoding.
- **Per-tier capacity UI**: the config dialog "DAQ Tier Capacity" section shows per-tier ODT usage (e.g., `10ms: 5/10 ODTs (15ch)`) and warns when over capacity.
- **Save-time validation**: saving is blocked if any DAQ tier exceeds the ECU's reported ODT capacity. User must adjust tier assignments first.
- **DAQ tier column**: the channel table "Tier" column cycles through `10ms` / `50ms` / `100ms` / `1ms` / `High` / `Low` on double-click (default: `10ms`).
- **DAQ active list count diagnostic**: `CCP/daq_active_list_count` tracks how many DAQ lists are running across all devices.
- **DAQ setup and poll logging**: successful DAQ setup prints all active lists and their ODT counts; periodic poll status logs DTO rates across all lists.
#### Changed
- **Per-channel priority replaced by tier**: `priority` field now stores tier strings (`10ms`/`50ms`/`100ms`/`1ms`/`high`/`low`). `high` maps to `10ms`, `low` maps to `100ms` for backward compatibility.
- **Device gate removed**: DAQ is no longer restricted to `device_index == 0`; any device with `acquisition_mode: daq` will attempt DAQ streaming.
- **Stop all active lists on shutdown**: `_stop_daq_ctx` now sends `START_STOP stop` for each running list, not just a single list.
- **SHORT_UP fallback retained**: if DAQ setup fails entirely for a device, it falls back to `SHORT_UP` polling (DAQ and SHORT_UP never run simultaneously for the same device).

### CCP SHORT_UP priority polling responsiveness — 04/27/2026
#### Added
- **CCP throughput diagnostics**: added request RTT, timeout, CRM error, poll-loop, reads/sec, estimated sweep, and NI-XNET receive-loop metrics to diagnose large-channel `SHORT_UP` refresh rates.
- **CCP throughput probe**: the CCP config test flow now reports a short selected-channel throughput probe with attempted reads/sec, successful reads/sec, timeout rate, average/p95 RTT, and estimated sweep time.
- **CCP selected-channel filter**: the CCP config dialog now has a "Show selected channels only" toggle that live-filters the A2L channel table to rows checked in the Use column.
- **CCP runtime load diagnostics**: added `CCP/bus_load_pct`, `CCP/poll_rtt_avg_ms`, `CCP/high_priority_budget_pct`, and `CCP/high_priority_over_budget`.
#### Changed
- **DTO ID normalization**: A2L DTO IDs with metadata bits, such as `0x8CFF5200`, are normalized for runtime receive matching as `0x0CFF5200`.
- **Responsive CCP config loading**: the dialog now opens with shallow `MEASUREMENT` name discovery, caches A2L name scans, preserves saved metadata for selected channels, and avoids parsing conversion/unit/limit data at open.
- **Lean channel table**: tier is now a plain double-click toggle cell instead of a per-row combo widget, and very large A2L lists are capped in the table with a status message.

### NI DAQ temperature acquisition optimization and YAML super-user controls — 04/21/2026
#### Added
- **NI DAQ temperature super-user settings** in `ni_daq.yaml` under `acquisition.temperature`:
  - `adc_timing_mode` (`default|automatic|high_speed|high_resolution|best_50hz|best_60hz`)
  - `auto_zero` (`default|none|once|every_sample`)
  - `sample_rate_hz` (continuous temp task attempt rate; default 4 Hz)
- **Hardware-timed continuous temp task attempt** for `ai_temp` channels. On success, NI DAQ starts a continuous temperature task and reads latest buffered samples.
- **Automatic fallback path**: if DAQmx rejects hardware-timed temperature task setup, plugin logs the failure and transparently falls back to on-demand temperature reads.
#### Changed
- **NI DAQ snapshot slow-loop diagnostics** now report slow-path payload updates and poll count in a clearer format.
- **Default temperature ADC behavior** is now driver-controlled (`adc_timing_mode: default`, `auto_zero: default`) unless a super-user explicitly overrides in YAML.

### Cycle plugin overhaul, LoadBank onsite commissioning, NI DI/DO fixes — 04/21/2026
#### Added
- **Cycle plugin play/pause/seek/loops/restart**: `CyclePlugin` now supports full runtime control — `play()`, `pause()`, `seek(time_s)`, `set_loops(n)`, `set_start_with_test(enabled)`. State machine covers `idle`, `running`, `paused`, `complete` with clean transitions. Restarting a completed cycle via Play resets to the beginning automatically.
- **Cycle telemetry channels**: `simulate_step()` publishes `Cycle/state`, `Cycle/position_s`, `Cycle/setpoint_kw`, `Cycle/loop_current`, `Cycle/loop_total`, `Cycle/progress_pct`, and `Cycle/schedule_len_s` every tick.
- **Cycle Control UI section**: new group box in the LoadBank operator panel with Play/Pause buttons, Seek spinner, Loops spinner, Start with Test checkbox, and labels for state/position/setpoint/loop/progress. Includes a `CycleChartWidget` (QPainter step-line chart with position marker) and `QProgressBar`.
- **`CycleChartWidget`** (`src/ui/widgets/cycle_chart.py`): lightweight custom QPainter widget displaying the load schedule as a step-line chart with filled area, axis labels, grid lines, and a red vertical marker for current position.
- **Cycle-to-LoadBank setpoint piping**: orchestrator pipes `cycle.current_setpoint_kw()` to `lb.command_setpoint_kw()` only when the value changes (not every tick). A 5-step cycle produces exactly 5 Modbus writes. Master Load is automatically enabled on cycle play and held through completion.
- **Start with Test recording gate**: when `start_with_test: true` in `cycle.yaml`, pressing Record checks that LoadBank Take Control is active before starting the cycle and recording simultaneously.
- **Simplex 750kW loadbank model map** (`configs/loadbanks/Simplex-750kW.yaml`): B-side Modbus addresses for the 750kW load bank — control coils, indicator coils, metering registers (float32 BA word order), and step array (300+200+150+50+25+25 = 750kW).
- **LoadBank Frequency channel**: added to both Simplex 750kW and 1.5MW model maps.
- **LoadBank recording telemetry channels**: `lDG_Fan`, `lPO_LdbAct`, `lPO_LdbStp`, `lCT_Ldb1/2/3`, `lVO_Ldb1/2/3` wired into recording telemetry for any active loadbank.
- **LoadBank secondary model "None" option**: configuration dropdown now includes a "None" option for single-loadbank setups.
- **Orchestrator cycle IPC handlers**: `cycle_play`, `cycle_pause`, `cycle_seek`, `cycle_set_loops`, `cycle_set_start_with_test` control messages routed from UI to CyclePlugin.
#### Fixed
- **NI DAQ DI boolean values**: digital input reads were returning raw DAQmx integers instead of clean `0`/`1` floats. Added explicit `int(bool(v))` conversion in `_nidaq_acquisition.py` `_read_di_real()` to ensure DI channels publish `0.0` or `1.0`. This fixed the `qDG_FacEspAct` estop circuit read that was preventing the estop relay from completing.
- **NI DAQ DO task creation for single-line ports**: `_nidaq_tasks.py` now uses `port0/line0:0` format (explicit start:end range) instead of `port0/line0` when a DO port has only one configured line. Prevents DAQmx from interpreting a bare line reference as the full port width.
- **Calculated Channels orchestrator ordering**: calculated channels now always evaluate after all source plugins (CAN, CCP, NI DAQ, Modbus, Vaisala, Omega, LoadBank) have published their values in the tick. Previously, ordering was not guaranteed and a calculated channel could evaluate before its inputs existed, causing incorrect results (e.g., `mOT_EngSsd` reading `1` instead of `0`).
- **LoadBank fan control initialization**: `_control_values_a` now initializes to `[False, False, False]` instead of `[True, True, True]`, preventing fan power and load from being commanded ON at startup.
- **LoadBank telemetry decoupled from Cycle**: loadbank telemetry (metering, status, setpoint) now publishes independently of whether the Cycle plugin is active.
- **LoadBank Modbus float32 word order**: corrected `word_order` from `AB` to `BA` for all metering registers (voltage, current, power, frequency) in both `Simplex-1.5MW.yaml` and `Simplex-750kW.yaml`. Fixes "very big numbers" in metering readback after loadbank power cycle.
- **LoadBank UI panel import guard**: `CycleChartWidget` import wrapped in `try/except` with `None` fallback so the LoadBank panel loads even if `cycle_chart.py` is missing from the workstation.
- **Cycle play from complete state**: hitting Play after a completed cycle now resets and restarts from the beginning instead of silently doing nothing.
- **Start with Test gate**: removed Master Load from the readiness check (only checks Take Control), since Master Load is automatically enabled by the cycle play handler — eliminates the chicken-and-egg block that prevented recording from starting.
#### Changed
- **Cycle config dialog cleaned up**: removed Plugin Enabled checkbox, Recording Rate spinner, Integration (load bank) section, and Optional Safety section — all were either obsolete or never wired. Added Start with Test checkbox to the Execution section.
- **`cycle.yaml` simplified**: removed `enabled`, `recording_rate_hz`, `execution.restart_policy`, `execution.skip_behavior`, `execution.interpolation`, `integration`, and `optional_safety` blocks. Retained: `source`, `execution.loops_total`, `execution.start_with_test`, `execution.inter_loop_dwell_s`.
- **LoadBank diagnostic logging reduced**: removed diagnostic register scan (`_scan_register_range`), capped fan read diagnostics at 5, capped control write diagnostics at 5, reduced metering diagnostics to 30s intervals.
- **Cycle hold-on-complete behavior**: when the cycle finishes, the last setpoint is held (no auto-zero). Operator uses Emergency Stop / Zero Load to drop load manually. Cycles are typically written to end at 0kW.

### Onsite testing: CCP A2L decode rewrite, pymodbus compat, Vaisala/Modbus data fixes — 04/21/2026
#### Added
- **CCP A2L COMPU_METHOD COEFFS parsing**: `parse_a2l()` now extracts the 6 COEFFS (a, b, c, d, e, f) from every `RAT_FUNC` COMPU_METHOD block and `IDENTICAL` methods. COEFFS are stored in a new `coeffs` field on the `A2LChannel` dataclass and linked to each MEASUREMENT via the compu_method reference. The A2L file tested contains 130+ unique COEFFS patterns; only ~25% are identity — the rest require active conversion.
- **`_apply_rat_func_inv()` function**: inverts the ASAM RAT_FUNC formula `INT = (a*PHYS² + b*PHYS + c) / (d*PHYS² + e*PHYS + f)` to compute physical from raw. Handles the common linear case `PHYS = (f*INT - c) / b`, the linear-rational case, and the full quadratic case with discriminant-based root selection.
- **Pymodbus version compatibility shim** (`src/plugins/_modbus_compat.py`): detects installed `pymodbus` version at import time and provides `uid_kwargs(unit_id)` returning the correct parameter name (`unit=` for <3.3, `slave=` for 3.3–3.9, `device_id=` for 3.10+). All four Modbus-using plugins (Modbus, Vaisala, Omega, LoadBank) updated to use this shim.
- **Vaisala configurable word order**: `vaisala.yaml` gains `connection.word_order` (default `little`). `_decode_float32()` and `_encode_float32()` accept a `word_order` parameter and swap register words accordingly for correct IEEE 754 float interpretation.
- **NI DAQ extended health diagnostics**: orchestrator telemetry filter (`_strip_debug_keys`) now allows `NI_DAQ/consec_failures`, `NI_DAQ/last_good_read_age_s`, `NI_DAQ/task_fast_alive`, and `NI_DAQ/last_error` to pass through when `health.expose_status_channels: true` is set. DO source picker excludes all `NI_DAQ/` diagnostic keys.
#### Fixed
- **CCP `decode_value()` rewrite**: complete overhaul of the raw-to-physical conversion pipeline:
  - **ULONG/SLONG**: previously returned raw integer with no conversion (ULONG) or decoded as unsigned (SLONG). Now properly handles signed decoding and applies COMPU_METHOD COEFFS. Fixes `HM_RAMr_seconds` (was 892601, now 247.9 hours) and `I_Gov2_acc` (was 134M raw, now 50%).
  - **FLOAT32_IEEE / FLOAT64_IEEE**: previously interpreted raw bytes as integer. Now uses `struct.unpack` for correct IEEE 754 float decoding.
  - **SBYTE**: previously decoded as unsigned. Now correctly signed.
  - **SWORD/UWORD with zero limits**: previously returned 0.0 always (multiplied by 0). Now uses COEFFS when available, or improved limits fallback with non-zero lower bound support.
- **A2L limits parsing**: fixed two bugs: (1) lines starting with `-` (negative lower bounds like `-460 563.98`) were rejected by `isdigit()` check; (2) the Resolution/Accuracy line `0 0` was incorrectly captured as limits instead of the actual limits line. Now uses a `numeric_line_index` counter to skip the first numeric pair and capture the second.
- **Vaisala write register addresses**: corrected 1-based PDU addresses to 0-based — `_PRESSURE_TEMP_REG` 771→770, `_FILTER_STD_REG` 1281→1280, `_FILTER_EXT_REG` 1282→1281.
- **Modbus ComApp register configuration**: mass-corrected `configs/modbus.yaml` — all `length: 2` entries changed to `length: 1` (tool used "length" to mean bytes, not registers), `uint32`→`uint16` and `int32`→`int16` type corrections, all `address` values decremented by 1 (1-based to 0-based Modbus PDU addressing).
- **CCP config UI A2L parser**: mirrored the same limits parsing and size cap fixes from `_ccp_a2l.py` into the UI's `_parse_a2l_channels()` method in `ccp_config.py`.
#### Changed
- `A2LChannel` dataclass extended with `coeffs: Optional[Tuple[float, ...]]` field (6-tuple for RAT_FUNC COEFFS).
- `decode_value()` signature extended with optional `coeffs` parameter; conversion priority is COEFFS → legacy limits fallback.
- CCP plugin passes `coeffs` from A2L parse through entry dicts to `decode_value()`.
- CCP entry size cap increased from 5 to 8 bytes (supports FLOAT64_IEEE).
- LoadBank Modbus calls simplified from multi-try `slave`/`unit`/`device_id` blocks to single `**uid_kwargs()` calls.

### iOT internal channels, shutdown types, and onsite hardening — 04/20/2026
#### Added
- **iOT shutdown-type differentiation**: per-channel `shutdown_type` (hard/soft) field in the alarm system. `AlarmEngine.evaluate()` now returns `any_soft_shutdown`, `any_hard_shutdown`, and `engine_running` in its summary dict alongside the existing `any_warning`/`any_shutdown`/`any_shutdown_request` flags.
- **Internal boolean channels fully wired**: orchestrator now injects `iOT_AlmSftSdn` (soft shutdown active), `iOT_AlmEmgSdn` (hard/emergency shutdown active), and `iDG_EngRunStp` (engine running) into telemetry every tick. These are derived from the alarm engine summary and are available to DO condition evaluation, so their mapped relay pins in `ni_daq.yaml` now fire correctly.
- **Channel Manager "Shutdown Type" column**: new Hard/Soft dropdown in the alarm table UI between "Alarm Action" and "Enabling Cond". Persisted as `shutdown_type` in each channel's `alarm` block in `channel_manager.yaml`. Defaults to "Hard" for backward compatibility.
- **Alarm engine tests**: 6 new test cases covering hard/soft shutdown type differentiation, mixed-channel scenarios, and `engine_running` summary exposure.
- **DO condition editor dialog** (`do_condition_editor.py`): popup on double-click of DO Condition column with operator picker, threshold input, live value display, manual Force HIGH/LOW/Release buttons, and condition preview. TRUE/FALSE operators disable the threshold field.
- **DO source picker dialog** (`do_source_picker.py`): dedicated picker for DO source channel (col 2) populated from live telemetry and all enabled NI DAQ aliases, replacing the generic alias picker for DOs.
- **NI DAQ chassis-grouped tasks**: `_nidaq_tasks.py` now groups AI channels by chassis rather than individual module, creating one consolidated DAQmx task per chassis. Resolves CompactDAQ `-200022` resource conflict when multiple modules share a chassis timing engine.
- **Plugin health indicators**: all Modbus-type plugins (Modbus, Vaisala, Omega) and CAN now publish `*/conn_ok` boolean channels. Console tiles show Green (OK), Red/Error (`health_ok=False`), or Red/Disconnected (`conn_ok=False`).
- **Modbus real transport**: full Modbus TCP client implementation in `modbus.py` with `_ServerConnection` management, `_decode_registers()` for float/int conversion with byte/word order support, multi-server polling, and scaling. Plugin now branches correctly between `real` and `sim` modes.
- **Global data_mode enforcement**: orchestrator explicitly sets `plugin.mode = "sim" if global_sim else "real"` across all plugin loading/reloading/sync paths, overriding individual YAML `mode` fields. Per-plugin mode dropdowns removed from Omega, Vaisala, and CAN config dialogs.
#### Fixed
- **NI DAQ duplicate alias validation**: `validate()` now only checks aliases of *enabled* channels for duplicates, preventing false failures from disabled channels sharing an alias.
- **NI DAQ resource conflict** (`-200022`): fixed by grouping AI channels per chassis instead of per module in `_nidaq_tasks.py`.
- **Vaisala/Modbus sim-despite-real**: plugins ignored the global `data_mode` and used their YAML `mode` field. Fixed by orchestrator enforcement and correcting individual YAML files.
- **Debug channel stripping**: `_strip_debug_keys()` updated to preserve `*/health_ok` and `*/conn_ok` channels so console can display plugin connection status.
#### Changed
- DO condition format simplified: source channel is always the DO's own alias (no separate source picker needed in the condition dialog). Condition column shows only `operator threshold` (e.g., `> 0.5`), not `alias operator threshold`.
- NI DAQ config dialog DO table: "Alias" column renamed to "Source Channel" for clarity.
- All Channels Table filters out `*/health_ok` and `*/conn_ok` from display.

### Pre-onsite fixes: Modbus, CAN multi-bus, DO conditions — 04/20/2026
#### Added
- **Multi-bus CAN support**: `can.yaml` schema updated to `buses[*]` with per-bus `channel`, `baudrate`, `bustype`, `dbc_path`, and `signals`. Legacy single-bus config (`session` + top-level `signals` + `dbc_path`) auto-migrates to `buses[0]`. CAN plugin (`can.py`) opens one `python-can` bus per entry, loads one DBC per bus, and drains frames from all buses in the snapshot loop. Config dialog (`can_config.py`) refactored to a tabbed per-bus layout with Add Bus / Remove Bus controls, each tab containing channel, baudrate, DBC path, signal filter, and signal table with alias picker.
- **NI DAQ DO condition column**: each DO channel in `ni_daq.yaml` can now have an optional `condition` dict (`source`, `operator`, `threshold`). The orchestrator evaluates all DO conditions each tick after all plugin values are merged, calling `ni_daq.write_do(alias, state)` for real-time output control. Condition format in the UI: `xCalc_FuelPumpCmd > 0.5`. Supported operators: `>`, `>=`, `<`, `<=`, `==`, `!=`. The NI DAQ config dialog now shows a Condition column in the Digital Output table with validation on save.
- **Modbus alias picker**: double-click the Alias column in the Modbus config dialog opens `AliasPickerDialog` for standard channel selection. Column header renamed from "Channel Name" to "Alias" for consistency. Alias validation added on save.
- **Modbus diagnostic print**: `[INFO] Modbus: N read channel(s) resolved` now printed during `configure()` for troubleshooting.
#### Fixed
- **Modbus channels not appearing in All Channels Table**: root cause was `enabled: false` at the top of `modbus.yaml`, which caused the orchestrator to skip initialization via `config_enabled = bool(plugin.config.get("enabled", True))`. Fixed by setting `enabled: true`; improved sim values to generate phase-shifted waveforms for all aliases.
#### Changed
- CAN plugin sim mode generates per-alias waveforms based on alias name patterns (RPM, pressure, temperature).
- Modbus sim mode updated from hardcoded Room Temp/Humidity to per-alias phase-shifted sine waveforms.

### Restore Displays button — 04/17/2026
#### Added
- **Restore Displays** button in the console Controls box. Clicking it re-reads `selected_displays` from `plugins.yaml`, checks which display windows are still alive (`isVisible()` / C++ object check), recreates any that were closed, and brings already-open windows to front. Messages pane confirms what was restored or that everything was already open.
- Display creation refactored into `_create_display(key)` factory method and `_DISPLAY_REGISTRY` lookup so future display types (plots, etc.) are added in one place.
- `_display_alive(key)` helper guards against `RuntimeError` from deleted Qt C++ objects when a display is closed between telemetry ticks.
#### Changed
- `_refresh_status()` telemetry push into display windows now uses `_display_alive()` instead of raw dict membership, preventing rare `RuntimeError` if a window is destroyed mid-tick.

### Auto Excel export, Lock status indicator, and Unlock Test — 03/09/2026
#### Added
- **Auto Excel export** after recording: when Stop Recording triggers the background Parquet merge, a successful merge now kicks off the Excel export automatically. Workbook is written to `<run>/data/` so it sits beside `Data_<run>.parquet` instead of living in a separate `exports/` folder. New IPC status messages `export_progress` (stage=started) and `export_done` (ok, error, files) surface completion and any failures to the console Messages box.
- **Status bar lock indicator** in the console: third label inserted between Connected/Disconnected and Recording so the bar now reads `Connected | Unlocked | Recording: Off`. Uses the same green/grey pattern as Recording (green = Locked, grey = Unlocked). Follows the existing auto-reset tied to `_prev_rec` so it flips back after Stop.
- **Unlock Test button** in the console Controls box, visible only in the locked-and-not-recording state. Opens a QMessageBox confirmation ("Unlock this test and discard the locked metadata? You will need to re-enter Engine/Test info before the next lock.") and, on accept, sends the existing `unlock_test` control message, clears the local lock flag, and resets the primary button back to "Lock Test" so the operator can correct EngineTest metadata without having to start and stop a throwaway recording.
- `--output-dir` flag on `py -m src.tools.export_excel` so super-users can still redirect workbooks to a custom folder; the default (both CLI and orchestrator-triggered) is `<run>/data/`.
#### Changed
- `src/tools/export_excel.py` `export_excel()` signature gains `output_dir: Optional[Path] = None`. Default destination is `<run>/data/` (previously `<run>/exports/`). The workbook stem is unchanged (`Data_<run>.xlsx`), so it now sits next to `Data_<run>.parquet`.
- `src/core/recording.py` `_on_done` callback chains `kickoff_export(orch)` on successful merge; worker now publishes `export_progress`/`export_done` status messages over the status bus and calls `mod.export_excel(run_dir, output_dir=run_dir / "data")`.
- `src/ui/widgets/console.py` `_handle_status_msg` gains branches for `export_progress` and `export_done` to display start/completion lines in Messages.
- `docs/flows.md` WF-LOCK_START_STOP and `docs/ai_context.yaml` console controls updated to reflect the new flow (auto export, status bar, Unlock Test).
#### Removed
- **Export Workbook button** removed from the console Controls box along with its `_on_export_clicked` handler and the `btn_export.setEnabled(...)` gate in `_refresh_status`. The Excel-export IPC message type (`export_excel`) is still honoured by the orchestrator for backward-compatibility with the CLI tool, but no longer has a UI entry point.

### Scale library JSON database — 03/09/2026
#### Added
- `configs/scale_library.json` — JSON scale database mirroring `configs/standard_channels.json` schema (`version`, `source`, `scales`). Each entry has `name` (selection key, old-tool style like "Druck 100psi"), `type` (`linear`/`table`), `unit`, optional `description`; linear entries carry `gain`/`offset`, table entries carry `points` (array of `[raw, scaled]` pairs) and `extrapolate`. Seeded with all 10 entries from the legacy YAML plus three Druck named scales (100/300/1000 psi) and a 12-point Omega FTB-1400 turbine flow meter for testing.
- `src/ui/widgets/scale_library.py` — shared `load_scale_library()` loader. Single server-swap point: when a future web-based super-user tool hosts the scale database, only this function needs to change (e.g., HTTP GET with local JSON cache fallback). Validates entries (non-empty name, `type` in {`linear`, `table`}) on load.
#### Changed
- `src/ui/widgets/nidaq_scaling_editor.py` `_import_from_library()` now calls `load_scale_library()` instead of reading YAML directly.
- `_LibraryPickerDialog` redesigned: added `QLineEdit` search box (case-insensitive match against name/description/unit), `Qt.UserRole` storage on each `QListWidgetItem` so filtering doesn't corrupt selection, hover tooltips showing formula (linear) or point count (table), and a filtered-count label. Read-only from the app per design — add/edit/delete happens in the separate web tool.
#### Deprecated
- `configs/scale_library.yaml` — no longer loaded by the app. Left in place for one release to avoid surprising external scripts; follows the same pattern as the earlier `configs/alias_library.yaml` deprecation.

### NI DAQ hardware migration system — 03/09/2026
#### Added
- **Hardware migration dialog** (`src/ui/widgets/nidaq_migration_dialog.py`): when NI DAQ hardware changes (chassis swap, card moved to a different slot), a module-level migration dialog auto-suggests mappings by `product_type` so the user can transfer aliases, scaling, and sensor config to new physical channels without starting from scratch.
- **Diff engine** in `_nidaq_discovery.py`: `compute_hardware_diff()` compares old config devices against new inventory and produces missing/new/unchanged/suggested_mappings. `apply_migration()` rewrites `phys` strings per confirmed mappings while preserving all channel configuration.
- **`device_map`** persistence in `ni_daq.yaml`: maps device names to product types (e.g., `AGENTMod1: "NI 9239"`). Populated automatically on config save and regeneration. Enables type-matching even after old hardware is disconnected.
- `build_device_map()` helper in `_nidaq_discovery.py`.
- **Type-safe candidate filtering** in the migration dialog: chassis (no I/O channels) are excluded from candidates; modules are filtered by I/O capability (AI voltage / AI thermocouple-RTD / Digital / AO) inferred from the old config's channel categories and new modules' product type pattern (TC/RTD modules: NI 9210/9211/9212/9213/9214/9216/9217/9219/9226/9235/9236/9237). Additional filter by channel count (new ≥ old) prevents data loss. Matching product types listed first, then compatible types, with channel counts shown in each option label.
#### Changed
- NI DAQ config dialog mismatch flow: now attempts migration dialog first (when mappable modules exist), falls back to regenerate prompt otherwise.

### Global offline mode and Omega config improvements — 03/09/2026
#### Changed
- **Launch dialog**: replaced "Data Mode" dropdown with an "Offline Mode (simulated data)" checkbox. When checked, all plugins run in simulation mode regardless of individual config.
- **Orchestrator**: reads `data_mode` from `plugins.yaml` at startup; overrides every plugin's `mode` to `sim` when offline. Applied consistently across all `load_config()` paths (startup, reload, sync, CCP test, EngineTest recording).
- **Omega config dialog**: replaced fixed-channels text label with an editable table (ID, Unit, Alias). Aliases default to planned values (`xTP_Amb`, `xPR_Amb`, `xHM_Amb`) but are user-configurable via double-click (opens shared AliasPickerDialog). Blank aliases trigger a warning. Saved to `omega.yaml` channels block.
- **Omega plugin**: `configure()` now reads aliases from `omega.yaml` channels config, falling back to CHANNEL_MAP defaults. Active channels list drives aliases, units, simulation, and Modbus reads.
#### Removed
- Mode (sim/real) dropdowns removed from Omega, Vaisala, and CAN config dialogs. Mode is now controlled globally via the launch dialog's offline checkbox.

### Omega Weather Station plugin — 03/09/2026
#### Added
- New `Omega` plugin (`src/plugins/omega.py`) for Omega weather station via Modbus TCP. Reads 3 fixed channels (temperature, barometric pressure, humidity) from holding registers 8-13 as big-endian float32. Handles Omega-specific error/NaN sentinel codes (0x7F800000-0x7F800003). Aliases configurable, defaults: `xTP_Amb` (C), `xPR_Amb` (kPa), `xHM_Amb` (Pct).
- `configs/omega.yaml` — minimal config with connection settings (host, port, timeout) and optional channels block for alias overrides.
- `src/ui/widgets/omega_config.py` — minimal config dialog with mode, host/IP, and port fields.
- Orchestrator integration: registered in both demo and real tick loops, plugin enablement list, and plugin cleanup.
- Console tile: right-click "Configure..." context menu for the Omega tile.

### Standard alias picker across all plugins — 03/09/2026
#### Added
- `configs/standard_channels.json` — server-ready JSON file converted from `StandardChannels.csv` containing 243 standard channel aliases with units. Schema includes `version`, `source`, and `channels` array for future API migration.
- `src/ui/widgets/standard_channels.py` — shared loader module with `load_standard_channels()`, `ALIAS_PATTERN`, and `validate_alias()` used by all plugin config dialogs.
- CAN config dialog (`can_config.py`) alias picker: replaced `QListWidget` with `QTableWidget` (checkbox, Message, Signal, Unit, Alias columns). Double-click the Alias column to open the alias picker. Blank aliases on checked signals are blocked on save.
- Vaisala config dialog (`vaisala_config.py`) alias picker: alias column is now read-only; double-click opens the same `AliasPickerDialog`. Consistent with NI DAQ behavior.

#### Changed
- `AliasPickerDialog` (`nidaq_alias_picker.py`) now loads from `standard_channels.json` via the shared loader instead of `alias_library.yaml`. Library tab renamed to "Standard Channels" and shows Alias + Unit columns. Search filters across both columns.
- Alias validation regex (`ALIAS_PATTERN`) and `validate_alias()` moved to `standard_channels.py`; re-exported from `nidaq_alias_picker.py` for backward compatibility.

### Strip debug channels from telemetry — 03/09/2026
#### Changed
- Diagnostic channels with prefixes `CAN/`, `CCP/`, `Core/`, `EngineTest/`, `NI_DAQ/` are no longer published in the telemetry stream or recorded to Parquet. Plugins still compute them internally; a `_strip_debug_keys()` filter at the orchestrator publish boundary removes them. To re-enable, remove the prefix from the `_DEBUG_PREFIXES` tuple.

### Vaisala config UI cleanup — 03/09/2026
#### Removed
- "Plugin enabled" checkbox removed from Vaisala config dialog (enablement is driven by console plugin selection list).
- "Poll rate", "Timeout", and "Calibration offsets" removed from the Vaisala config UI. Poll rate and timeout remain in `vaisala.yaml` for super-user access; calibration offsets removed entirely from UI, YAML, and plugin code.

### Grouped All Channels Table — 03/09/2026
#### Changed
- All Channels Table (`channels_table.py`) redesigned from a single flat alphabetical table into side-by-side category panels ("Death by Numbers" style). Channels are auto-categorized by alias prefix: Temperatures (TP), Pressures (PR), ECU Data (CAN `c` / CCP `e` prefix), Facility (Ldb/Alm/Fan keywords, HM code), Engine Conditions (Modbus `m` prefix), and Other (catch-all).
- Each category is a `QGroupBox` with a compact `QTableWidget` (Alias, Value, Unit columns) including alarm state coloring.
- Panels with no channels are hidden automatically.
- `FlowLayout` arranges panels left-to-right and wraps to the next row when the window is too narrow; the whole view is vertically scrollable.

### Vaisala parameter writes (pressure & filtering) — 03/09/2026
#### Added
- Pressure compensation mode (Fixed / Dynamic) written to Vaisala temporary register 771-772 every poll cycle. Fixed mode writes a constant hPa value; Dynamic mode reads a source channel from any plugin's telemetry, applies `gain * value + offset`, and writes the result.
- Filtering mode (None / Standard / Extended) written to flag registers 1281 and 1282 every poll cycle. Flags are mutually exclusive per Vaisala spec.
- `_encode_float32()` helper in `vaisala.py` — inverse of `_decode_float32`, packs a Python float into two Big-Endian 16-bit register values.
- `update_telemetry(vals)` method on `VaisalaPlugin` — orchestrator feeds the full merged telemetry dict each tick so the poll thread can resolve the dynamic pressure source channel.
- `_write_parameters()` in `vaisala.py` — called each poll cycle to write pressure and filtering registers with error isolation from the read path.
- Config dialog gains Pressure Compensation group (mode combo, fixed hPa spin box, dynamic sub-panel with editable channel picker populated from plugin YAMLs, source unit, gain, offset) and Filtering group (None/Standard/Extended combo).
- Orchestrator (`orchestrator.py`) calls `vaisala.update_telemetry(vals)` in both demo and real tick loops after Calculated Channels, before Statistics.

#### Changed
- `configs/vaisala.yaml` gains `pressure` and `filtering` blocks.

### Vaisala Modbus TCP plugin — 03/09/2026
#### Added
- Vaisala plugin (`vaisala.py`) now supports real-mode Modbus TCP acquisition via pymodbus. Reads float32 holding registers for 13 measurement channels (RH, T, Td, Td/f, a, x, Tw, H2Ov, pw, pws, H, dT, H2Ow) using two bulk register reads per poll cycle.
- Hardcoded `REGISTER_MAP` constant with register addresses, units, and simulation parameters for all Vaisala HMT/HMP channels.
- Threaded poll loop with configurable poll rate, automatic reconnect on connection loss, and sample-and-hold on read errors.
- Config dialog redesigned with checkbox-based channel selection from the register map, user-editable aliases per channel (blank by default, matching NI DAQ convention), connection settings (host, port, timeout, poll rate), and duplicate-alias validation.
- Model dropdown (HMT330 / Indigo510) with automatic Modbus unit ID assignment (HMT330=1, Indigo510=241). Unit ID is hidden from the user.

#### Changed
- `configs/vaisala.yaml` restructured: channels now reference register map by `id` with per-channel `alias` and `enabled` fields.

### CAN duplicate alias fix — 03/09/2026
#### Fixed
- `validate()` in `can.py` now only checks enabled signals for duplicate aliases, matching the filter used by `configure()` and `aliases()`. Previously, a DBC with the same signal name in multiple messages would fail validation even if only one was checked.
- CAN config dialog (`can_config.py`) now blocks save when checked signals produce duplicate aliases, showing a warning that names the conflicting aliases and their source messages so the user can deselect one.

### Fix runtime plugin enable/disable — 03/09/2026
#### Fixed
- Plugins added to or removed from `selected_plugins` after core startup are now recognized automatically. ConsoleWindow sends a `sync_plugin_selections` control message on connect; the orchestrator's new `_sync_all_plugin_selections()` method re-reads `plugins.yaml`, diffs against `_plugin_enabled`, and starts newly-enabled or stops newly-disabled plugins in one pass. Per-plugin `_reload_plugin` also re-reads via `_refresh_plugin_selection()` for configure-triggered reloads.
- Both tick loops (demo and real) now re-resolve plugin references from `_plugin_enabled` each iteration, so runtime enable/disable takes effect on the next tick without a core restart.

### Fix plugin enablement — 03/09/2026
#### Fixed
- Orchestrator now reads `selected_plugins` from `configs/plugins.yaml` (written by the launch dialog) to determine which plugins run. Previously, all plugins with `enabled: true` in their own config files would configure/start/stream data regardless of the user's launch selection. A plugin now runs only if it appears in `selected_plugins` AND its own config does not have `enabled: false`. `Channel_Manager` and `EngineTest` remain always-on. Falls back to all-enabled if `plugins.yaml` is missing or empty.

### NI DAQ streaming optimization — 03/09/2026
#### Added
- Configurable oversample block (`acquisition.oversample`) in `ni_daq.yaml`: `factor` (default 10), `applies_to` (voltage|all), `filter` (butterworth|average|none), `butterworth_order` (default 4).
- `IIRFilter` class in `_nidaq_scaling.py`: stateful 4th-order IIR Butterworth low-pass filter using SOS (second-order sections) via scipy for numerical stability; coefficients computed once, per-sample cost ~8 multiply-adds per order; graceful fallback to passthrough if scipy unavailable.
- `presort_scaling_points()` helper in `_nidaq_scaling.py`: pre-sorts table scaling points at config load time to avoid runtime sort overhead in `_table_interp`.
- Butterworth fast reader thread mode (`_spawn_butterworth_reader` in `_nidaq_tasks.py`): applies IIR filter + scaling per sample (thread-local, no lock), writes pre-computed float per alias to shared dict under brief lock.
- ZMQ PUB/SUB high-water mark (HWM=10) in `bus.py` to bound memory on laggy subscribers.

#### Changed
- **Tick rate alignment**: NI DAQ snapshot period now inherits from core tick rate (`channel_manager.yaml` `recording_rate_hz`) via orchestrator; `ni_daq.yaml` `recording_rate_hz` deprecated to `auto` (numeric value overrides with logged warning).
- Orchestrator passes `_core_tick_rate_hz` to NI DAQ plugin before `configure()` in start, run, and reload paths.
- Fast reader thread architecture: butterworth mode eliminates deques entirely for voltage channels; data copy chain reduced from 6 stages to 4; lock hold time reduced from O(aliases * deque_size) to O(1) per alias.
- `_read_threaded_fast_ai` in `_nidaq_acquisition.py` dispatches to `_read_threaded_butterworth` (dict copy) or `_read_threaded_deque` (legacy averaging) based on filter mode.
- Temperature unit map cached once at `start()` in `ni_daq.py` (`_temp_unit_map`) instead of rebuilt per `read_real()` call.
- Table scaling points pre-sorted at config load via `presort_scaling_points()`; `_table_interp` assumes sorted input.
- `create_tasks_real` in `_nidaq_tasks.py` uses `_sim_rate_hz` (core-aligned rate) instead of reading `recording_rate_hz` from config directly.
- NI DAQ config dialog "Recording rate (Hz)" field is now read-only, displaying inherited rate; save always writes `recording_rate_hz: auto`.
- Orchestrator `_apply_channel_manager_runtime()` now propagates the new tick rate to NI DAQ's `_snapshot_period_s` live, without requiring a full plugin restart.

### NI DAQ constrained aliases and channel scaling — 03/09/2026
#### Added
- Constrained alias system for all NI DAQ channel types: regex-enforced naming convention with `AliasPickerDialog` (searchable library tab from `configs/alias_library.yaml` + custom entry with live validation).
- Channel scaling for AI voltage: `ScalingEditorDialog` with No Scale / Linear (gain+offset) / Table (multi-point interpolation) modes, live telemetry preview at 5 Hz, and import from `configs/scale_library.yaml`.
- Table scaling extrapolation option: "Extrapolate beyond table range" checkbox allows linear extrapolation past table min/max instead of clamping; persisted as `extrapolate: true` in scaling config.
- Temperature unit picker dialog for RTD/TC channels (C / F / K selection).
- Shared scaling helper module `src/plugins/_nidaq_scaling.py` with `apply_scaling()`, `convert_temp_unit()`, `scaling_summary()`.
- Stub YAML library files: `configs/alias_library.yaml` (60+ premade aliases), `configs/scale_library.yaml` (10+ premade linear/table scales).

#### Changed
- NI DAQ config dialog: all editing is now via double-click dialogs (alias picker, scaling editor); inline cell editing disabled; alias regex validation enforced on save.
- NI DAQ config dialog: disabled channels now display blank alias and unit columns on load for cleaner presentation.
- NI DAQ YAML scaling format expanded from `{m, b, unit}` to `{type, gain, offset, unit, points, extrapolate}` for full linear/table support.
- Real acquisition path (`_nidaq_acquisition.py`): applies `apply_scaling()` to voltage reads and `convert_temp_unit()` to temperature reads before publishing to orchestrator.
- Simulation path (`_nidaq_simulation.py`): applies same scaling/unit conversion as real path for consistent behavior.

### Config UIs, CCP tuning, UI responsiveness — 03/09/2026
#### Added
- Cycle config dialog: embedded QtCharts staircase plot preview (step/hold matching load bank behavior), BOM-safe CSV parsing, status bar with point count/loops/duration.
- Statistics config dialog: right-click configure for window settings, metrics, trigger configuration.
- Vaisala config dialog: right-click configure for IP, model, polling rate, calibration offsets.
- LoadBank operator control UI panel (dockable widget): Take Control, Fan Power, Load Setpoint, Emergency Stop, live readback metering.
- EngineTest promoted to dedicated plugin (`src/plugins/engine_test.py`) with Lock/Start/Stop lifecycle and metadata validation.
- Pytest framework with unit tests for alarm engine, BCD encoding, calculated expressions, CCP protocol.

#### Changed
- CCP: removed 15 ms hard cap on SHORT_UP poll timeout; now honors configured `io_timeout_s` (default 50 ms). Significantly reduces poll fail rate under real ECU load.
- UI: tightened ZMQ telemetry poll interval from 100 ms to 20 ms (50 Hz) and display refresh from 250 ms to 50 ms (20 Hz) for near-real-time responsiveness.
- Cycle config preview uses `utf-8-sig` encoding to handle BOM in CSV files.

### Structural refactor — 03/09/2026
#### Changed
- Split `src/plugins/ni_daq.py` into the main NI DAQ plugin module plus helpers: `_nidaq_discovery.py` (MAX/template generation), `_nidaq_simulation.py` (sim timestep), `_nidaq_tasks.py` (DAQmx tasks/buffers), and `_nidaq_acquisition.py` (worker and snapshot buffer).
- Split `src/plugins/ccp.py` into the main CCP plugin module plus `_ccp_a2l.py` (A2L parse/decode) and `_ccp_protocol.py` (CCP session and polling).
- Slimmed `src/core/orchestrator.py` by moving recording session and export kickoff logic into `src/core/recording.py` (`begin_recording`, `end_recording`, `kickoff_export`, `build_parquet_settings`, related helpers).
- Promoted Channel Manager and Engine Test from orchestrator-internal stubs to first-class plugins: `src/plugins/channel_manager.py` and `src/plugins/engine_test.py`.

### Added
- Integrated CCP real-mode plugin path with NI-XNET session, connect/seed/unlock, and SHORT_UP polling using A2L-defined measurements.
- Added access-key unlock algorithm support in CCP (no vendor DLL required), with fallback to `CCP_ACCESS_KEY` environment variable.
- Added CCP configuration dialog in UI and wired CCP tile context menu configure/reload workflow.
- Added CCP diagnostics telemetry channels (`CCP/connected`, state/counters/error indicators) and runtime logs for connect/unlock/poll stages.
- Added core tick diagnostics channels: `Core/tick_dt_s`, `Core/tick_jitter_s`, `Core/tick_overrun`.
- Added CAN diagnostics channels for runtime tuning: `CAN/frames_rx`, `CAN/decode_hits`, `CAN/last_decode_age_s`.
- Added CAN configuration dialog with right-click tile integration, DBC import, signal selection/filtering, and YAML persistence.
- Added Modbus configuration dialog with right-click tile integration, channel table editing, live Value test view, and YAML persistence.
- Added multi-device Modbus configuration UI with per-device tabs and independent TCP/RS485 settings.
- Added CCP multi-device configuration support with tabbed UI (`Add Device`/`Remove Device`) for up to two ECMs.
- Added CCP device role selection (`Primary`/`Secondary`) with station-address mapping (`0x0`/`0x1`) persisted in `configs/ccp.yaml` under `devices[*]`.
- Added CAN runtime dependencies to project requirements: `python-can`, `cantools`.
- Added Channel Manager configuration dialog with right-click tile integration, YAML import/export, active-channel alarm table, and save/reload workflow.
- Added Channel Manager enabling condition controls (`Always Enabled`, `Engine Running`, `Engine Run time`, `Test Time`) with engine-speed alias selection and thresholding.
- Added aggregate alarm boolean telemetry channels: `iOT_Warning` and `iOT_Alarm`.
- Added alarm-state row coloring in All Channels Table (`WARN` yellow, tier-2 alarm red) using reusable table color helper.

### Changed
- Updated CCP default alias prefix to reduce cross-plugin alias collisions (e.g., `CCP_` namespace).
- Improved CCP polling stability by using bounded per-tick polling and shorter IO timeouts to avoid UI connection flicker.
- Improved core recording cadence by removing repeated A2L parsing from CCP unit lookup on the tick path.
- Decoupled plugin acquisition from core tick for latest-value snapshot behavior in `NI_DAQ`, `CAN`, `Statistics`, `Calculated_Channels`, and `Modbus`.
- Updated CCP runtime to background polling with cached snapshot reads on tick path and freshness/staleness telemetry.
- Updated CCP runtime to resolve/read from `devices[*]` first (multi-device), with backward compatibility to legacy single-device top-level keys.
- Updated CAN runtime to support real-mode DBC decode with event-driven frame drain and J1939 PGN-aware fallback matching.
- Updated Modbus runtime config resolution to use `devices[*].reads[*]` first, with fallback to legacy top-level `reads`.
- Updated Channel Manager alarm model to support two-tier warning/alarm semantics with per-limit debounce and per-tier actions (`Visible Alert` / `Visible Alert + Shutdown`).
- Updated Channel Manager second-tier naming from `shutdown` to `alarm` in UI/config output while retaining runtime backward compatibility for legacy `shutdown` keys.
- Updated core tick cadence authority so `channel_manager.yaml` `recording_rate_hz` controls core tick/logging cadence.
- Updated Channel Manager reload handling to reapply runtime alarm/tick settings without full app restart.

### Notes
- Deferred CCP optimization backlog (tracked in `docs/plugins/ccp.md`):
  - Hybrid DAQ + SHORT_UP within a single device context,
  - reduce stale/freshness warnings while keeping current channel responsiveness,
  - add rolling CCP health metrics (success-rate window, consecutive-fail counters),
  - cold-start grace period for ECU measurements that need longer initial timeout.

## [0.1.0-alpha.1] - 08/11/2025
### Added
- Documentation scaffold: README, specs, flows, interfaces, test plan, RTM, AI context
- Established architecture decisions and naming/segmentation/export policies
- Defined plugin set and lifecycle, configuration approach, and run folder structure


