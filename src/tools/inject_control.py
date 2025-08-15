# Author: T. Onkst | Date: 08132025

from __future__ import annotations

import argparse
import json
import sys

from pathlib import Path

from ..core.ipc.bus import create_ui_control_push


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Send a control message to inject a test failure into a plugin")
    parser.add_argument("--plugin", required=True, help="Plugin id, e.g., NI_DAQ")
    parser.add_argument("--mode", default="read_error", help="Failure mode, e.g., read_error")
    parser.add_argument("--count", type=int, default=1, help="How many failures to inject")
    parser.add_argument("--duration_s", type=float, default=0.0, help="Optional duration for time-based modes")
    args = parser.parse_args(argv)

    ctrl = create_ui_control_push()
    if ctrl is None:
        print("[ERROR] Control channel unavailable (pyzmq missing?)")
        return 2
    msg = {
        "type": "plugin_inject_fail",
        "plugin": args.plugin,
        "mode": args.mode,
        "count": int(args.count),
        "duration_s": float(args.duration_s),
    }
    ctrl["control_push"].send(json.dumps(msg).encode("utf-8"))
    print(f"[INFO] Sent: {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


