# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import math

import pytest

from src.plugins.calculated import ALLOWED_FUNCS, SafeExprEvaluator


@pytest.fixture
def evaluator() -> SafeExprEvaluator:
    return SafeExprEvaluator(ALLOWED_FUNCS)


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
