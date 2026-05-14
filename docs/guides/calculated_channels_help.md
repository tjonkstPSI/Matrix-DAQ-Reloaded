<!-- Author: T. Onkst | Date: 04292026 -->

# Calculated Channels — User Guide

## What Are Calculated Channels?

Calculated Channels let you create new data channels from math and logic applied to existing channels. For example, you can convert units, compute running totals, build estop logic chains, or derive deltas from raw sensor data — all without any external scripting.

Each calculation is organized as a **block** containing:
- **Input Symbols** — map short variable names to real channel aliases or constants
- **Expression Body** — one or more lines of `variable = expression` assignments
- **Exposed Outputs** — which variables from the body to publish as telemetry channels

---

## Getting Started

1. Right-click the **Calculated_Channels** tile in the console
2. Click **Configure...**
3. Use **Add** to create a new calculation block
4. Fill in the block name, symbols, body, and outputs
5. Click **OK** to save and reload

---

## The Config Dialog

```
+-----------------------------------------------------------------------+
|  Calculation Blocks        |  Block Editor                            |
|  +--------------------+   |  Name: [_______________]  [x] Enabled    |
|  | [x] Estop Logic    |   |                                          |
|  | [x] Pressure Conv  |   |  --- Input Symbols ---                   |
|  +--------------------+   |  | Symbol | Channel Alias or Constant |  |
|  [Add] [Remove] [Dup]    |  | rpm    | cSP_Eng                    |  |
|  [Export] [Import]        |  [Add Symbol] [Remove Symbol]            |
|                            |                                          |
|                            |  --- Expression Body ---                  |
|                            |  | SoftShutdown = 1.0 if ...          | |
|                            |  | Estop = 0 if ...                   | |
|                            |                                          |
|                            |  --- Exposed Outputs ---                  |
|                            |  | Variable   | Alias        | Unit |   |
|                            |  | FuelLockoff| eFuelLockoff | bool |   |
|                            |  [Add Output] [Remove Output]           |
+-----------------------------------------------------------------------+
|  Calculation Evaluation Rate (Hz): [50]              [OK] [Cancel]    |
+-----------------------------------------------------------------------+
```

### Left Panel — Block List
- Each block has a checkbox to enable/disable it
- **Add** creates a new empty block
- **Remove** deletes the selected block
- **Duplicate** copies the selected block
- **Export Recipe** saves the block as a `.json` file for sharing
- **Import Recipe** loads a `.json` recipe file as a new block

### Right Panel — Block Editor
- **Name**: Human-readable label (also used as the recipe title)
- **Enabled**: Toggle this block on/off
- **Input Symbols**: Map short names to real channel aliases or numeric constants
- **Expression Body**: Multiline code editor for your calculations
- **Exposed Outputs**: Which variables to publish as telemetry channels

---

## Writing Expressions

### Basic Rules
- One assignment per line: `variable = expression`
- Lines starting with `#` are comments
- Blank lines are ignored
- Later lines can reference variables from earlier lines
- All values are floating-point numbers

### Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `+` | Addition | `x + y` |
| `-` | Subtraction | `x - y` |
| `*` | Multiplication | `x * y` |
| `/` | Division | `x / y` |
| `**` | Power | `x ** 2` |
| `%` | Modulo | `x % 10` |
| `-x` | Negation | `-x` |

### Comparisons

Comparisons return `1.0` (true) or `0.0` (false).

| Operator | Description | Example |
|----------|-------------|---------|
| `>` | Greater than | `x > 100` |
| `>=` | Greater or equal | `x >= 100` |
| `<` | Less than | `x < 50` |
| `<=` | Less or equal | `x <= 50` |
| `==` | Equal | `x == 0` |
| `!=` | Not equal | `x != 0` |

Chained comparisons work: `0 < x < 100` is valid.

### Boolean Logic

Boolean operations return `1.0` (true) or `0.0` (false).

| Operator | Description | Example |
|----------|-------------|---------|
| `and` | Both true | `a == 1 and b == 1` |
| `or` | Either true | `a == 1 or b == 1` |

**Note:** There is no `not` operator. Use `== 0` instead: `alarm == 0` instead of `not alarm`.

### Conditional (Ternary)

```
result = value_if_true if condition else value_if_false
```

