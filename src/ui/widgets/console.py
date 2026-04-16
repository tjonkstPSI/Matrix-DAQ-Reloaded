# Author: T. Onkst | Date: 08182025

from __future__ import annotations

import time
import json
from typing import Any, Dict, List, Optional
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
        QDockWidget,
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
        self._conn_latched: bool = False
        # Run state
        self._locked: bool = False
        self._prev_rec: bool = False
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
        self.btn_loadbank_panel = QPushButton("Load Bank Panel")
        self.btn_loadbank_panel.setCheckable(True)
        self.btn_loadbank_panel.setVisible("LoadBank" in self._load_selected_plugins())
        self.btn_loadbank_panel.toggled.connect(self._on_loadbank_panel_toggled)  # type: ignore
        header.addWidget(self.btn_loadbank_panel)
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
            # Enable Configure on NI_DAQ via right-click
            if pid == "NI_DAQ":
                tile.setContextMenuPolicy(Qt.CustomContextMenu)
                tile.customContextMenuRequested.connect(self._show_nidaq_menu)  # type: ignore
            if pid == "CCP":
                tile.setContextMenuPolicy(Qt.CustomContextMenu)
                tile.customContextMenuRequested.connect(self._show_ccp_menu)  # type: ignore
            if pid == "CAN":
                tile.setContextMenuPolicy(Qt.CustomContextMenu)
                tile.customContextMenuRequested.connect(self._show_can_menu)  # type: ignore
            if pid == "Modbus":
                tile.setContextMenuPolicy(Qt.CustomContextMenu)
                tile.customContextMenuRequested.connect(self._show_modbus_menu)  # type: ignore
            if str(pid).replace("_", "").replace(" ", "").lower() == "loadbank":
                tile.setContextMenuPolicy(Qt.CustomContextMenu)
                tile.customContextMenuRequested.connect(self._show_loadbank_menu)  # type: ignore
            if pid == "Calculated_Channels":
                tile.setContextMenuPolicy(Qt.CustomContextMenu)
                tile.customContextMenuRequested.connect(self._show_calculated_menu)  # type: ignore
            if pid == "Channel_Manager":
                tile.setContextMenuPolicy(Qt.CustomContextMenu)
                tile.customContextMenuRequested.connect(self._show_channel_manager_menu)  # type: ignore
            if pid == "Statistics":
                tile.setContextMenuPolicy(Qt.CustomContextMenu)
                tile.customContextMenuRequested.connect(self._show_statistics_menu)  # type: ignore
            if pid == "Vaisala":
                tile.setContextMenuPolicy(Qt.CustomContextMenu)
                tile.customContextMenuRequested.connect(self._show_vaisala_menu)  # type: ignore
            if pid == "Omega":
                tile.setContextMenuPolicy(Qt.CustomContextMenu)
                tile.customContextMenuRequested.connect(self._show_omega_menu)  # type: ignore
            if pid == "Cycle":
                tile.setContextMenuPolicy(Qt.CustomContextMenu)
                tile.customContextMenuRequested.connect(self._show_cycle_menu)  # type: ignore
            pv.addWidget(tile)
            self._tiles[pid] = tile
        v.addWidget(plugins_box)

        # Controls (stacked top-to-bottom)
        controls_box = QGroupBox("Controls")
        cv = QVBoxLayout(controls_box)
        cv.setContentsMargins(8, 8, 8, 8)
        cv.setSpacing(8)
        self.btn_primary = QPushButton("Lock Test"); self.btn_primary.setEnabled(False)
        self.btn_primary.clicked.connect(self._on_primary_clicked)  # type: ignore
        self.btn_export = QPushButton("Export Workbook"); self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._on_export_clicked)  # type: ignore
        self.btn_stats = QPushButton("Log Statistics"); self.btn_stats.setEnabled(False)
        self.btn_stats.clicked.connect(self._on_stats_clicked)  # type: ignore
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

        # Load Bank operator panel (dock — can be dragged to float as a separate window)
        self._lb_dock: Any = None
        self._lb_panel_main: Any = None
        self._lb_operator_windows: List[Any] = []
        if "LoadBank" in self._tiles:
            try:
                from .loadbank_control import LoadBankControlPanel
            except Exception:
                LoadBankControlPanel = None  # type: ignore
            if LoadBankControlPanel is not None:
                self._lb_panel_main = LoadBankControlPanel(self)
                try:
                    from src.core.ipc.bus import create_ui_control_push
                    _bus = create_ui_control_push()
                    self._lb_panel_main.set_bus(_bus)
                except Exception:
                    pass
                dock = QDockWidget("Load Bank — Operator", self)
                dock.setObjectName("LoadBankOperatorDock")
                dock.setAllowedAreas(
                    Qt.LeftDockWidgetArea
                    | Qt.RightDockWidgetArea
                    | Qt.BottomDockWidgetArea
                )
                dock.setWidget(self._lb_panel_main)
                dock.setMinimumWidth(320)
                self.addDockWidget(Qt.RightDockWidgetArea, dock)
                dock.hide()
                dock.visibilityChanged.connect(self._on_lb_dock_visibility_changed)  # type: ignore
                self._lb_dock = dock

        # Periodic UI refresh
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(50)
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
        try:
            dock = getattr(self, "_lb_dock", None)
            if dock is not None:
                dock.hide()
            btn = getattr(self, "btn_loadbank_panel", None)
            if btn is not None:
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
            for w in list(getattr(self, "_lb_operator_windows", []) or []):
                try:
                    w.close()
                except Exception:
                    pass
            self._lb_operator_windows = []
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

    def _show_nidaq_menu(self, pos) -> None:
        # Context menu for NI_DAQ tile: Configure
        try:
            from PySide6.QtWidgets import QMenu
        except Exception:
            return
        sender = self.sender()
        if not sender or not isinstance(sender, QFrame):
            return
        menu = QMenu(self)
        act_cfg = menu.addAction("Configure…")
        act = menu.exec_(sender.mapToGlobal(pos))
        if act == act_cfg:
            try:
                from .nidaq_config import NiDaqConfigDialog
            except Exception:
                NiDaqConfigDialog = None  # type: ignore
            if NiDaqConfigDialog is None:
                return
            dlg = NiDaqConfigDialog(self)
            dlg.exec()

    def _show_ccp_menu(self, pos) -> None:
        # Context menu for CCP tile: Configure
        try:
            from PySide6.QtWidgets import QMenu
        except Exception:
            return
        sender = self.sender()
        if not sender or not isinstance(sender, QFrame):
            return
        menu = QMenu(self)
        act_cfg = menu.addAction("Configure…")
        act = menu.exec_(sender.mapToGlobal(pos))
        if act == act_cfg:
            try:
                from .ccp_config import CCPConfigDialog
            except Exception:
                CCPConfigDialog = None  # type: ignore
            if CCPConfigDialog is None:
                return
            dlg = CCPConfigDialog(self)
            dlg.exec()

    def _show_can_menu(self, pos) -> None:
        # Context menu for CAN tile: Configure
        try:
            from PySide6.QtWidgets import QMenu
        except Exception:
            return
        sender = self.sender()
        if not sender or not isinstance(sender, QFrame):
            return
        menu = QMenu(self)
        act_cfg = menu.addAction("Configure…")
        act = menu.exec_(sender.mapToGlobal(pos))
        if act == act_cfg:
            try:
                from .can_config import CANConfigDialog
            except Exception:
                CANConfigDialog = None  # type: ignore
            if CANConfigDialog is None:
                return
            dlg = CANConfigDialog(self)
            dlg.exec()

    def _show_modbus_menu(self, pos) -> None:
        # Context menu for Modbus tile: Configure
        try:
            from PySide6.QtWidgets import QMenu
        except Exception:
            return
        sender = self.sender()
        if not sender or not isinstance(sender, QFrame):
            return
        menu = QMenu(self)
        act_cfg = menu.addAction("Configure…")
        act = menu.exec_(sender.mapToGlobal(pos))
        if act == act_cfg:
            try:
                from .modbus_config import ModbusConfigDialog
            except Exception:
                ModbusConfigDialog = None  # type: ignore
            if ModbusConfigDialog is None:
                return
            dlg = ModbusConfigDialog(self)
            dlg.exec()

    def _show_calculated_menu(self, pos) -> None:
        # Context menu for Calculated_Channels tile: Configure
        try:
            from PySide6.QtWidgets import QMenu
        except Exception:
            return
        sender = self.sender()
        if not sender or not isinstance(sender, QFrame):
            return
        menu = QMenu(self)
        act_cfg = menu.addAction("Configure…")
        act = menu.exec_(sender.mapToGlobal(pos))
        if act == act_cfg:
            try:
                from .calculated_config import CalculatedConfigDialog
            except Exception:
                CalculatedConfigDialog = None  # type: ignore
            if CalculatedConfigDialog is None:
                return
            dlg = CalculatedConfigDialog(self)
            dlg.exec()

    def _show_loadbank_menu(self, pos) -> None:
        # Context menu for LoadBank tile: Configure
        try:
            from PySide6.QtWidgets import QMenu, QMessageBox
        except Exception:
            return
        sender = self.sender()
        if sender is None:
            return
        anchor = sender if hasattr(sender, "mapToGlobal") else self
        menu = QMenu(self)
        act_cfg = menu.addAction("Configure…")
        act_panel = menu.addAction("Show operator panel…")
        act_win = menu.addAction("Open operator panel in new window…")
        act = menu.exec_(anchor.mapToGlobal(pos))
        if act == act_panel:
            self._show_loadbank_operator_dock(True)
            return
        if act == act_win:
            self._open_loadbank_operator_window()
            return
        if act == act_cfg:
            try:
                from .loadbank_config import LoadBankConfigDialog
            except Exception as e:
                try:
                    QMessageBox.critical(self, "LoadBank Configure Error", f"Failed to import LoadBank config dialog:\n{e}")
                except Exception:
                    pass
                return
            try:
                dlg = LoadBankConfigDialog(self)
                dlg.exec()
            except Exception as e:
                try:
                    QMessageBox.critical(self, "LoadBank Configure Error", f"Failed to open LoadBank config dialog:\n{e}")
                except Exception:
                    pass

    def _show_channel_manager_menu(self, pos) -> None:
        # Context menu for Channel_Manager tile: Configure
        try:
            from PySide6.QtWidgets import QMenu
        except Exception:
            return
        sender = self.sender()
        if not sender or not isinstance(sender, QFrame):
            return
        menu = QMenu(self)
        act_cfg = menu.addAction("Configure…")
        act = menu.exec_(sender.mapToGlobal(pos))
        if act == act_cfg:
            try:
                from .channel_manager_config import ChannelManagerConfigDialog
            except Exception:
                ChannelManagerConfigDialog = None  # type: ignore
            if ChannelManagerConfigDialog is None:
                return
            dlg = ChannelManagerConfigDialog(self)
            dlg.exec()

    def _show_statistics_menu(self, pos) -> None:
        try:
            from PySide6.QtWidgets import QMenu, QMessageBox
        except Exception:
            return
        sender = self.sender()
        if sender is None:
            return
        anchor = sender if hasattr(sender, "mapToGlobal") else self
        menu = QMenu(self)
        act_cfg = menu.addAction("Configure…")
        act = menu.exec_(anchor.mapToGlobal(pos))
        if act == act_cfg:
            try:
                from .statistics_config import StatisticsConfigDialog
            except Exception as e:
                try:
                    QMessageBox.critical(self, "Statistics Configure Error", f"Failed to import Statistics config dialog:\n{e}")
                except Exception:
                    pass
                return
            try:
                dlg = StatisticsConfigDialog(self)
                dlg.exec()
            except Exception as e:
                try:
                    QMessageBox.critical(self, "Statistics Configure Error", f"Failed to open Statistics config dialog:\n{e}")
                except Exception:
                    pass

    def _show_vaisala_menu(self, pos) -> None:
        try:
            from PySide6.QtWidgets import QMenu, QMessageBox
        except Exception:
            return
        sender = self.sender()
        if sender is None:
            return
        anchor = sender if hasattr(sender, "mapToGlobal") else self
        menu = QMenu(self)
        act_cfg = menu.addAction("Configure…")
        act = menu.exec_(anchor.mapToGlobal(pos))
        if act == act_cfg:
            try:
                from .vaisala_config import VaisalaConfigDialog
            except Exception as e:
                try:
                    QMessageBox.critical(self, "Vaisala Configure Error", f"Failed to import Vaisala config dialog:\n{e}")
                except Exception:
                    pass
                return
            try:
                dlg = VaisalaConfigDialog(self)
                dlg.exec()
            except Exception as e:
                try:
                    QMessageBox.critical(self, "Vaisala Configure Error", f"Failed to open Vaisala config dialog:\n{e}")
                except Exception:
                    pass

    def _show_omega_menu(self, pos) -> None:
        try:
            from PySide6.QtWidgets import QMenu, QMessageBox
        except Exception:
            return
        sender = self.sender()
        if sender is None:
            return
        anchor = sender if hasattr(sender, "mapToGlobal") else self
        menu = QMenu(self)
        act_cfg = menu.addAction("Configure…")
        act = menu.exec_(anchor.mapToGlobal(pos))
        if act == act_cfg:
            try:
                from .omega_config import OmegaConfigDialog
            except Exception as e:
                try:
                    QMessageBox.critical(self, "Omega Configure Error", f"Failed to import Omega config dialog:\n{e}")
                except Exception:
                    pass
                return
            try:
                dlg = OmegaConfigDialog(self)
                dlg.exec()
            except Exception as e:
                try:
                    QMessageBox.critical(self, "Omega Configure Error", f"Failed to open Omega config dialog:\n{e}")
                except Exception:
                    pass

    def _show_cycle_menu(self, pos) -> None:
        try:
            from PySide6.QtWidgets import QMenu, QMessageBox
        except Exception:
            return
        sender = self.sender()
        if sender is None:
            return
        anchor = sender if hasattr(sender, "mapToGlobal") else self
        menu = QMenu(self)
        act_cfg = menu.addAction("Configure…")
        act = menu.exec_(anchor.mapToGlobal(pos))
        if act == act_cfg:
            try:
                from .cycle_config import CycleConfigDialog
            except Exception as e:
                try:
                    QMessageBox.critical(self, "Cycle Configure Error", f"Failed to import Cycle config dialog:\n{e}")
                except Exception:
                    pass
                return
            try:
                dlg = CycleConfigDialog(self)
                dlg.exec()
            except Exception as e:
                try:
                    QMessageBox.critical(self, "Cycle Configure Error", f"Failed to open Cycle config dialog:\n{e}")
                except Exception:
                    pass

    def _set_tile(self, tile: QFrame, color: str, subtitle: str) -> None:
        tile.setStyleSheet(f"QFrame {{ background-color: {color}; border-radius: 6px; }}")
        # Hide subtitle for compact OK state
        has_sub = bool(subtitle.strip())
        tile._lbl_sub.setText(subtitle if has_sub else "")  # type: ignore
        tile._lbl_sub.setVisible(has_sub)  # type: ignore
        tile.setFixedHeight(48 if not has_sub else 64)

    def _on_loadbank_panel_toggled(self, checked: bool) -> None:
        self._show_loadbank_operator_dock(checked)

    def _on_lb_dock_visibility_changed(self, visible: bool) -> None:
        btn = getattr(self, "btn_loadbank_panel", None)
        if btn is None:
            return
        if btn.isChecked() != bool(visible):
            btn.blockSignals(True)
            btn.setChecked(bool(visible))
            btn.blockSignals(False)

    def _show_loadbank_operator_dock(self, visible: bool) -> None:
        dock = getattr(self, "_lb_dock", None)
        if dock is None:
            return
        dock.setVisible(bool(visible))
        btn = getattr(self, "btn_loadbank_panel", None)
        if btn is not None and btn.isChecked() != bool(visible):
            btn.blockSignals(True)
            btn.setChecked(bool(visible))
            btn.blockSignals(False)

    def _open_loadbank_operator_window(self) -> None:
        try:
            from .loadbank_control import LoadBankControlPanel
        except Exception:
            return
        win = _QMainWindow(self)
        win.setWindowTitle("Load Bank — Operator")
        panel = LoadBankControlPanel(win)
        try:
            from src.core.ipc.bus import create_ui_control_push

            panel.set_bus(create_ui_control_push())
        except Exception:
            pass
        win.setCentralWidget(panel)
        win.resize(380, 560)
        win.show()
        try:
            self._lb_operator_windows.append(win)
        except Exception:
            pass

    def _loadbank_ready_from_values(self, vals: Dict[str, Any]) -> Optional[bool]:
        try:
            if not hasattr(self, "_lb_ready_alias"):
                self._lb_ready_alias = "LB Ready"
                try:
                    path = Path(__file__).resolve().parents[3] / "configs" / "loadbank.yaml"
                    import yaml  # type: ignore

                    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    exposes = data.get("expose_channels") or {}
                    self._lb_ready_alias = str(exposes.get("ready_alias", "LB Ready"))
                except Exception:
                    pass
            v = vals.get(self._lb_ready_alias)
            if v is None:
                return None
            return bool(int(float(v)))
        except Exception:
            return None

    def _refresh_loadbank_panels(self, vals: Dict[str, Any]) -> None:
        ready = self._loadbank_ready_from_values(vals)
        main = getattr(self, "_lb_panel_main", None)
        if main is not None and hasattr(main, "update_values"):
            main.update_values(vals)
            if hasattr(main, "set_link_status"):
                main.set_link_status(bool(self._conn_latched), device_ready=ready)
        for w in list(getattr(self, "_lb_operator_windows", []) or []):
            try:
                if not w.isVisible():
                    continue
                cw = w.centralWidget()
                if cw is not None and hasattr(cw, "update_values"):
                    cw.update_values(vals)
                    if hasattr(cw, "set_link_status"):
                        cw.set_link_status(bool(self._conn_latched), device_ready=ready)
            except Exception:
                pass
        try:
            self._lb_operator_windows = [w for w in self._lb_operator_windows if w.isVisible()]
        except Exception:
            pass

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
        self._poll_timer.setInterval(20)  # 50 Hz poll for messages
        self._poll_timer.timeout.connect(self._poll_telemetry)  # type: ignore
        self._poll_timer.start()
        try:
            from src.core.ipc.bus import create_ui_control_push
            _sync_bus = create_ui_control_push()
            if _sync_bus is not None:
                _msg = json.dumps({"type": "sync_plugin_selections"}).encode("utf-8")
                _sync_bus["control_push"].send(_msg)
        except Exception:
            pass
        # Progress dialog placeholder
        self._merge_dlg = None

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
                try:
                    msg = json.loads(payload.decode("utf-8"))
                except Exception:
                    continue
                if topic == b"telemetry":
                    self._last_payload = msg
                    self._last_rx_ts = time.time()
                elif topic == b"status":
                    self._handle_status_msg(msg)
        except Exception:
            pass

    def _refresh_status(self) -> None:
        now = time.time()
        age = (now - self._last_rx_ts) if self._last_rx_ts > 0 else 1e9
        # Use hysteresis so brief telemetry stalls do not cause UI flicker.
        connect_threshold_s = 1.0
        disconnect_threshold_s = 3.0
        if self._conn_latched:
            if age > disconnect_threshold_s:
                self._conn_latched = False
        else:
            if age < connect_threshold_s:
                self._conn_latched = True
        connected = self._conn_latched
        self.lbl_conn.setText("Connected" if connected else "Disconnected")
        self.lbl_conn.setStyleSheet("color: #2ecc71;" if connected else "color: #e74c3c;")
        # Recording flag
        rec = False
        try:
            rec = bool(self._last_payload.get("recording", False))
        except Exception:
            rec = False
        # If recording just stopped, return to ready/idle (unlock)
        if self._prev_rec and not rec:
            self._locked = False
        self._prev_rec = rec
        self.lbl_rec.setText("Recording: On" if rec else "Recording: Off")
        self.lbl_rec.setStyleSheet("color: #2ecc71;" if rec else "color: #bdc3c7;")
        # Update primary button enable/state
        # Enabled when connected and required plugins have valid configs
        connected = self._conn_latched
        can_lock = connected and all(self._plugin_config_ok(pid) for pid in self._tiles.keys())
        self.btn_primary.setEnabled(can_lock)
        # Export allowed only when connected and not recording
        self.btn_export.setEnabled(connected and not rec)
        # Log Statistics allowed only when connected, recording, and Statistics plugin selected
        has_stats = "Statistics" in self._tiles
        self.btn_stats.setEnabled(connected and rec and has_stats)
        # Toggle label based on recording flag and an internal lock flag
        label = "Start Recording" if self._locked and not rec else ("Stop Recording" if self._locked and rec else "Lock Test")
        if self.btn_primary.text() != label:
            self.btn_primary.setText(label)
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
            states = self._last_payload.get("states") if isinstance(self._last_payload, dict) else None
            if "AllChannelsTable" in self._display_windows:
                table = self._display_windows["AllChannelsTable"].centralWidget()
                if hasattr(table, "update_data"):
                    table.update_data(vals, units, states)
            if isinstance(vals, dict):
                self._refresh_loadbank_panels(vals)
        except Exception:
            pass

    def _handle_status_msg(self, msg: Dict[str, Any]) -> None:
        t = str(msg.get("type", ""))
        if t == "merge_progress":
            try:
                from PySide6.QtWidgets import QProgressDialog
            except Exception:
                QProgressDialog = None  # type: ignore
            if QProgressDialog is None:
                return
            pct = float(msg.get("percent", 0.0))
            detail = str(msg.get("detail", ""))
            if self._merge_dlg is None:
                self._merge_dlg = QProgressDialog("Combining files…", None, 0, 100, self)
                self._merge_dlg.setWindowTitle("Finalizing Data")
                self._merge_dlg.setAutoClose(False)
                self._merge_dlg.setAutoReset(False)
                self._merge_dlg.setModal(False)
                self._merge_dlg.show()
            self._merge_dlg.setValue(int(max(0, min(100, pct*100))))
            if detail:
                self._merge_dlg.setLabelText(f"Combining files… {int(pct*100)}%\n{detail}")
        elif t == "merge_done":
            ok = bool(msg.get("ok", True))
            if self._merge_dlg is not None:
                self._merge_dlg.setValue(100 if ok else 0)
                self._merge_dlg.close()
                self._merge_dlg = None
            try:
                if ok:
                    self.txt_messages.append("[INFO] Data combine complete.")
                else:
                    self.txt_messages.append(f"[WARN] Data combine failed: {msg.get('error','unknown')}")
            except Exception:
                pass

    def _on_export_clicked(self) -> None:
        # Request Excel export from Core; Core will export latest run (run_dir or last_run_dir)
        ctrl = None
        try:
            from src.core.ipc.bus import create_ui_control_push
            ctrl = create_ui_control_push()
        except Exception:
            ctrl = None
        if ctrl is None:
            return
        try:
            msg = json.dumps({"type": "export_excel"}).encode("utf-8")
            ctrl["control_push"].send(msg)
            self.btn_export.setEnabled(False)
            try:
                self.txt_messages.append("[INFO] Export requested; running in background…")
            except Exception:
                pass
        except Exception:
            pass

    def _on_stats_clicked(self) -> None:
        # Request manual stats snapshot from Core
        ctrl = None
        try:
            from src.core.ipc.bus import create_ui_control_push
            ctrl = create_ui_control_push()
        except Exception:
            ctrl = None
        if ctrl is None:
            return
        try:
            msg = json.dumps({"type": "stats_snapshot"}).encode("utf-8")
            ctrl["control_push"].send(msg)
            try:
                self.txt_messages.append("[INFO] Manual statistics snapshot requested.")
            except Exception:
                pass
        except Exception:
            pass

    def _on_primary_clicked(self) -> None:
        # Three-state behavior: Lock → Start → Stop
        rec = False
        try:
            rec = bool(self._last_payload.get("recording", False))
        except Exception:
            rec = False
        locked = bool(self._locked)
        if not locked:
            # Show Lock dialog (EngineTest metadata)
            try:
                from .lock_dialog import LockDialog
            except Exception:
                LockDialog = None  # type: ignore
            if LockDialog is None:
                return
            dlg = LockDialog(self)
            if dlg.exec() == QDialog.Accepted:
                # Mark locked; next press will Start Recording
                self._locked = True
                self.btn_primary.setText("Start Recording")
                try:
                    from src.core.ipc.bus import create_ui_control_push
                    _ctrl = create_ui_control_push()
                    if _ctrl is not None:
                        _msg = json.dumps({"type": "lock_test"}).encode("utf-8")
                        _ctrl["control_push"].send(_msg)
                except Exception:
                    pass
            return
        # If locked and not recording → start
        ctrl = None
        try:
            from src.core.ipc.bus import create_ui_control_push
            ctrl = create_ui_control_push()
        except Exception:
            ctrl = None
        if locked and not rec:
            if ctrl is None:
                return
            try:
                msg = json.dumps({"type": "start_recording"}).encode("utf-8")
                ctrl["control_push"].send(msg)
                self.btn_primary.setText("Stop Recording")
            except Exception:
                pass
            return
        # If locked and recording → stop
        if locked and rec:
            if ctrl is None:
                return
            try:
                msg = json.dumps({"type": "stop_recording"}).encode("utf-8")
                ctrl["control_push"].send(msg)
                msg_u = json.dumps({"type": "unlock_test"}).encode("utf-8")
                ctrl["control_push"].send(msg_u)
                # Immediately revert to ready/idle so operator can change metadata
                self._locked = False
                self.btn_primary.setText("Lock Test")
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
            "Omega": "omega.yaml",
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


