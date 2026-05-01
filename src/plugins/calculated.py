# Author: T. Onkst | Date: 04292026

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Set
import threading

from .base import BasePlugin, PluginStatus


_HISTORY_DEPTH = 10

ALLOWED_FUNCS = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "pow": pow,
}

try:
    import math as _math
    for _n in ("sin", "cos", "tan", "exp", "log", "sqrt"):
        fn = getattr(_math, _n, None)
        if fn is not None:
            ALLOWED_FUNCS[_n] = fn
except Exception:
    pass


@dataclass
class CalcBlock:
    name: str
    body: str
    symbols: Dict[str, Any]
    outputs: List[Dict[str, str]] = field(default_factory=list)
    enabled: bool = True


class BlockHistory:
    """Per-block rolling history of scope snapshots for prev() lookups."""

    def __init__(self, depth: int = _HISTORY_DEPTH) -> None:
        self._depth = depth
        self._ring: Deque[Dict[str, float]] = deque(maxlen=depth)

    def push(self, scope: Dict[str, Any]) -> None:
        self._ring.append({k: float(v) for k, v in scope.items()
                           if isinstance(v, (int, float))})

    def get(self, var: str, steps: int) -> float:
        """Return value of *var* from *steps* cycles ago.  Returns 0.0 if unavailable."""
        idx = len(self._ring) - steps
        if idx < 0 or idx >= len(self._ring):
            return 0.0
        return self._ring[idx].get(var, 0.0)

    def clear(self) -> None:
        self._ring.clear()


def _migrate_legacy_channel(c: dict) -> dict:
    """Convert old single-expression channel dict to new block format."""
    expr = str(c.get("expr", ""))
    alias = str(c.get("alias", ""))
    unit = str(c.get("unit", ""))
    return {
        "name": alias,
        "enabled": bool(c.get("enabled", True)),
        "symbols": dict(c.get("symbols") or {}),
        "body": f"result = {expr}",
        "outputs": [{"var": "result", "alias": alias, "unit": unit}],
    }


class SafeExprEvaluator:
    def __init__(self, allowed_funcs: Dict[str, Any]) -> None:
        self.allowed_funcs = allowed_funcs

    def eval(self, expr: str, bindings: Dict[str, Any]) -> float:
        import ast
        node = ast.parse(expr, mode="eval")
        return float(self._eval_node(node.body, bindings))

    def evaluate_block(
        self,
        body: str,
        bindings: Dict[str, Any],
        history: Optional[BlockHistory] = None,
    ) -> Dict[str, Any]:
        """Evaluate a multiline block of `var = expr` assignments.

        *history* enables the ``prev(var, steps)`` function.
        Returns the full scope dict (inputs + all computed intermediates).
        """
        scope = dict(bindings)
        scope["_history"] = history
        for line in body.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            varname, sep, rhs = line.partition("=")
            if not sep:
                continue
            varname = varname.strip()
            rhs = rhs.strip()
            if not varname or not rhs:
                continue
            scope[varname] = self.eval(rhs, scope)
        scope.pop("_history", None)
        return scope

    def _eval_node(self, node, bindings: Dict[str, Any]) -> Any:
        import ast
        if isinstance(node, ast.BinOp):
            left = self._eval_node(node.left, bindings)
            right = self._eval_node(node.right, bindings)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Pow):
                return left ** right
            if isinstance(node.op, ast.Mod):
                return left % right
            raise ValueError("unsupported operator")
        if isinstance(node, ast.UnaryOp):
            operand = self._eval_node(node.operand, bindings)
            if isinstance(node.op, ast.UAdd):
                return +operand
            if isinstance(node.op, ast.USub):
                return -operand
            raise ValueError("unsupported unary operator")
        if isinstance(node, ast.Num):  # py<3.8
            return node.n
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            key = node.id
            if key in bindings:
                return bindings[key]
            raise ValueError(f"unknown symbol: {key}")
        if isinstance(node, ast.Call):
            fn_name = getattr(node.func, 'id', None)
            if fn_name is None:
                raise ValueError("unsupported function")
            if fn_name == "prev":
                return self._handle_prev(node, bindings)
            if fn_name not in self.allowed_funcs:
                raise ValueError("unsupported function")
            args = [self._eval_node(a, bindings) for a in node.args]
            return self.allowed_funcs[fn_name](*args)
        if isinstance(node, ast.IfExp):
            test = self._eval_node(node.test, bindings)
            return self._eval_node(node.body if test else node.orelse, bindings)
        if isinstance(node, ast.Compare):
            left = self._eval_node(node.left, bindings)
            ok = True
            cur = left
            for op, comp in zip(node.ops, node.comparators):
                val = self._eval_node(comp, bindings)
                if isinstance(op, ast.Gt):
                    ok = ok and (cur > val)
                elif isinstance(op, ast.GtE):
                    ok = ok and (cur >= val)
                elif isinstance(op, ast.Lt):
                    ok = ok and (cur < val)
                elif isinstance(op, ast.LtE):
                    ok = ok and (cur <= val)
                elif isinstance(op, ast.Eq):
                    ok = ok and (cur == val)
                elif isinstance(op, ast.NotEq):
                    ok = ok and (cur != val)
                else:
                    raise ValueError("unsupported comparator")
                cur = val
            return 1.0 if ok else 0.0
        if isinstance(node, ast.BoolOp):
            vals = [bool(self._eval_node(v, bindings)) for v in node.values]
            if isinstance(node.op, ast.And):
                return 1.0 if all(vals) else 0.0
            if isinstance(node.op, ast.Or):
                return 1.0 if any(vals) else 0.0
            raise ValueError("unsupported boolean op")
        raise ValueError("unsupported expression element")

    def _handle_prev(self, node, bindings: Dict[str, Any]) -> float:
        """Evaluate prev(varname, steps) using the block history buffer."""
        import ast
        history: Optional[BlockHistory] = bindings.get("_history")
        if history is None:
            return 0.0
        args = node.args
        if len(args) < 1 or len(args) > 2:
            raise ValueError("prev() requires 1-2 arguments: prev(var) or prev(var, steps)")
        var_node = args[0]
        if not isinstance(var_node, ast.Name):
            raise ValueError("prev() first argument must be a variable name")
        var_name = var_node.id
        steps = 1
        if len(args) == 2:
            steps = int(self._eval_node(args[1], bindings))
        if steps < 1:
            steps = 1
        return history.get(var_name, steps)


