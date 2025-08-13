# Author: T. Onkst | Date: 08122025

"""
UI process entrypoint. Provides Console window and a Display window.
Skeleton only; no real IPC yet.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget, QTableWidget, QTableWidgetItem, QPushButton
from PySide6.QtGui import QColor, QBrush

from ..core.ipc.bus import create_ui_subscriber, create_ui_control_push


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
    btn_stats = QPushButton("Log Statistics")
    btn_record = QPushButton("Start Recording")
    btn_export = QPushButton("Export Workbook")
    layout.addWidget(btn_stats)
    layout.addWidget(btn_record)
    layout.addWidget(btn_export)
    layout.addWidget(table)
    window.resize(420, 240)
    window.show()

    sub = create_ui_subscriber()
    ctrl = create_ui_control_push()

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
                states = data.get("states", {})
                # Mark last message time for connection status
                import time as _t
                last_msg_time["t"] = _t.time()
                # Update table
                table.setRowCount(len(values))
                for row, (alias, val) in enumerate(values.items()):
                    alias_str = str(alias)
                    state = str(states.get(alias_str, "OK"))
                    item_alias = QTableWidgetItem(alias_str)
                    item_val = QTableWidgetItem(f"{val:.2f}")
                    item_unit = QTableWidgetItem(str(units.get(alias_str, "")))
                    # Color rows based on state
                    if state == "WARN":
                        brush = QBrush(QColor("#fff8b3"))  # pale yellow
                    elif state == "SHUT":
                        brush = QBrush(QColor("#ffb3b3"))  # pale red
                    else:
                        brush = QBrush()
                    item_alias.setBackground(brush)
                    item_val.setBackground(brush)
                    item_unit.setBackground(brush)
                    table.setItem(row, 0, item_alias)
                    table.setItem(row, 1, item_val)
                    table.setItem(row, 2, item_unit)
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

    def on_log_stats():
        if ctrl is None:
            return
        try:
            import json
            msg = json.dumps({"type": "stats_snapshot", "mode": None}).encode("utf-8")
            ctrl["control_push"].send(msg)
        except Exception:
            pass

    btn_stats.clicked.connect(on_log_stats)
    
    def on_export():
        if ctrl is None:
            return
        try:
            import json
            msg = json.dumps({"type": "export_excel"}).encode("utf-8")
            ctrl["control_push"].send(msg)
        except Exception:
            pass

    btn_export.clicked.connect(on_export)

    # Simple client-side toggle; assumes success (later can read a status flag from telemetry)
    state = {"recording": False}

    def on_record_toggle():
        if ctrl is None:
            return
        try:
            import json
            btn_record.setEnabled(False)
            if not state["recording"]:
                msg = json.dumps({"type": "start_recording"}).encode("utf-8")
                ctrl["control_push"].send(msg)
                state["recording"] = True
                btn_record.setText("Stop Recording")
                btn_export.setEnabled(False)
            else:
                msg = json.dumps({"type": "stop_recording"}).encode("utf-8")
                ctrl["control_push"].send(msg)
                state["recording"] = False
                btn_record.setText("Start Recording")
                btn_export.setEnabled(True)
        except Exception:
            pass
        finally:
            # brief debounce
            from PySide6.QtCore import QTimer
            QTimer.singleShot(250, lambda: btn_record.setEnabled(True))

    btn_record.clicked.connect(on_record_toggle)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())


