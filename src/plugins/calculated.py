# Author: T. Onkst | Date: 08132025

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set
import threading

from .base import BasePlugin, PluginStatus


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
class CalcItem:
    alias: str
    expr: str
    symbols: Dict[str, Any]
    unit: str = ""
    enabled: bool = True


class SafeExprEvaluator:
    def __init__(self, allowed_funcs: Dict[str, Any]) -> None:
        self.allowed_funcs = allowed_funcs

    def eval(self, expr: str, bindings: Dict[str, Any]) -> float:
        import ast
        node = ast.parse(expr, mode="eval")
        return float(self._eval_node(node.body, bindings))

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
            if fn_name is None or fn_name not in self.allowed_funcs:
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


class CalculatedChannelsPlugin(BasePlugin):
    id = "Calculated_Channels"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._items: List[CalcItem] = []
        self._evaluator = SafeExprEvaluator(ALLOWED_FUNCS)
        self._units: Dict[str, str] = {}
        self._snapshot_values: Dict[str, Any] = {}
        self._latest_source_values: Dict[str, Any] = {}
        self._snapshot_lock = threading.Lock()
        self._source_lock = threading.Lock()
        self._worker_thread = None
        self._worker_stop = threading.Event()
        self._worker_period_s: float = 0.05

    def configure(self) -> None:
        cfg = self.config or {}
        items: List[CalcItem] = []
        for c in cfg.get("channels", []) or []:
            if not isinstance(c, dict):
                continue
            alias = c.get("alias")
            expr = c.get("expr")
            symbols = c.get("symbols") or {}
            unit = str(c.get("unit", ""))
            enabled = bool(c.get("enabled", True))
            if not alias or not expr or not isinstance(symbols, dict):
                continue
            items.append(CalcItem(alias=str(alias), expr=str(expr), symbols=dict(symbols), unit=unit, enabled=enabled))
        self._items = items
        self._units = {it.alias: it.unit for it in self._items if it.enabled}
        try:
            hz = float(self.config.get("recording_rate_hz", 10.0))
        except Exception:
            hz = 10.0
        self._worker_period_s = max(0.01, 1.0 / max(1.0, hz))

    def validate(self) -> PluginStatus:
        # Basic structure validation
        chans = self.config.get("channels", []) or []
        if not isinstance(chans, list):
            return PluginStatus(ok=False, message="channels must be a list")
        for c in chans:
            if not isinstance(c, dict):
                continue
            if not c.get("alias"):
                return PluginStatus(ok=False, message="alias required")
            if not c.get("expr"):
                return PluginStatus(ok=False, message="expr required")
            if not isinstance(c.get("symbols"), dict):
                return PluginStatus(ok=False, message="symbols must be a mapping")
        # No duplicate output aliases
        aliases = [str(c.get("alias")) for c in chans if isinstance(c, dict) and c.get("alias")]
        if len(aliases) != len(set(aliases)):
            return PluginStatus(ok=False, message="duplicate calculated alias")
        return PluginStatus(ok=True)

    def aliases(self) -> Set[str]:
        return {it.alias for it in self._items if it.enabled}

    def units(self) -> Dict[str, str]:
        return dict(self._units)

    def start(self) -> None:
        self._worker_stop.clear()
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
        # Non-blocking on core tick: cache latest source and return latest computed snapshot.
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
        """Evaluate calculations against provided source values and any prior calcs in order."""
        out: Dict[str, Any] = {}
        for it in self._items:
            if not it.enabled:
                continue
            # Build bindings from symbols mapping
            bindings: Dict[str, Any] = {}
            for name, mapped in it.symbols.items():
                if isinstance(mapped, (int, float)):
                    bindings[name] = float(mapped)
                elif isinstance(mapped, str):
                    # map to source alias or prior calc alias
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
                out[it.alias] = self._evaluator.eval(it.expr, bindings)
            except Exception:
                # On evaluation error, emit NaN to keep schema stable
                out[it.alias] = float('nan')
        return out


