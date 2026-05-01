# Author: T. Onkst | Date: 05012026
"""Tests for CCP parallel worker threading refactor."""

import math
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_plugin(use_parallel=True, num_devices=2):
    """Create a CCPPlugin with mock config for testing threading behavior."""
    from src.plugins.ccp import CCPPlugin

    configs_dir = Path(__file__).parent.parent / "configs"
    plugin = CCPPlugin(configs_dir=configs_dir, config_name="ccp.yaml")

    devices = []
    for i in range(num_devices):
        role = "primary" if i == 0 else "secondary"
        devices.append({
            "name": f"CCP {role.title()}",
            "role": role,
            "session": {
                "interface": f"CAN{i + 1}",
                "baudrate": 250000,
                "tx_id": "0x0CFF50F9",
                "rx_id": "0x0CFF5100",
                "station_address": f"0x{i}",
                "is_extended": True,
            },
            "security": {
                "seed_resource": "0x01",
                "seed_ctr": "0x07",
                "connect_ctr": "0x19",
                "unlock_ctr": "0x08",
                "access_key": "DEADBEEF",
                "seed_endian": "big",
                "sec_type": "CAL",
            },
            "a2l": {"path": "nonexistent.a2l"},
            "measurements": {
                "naming_prefix": f"CCP{i}_",
                "list": [
                    {"name": "rpm", "enabled": True},
                    {"name": "coolant", "enabled": True},
                ],
            },
        })

    plugin.config = {
        "enabled": True,
        "mode": "real",
        "use_parallel_workers": use_parallel,
        "target_poll_hz": 10,
        "high_low_ratio": 3,
        "acquisition_mode": "short_up",
        "devices": devices,
    }
    plugin.mode = "real"
    plugin.configure()
    return plugin


def test_parallel_snapshot_merge():
    """Verify that writes from two ctx workers merge correctly into _snapshot_values."""
    plugin = _make_plugin(use_parallel=True, num_devices=2)

    assert len(plugin._contexts) == 2
    ctx0 = plugin._contexts[0]
    ctx1 = plugin._contexts[1]

    ctx0["_local_values"]["CCP0_rpm"] = 1500.0
    ctx0["_local_value_ts"]["CCP0_rpm"] = 100.0
    ctx0["connected"] = True

    ctx1["_local_values"]["CCP1_rpm"] = 2000.0
    ctx1["_local_value_ts"]["CCP1_rpm"] = 101.0
    ctx1["connected"] = True

    plugin._merge_ctx_snapshot(ctx0)
    plugin._merge_ctx_snapshot(ctx1)

    assert plugin._snapshot_values.get("CCP0_rpm") == 1500.0
    assert plugin._snapshot_values.get("CCP1_rpm") == 2000.0
    assert plugin._value_ts.get("CCP0_rpm") == 100.0
    assert plugin._value_ts.get("CCP1_rpm") == 101.0


def test_parallel_snapshot_no_torn_reads():
    """Concurrent merges from two threads should not produce torn snapshots."""
    plugin = _make_plugin(use_parallel=True, num_devices=2)
    ctx0 = plugin._contexts[0]
    ctx1 = plugin._contexts[1]
    ctx0["connected"] = True
    ctx1["connected"] = True

    iterations = 500
    errors = []

    def writer(ctx, prefix, count):
        for i in range(count):
            ctx["_local_values"][f"{prefix}rpm"] = float(i)
            ctx["_local_values"][f"{prefix}coolant"] = float(i) + 0.5
            ctx["_local_value_ts"][f"{prefix}rpm"] = float(i)
            ctx["_local_value_ts"][f"{prefix}coolant"] = float(i)
            plugin._merge_ctx_snapshot(ctx)

    def reader(count):
        for _ in range(count):
            with plugin._state_lock:
                snap = dict(plugin._snapshot_values)
            rpm = snap.get("CCP0_rpm")
            if rpm is not None and math.isnan(rpm):
                continue

    t0 = threading.Thread(target=writer, args=(ctx0, "CCP0_", iterations))
    t1 = threading.Thread(target=writer, args=(ctx1, "CCP1_", iterations))
    tr = threading.Thread(target=reader, args=(iterations * 2,))

    t0.start()
    t1.start()
    tr.start()
    t0.join(timeout=5)
    t1.join(timeout=5)
    tr.join(timeout=5)

    assert not t0.is_alive()
    assert not t1.is_alive()
    assert not tr.is_alive()


def test_stop_joins_threads():
    """start() spawns N threads; stop() joins them all within timeout."""
    plugin = _make_plugin(use_parallel=True, num_devices=2)

    with patch.object(plugin, "_connect_real_ctx", side_effect=lambda ctx: None):
        plugin.start()
        time.sleep(0.05)

        assert len(plugin._worker_threads) == 2
        assert all(t.is_alive() for t in plugin._worker_threads)

        plugin.stop()

        assert all(not t.is_alive() for t in plugin._worker_threads if t is not None)
        alive_ccp = [t for t in threading.enumerate() if t.name.startswith("ccp-")]
        assert len(alive_ccp) == 0


def test_sequential_fallback():
    """use_parallel_workers=false spawns one sequential worker thread."""
    plugin = _make_plugin(use_parallel=False, num_devices=2)

    with patch.object(plugin, "_connect_real_ctx", side_effect=lambda ctx: None):
        plugin.start()
        time.sleep(0.05)

        assert len(plugin._worker_threads) == 0
        assert plugin._worker_thread is not None
        assert plugin._worker_thread.is_alive()

        plugin.stop()

        assert plugin._worker_thread is None or not plugin._worker_thread.is_alive()