Examples:
```
status = 1.0 if (rpm > 0) else 0.0
clamped = 100.0 if (x > 100) else x
```

### Math Functions

| Function | Description | Example |
|----------|-------------|---------|
| `abs(x)` | Absolute value | `abs(delta)` |
| `min(a, b)` | Minimum | `min(x, 100)` |
| `max(a, b)` | Maximum | `max(x, 0)` |
| `round(x)` | Round to nearest | `round(x)` |
| `pow(x, n)` | Power (same as `**`) | `pow(x, 2)` |
| `sqrt(x)` | Square root | `sqrt(x)` |
| `sin(x)` | Sine (radians) | `sin(angle)` |
| `cos(x)` | Cosine (radians) | `cos(angle)` |
| `tan(x)` | Tangent (radians) | `tan(angle)` |
| `exp(x)` | e^x | `exp(rate)` |
| `log(x)` | Natural log (ln) | `log(x)` |

---

## Special Functions

### `prev(variable, steps)` — Previous Value Lookup

Access the value of any variable from a previous evaluation cycle. This enables delta calculations, running totals, timers, and any pattern that needs to remember past values.

**Syntax:** `prev(variable, steps)`

| Argument | Description | Default |
|----------|-------------|---------|
| `variable` | The variable name to look up (must be a name, not an expression) | required |
| `steps` | How many cycles back to look (1 = last cycle, 2 = two cycles ago) | required |

**Returns:** The value from `steps` cycles ago, or `0.0` if no history exists yet (e.g., on startup).

**History depth:** Up to 10 previous cycles are stored per block. Requesting `prev(x, 11)` or more will return `0.0`.

**Examples:**

```
# Delta: how much did RPM change since last cycle?
delta_rpm = rpm - prev(rpm, 1)

# Running total: accumulate flow readings
total_flow = prev(total_flow, 1) + flow_rate * dt

# Rate of change over 5 cycles
roc = (temp - prev(temp, 5)) / (5 * dt) if dt > 0 else 0
```

### `dt` — Time Since Last Evaluation

A built-in variable automatically available in every block. It contains the elapsed time in **seconds** since the last evaluation cycle.

**Typical values:** At 50 Hz update rate, `dt` is approximately `0.02` seconds. On the very first cycle after startup, `dt` is `0.0`.

**Examples:**

```
# Simple timer (seconds since start)
timer = prev(timer, 1) + dt

# Integrate a rate signal over time
total_fuel = prev(total_fuel, 1) + fuel_rate * dt

# Time-based ramp (increase by 1.0 per second)
ramp = prev(ramp, 1) + 1.0 * dt
```

---

## Input Symbols

Symbols map short variable names to data sources. This keeps expressions portable — if a channel alias changes, you only update the symbol mapping, not every line that references it.

| Symbol | Maps To | Type |
|--------|---------|------|
| `rpm` | `cSP_Eng` | Channel alias — reads live value |
| `k` | `0.1450377` | Numeric constant |
| `softalarm` | `iOT_AlmSftSdn` | Channel alias |

- If a mapped channel has no data yet, the symbol value is `NaN`
- Symbols from earlier blocks' outputs can be referenced by mapping to their output alias

### Naming Rules
- Symbol names must be valid Python identifiers (letters, numbers, underscores; cannot start with a number)
- Good: `rpm`, `temp_in`, `k1`, `EstopStat`
- Bad: `1st_value`, `my-var`, `rpm!`

---

## Exposed Outputs

Only variables listed in the Outputs table become telemetry channels. Everything else is an intermediate variable — computed but not published.

| Field | Description |
|-------|-------------|
| **Variable Name** | Must exactly match a variable assigned in the body (left side of `=`) |
| **Output Alias** | The telemetry channel name (must be unique across all blocks) |
| **Unit** | Display unit (informational, e.g., `psi`, `bool`, `C`) |

---

## Recipes (Import / Export)

A **recipe** is a portable JSON file containing one calculation block. Use recipes to:
- Share calculations between workstations
- Back up complex logic
- Build a library of reusable calculation templates

### Exporting
1. Select a block in the left list
2. Click **Export Recipe**
3. Choose a save location — saves as `.json`

### Importing
1. Click **Import Recipe**
2. Select a `.json` recipe file
3. The block is added to the list — adjust symbols as needed for your channel names

