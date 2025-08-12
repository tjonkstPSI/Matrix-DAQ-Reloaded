# Author: T. Onkst | Date: 08122025

"""
Core process entrypoint. Loads configuration, initializes orchestrator, starts IPC, and runs the main loop.
Hardware I/O is not implemented here; plugins default to simulation until configured.
"""

from __future__ import annotations

import sys
from pathlib import Path

def main() -> int:
    # Deferred heavy imports to keep skeleton light
    from .orchestrator import Orchestrator

    project_root = Path(__file__).resolve().parents[2]
    configs_dir = project_root / "configs"

    orchestrator = Orchestrator(configs_dir=configs_dir)
    orchestrator.start()
    try:
        orchestrator.run()
    except KeyboardInterrupt:
        print("\n[INFO] KeyboardInterrupt received; stopping...")
        orchestrator.request_stop()
    finally:
        orchestrator.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())


