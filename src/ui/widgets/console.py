# Author: T. Onkst | Date: 08182025

from __future__ import annotations

import time
import json
from typing import Any, Dict, List
from pathlib import Path

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (
        QMainWindow,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QGroupBox,
        QTextEdit,
        QFrame,
        QApplication,
        QDialog,
        QMainWindow as _QMainWindow,
    )
except Exception:
    raise


class ConsoleWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Engine Test Data Recorder — Console")
        self.resize(420, 900)
        # Telemetry state
        self._last_rx_ts: float = 0.0
        self._last_payload: Dict[str, Any] = {}
        # UI
        self._init_ui()
        # Telemetry
        self._init_telemetry()

    def _init_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)
        v = QVBoxLayout(root)

        # Header with Close Plugins (reopen launcher)
        header = QHBoxLayout()
        self.btn_close_plugins = QPushButton("Close Plugins")
        self.btn_close_plugins.clicked.connect(self._reopen_launcher)  # type: ignore
        header.addWidget(self.btn_close_plugins)
        header.addStretch(1)
        v.addLayout(header)

        # Plugin tiles (stacked top-to-bottom)
        plugins_box = QGroupBox("Plugins")
        pv = QVBoxLayout(plugins_box)
        pv.setContentsMargins(8, 8, 8, 8)
        pv.setSpacing(8)
        self._tiles: Dict[str, QFrame] = {}
        for pid in self._load_selected_plugins():
            tile = self._create_tile(pid, color="#888888", subtitle="Unknown")
            pv.addWidget(tile)
            self._tiles[pid] = tile
        v.addWidget(plugins_box)

        # Controls (stacked top-to-bottom)
        controls_box = QGroupBox("Controls")
        cv = QVBoxLayout(controls_box)
        cv.setContentsMargins(8, 8, 8, 8)
        cv.setSpacing(8)
        self.btn_primary = QPushButton("Lock Test"); self.btn_primary.setEnabled(False)
        self.btn_export = QPushButton("Export Workbook"); self.btn_export.setEnabled(False)
        self.btn_stats = QPushButton("Log Statistics"); self.btn_stats.setEnabled(False)
        for b in (self.btn_primary, self.btn_export, self.btn_stats):
            cv.addWidget(b)
        v.addWidget(controls_box)

        # Create selected displays as separate windows
        self._display_windows: Dict[str, _QMainWindow] = {}
        for name in self._load_selected_displays():
            if name.lower().startswith("allchannels"):
                try:
                    from .channels_table import ChannelsTable
                    win = _QMainWindow(self)
                    win.setWindowTitle("All Channels Table")
                    table = ChannelsTable(win)
                    win.setCentralWidget(table)
                    win.resize(800, 600)
                    win.show()
                    self._display_windows["AllChannelsTable"] = win
                except Exception:
                    pass

        # Error / messages area
        msg_box = QGroupBox("Messages")
        mv = QVBoxLayout(msg_box)
        self.txt_messages = QTextEdit(); self.txt_messages.setReadOnly(True)
        mv.addWidget(self.txt_messages)
        v.addWidget(msg_box, 1)

        # Status bar
        status_bar = self.statusBar()
        self.lbl_conn = QLabel("Disconnected")
        self.lbl_rec = QLabel("Recording: Off")
        status_bar.addPermanentWidget(self.lbl_conn)
        status_bar.addPermanentWidget(self.lbl_rec)

        # Periodic UI refresh
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(250)
        self._ui_timer.timeout.connect(self._refresh_status)  # type: ignore
        self._ui_timer.start()

    def _reopen_launcher(self) -> None:
        # Fully close this console window, then reopen the launch configuration.
        try:
            from .launch_dialog import LaunchDialog
        except Exception:
            LaunchDialog = None  # type: ignore
        app = QApplication.instance()
        # Close any open display windows first
        try:
            for w in list(getattr(self, "_display_windows", {}).values()):
                try:
                    w.close()
                except Exception:
                    pass
            self._display_windows = {}
        except Exception:
            pass
        # Close current console immediately
        self.close()
        if LaunchDialog is None or app is None:
            return
        # Open launcher without parent for a clean session
        dlg = LaunchDialog(None)
        result = dlg.exec()
        if result == QDialog.Accepted:
            from .console import ConsoleWindow  # type: ignore
            # Keep persistent reference on the QApplication to avoid GC
            app._console = ConsoleWindow()  # type: ignore[attr-defined]
            app._console.show()  # type: ignore

    def _create_tile(self, title: str, color: str, subtitle: str = "") -> QFrame:
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        frame.setStyleSheet(f"QFrame {{ background-color: {color}; border-radius: 6px; }}")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(2)
        lbl_title = QLabel(title)
        lbl_title.setStyleSheet("QLabel { color: white; font-weight: 600; font-size: 14px; }")
        lbl_sub = QLabel(subtitle)
        lbl_sub.setStyleSheet("QLabel { color: white; font-size: 12px; }")
        lay.addWidget(lbl_title)
        lay.addWidget(lbl_sub)
        # Default compact height
        frame.setFixedHeight(48)
        # Attach refs
        frame._lbl_title = lbl_title  # type: ignore
        frame._lbl_sub = lbl_sub  # type: ignore
        return frame

    def _set_tile(self, tile: QFrame, color: str, subtitle: str) -> None:
        tile.setStyleSheet(f"QFrame {{ background-color: {color}; border-radius: 6px; }}")
        # Hide subtitle for compact OK state
        has_sub = bool(subtitle.strip())
        tile._lbl_sub.setText(subtitle if has_sub else "")  # type: ignore
        tile._lbl_sub.setVisible(has_sub)  # type: ignore
        tile.setFixedHeight(48 if not has_sub else 64)

    def _init_telemetry(self) -> None:
        self._sub = None
        try:
            from src.core.ipc.bus import create_ui_subscriber
            sockets = create_ui_subscriber()
            if sockets is not None:
                self._sub = sockets.telemetry_sub
        except Exception:
            self._sub = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(100)  # 10 Hz poll for messages
        self._poll_timer.timeout.connect(self._poll_telemetry)  # type: ignore
        self._poll_timer.start()

    def _poll_telemetry(self) -> None:
        if self._sub is None:
            return
        try:
            import zmq
            while True:
                try:
                    topic, payload = self._sub.recv_multipart(flags=zmq.NOBLOCK)
                except Exception:
                    break
                if topic != b"telemetry":
                    continue
                try:
                    msg = json.loads(payload.decode("utf-8"))
                except Exception:
                    continue
                self._last_payload = msg
                self._last_rx_ts = time.time()
        except Exception:
            pass

    def _refresh_status(self) -> None:
        now = time.time()
        connected = (now - self._last_rx_ts) < 1.0 if self._last_rx_ts > 0 else False
        self.lbl_conn.setText("Connected" if connected else "Disconnected")
        self.lbl_conn.setStyleSheet("color: #2ecc71;" if connected else "color: #e74c3c;")
        # Recording flag
        rec = False
        try:
            rec = bool(self._last_payload.get("recording", False))
        except Exception:
            rec = False
        self.lbl_rec.setText("Recording: On" if rec else "Recording: Off")
        self.lbl_rec.setStyleSheet("color: #2ecc71;" if rec else "color: #bdc3c7;")
        # Update plugin tiles with health/config policy
        for pid, tile in self._tiles.items():
            if not connected:
                self._set_tile(tile, "#888888", "Unknown")
                continue
            if pid == "NI_DAQ":
                health_ok = None
                try:
                    vals = self._last_payload.get("values") or {}
                    if isinstance(vals, dict):
                        v = vals.get("NI_DAQ/health_ok")
                        if v is not None:
                            health_ok = bool(int(v))
                except Exception:
                    health_ok = None
                if health_ok is True:
                    self._set_tile(tile, "#27ae60", "")
                elif health_ok is False:
                    self._set_tile(tile, "#c0392b", "Error")
                else:
                    # Fallback to config validity
                    if self._plugin_config_ok(pid):
                        self._set_tile(tile, "#27ae60", "")
                    else:
                        self._set_tile(tile, "#c0392b", "Invalid config")
            else:
                if self._plugin_config_ok(pid):
                    self._set_tile(tile, "#27ae60", "")
                else:
                    self._set_tile(tile, "#c0392b", "Invalid config")

        # Push latest values into display windows
        try:
            vals = self._last_payload.get("values") if isinstance(self._last_payload, dict) else None
            units = self._last_payload.get("units") if isinstance(self._last_payload, dict) else None
            if "AllChannelsTable" in self._display_windows:
                table = self._display_windows["AllChannelsTable"].centralWidget()
                if hasattr(table, "update_data"):
                    table.update_data(vals, units)
        except Exception:
            pass

    # Helpers
    def _plugin_config_ok(self, plugin_id: str) -> bool:
        cfg_map = {
            "NI_DAQ": "ni_daq.yaml",
            "CAN": "can.yaml",
            "CCP": "ccp.yaml",
            "Calculated_Channels": "calculated_channels.yaml",
            "Cycle": "cycle.yaml",
            "LoadBank": "loadbank.yaml",
            "Modbus": "modbus.yaml",
            "Statistics": "statistics.yaml",
            "Vaisala": "vaisala.yaml",
            "EngineTest": "engine_test.yaml",
            "Channel_Manager": "channel_manager.yaml",
        }
        fname = cfg_map.get(plugin_id)
        if not fname:
            return False
        cfg_path = Path(__file__).resolve().parents[3] / "configs" / fname
        if not cfg_path.exists():
            return False
        try:
            import yaml  # type: ignore
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            return isinstance(data, dict) and bool(data)
        except Exception:
            return False

    def _load_selected_plugins(self) -> List[str]:
        ALWAYS_ON = ["Channel_Manager", "EngineTest"]
        path = Path(__file__).resolve().parents[3] / "configs" / "plugins.yaml"
        try:
            import yaml  # type: ignore
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            sel = [str(x) for x in (data.get("selected_plugins") or [])]
        except Exception:
            sel = []
        # Ensure always-on plugins are included and appear first
        # Place ALWAYS_ON first, others alphabetical
        others = sorted([p for p in sel if p not in ALWAYS_ON])
        ordered = list(dict.fromkeys(ALWAYS_ON + others))
        return ordered

    def _load_selected_displays(self) -> List[str]:
        path = Path(__file__).resolve().parents[3] / "configs" / "plugins.yaml"
        try:
            import yaml  # type: ignore
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            sel = [str(x) for x in (data.get("selected_displays") or [])]
        except Exception:
            sel = []
        return sel