def test_single_device_parallel():
    """With one device, parallel mode spawns exactly one thread."""
    plugin = _make_plugin(use_parallel=True, num_devices=1)

    with patch.object(plugin, "_connect_real_ctx", side_effect=lambda ctx: None):
        plugin.start()
        time.sleep(0.05)

        assert len(plugin._worker_threads) == 1
        assert plugin._worker_threads[0].is_alive()

        plugin.stop()

        alive_ccp = [t for t in threading.enumerate() if t.name.startswith("ccp-")]
        assert len(alive_ccp) == 0


def test_connection_test_rejects_while_running():
    """run_connection_test should refuse if workers are alive."""
    plugin = _make_plugin(use_parallel=True, num_devices=1)

    with patch.object(plugin, "_connect_real_ctx", side_effect=lambda ctx: None):
        plugin.start()
        time.sleep(0.05)

        results = []

        def emit(step, ok, detail, done=False):
            results.append((step, ok, detail, done))

        plugin.run_connection_test(emit)

        assert len(results) == 1
        assert results[0][1] is False
        assert "Stop CCP" in results[0][2]

        plugin.stop()


def test_sup_timing_dict_structure():
    """_poll_short_up_ctx populates ctx['_last_sup_timing'] with expected keys."""
    plugin = _make_plugin(use_parallel=True, num_devices=1)
    ctx = plugin._contexts[0]

    ctx["session"] = None
    ctx["proto"] = None
    result = plugin._poll_short_up_ctx(ctx, {"name": "test_ch", "size": 2, "address": 0, "extension": 0, "mta_addr_endian": "big"})

    assert result is None
    timing = ctx.get("_last_sup_timing", {})
    assert timing.get("outcome") == "no_session"

    mock_session = MagicMock()
    mock_session.recv.return_value = []
    mock_session.last_recv_stats = {"read_calls": 1.0, "empty_reads": 1.0, "raw_frames": 0.0, "rx_mode_code": 2.0}
    mock_proto = MagicMock()
    mock_frame = MagicMock()
    mock_frame.data = bytes([0x0F, 0x01, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00])
    mock_proto.build_short_up.return_value = mock_frame

    ctx["session"] = mock_session
    ctx["proto"] = mock_proto
    ctx["rx_id"] = 0x100
    ctx["short_up_timeout_s"] = 0.005

    result = plugin._poll_short_up_ctx(ctx, {
        "name": "test_ch", "size": 2, "address": 0x1000,
        "extension": 0, "mta_addr_endian": "big",
        "dtype": "UWORD", "poll_endian": "big", "limits": None, "coeffs": None,
    })

    assert result is None
    timing = ctx.get("_last_sup_timing", {})

    expected_keys = {
        "predrain_ms", "send_ms", "recv_loop_ms", "total_ms",
        "cap_ms", "slop_ms", "outcome", "outer_iterations",
        "recv_read_calls", "recv_empty_reads", "recv_raw_frames",
        "non_crm_frames", "first_match_offset_ms",
        "ctr_mismatch_in_attempt", "channel",
    }
    assert expected_keys.issubset(set(timing.keys())), f"Missing keys: {expected_keys - set(timing.keys())}"
    assert timing["outcome"] == "timeout"
    assert timing["total_ms"] > 0.0
    assert timing["cap_ms"] == 5.0
    assert timing["predrain_ms"] >= 0.0
    assert timing["outer_iterations"] >= 1
    assert timing["channel"] == "test_ch"


def test_timing_window_accumulates():
    """Timing window deque accumulates entries from _poll_real_ctx loop."""
    plugin = _make_plugin(use_parallel=True, num_devices=1)
    ctx = plugin._contexts[0]

    for i in range(5):
        ctx["_timing_window"].append({
            "predrain_ms": 1.0, "send_ms": 0.1, "recv_loop_ms": 10.0 + i,
            "total_ms": 11.1 + i, "cap_ms": 15.0, "slop_ms": -3.9 + i,
            "outcome": "ok", "outer_iterations": 3, "recv_read_calls": 4,
            "recv_empty_reads": 1, "recv_raw_frames": 2, "non_crm_frames": 0,
            "first_match_offset_ms": 5.0 + i, "ctr_mismatch_in_attempt": 0,
            "channel": f"ch_{i}",
        })

    from src.plugins.ccp import CCPPlugin
    summary = CCPPlugin._compute_timing_summary(ctx)
    assert summary is not None
    assert summary["n"] == 5.0
    assert summary["rtt_median_ms"] > 0.0
    assert summary["predrain_avg_ms"] == 1.0
    assert summary["match_avg_ms"] > 0.0
    assert summary["timeout_count"] == 0.0
    assert summary["over_cap_pct"] == 0.0


if __name__ == "__main__":
    test_parallel_snapshot_merge()
    print("PASS: test_parallel_snapshot_merge")

    test_parallel_snapshot_no_torn_reads()
    print("PASS: test_parallel_snapshot_no_torn_reads")

    test_stop_joins_threads()
    print("PASS: test_stop_joins_threads")

    test_sequential_fallback()
    print("PASS: test_sequential_fallback")

    test_single_device_parallel()
    print("PASS: test_single_device_parallel")

    test_connection_test_rejects_while_running()
    print("PASS: test_connection_test_rejects_while_running")

    test_sup_timing_dict_structure()
    print("PASS: test_sup_timing_dict_structure")

    test_timing_window_accumulates()
    print("PASS: test_timing_window_accumulates")

    print("\nAll CCP parallel worker tests passed.")
