<!-- Author: T. Onkst | Date: 08122025 -->

## Calculated Channels Plugin Specification

### Purpose
Define channels computed from existing channels using a restricted Python expression engine evaluated at the recording rate R (≤ 100 Hz). Supports numeric and boolean outputs, rolling functions, and conditional logic. Results participate in alarms, UI displays, recording, Excel export, and may be used to implement site-specific E‑stop logic.

### Expression Model
- Evaluated once per tick on the canonical R grid
- Inputs:
  - Existing channel aliases (NI DAQ, CAN/CCP, Modbus, LoadBank, Statistics outputs if desired)
  - Previously defined calculated channels (dependency order enforced; cycles disallowed)
  - Built-ins: `time_s` (relative), `dt_s` (tick duration), `pi`, `e`
  - Namespaces: `math` and a constrained `np` (subset)
- Allowed operations:
  - Arithmetic: `+ - * / // % **`
  - Comparisons: `== != < <= > >=`
  - Logical: `and or not`
  - Parentheses and indexing of plain Python lists/tuples
  - Function calls only to whitelisted functions (see Allowed functions)
- Disallowed:
  - Imports, attribute access beyond whitelisted namespaces (`__` names blocked)
  - I/O, filesystem, network, eval/exec, comprehension side-effects
  - Assignment/augmented assignment; expressions are read-only

### Allowed Functions (initial set)
- Math: `abs, min, max, sqrt, sin, cos, tan, exp, log, log10`
- Aggregates (rolling/fixed via helpers):
  - `rolling_mean(x, window_s)`, `rolling_stdev(x, window_s)`
  - `rolling_min(x, window_s)`, `rolling_max(x, window_s)`
- Conditionals: `if_then_else(cond, a, b)`
- Guards: `clip(x, lo, hi)`, `nan_to_num(x, nan=0.0)`

Notes:
- Rolling helpers maintain internal state per channel; window defined in seconds and mapped to samples via R
- Slow sources use last-value-hold; missing values propagate NaN unless wrapped with `nan_to_num`

### Channel Types
- numeric: produces float values with optional units
- boolean: produces True/False; rendered as 1/0 in data files, used for UI indicators and alarm or E‑stop logic

### Configuration (YAML)
File: `configs/calculated_channels.yaml`

```yaml
recording_rate_hz: 100                  # must match session R

channels:
  - id: "delta_p"
    alias: "Delta P"
    type: numeric                        # numeric | boolean
    units: "kPa"
    expression: "P_in - P_out"
    enabled: true

  - id: "oil_overtemp"
    alias: "Oil Overtemp"
    type: boolean
    expression: "Oil_Temperature > 120"
    latching:
      enable: true
      set_after_s: 1.0                   # condition must be true for this long to set
      reset_after_s: 5.0                 # must be false for this long to reset
    enabled: true

  - id: "smoothed_rpm"
    alias: "RPM Smoothed"
    type: numeric
    units: "rpm"
    expression: "rolling_mean(RPM, 2.0)" # 2 s rolling mean
    enabled: true

dependencies:
  allow_calc_as_inputs: true             # calculated outputs can feed later expressions
```

#### Validation Rules
- `alias` unique among all enabled channel names (including other plugins)
- `type` in {numeric, boolean}; `units` required for numeric
- Expression references must resolve to existing inputs or previously defined calculated channels; detect and reject cycles
- Expressions must compile under the restricted evaluator (AST-based); only whitelisted functions/names allowed
- Rolling window arguments must be positive; latching times ≥ 0
- `recording_rate_hz` must match session R

### Execution & Ordering
- Build a dependency graph from expressions; topologically sort; evaluate in order each tick
- Maintain per-expression state for rolling functions and latches
- Emit NaN for numeric expressions that cannot be computed; False for boolean if evaluation yields NaN unless explicitly handled

### UI Flow
- Right-click Calculated Channels tile → Configure:
  1) Add/edit channels (alias, type, units, expression)
  2) Syntax and reference validation with live preview (uses recent samples)
  3) Latching options for boolean channels
  4) Ordering and dependency preview; conflicts/cycles highlighted
  5) Save
- Runtime: show evaluation status, last errors, and quick values for selected expressions

### Outputs & Metadata
- Record calculated channels at rate R like physical channels
- Sidecar YAML stores: expression text, dependency list, units/type, and latching params
- Calculated booleans can be used by alarm rules and to drive site E‑stop logic (via separate calculated logic channels)

### Error Conditions (Examples)
- Unresolved name in expression → validation error
- Disallowed function or attribute access → validation error
- Cycle detected across calculated channels → validation error
- Runtime errors (e.g., divide by zero) → value becomes NaN (numeric) or False (boolean), error logged; preview shows issue

### Test Cases (Calculated Channels)
- CALC-Validate-001: Allowed syntax/operators; disallowed constructs rejected
- CALC-Deps-001: Topological order honors dependencies; cycles detected
- CALC-Rolling-001: rolling_mean/stdev/min/max correctness over synthetic data
- CALC-Latch-001: Boolean latching set/reset delays applied correctly
- CALC-NaN-Guard-001: nan_to_num and clip behave as expected
- CALC-Perf-001: 100+ expressions evaluate within budget at R=100 Hz

