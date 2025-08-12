# Author: T. Onkst | Date: 08122025

"""
UI process entrypoint. Provides Console window and a Display window.
Skeleton only; no real IPC yet.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget, QTableWidget, QTableWidgetItem

from ..core.ipc.bus import create_ui_subscriber


def main() -> int:
    app = QApplication(sys.argv)
    window = QWidget()
    layout = QVBoxLayout(window)
    header = QLabel("UI skeleton running — live telemetry table")
    status = QLabel("Disconnected")
    status.setStyleSheet("color: red;")
    table = QTableWidget(0, 3)
    table.setHorizontalHeaderLabels(["Alias", "Value", "Unit"]) 
    layout.addWidget(header)
    layout.addWidget(status)
    layout.addWidget(table)
    window.resize(420, 240)
    window.show()

    sub = create_ui_subscriber()

    last_msg_time = {"t": 0.0}

    def poll():
        if sub is None:
            return
        try:
            import zmq
            flags = zmq.NOBLOCK
            while True:
                topic, payload = sub.telemetry_sub.recv_multipart(flags=flags)
                import json
                data = json.loads(payload.decode("utf-8"))
                values = data.get("values", {})
                units = data.get("units", {})
                # Mark last message time for connection status
                import time as _t
                last_msg_time["t"] = _t.time()
                # Update table
                table.setRowCount(len(values))
                for row, (alias, val) in enumerate(values.items()):
                    table.setItem(row, 0, QTableWidgetItem(str(alias)))
                    table.setItem(row, 1, QTableWidgetItem(f"{val:.2f}"))
                    table.setItem(row, 2, QTableWidgetItem(str(units.get(alias, ""))))
        except Exception:
            # No message available or other non-fatal issue
            pass
        # Update connection status based on recent message
        try:
            import time as _t
            now = _t.time()
            connected = (now - last_msg_time["t"]) < 1.0  # 1s freshness window
            if connected:
                status.setText("Connected")
                status.setStyleSheet("color: green;")
            else:
                status.setText("Disconnected")
                status.setStyleSheet("color: red;")
        except Exception:
            pass

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(100)  # 10 Hz UI poll
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())