Recipe files are designed for future server-based sharing. The format is:
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

---

## Worked Examples

### Example 1: Unit Conversion (kPa to PSI)

**Symbols:**
| Symbol | Maps To |
|--------|---------|
| `k` | `0.1450377` |
| `kpa` | `qPR_Amb` |

**Body:**
```
result = k * kpa
```

**Outputs:**
| Variable | Alias | Unit |
|----------|-------|------|
| `result` | `mPR_Amb_psi` | `psi` |

### Example 2: Estop Logic Chain

**Symbols:**
| Symbol | Maps To |
|--------|---------|
| `rpm` | `cSP_Eng` |
| `softalarm` | `iOT_AlmSftSdn` |
| `EstopStat` | `qDG_FacEspAct` |

**Body:**
```
SoftShutdown = 1.0 if (softalarm == 1 and rpm == 0) else 0.0
GenEstopStatus = 0 if (SoftShutdown == 1 or EstopStat == 0) else 1
Estop = 0 if (SoftShutdown == 1) else 1
FuelLockoff = 0 if (Estop == 0) else 1
```

**Outputs:**
| Variable | Alias | Unit |
|----------|-------|------|
| `SoftShutdown` | `mOT_EngSsd` | `bool` |
| `FuelLockoff` | `eFuelLockoffStatus` | `bool` |

Note: `GenEstopStatus` and `Estop` are intermediate — computed but not published because they aren't in the outputs table. Add them if you want to see them in telemetry.

### Example 3: RPM Delta with Timer

**Symbols:**
| Symbol | Maps To |
|--------|---------|
| `rpm` | `cSP_Eng` |

**Body:**
```
# Track change in RPM since last cycle
delta_rpm = rpm - prev(rpm, 1)

# Running timer in seconds
run_time = prev(run_time, 1) + dt

# Running average RPM (exponential moving average)
alpha = 0.1
avg_rpm = prev(avg_rpm, 1) * (1 - alpha) + rpm * alpha
```

**Outputs:**
| Variable | Alias | Unit |
|----------|-------|------|
| `delta_rpm` | `mSP_DeltaRPM` | `rpm` |
| `run_time` | `mTM_RunTime` | `s` |
| `avg_rpm` | `mSP_AvgRPM` | `rpm` |

### Example 4: Fuel Flow Integration

**Symbols:**
| Symbol | Maps To |
|--------|---------|
| `flow_rate` | `qFU_FlowRate` |

**Body:**
```
# Integrate instantaneous flow rate over time
total_fuel = prev(total_fuel, 1) + flow_rate * dt
```

**Outputs:**
| Variable | Alias | Unit |
|----------|-------|------|
| `total_fuel` | `mFU_TotalFlow` | `L` |

---

## Troubleshooting

### "unknown symbol: xyz"
The variable `xyz` is used in an expression but isn't defined. Check:
- Is it in the Input Symbols table?
- Is it assigned on a previous line in the body?
- Is the spelling exact (case-sensitive)?

### Output shows NaN
- A mapped channel has no data (sensor disconnected, plugin not running)
- Division by zero occurred
- A symbol maps to a channel that doesn't exist

### "expected 'var = expr' format"
Every non-blank, non-comment line must be an assignment. Make sure each line has exactly one `=` with a variable name on the left and an expression on the right.

### "variable 'x' is not assigned in body"
An output references a variable that doesn't appear as a left-hand side assignment in the body. Check spelling and make sure the variable is actually computed, not just referenced.

### prev() returns 0.0 unexpectedly
- On the first evaluation cycle after startup, `prev()` always returns `0.0` because there is no history yet
- If `steps` exceeds 10 (the history depth), it returns `0.0`
- If the variable name doesn't match exactly, it returns `0.0`

---

## Limitations

- **No loops** (`for`, `while`) — use `prev()` + `dt` for iterative patterns
- **No `not` operator** — use `== 0` instead
- **No strings** — all values are floating-point numbers
- **No lists, dicts, or indexing** (`a[0]`)
- **No imports or function definitions**
- **No `print()` or side effects**
- **History limited to 10 cycles** per block
- **`prev()` first argument must be a plain variable name**, not an expression
- Functions are limited to the math set listed above — no custom functions

These limitations exist by design to keep the evaluator safe and predictable. If you need something not listed here, contact the development team.