class CalculatedChannelsPlugin(BasePlugin):
    id = "Calculated_Channels"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._blocks: List[CalcBlock] = []
        self._evaluator = SafeExprEvaluator(ALLOWED_FUNCS)
        self._units: Dict[str, str] = {}
        self._snapshot_values: Dict[str, Any] = {}
        self._latest_source_values: Dict[str, Any] = {}
        self._snapshot_lock = threading.Lock()
        self._source_lock = threading.Lock()
        self._worker_thread = None
        self._worker_stop = threading.Event()
        self._worker_period_s: float = 0.05
        self._block_histories: List[BlockHistory] = []
        self._last_eval_time: float = 0.0

    def configure(self) -> None:
        cfg = self.config or {}
        blocks: List[CalcBlock] = []
        for c in cfg.get("channels", []) or []:
            if not isinstance(c, dict):
                continue
            if "body" in c:
                name = str(c.get("name", ""))
                body = str(c.get("body", ""))
                symbols = c.get("symbols") or {}
                outputs = c.get("outputs") or []
                enabled = bool(c.get("enabled", True))
                if not body or not isinstance(symbols, dict):
                    continue
                if not isinstance(outputs, list):
                    continue
                out_list = []
                for o in outputs:
                    if isinstance(o, dict) and o.get("var") and o.get("alias"):
                        out_list.append({
                            "var": str(o["var"]),
                            "alias": str(o["alias"]),
                            "unit": str(o.get("unit", "")),
                        })
                blocks.append(CalcBlock(
                    name=name,
                    body=body,
                    symbols=dict(symbols),
                    outputs=out_list,
                    enabled=enabled,
                ))
            elif "expr" in c:
                migrated = _migrate_legacy_channel(c)
                blocks.append(CalcBlock(
                    name=migrated["name"],
                    body=migrated["body"],
                    symbols=migrated["symbols"],
                    outputs=migrated["outputs"],
                    enabled=migrated["enabled"],
                ))
        self._blocks = blocks
        self._block_histories = [BlockHistory() for _ in self._blocks]
        self._last_eval_time = 0.0
        self._units = {}
        for blk in self._blocks:
            if not blk.enabled:
                continue
            for o in blk.outputs:
                self._units[o["alias"]] = o.get("unit", "")
        try:
            hz = float(self.config.get("recording_rate_hz", 10.0))
        except Exception:
            hz = 10.0
        self._worker_period_s = max(0.01, 1.0 / max(1.0, hz))

    def validate(self) -> PluginStatus:
        chans = self.config.get("channels", []) or []
        if not isinstance(chans, list):
            return PluginStatus(ok=False, message="channels must be a list")
        try:
            hz = float(self.config.get("recording_rate_hz", 10.0))
            if hz <= 0.0:
                return PluginStatus(ok=False, message="recording_rate_hz must be > 0")
        except Exception:
            return PluginStatus(ok=False, message="recording_rate_hz must be numeric")
        import ast
        all_aliases: List[str] = []
        for i, c in enumerate(chans):
            if not isinstance(c, dict):
                continue
            if "body" in c:
                err = self._validate_block(i, c)
                if err:
                    return PluginStatus(ok=False, message=err)
                for o in (c.get("outputs") or []):
                    if isinstance(o, dict) and o.get("alias"):
                        all_aliases.append(str(o["alias"]))
            elif "expr" in c:
                if not c.get("alias"):
                    return PluginStatus(ok=False, message=f"channels[{i}].alias required")
                if not c.get("expr"):
                    return PluginStatus(ok=False, message=f"channels[{i}].expr required")
                symbols = c.get("symbols")
                if not isinstance(symbols, dict):
                    return PluginStatus(ok=False, message=f"channels[{i}].symbols must be a mapping")
                try:
                    ast.parse(str(c.get("expr")), mode="eval")
                except Exception as e:
                    return PluginStatus(ok=False, message=f"channels[{i}].expr syntax error: {e}")
                all_aliases.append(str(c["alias"]))
        if len(all_aliases) != len(set(all_aliases)):
            return PluginStatus(ok=False, message="duplicate calculated alias")
        return PluginStatus(ok=True)

    @staticmethod
    def _validate_block(idx: int, c: dict) -> Optional[str]:
        import ast
        body = str(c.get("body", "")).strip()
        if not body:
            return f"channels[{idx}]: body is empty"
        symbols = c.get("symbols")
        if not isinstance(symbols, dict):
            return f"channels[{idx}]: symbols must be a mapping"
        for key in symbols.keys():
            sk = str(key).strip()
            if not sk:
                return f"channels[{idx}]: symbols contains empty key"
            if not sk.isidentifier():
                return f"channels[{idx}]: symbols key '{sk}' is not a valid identifier"
        assigned_vars: set = set()
        for line_num, line in enumerate(body.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            varname, sep, rhs = line.partition("=")
            if not sep:
                return f"channels[{idx}] line {line_num}: expected 'var = expr' format"
            varname = varname.strip()
            rhs = rhs.strip()
            if not varname:
                return f"channels[{idx}] line {line_num}: variable name is empty"
            if not varname.isidentifier():
                return f"channels[{idx}] line {line_num}: '{varname}' is not a valid identifier"
            if not rhs:
                return f"channels[{idx}] line {line_num}: expression is empty"
            try:
                ast.parse(rhs, mode="eval")
            except Exception as e:
                return f"channels[{idx}] line {line_num}: syntax error: {e}"
            assigned_vars.add(varname)
        outputs = c.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            return f"channels[{idx}]: at least one output is required"
        for oi, o in enumerate(outputs):
            if not isinstance(o, dict):
                return f"channels[{idx}].outputs[{oi}]: must be a mapping"
            var = str(o.get("var", "")).strip()
            alias = str(o.get("alias", "")).strip()
            if not var:
                return f"channels[{idx}].outputs[{oi}]: var is required"
            if not alias:
                return f"channels[{idx}].outputs[{oi}]: alias is required"
            if var not in assigned_vars:
                return f"channels[{idx}].outputs[{oi}]: var '{var}' is not assigned in body"
        return None

    def aliases(self) -> Set[str]:
        out: Set[str] = set()
        for blk in self._blocks:
            if not blk.enabled:
                continue
            for o in blk.outputs:
                out.add(o["alias"])
        return out

    def units(self) -> Dict[str, str]:
        return dict(self._units)

    def start(self) -> None:
        self._worker_stop.clear()
        self._last_eval_time = 0.0
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def stop(self) -> None:
        self._worker_stop.set()
        t = self._worker_thread
        if t is not None and t.is_alive():
            try:
                t.join(timeout=0.5)
            except Exception:
                pass
        self._worker_thread = None

    def simulate_step(self, source_values: Dict[str, Any]) -> Dict[str, Any]:
        with self._source_lock:
            self._latest_source_values = dict(source_values)
        with self._snapshot_lock:
            return dict(self._snapshot_values)

    def _worker_loop(self) -> None:
        while not self._worker_stop.is_set():
            with self._source_lock:
                src = dict(self._latest_source_values)
            vals = self._compute_step_values(src)
            with self._snapshot_lock:
                self._snapshot_values = vals
            self._worker_stop.wait(self._worker_period_s)

    def _compute_step_values(self, source_values: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate all blocks and extract exposed outputs."""
        now = time.perf_counter()
        dt = now - self._last_eval_time if self._last_eval_time > 0.0 else 0.0
        self._last_eval_time = now

        out: Dict[str, Any] = {}
        for bi, blk in enumerate(self._blocks):
            if not blk.enabled:
                continue
            history = self._block_histories[bi] if bi < len(self._block_histories) else None
            bindings: Dict[str, Any] = {"dt": dt}
            for name, mapped in blk.symbols.items():
                if isinstance(mapped, (int, float)):
                    bindings[name] = float(mapped)
                elif isinstance(mapped, str):
                    if mapped in out:
                        bindings[name] = out[mapped]
                    else:
                        val = source_values.get(mapped)
                        if val is None:
                            bindings[name] = float('nan')
                        else:
                            try:
                                bindings[name] = float(val)
                            except Exception:
                                bindings[name] = float('nan')
                else:
                    bindings[name] = float('nan')
            try:
                scope = self._evaluator.evaluate_block(blk.body, bindings, history)
                if history is not None:
                    history.push(scope)
                for o in blk.outputs:
                    var = o["var"]
                    alias = o["alias"]
                    out[alias] = float(scope.get(var, float('nan')))
            except Exception:
                for o in blk.outputs:
                    out[o["alias"]] = float('nan')
        return out
