# Author: T. Onkst | Date: 04292026

from __future__ import annotations

import math

import pytest

from src.plugins.calculated import (
    ALLOWED_FUNCS,
    BlockHistory,
    CalcBlock,
    SafeExprEvaluator,
    _migrate_legacy_channel,
)


@pytest.fixture
def evaluator() -> SafeExprEvaluator:
    return SafeExprEvaluator(ALLOWED_FUNCS)


# ── Single-expression eval (unchanged) ──────────────────────────────

def test_basic_arithmetic_add_sub_mul_div(evaluator: SafeExprEvaluator):
    assert evaluator.eval("1 + 2 * 3", {}) == 7.0
    assert evaluator.eval("(1 + 2) * 3", {}) == 9.0
    assert evaluator.eval("10 - 4", {}) == 6.0
    assert evaluator.eval("15 / 3", {}) == 5.0
    assert evaluator.eval("2 ** 8", {}) == 256.0
    assert evaluator.eval("7 % 3", {}) == 1.0


def test_unary_plus_minus(evaluator: SafeExprEvaluator):
    assert evaluator.eval("-5 + 3", {}) == -2.0
    assert evaluator.eval("+10", {}) == 10.0


def test_symbol_substitution_from_bindings(evaluator: SafeExprEvaluator):
    assert evaluator.eval("a + b", {"a": 2.0, "b": 3.0}) == 5.0
    assert evaluator.eval("x * y + z", {"x": 1.5, "y": 2.0, "z": 1.0}) == 4.0


def test_conditional_expression(evaluator: SafeExprEvaluator):
    assert evaluator.eval("a if 1 > 0 else b", {"a": 42.0, "b": 0.0}) == 42.0
    assert evaluator.eval("a if 1 < 0 else b", {"a": 42.0, "b": 7.0}) == 7.0


def test_comparison_chains(evaluator: SafeExprEvaluator):
    assert evaluator.eval("1 < 2 < 3", {}) == 1.0
    assert evaluator.eval("1 < 2 > 3", {}) == 0.0


def test_allowed_math_functions(evaluator: SafeExprEvaluator):
    assert evaluator.eval("sqrt(9)", {}) == 3.0
    assert evaluator.eval("sin(0)", {}) == 0.0
    assert evaluator.eval("abs(-4)", {}) == 4.0
    assert evaluator.eval("min(3, 5)", {}) == 3.0
    assert evaluator.eval("max(3, 5)", {}) == 5.0


def test_unknown_symbol_raises(evaluator: SafeExprEvaluator):
    with pytest.raises(ValueError, match="unknown symbol"):
        evaluator.eval("a + 1", {})


def test_disallowed_syntax_raises(evaluator: SafeExprEvaluator):
    with pytest.raises(ValueError, match="unsupported"):
        evaluator.eval("[]", {})


def test_disallowed_function_raises(evaluator: SafeExprEvaluator):
    with pytest.raises(ValueError, match="unsupported function"):
        evaluator.eval("open()", {})


def test_nan_propagation_from_bindings(evaluator: SafeExprEvaluator):
    out = evaluator.eval("a + 1", {"a": float("nan")})
    assert math.isnan(out)


# ── evaluate_block() ────────────────────────────────────────────────

def test_evaluate_block_simple(evaluator: SafeExprEvaluator):
    body = "x = a + b\ny = x * 2"
    scope = evaluator.evaluate_block(body, {"a": 3.0, "b": 4.0})
    assert scope["x"] == 7.0
    assert scope["y"] == 14.0


def test_evaluate_block_with_comments_and_blanks(evaluator: SafeExprEvaluator):
    body = """
    # first step
    x = a + 1

    # second step
    y = x * 2
    """
    scope = evaluator.evaluate_block(body, {"a": 5.0})
    assert scope["x"] == 6.0
    assert scope["y"] == 12.0


