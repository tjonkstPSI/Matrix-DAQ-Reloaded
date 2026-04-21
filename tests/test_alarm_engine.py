# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import pytest

from src.core.alarms.engine import AlarmEngine


def test_warning_high_threshold_sets_warn_state_and_any_warning():
    cfg = {
        "channels": [
            {
                "alias": "P",
                "warning": {"high": 80.0},
                "alarm": {"high": 100.0},
            }
        ],
    }
    eng = AlarmEngine(cfg)
    per, summary, _events = eng.evaluate({"P": 85.0}, 1.0)
    assert per["P"] == "WARN"
    assert summary["any_warning"] is True
    assert summary["any_shutdown"] is False
    # Orchestrator publishes iOT_Warning / iOT_Alarm from these summary flags
    assert (1.0 if summary["any_warning"] else 0.0) == 1.0
    assert (1.0 if summary["any_shutdown"] else 0.0) == 0.0


def test_alarm_high_threshold_takes_priority_over_warning():
    cfg = {
        "channels": [
            {
                "alias": "P",
                "warning": {"high": 80.0},
                "alarm": {"high": 100.0},
            }
        ],
    }
    eng = AlarmEngine(cfg)
    per, summary, _ = eng.evaluate({"P": 105.0}, 1.0)
    assert per["P"] == "SHUT"
    assert summary["any_warning"] is False
    assert summary["any_shutdown"] is True


def test_warning_low_threshold():
    cfg = {
        "channels": [
            {
                "alias": "T",
                "warning": {"low": 10.0},
                "alarm": {"low": 0.0},
            }
        ],
    }
    eng = AlarmEngine(cfg)
    per, summary, _ = eng.evaluate({"T": 5.0}, 1.0)
    assert per["T"] == "WARN"
    assert summary["any_warning"] is True


def test_alarm_low_threshold():
    cfg = {
        "channels": [
            {
                "alias": "T",
                "warning": {"low": 10.0},
                "alarm": {"low": 5.0},
            }
        ],
    }
    eng = AlarmEngine(cfg)
    per, summary, _ = eng.evaluate({"T": 3.0}, 1.0)
    assert per["T"] == "SHUT"
    assert summary["any_shutdown"] is True


def test_enter_debounce_delays_transition_to_warn():
    cfg = {
        "channels": [
            {
                "alias": "P",
                "warning": {"high": 80.0, "high_enter_delay_s": 2.0},
                "alarm": {"high": 200.0},
            }
        ],
    }
    eng = AlarmEngine(cfg)
    per1, _, events1 = eng.evaluate({"P": 90.0}, 10.0)
    assert per1["P"] == "OK"
    assert not events1
    per2, _, events2 = eng.evaluate({"P": 90.0}, 11.0)
    assert per2["P"] == "OK"
    per3, _, events3 = eng.evaluate({"P": 90.0}, 12.0)
    assert per3["P"] == "WARN"
    assert len(events3) == 1
    assert events3[0]["to"] == "WARN"


def test_clear_debounce_delays_return_to_ok():
    cfg = {
        "channels": [
            {
                "alias": "P",
                "warning": {"high": 80.0, "high_clear_delay_s": 1.5},
                "alarm": {"high": 200.0},
            }
        ],
    }
    eng = AlarmEngine(cfg)
    eng.evaluate({"P": 90.0}, 0.0)
    per_a, _, _ = eng.evaluate({"P": 90.0}, 0.0)
    assert per_a["P"] == "WARN"
    eng.evaluate({"P": 50.0}, 10.0)
    per_b, _, ev_b = eng.evaluate({"P": 50.0}, 10.5)
    assert per_b["P"] == "WARN"
    assert not any(e.get("to") == "OK" for e in ev_b)
    per_c, _, ev_c = eng.evaluate({"P": 50.0}, 11.6)
    assert per_c["P"] == "OK"
    assert any(e.get("to") == "OK" for e in ev_c)


def test_iot_style_booleans_from_summary_match_orchestrator_semantics():
    """same keys orchestrator uses for iOT_Warning (any_warning) and iOT_Alarm (any_shutdown)."""
    cfg = {
        "channels": [
            {
                "alias": "A",
                "warning": {"high": 1.0},
                "alarm": {"high": 10.0},
            },
            {
                "alias": "B",
                "warning": {"high": 50.0},
                "alarm": {"high": 200.0},
            },
        ],
    }
    eng = AlarmEngine(cfg)
    _, summary_warn, _ = eng.evaluate({"A": 5.0, "B": 0.0}, 1.0)
    assert summary_warn["any_warning"] is True
    assert summary_warn["any_shutdown"] is False
    iot_w = 1.0 if summary_warn["any_warning"] else 0.0
    iot_a = 1.0 if summary_warn["any_shutdown"] else 0.0
    assert iot_w == 1.0 and iot_a == 0.0

    _, summary_alm, _ = eng.evaluate({"A": 5.0, "B": 250.0}, 2.0)
    assert summary_alm["any_shutdown"] is True
    iot_a2 = 1.0 if summary_alm["any_shutdown"] else 0.0
    assert iot_a2 == 1.0


