<!-- Author: T. Onkst | Date: 04212026 -->

## Calculated Channels Plugin Specification

### Purpose
Define computed channels from existing recorder channels using safe expressions with explicit symbol mappings, then publish latest calculated values through a background snapshot worker.

### Current Implementation Status
- Implemented now:
  - Expression model: `alias + expr + symbols + unit + enabled`
  - Safe AST evaluator with a restricted operation/function set
  - Background worker that computes independently of core tick
  - Right-click Configure dialog with split layout:
    - left: symbol mapping for selected calculation
    - right: calculation list + expressions + global update rate
  - YAML persistence to `configs/calculated_channels.yaml`
  - Plugin reload on save
- Not implemented:
  - Per-calculation update rates
  - Assignment-style expressions (`x+y=z`)
  - Rolling helper functions, latching model, dependency graph engine

### Configuration (Current YAML)
File: `configs/calculated_channels.yaml`

```yaml
enabled: true
recording_rate_hz: 10
channels:
  - alias: mPR_Amb_psi
    expr: k * kpa
    symbols:
      k: 0.1450377
      kpa: qPR_Amb
    unit: psi
    enabled: true
```

### Runtime Semantics
- `simulate_step(source_values)` is non-blocking:
  - stores latest source snapshot
  - returns latest computed calc snapshot
- Worker thread computes at `recording_rate_hz` period (minimum 10 ms).
- Core tick/log cadence is controlled by Channel Manager; calculated outputs are sampled from this plugin's latest snapshot at tick time.
- **Orchestrator evaluation order**: Calculated Channels always evaluate **after** all source plugins (NI DAQ, CAN, CCP, Modbus, Vaisala, Omega, LoadBank) have published their values in the tick. This guarantees that all hardware inputs exist before expressions reference them. Without this ordering, a calculated channel could evaluate before its source plugin runs, producing incorrect results (e.g., a conditional expression returning `1` when both inputs are actually `0` because one hadn't been published yet).
- Evaluation order is top-to-bottom in `channels`; later rows may reference earlier calculated aliases by symbol mapping.
- Symbol resolution:
  - numeric mapping -> constant
  - string mapping -> source alias lookup (or prior calc output alias)
  - missing/unparseable values -> `NaN`
- Evaluation errors emit `NaN` for output alias.

### Allowed Expression Features
- Arithmetic: `+`, `-`, `*`, `/`, `%`, `**`
- Unary: `+`, `-`
- Comparisons: `>`, `>=`, `<`, `<=`, `==`, `!=` (returns `1.0` / `0.0`)
- Boolean ops: `and`, `or` (returns `1.0` / `0.0`)
- Ternary: `a if cond else b`
- Functions: `abs`, `min`, `max`, `round`, `pow`, `sin`, `cos`, `tan`, `exp`, `log`, `sqrt`

### Validation Rules (Current)
- `channels` must be a list.
- `recording_rate_hz` must be numeric and `> 0`.
- Each channel row requires:
  - `alias`
  - `expr` (parseable Python expression syntax)
  - `symbols` mapping
- Symbol keys must be valid identifiers.
- No duplicate output aliases within Calculated plugin.

### UI Flow (Current)
- Right-click `Calculated_Channels` tile -> `Configure...`
- In dialog:
  1) Set global update rate
  2) Add/remove/duplicate calculation rows
  3) Select a row and edit symbol mapping on left pane (`symbol -> channel alias or constant`)
  4) Save (writes YAML + triggers `reload_plugin` for `Calculated_Channels`)

### Outputs
- Output channels: enabled `channels[*].alias`
- Units map: `channels[*].unit` for enabled rows

