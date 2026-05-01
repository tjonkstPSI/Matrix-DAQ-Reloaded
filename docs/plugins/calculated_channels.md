<!-- Author: T. Onkst | Date: 04292026 -->

## Calculated Channels Plugin Specification

### Purpose
Define computed channels from existing recorder channels using safe multiline expression blocks with explicit symbol mappings, then publish latest calculated values through a background snapshot worker.

### Current Implementation Status
- Implemented:
  - **Block model**: `name + body + symbols + outputs + enabled`
  - Each block contains a multiline expression body (`var = expr` per line), input symbol mappings, and an explicit list of exposed outputs
  - Safe AST evaluator with a restricted operation/function set — no `eval()` or `exec()`
  - `evaluate_block()` processes assignments sequentially, building a scope so later lines can reference earlier intermediates
  - **`prev(variable, steps)`** function for accessing previous cycle values (per-block history buffer, 10-cycle depth, defaults to `0.0`)
  - **`dt` built-in** — elapsed seconds since last evaluation cycle, auto-injected into every block
  - Background worker that computes independently of core tick
  - Config dialog with list+detail layout:
    - left: block list with checkboxes (Add / Remove / Duplicate)
    - right: block editor (name, enabled, symbol table, multiline body editor, outputs table)
  - Recipe import/export (JSON files) for server-ready portability
  - Auto-migration of legacy single-expression (`expr`) configs to block format
  - YAML persistence to `configs/calculated_channels.yaml`
  - Plugin reload on save
  - User guide: `docs/guides/calculated_channels_help.md`
- Not implemented:
  - Per-block update rates
  - Latching model, dependency graph engine
  - Server-side recipe storage (schema ready, transport deferred)

### Configuration (YAML)
File: `configs/calculated_channels.yaml`

#### New Block Schema
```yaml
enabled: true
recording_rate_hz: 50.0
channels:
- name: Estop Logic
  enabled: true
  symbols:
    rpm: cSP_Eng
    softalarm: iOT_AlmSftSdn
    EstopStat: qDG_FacEspAct
  body: |
    SoftShutdown = 1.0 if (softalarm == 1 and rpm == 0) else 0.0
    GenEstopStatus = 0 if (SoftShutdown == 1 or EstopStat == 0) else 1
    Estop = 0 if (SoftShutdown == 1) else 1
    FuelLockoff = 0 if (Estop == 0) else 1
  outputs:
    - var: SoftShutdown
      alias: mOT_EngSsd
      unit: bool
    - var: FuelLockoff
      alias: eFuelLockoffStatus
      unit: bool
```

Key fields:
- `name` — human-readable block title; also serves as recipe title for export
- `body` — YAML literal block scalar (`|`), one `var = expr` assignment per line; lines starting with `#` are comments
- `outputs` — list of `{var, alias, unit}` dicts; only these variables become telemetry channels
- `symbols` — maps expression variable names to source aliases or numeric constants
- `enabled` — block-level enable/disable toggle

#### Legacy Format (auto-migrated)
```yaml
- alias: mPR_Amb_psi
  expr: k * kpa
  symbols:
    k: 0.1450377
    kpa: qPR_Amb
  unit: psi
  enabled: true
```
On load, legacy `expr` entries are auto-converted: `body = "result = {expr}"`, `outputs = [{var: "result", alias: old_alias, unit: old_unit}]`. No manual migration required.

### Recipe Schema (Import/Export)
A recipe is a JSON file containing a single block definition:
```json
{
    "name": "Estop Logic",
    "description": "",
    "version": "1.0",
    "symbols": {"rpm": "cSP_Eng", "softalarm": "iOT_AlmSftSdn"},
    "body": "SoftShutdown = 1.0 if ...\nFuelLockoff = ...",
    "outputs": [{"var": "SoftShutdown", "alias": "mOT_EngSsd", "unit": "bool"}]
}
```
The JSON schema matches the YAML block schema 1:1 so future server API integration is a transport change only.

### Runtime Semantics
- `simulate_step(source_values)` is non-blocking:
  - stores latest source snapshot
  - returns latest computed calc snapshot
- Worker thread computes at `recording_rate_hz` period (minimum 10 ms).
- Core tick/log cadence is controlled by Channel Manager; calculated outputs are sampled from this plugin's latest snapshot at tick time.
- **Orchestrator evaluation order**: Calculated Channels always evaluate **after** all source plugins (NI DAQ, CAN, CCP, Modbus, Vaisala, Omega, LoadBank) have published their values in the tick.
- **Block evaluation order**: top-to-bottom in `channels` list; later blocks may reference earlier block outputs via symbol mapping.
- **Within a block**: lines are evaluated top-to-bottom; later lines can reference variables assigned by earlier lines in the same block.
- **History**: after each block evaluation, the full scope is pushed into a per-block `BlockHistory` ring buffer (depth 10). The `prev()` function queries this buffer.
- **`dt` injection**: `dt` (seconds since last evaluation) is injected into every block's bindings before evaluation. `0.0` on the first cycle.
- Symbol resolution:
  - numeric mapping -> constant
  - string mapping -> source alias lookup (or prior block output alias)
  - missing/unparseable values -> `NaN`
- Evaluation errors emit `NaN` for all block output aliases.

### Allowed Expression Features
- Arithmetic: `+`, `-`, `*`, `/`, `%`, `**`
- Unary: `+`, `-`
- Comparisons: `>`, `>=`, `<`, `<=`, `==`, `!=` (returns `1.0` / `0.0`)
- Boolean ops: `and`, `or` (returns `1.0` / `0.0`)
- Ternary: `a if cond else b`
- Math functions: `abs`, `min`, `max`, `round`, `pow`, `sin`, `cos`, `tan`, `exp`, `log`, `sqrt`
- **`prev(variable, steps)`**: Returns value of `variable` from `steps` evaluation cycles ago. Returns `0.0` if no history exists. Max depth: 10 cycles.
- **`dt`**: Built-in variable — elapsed seconds since last evaluation cycle. `0.0` on first cycle after startup.

### Validation Rules
- `channels` must be a list.
- `recording_rate_hz` must be numeric and `> 0`.
- Each block requires:
  - `name` (non-empty)
  - `body` (non-empty, every non-blank/non-comment line must be `var = expr`)
  - `symbols` mapping (keys must be valid Python identifiers)
  - At least one output
- Each output requires:
  - `var` — must appear as a LHS assignment in the body
  - `alias` — non-empty, unique across all blocks
  - `unit` — optional
- No duplicate output aliases across all blocks.

### UI Flow
- Right-click `Calculated_Channels` tile -> `Configure...`
- In dialog:
  1. Select or add a calculation block from the left list
  2. Edit block name, enable/disable, input symbols, multiline body, and exposed outputs in the right panel
  3. Set global update rate (Hz) at the bottom
  4. Optional: Export Recipe / Import Recipe for server-ready sharing
  5. Save (writes YAML + triggers `reload_plugin` for `Calculated_Channels`)

### Outputs
- Output channels: all `outputs[*].alias` from enabled blocks
- Units map: `outputs[*].unit` for all exposed outputs of enabled blocks