def test_non_numeric_value_classifies_ok():
    cfg = {
        "channels": [
            {
                "alias": "X",
                "warning": {"high": 1.0},
                "alarm": {"high": 2.0},
            }
        ],
    }
    eng = AlarmEngine(cfg)
    per, summary, _ = eng.evaluate({"X": "bad"}, 1.0)
    assert per["X"] == "OK"
    assert summary["any_warning"] is False
    assert summary["any_shutdown"] is False


def test_any_shutdown_request_when_shut_action_requests_shutdown():
    cfg = {
        "channels": [
            {
                "alias": "P",
                "warning": {"high": 80.0},
                "alarm": {"high": 100.0, "action": "visible_alert_shutdown"},
            }
        ],
    }
    eng = AlarmEngine(cfg)
    _, summary, _ = eng.evaluate({"P": 110.0}, 1.0)
    assert summary["any_shutdown"] is True
    assert summary.get("any_shutdown_request") is True


def test_nan_classifies_ok():
    cfg = {
        "channels": [
            {
                "alias": "P",
                "warning": {"high": 80.0},
                "alarm": {"high": 100.0},
            }
        ],
    }
    eng = AlarmEngine(cfg)
    per, _, _ = eng.evaluate({"P": float("nan")}, 1.0)
    assert per["P"] == "OK"


def test_positive_infinity_triggers_shutdown_high():
    cfg = {
        "channels": [
            {
                "alias": "P",
                "warning": {"high": 80.0},
                "alarm": {"high": 100.0},
            }
        ],
    }
    eng = AlarmEngine(cfg)
    per, _, _ = eng.evaluate({"P": float("inf")}, 1.0)
    assert per["P"] == "SHUT"


def test_shutdown_type_hard_default():
    cfg = {
        "channels": [
            {
                "alias": "P",
                "warning": {"high": 80.0},
                "alarm": {"high": 100.0},
            }
        ],
    }
    eng = AlarmEngine(cfg)
    _, summary, _ = eng.evaluate({"P": 110.0}, 1.0)
    assert summary["any_hard_shutdown"] is True
    assert summary["any_soft_shutdown"] is False


def test_shutdown_type_soft_explicit():
    cfg = {
        "channels": [
            {
                "alias": "P",
                "warning": {"high": 80.0},
                "alarm": {"high": 100.0, "shutdown_type": "soft"},
            }
        ],
    }
    eng = AlarmEngine(cfg)
    _, summary, _ = eng.evaluate({"P": 110.0}, 1.0)
    assert summary["any_soft_shutdown"] is True
    assert summary["any_hard_shutdown"] is False


def test_shutdown_type_mixed_channels():
    cfg = {
        "channels": [
            {
                "alias": "P",
                "warning": {"high": 80.0},
                "alarm": {"high": 100.0, "shutdown_type": "hard"},
            },
            {
                "alias": "T",
                "warning": {"high": 200.0},
                "alarm": {"high": 300.0, "shutdown_type": "soft"},
            },
        ],
    }
    eng = AlarmEngine(cfg)
    _, summary, _ = eng.evaluate({"P": 110.0, "T": 310.0}, 1.0)
    assert summary["any_hard_shutdown"] is True
    assert summary["any_soft_shutdown"] is True


def test_no_shutdown_type_flags_when_ok():
    cfg = {
        "channels": [
            {
                "alias": "P",
                "warning": {"high": 80.0},
                "alarm": {"high": 100.0, "shutdown_type": "soft"},
            }
        ],
    }
    eng = AlarmEngine(cfg)
    _, summary, _ = eng.evaluate({"P": 50.0}, 1.0)
    assert summary["any_soft_shutdown"] is False
    assert summary["any_hard_shutdown"] is False


def test_engine_running_exposed_in_summary():
    cfg = {
        "engine_running": {
            "source_alias": "RPM",
            "rpm_threshold": 500.0,
        },
        "channels": [],
    }
    eng = AlarmEngine(cfg)
    _, summary_off, _ = eng.evaluate({"RPM": 100.0}, 1.0)
    assert summary_off["engine_running"] is False

    _, summary_on, _ = eng.evaluate({"RPM": 600.0}, 2.0)
    assert summary_on["engine_running"] is True


def test_engine_running_false_when_no_alias_configured():
    cfg = {
        "engine_running": {},
        "channels": [],
    }
    eng = AlarmEngine(cfg)
    _, summary, _ = eng.evaluate({}, 1.0)
    assert summary["engine_running"] is False