def test_evaluate_block_conditional_chain(evaluator: SafeExprEvaluator):
    body = (
        "SoftShutdown = 1.0 if (softalarm == 1 and rpm == 0) else 0.0\n"
        "Estop = 0 if (SoftShutdown == 1) else 1\n"
        "FuelLockoff = 0 if (Estop == 0) else 1"
    )
    scope = evaluator.evaluate_block(body, {"softalarm": 1.0, "rpm": 0.0})
    assert scope["SoftShutdown"] == 1.0
    assert scope["Estop"] == 0.0
    assert scope["FuelLockoff"] == 0.0


def test_evaluate_block_no_shutdown(evaluator: SafeExprEvaluator):
    body = (
        "SoftShutdown = 1.0 if (softalarm == 1 and rpm == 0) else 0.0\n"
        "Estop = 0 if (SoftShutdown == 1) else 1\n"
        "FuelLockoff = 0 if (Estop == 0) else 1"
    )
    scope = evaluator.evaluate_block(body, {"softalarm": 0.0, "rpm": 1500.0})
    assert scope["SoftShutdown"] == 0.0
    assert scope["Estop"] == 1.0
    assert scope["FuelLockoff"] == 1.0


def test_evaluate_block_empty_body(evaluator: SafeExprEvaluator):
    scope = evaluator.evaluate_block("", {"a": 1.0})
    assert scope == {"a": 1.0}


def test_evaluate_block_preserves_bindings(evaluator: SafeExprEvaluator):
    scope = evaluator.evaluate_block("y = x + 1", {"x": 10.0})
    assert scope["x"] == 10.0
    assert scope["y"] == 11.0


# ── _migrate_legacy_channel() ───────────────────────────────────────

def test_migrate_legacy_channel():
    old = {
        "alias": "mPR_Amb_psi",
        "expr": "k * kpa",
        "symbols": {"k": 0.145, "kpa": "qPR_Amb"},
        "unit": "psi",
        "enabled": True,
    }
    new = _migrate_legacy_channel(old)
    assert new["name"] == "mPR_Amb_psi"
    assert new["body"] == "result = k * kpa"
    assert new["outputs"] == [{"var": "result", "alias": "mPR_Amb_psi", "unit": "psi"}]
    assert new["symbols"] == {"k": 0.145, "kpa": "qPR_Amb"}
    assert new["enabled"] is True


def test_migrate_legacy_channel_disabled():
    old = {"alias": "foo", "expr": "a + b", "symbols": {"a": "x", "b": "y"}, "enabled": False}
    new = _migrate_legacy_channel(old)
    assert new["enabled"] is False
    assert new["name"] == "foo"


# ── CalcBlock dataclass ─────────────────────────────────────────────

def test_calc_block_defaults():
    blk = CalcBlock(name="Test", body="x = 1", symbols={})
    assert blk.outputs == []
    assert blk.enabled is True


def test_calc_block_with_outputs():
    blk = CalcBlock(
        name="Logic",
        body="x = a + 1",
        symbols={"a": "src"},
        outputs=[{"var": "x", "alias": "out_x", "unit": "V"}],
        enabled=False,
    )
    assert blk.name == "Logic"
    assert len(blk.outputs) == 1
    assert blk.enabled is False


# ── BlockHistory ─────────────────────────────────────────────────────

def test_block_history_returns_zero_when_empty():
    h = BlockHistory()
    assert h.get("x", 1) == 0.0
    assert h.get("x", 5) == 0.0


def test_block_history_push_and_get():
    h = BlockHistory()
    h.push({"x": 10.0, "y": 20.0})
    assert h.get("x", 1) == 10.0
    assert h.get("y", 1) == 20.0
    assert h.get("z", 1) == 0.0  # var not in scope


def test_block_history_multiple_steps():
    h = BlockHistory()
    h.push({"x": 1.0})
    h.push({"x": 2.0})
    h.push({"x": 3.0})
    assert h.get("x", 1) == 3.0  # 1 cycle ago
    assert h.get("x", 2) == 2.0  # 2 cycles ago
    assert h.get("x", 3) == 1.0  # 3 cycles ago
    assert h.get("x", 4) == 0.0  # beyond history


def test_block_history_depth_cap():
    h = BlockHistory(depth=3)
    for i in range(5):
        h.push({"x": float(i)})
    # Only last 3 kept: 2.0, 3.0, 4.0
    assert h.get("x", 1) == 4.0
    assert h.get("x", 2) == 3.0
    assert h.get("x", 3) == 2.0
    assert h.get("x", 4) == 0.0  # evicted


def test_block_history_clear():
    h = BlockHistory()
    h.push({"x": 5.0})
    h.clear()
    assert h.get("x", 1) == 0.0


# ── prev() function in evaluator ────────────────────────────────────

def test_prev_returns_zero_no_history(evaluator: SafeExprEvaluator):
    h = BlockHistory()
    scope = evaluator.evaluate_block("y = prev(x, 1)", {"x": 10.0}, history=h)
    assert scope["y"] == 0.0  # no history yet


def test_prev_returns_previous_value(evaluator: SafeExprEvaluator):
    h = BlockHistory()
    h.push({"x": 42.0})
    scope = evaluator.evaluate_block("y = prev(x, 1)", {"x": 99.0}, history=h)
    assert scope["y"] == 42.0


def test_prev_default_steps_is_one(evaluator: SafeExprEvaluator):
    h = BlockHistory()
    h.push({"x": 7.0})
    scope = evaluator.evaluate_block("y = prev(x, 1)", {"x": 10.0}, history=h)
    assert scope["y"] == 7.0


def test_prev_multi_step(evaluator: SafeExprEvaluator):
    h = BlockHistory()
    h.push({"val": 100.0})
    h.push({"val": 200.0})
    h.push({"val": 300.0})
    scope = evaluator.evaluate_block(
        "a = prev(val, 1)\nb = prev(val, 2)\nc = prev(val, 3)",
        {"val": 400.0},
        history=h,
    )
    assert scope["a"] == 300.0
    assert scope["b"] == 200.0
    assert scope["c"] == 100.0


def test_prev_accumulator_pattern(evaluator: SafeExprEvaluator):
    """Simulate a running total that adds 'step' each cycle."""
    h = BlockHistory()
    body = "total = prev(total, 1) + step"
    for i in range(5):
        scope = evaluator.evaluate_block(body, {"step": 1.0}, history=h)
        h.push(scope)
    assert scope["total"] == 5.0


def test_prev_delta_pattern(evaluator: SafeExprEvaluator):
    """Simulate computing a delta from the previous value."""
    h = BlockHistory()
    body = "delta = rpm - prev(rpm, 1)"
    values = [1000.0, 1050.0, 1100.0, 1080.0]
    results = []
    for v in values:
        scope = evaluator.evaluate_block(body, {"rpm": v}, history=h)
        h.push(scope)
        results.append(scope["delta"])
    assert results[0] == 1000.0  # prev is 0.0 (no history)
    assert results[1] == 50.0
    assert results[2] == 50.0
    assert results[3] == -20.0


def test_prev_without_history_object(evaluator: SafeExprEvaluator):
    """prev() returns 0.0 when no history is provided (backward compat)."""
    scope = evaluator.evaluate_block("y = prev(x, 1)", {"x": 5.0}, history=None)
    assert scope["y"] == 0.0


# ── dt built-in ──────────────────────────────────────────────────────

def test_dt_available_in_bindings(evaluator: SafeExprEvaluator):
    scope = evaluator.evaluate_block("elapsed = dt", {"dt": 0.02})
    assert scope["elapsed"] == 0.02


def test_dt_timer_pattern(evaluator: SafeExprEvaluator):
    """Simulate a timer that accumulates dt each cycle."""
    h = BlockHistory()
    body = "timer = prev(timer, 1) + dt"
    total = 0.0
    for _ in range(10):
        scope = evaluator.evaluate_block(body, {"dt": 0.05}, history=h)
        h.push(scope)
        total = scope["timer"]
    assert abs(total - 0.5) < 1e-9
