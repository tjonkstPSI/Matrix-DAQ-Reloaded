from __future__ import annotations

import json
import copy
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import (
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QTabBar,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
    )
except Exception:
    raise

from .nidaq_alias_picker import AliasPickerDialog
from .standard_channels import validate_alias


_COLS = [
    "Alias",
    "Unit",
    "Type",
    "Address",
    "Length",
    "Data Type",
    "Gain",
    "Offset",
    "Value",
]


class ModbusConfigDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure Modbus")
        self.resize(1100, 760)
        self._cfg_path = Path(__file__).resolve().parents[3] / "configs" / "modbus.yaml"
        self._cfg: Dict[str, Any] = {}
        self._devices: List[Dict[str, Any]] = []
        self._active_device_idx: int = -1
        self._sub = None
        self._testing = False
        self._init_ui()
        self._load()
        self._init_subscriber()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)

        tabs_row = QHBoxLayout()
        self.tabs = QTabBar(self)
        self.tabs.currentChanged.connect(self._on_tab_changed)  # type: ignore
        self.btn_add_device = QPushButton("Add Device", self)
        self.btn_add_device.clicked.connect(self._add_device)  # type: ignore
        self.btn_remove_device = QPushButton("Remove Device", self)
        self.btn_remove_device.clicked.connect(self._remove_device)  # type: ignore
        tabs_row.addWidget(self.tabs, 1)
        tabs_row.addWidget(self.btn_add_device)
        tabs_row.addWidget(self.btn_remove_device)
        root.addLayout(tabs_row)

        conn_box = QFormLayout()
        self.cmb_iface = QComboBox(self)
        self.cmb_iface.addItems(["TCP/IP", "RS485"])
        self.cmb_iface.currentTextChanged.connect(self._update_iface_fields)  # type: ignore

        self.txt_ip = QLineEdit(self)
        self.txt_port = QLineEdit(self)
        self.txt_port.setPlaceholderText("502")

        self.cmb_com = QComboBox(self)
        self.cmb_com.addItems(["COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8"])
        self.txt_unit_id = QLineEdit(self)
        self.txt_unit_id.setPlaceholderText("1")
        self.cmb_baud = QComboBox(self)
        self.cmb_baud.addItems(["9600", "19200", "38400", "57600", "115200"])
        self.cmb_serial_type = QComboBox(self)
        self.cmb_serial_type.addItems(["RTU", "ASCII"])
        self.cmb_word_order = QComboBox(self)
        self.cmb_word_order.addItems(["Little Endian", "Big Endian"])
        self.txt_device_name = QLineEdit(self)
        self.txt_device_name.setPlaceholderText("Device name")
        self.txt_device_name.textEdited.connect(self._on_device_name_changed)  # type: ignore

        conn_box.addRow("Device Name", self.txt_device_name)
        conn_box.addRow("Interface Type", self.cmb_iface)
        conn_box.addRow("IP Address (TCP)", self.txt_ip)
        conn_box.addRow("Network Port (TCP)", self.txt_port)
        conn_box.addRow("Com Port (RS485)", self.cmb_com)
        conn_box.addRow("Unit ID (RS485)", self.txt_unit_id)
        conn_box.addRow("Baud Rate (RS485)", self.cmb_baud)
        conn_box.addRow("Serial Type (RS485)", self.cmb_serial_type)
        conn_box.addRow("Word Order", self.cmb_word_order)
        root.addLayout(conn_box)

        root.addWidget(QLabel("Channels"))
        self.table = QTableWidget(self)
        self.table.setColumnCount(len(_COLS))
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.cellDoubleClicked.connect(self._on_cell_dblclick)  # type: ignore
        root.addWidget(self.table)

        row_btns = QHBoxLayout()
        self.btn_add = QPushButton("Add Channel", self)
        self.btn_add.clicked.connect(self._add_row)  # type: ignore
        self.btn_remove = QPushButton("Remove Selected", self)
        self.btn_remove.clicked.connect(self._remove_selected_rows)  # type: ignore
        self.btn_test = QPushButton("Test", self)
        self.btn_test.clicked.connect(self._run_test)  # type: ignore
        row_btns.addWidget(self.btn_add)
        row_btns.addWidget(self.btn_remove)
        row_btns.addWidget(self.btn_test)
        row_btns.addStretch(1)
        root.addLayout(row_btns)

        self.lbl_test = QLabel("Test: idle")
        root.addWidget(self.lbl_test)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        btns.accepted.connect(self._on_accept)  # type: ignore
        btns.rejected.connect(self.reject)  # type: ignore
        root.addWidget(btns)

    def _init_subscriber(self) -> None:
        try:
            from src.core.ipc.bus import create_ui_subscriber
            sockets = create_ui_subscriber()
            if sockets is not None:
                self._sub = sockets.telemetry_sub
        except Exception:
            self._sub = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(150)
        self._poll_timer.timeout.connect(self._poll_telemetry)  # type: ignore
        self._poll_timer.start()

    def _read_yaml(self, path: Path) -> Dict[str, Any]:
        try:
            import yaml  # type: ignore
            if not path.exists():
                return {}
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

    def _load(self) -> None:
        self._cfg = self._read_yaml(self._cfg_path)
        self._devices = self._load_devices_from_cfg(self._cfg)
        self.tabs.blockSignals(True)
        while self.tabs.count() > 0:
            self.tabs.removeTab(0)
        for dev in self._devices:
            self.tabs.addTab(str(dev.get("name", "Device")))
        self.tabs.blockSignals(False)
        if self._devices:
            self.tabs.setCurrentIndex(0)
            self._active_device_idx = 0
            self._load_device_ui(0)
        self._update_device_buttons()

    def _update_iface_fields(self) -> None:
        tcp = self.cmb_iface.currentText() == "TCP/IP"
        self.txt_ip.setEnabled(tcp)
        self.txt_port.setEnabled(tcp)
        self.cmb_com.setEnabled(not tcp)
        self.txt_unit_id.setEnabled(not tcp)
        self.cmb_baud.setEnabled(not tcp)
        self.cmb_serial_type.setEnabled(not tcp)

    def _blank_device(self, name: str) -> Dict[str, Any]:
        return {
            "name": name,
            "connection": {
                "interface_type": "TCP/IP",
                "ip_address": "",
                "network_port": 502,
                "com_port": "COM1",
                "unit_id": 1,
                "baud_rate": 115200,
                "serial_type": "RTU",
                "word_order": "big",
            },
            "reads": [],
        }

    def _load_devices_from_cfg(self, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
        devices = cfg.get("devices")
        if isinstance(devices, list) and devices:
            out: List[Dict[str, Any]] = []
            for i, dev in enumerate(devices):
                if not isinstance(dev, dict):
                    continue
                name = str(dev.get("name") or f"Device {i+1}")
                conn = dev.get("connection") or {}
                reads = [r for r in (dev.get("reads") or []) if isinstance(r, dict)]
                out.append({"name": name, "connection": dict(conn), "reads": list(reads)})
            if out:
                return out
        # Legacy single-device shape fallback.
        name = "Device 1"
        conn = dict(cfg.get("connection") or {})
        reads = [r for r in (cfg.get("reads") or []) if isinstance(r, dict)]
        if not conn:
            conn = dict(self._blank_device(name)["connection"])
        return [{"name": name, "connection": conn, "reads": reads}]

    def _save_current_device_ui(self) -> None:
        idx = self._active_device_idx
        if idx < 0 or idx >= len(self._devices):
            return
        self._devices[idx]["name"] = self.txt_device_name.text().strip() or f"Device {idx+1}"
        conn = {
            "interface_type": self.cmb_iface.currentText().strip(),
            "ip_address": self.txt_ip.text().strip(),
            "network_port": int(self.txt_port.text().strip() or "502"),
            "com_port": self.cmb_com.currentText().strip(),
            "unit_id": int(self.txt_unit_id.text().strip() or "1"),
            "baud_rate": int(self.cmb_baud.currentText().strip() or "115200"),
            "serial_type": self.cmb_serial_type.currentText().strip(),
            "word_order": "little" if self.cmb_word_order.currentText().startswith("Little") else "big",
        }
        reads: List[Dict[str, Any]] = []
        word_order = conn["word_order"]
        for r in range(self.table.rowCount()):
            item = self._row_to_read(r, word_order=word_order)
            if item is not None:
                reads.append(item)
        self._devices[idx]["connection"] = conn
        self._devices[idx]["reads"] = reads

    def _load_device_ui(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._devices):
            return
        dev = self._devices[idx]
        self.txt_device_name.setText(str(dev.get("name", f"Device {idx+1}")))
        conn = dev.get("connection") or {}
        iface = str(conn.get("interface_type", "TCP/IP"))
        self.cmb_iface.setCurrentText("RS485" if iface.upper().startswith("RS") else "TCP/IP")
        self.txt_ip.setText(str(conn.get("ip_address", "")))
        self.txt_port.setText(str(conn.get("network_port", "502")))
        self.cmb_com.setCurrentText(str(conn.get("com_port", "COM1")))
        self.txt_unit_id.setText(str(conn.get("unit_id", "1")))
        self.cmb_baud.setCurrentText(str(conn.get("baud_rate", "115200")))
        self.cmb_serial_type.setCurrentText(str(conn.get("serial_type", "RTU")).upper())
        self.cmb_word_order.setCurrentText("Little Endian" if str(conn.get("word_order", "big")).lower().startswith("little") else "Big Endian")
        self._update_iface_fields()
        self.table.setRowCount(0)
        for read in dev.get("reads", []) or []:
            if isinstance(read, dict):
                self._add_row(self._row_from_read(read))

    def _on_tab_changed(self, idx: int) -> None:
        old_idx = self._active_device_idx
        if old_idx >= 0:
            self._save_current_device_ui()
            self.tabs.setTabText(old_idx, str(self._devices[old_idx].get("name", f"Device {old_idx+1}")))
        self._active_device_idx = idx
        self._load_device_ui(idx)
        self._update_device_buttons()

    def _add_device(self) -> None:
        self._save_current_device_ui()
        if self._active_device_idx >= 0:
            self.tabs.setTabText(self._active_device_idx, str(self._devices[self._active_device_idx].get("name", f"Device {self._active_device_idx+1}")))
        name = f"Device {len(self._devices) + 1}"
        self._devices.append(self._blank_device(name))
        self.tabs.addTab(name)
        self.tabs.setCurrentIndex(self.tabs.count() - 1)
        self._update_device_buttons()

    def _remove_device(self) -> None:
        if len(self._devices) <= 1:
            return
        idx = self.tabs.currentIndex()
        if idx < 0:
            return
        self._devices.pop(idx)
        self.tabs.removeTab(idx)
        next_idx = min(idx, len(self._devices) - 1)
        self.tabs.setCurrentIndex(next_idx)
        self._active_device_idx = next_idx
        self._load_device_ui(next_idx)
        self._update_device_buttons()

    def _on_device_name_changed(self, text: str) -> None:
        idx = self._active_device_idx
        if idx < 0 or idx >= len(self._devices):
            return
        name = text.strip() or f"Device {idx+1}"
        self._devices[idx]["name"] = name
        self.tabs.setTabText(idx, name)

    def _update_device_buttons(self) -> None:
        self.btn_remove_device.setEnabled(len(self._devices) > 1)

    def _row_from_read(self, read: Dict[str, Any]) -> Dict[str, str]:
        fc = int(read.get("fc", 4))
        typ = "Holding Register"
        if fc == 1:
            typ = "Coil"
        elif fc == 2:
            typ = "Discrete Input"
        elif fc == 4:
            typ = "Input Register"
        modbus_type = str(read.get("type", "uint16")).lower()
        if modbus_type in {"int16", "int32"}:
            data_type = "Signed"
        elif modbus_type in {"float32", "float64"}:
            data_type = "Float"
        else:
            data_type = "Unsigned"
        if modbus_type in {"uint32", "int32", "float32"}:
            length = "2"
        elif modbus_type in {"float64"}:
            length = "4"
        else:
            length = str(read.get("length", 1))
        sc = read.get("scaling") or {}
        return {
            "Alias": str(read.get("alias", "")),
            "Unit": str(sc.get("unit", "")),
            "Type": typ,
            "Address": str(read.get("address", 0)),
            "Length": str(length),
            "Data Type": data_type,
            "Gain": str(sc.get("m", 1.0)),
            "Offset": str(sc.get("b", 0.0)),
            "Value": "",
        }

    def _on_cell_dblclick(self, row: int, col: int) -> None:
        if col != 0:
            return
        current = ""
        item = self.table.item(row, 0)
        if item is not None:
            current = item.text().strip()
        try:
            dlg = AliasPickerDialog(parent=self, current_alias=current)
            if dlg.exec() == QDialog.Accepted and dlg.selected_alias:
                if item is None:
                    item = QTableWidgetItem("")
                    self.table.setItem(row, 0, item)
                item.setText(dlg.selected_alias)
        except Exception as exc:
            QMessageBox.warning(self, "Alias Picker", f"Could not open alias picker: {exc}")

    def _add_row(self, values: Optional[Dict[str, str]] = None) -> None:
        values = values or {
            "Alias": "",
            "Unit": "",
            "Type": "Holding Register",
            "Address": "0",
            "Length": "1",
            "Data Type": "Unsigned",
            "Gain": "1.0",
            "Offset": "0.0",
            "Value": "",
        }
        r = self.table.rowCount()
        self.table.insertRow(r)

        self.table.setItem(r, 0, QTableWidgetItem(values.get("Alias") or values.get("Channel Name", "")))
        self.table.setItem(r, 1, QTableWidgetItem(values["Unit"]))

        cmb_type = QComboBox(self.table)
        cmb_type.addItems(["Coil", "Discrete Input", "Holding Register", "Input Register"])
        cmb_type.setCurrentText(values["Type"])
        self.table.setCellWidget(r, 2, cmb_type)

        self.table.setItem(r, 3, QTableWidgetItem(values["Address"]))
        self.table.setItem(r, 4, QTableWidgetItem(values["Length"]))

        cmb_dtype = QComboBox(self.table)
        cmb_dtype.addItems(["Unsigned", "Signed", "Float"])
        cmb_dtype.setCurrentText(values["Data Type"])
        self.table.setCellWidget(r, 5, cmb_dtype)

        self.table.setItem(r, 6, QTableWidgetItem(values["Gain"]))
        self.table.setItem(r, 7, QTableWidgetItem(values["Offset"]))
        val_item = QTableWidgetItem(values["Value"])
        val_item.setFlags(val_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(r, 8, val_item)

    def _remove_selected_rows(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def _row_to_read(self, r: int, word_order: str) -> Optional[Dict[str, Any]]:
        def _txt(c: int) -> str:
            it = self.table.item(r, c)
            return (it.text().strip() if it is not None else "")

        alias = _txt(0)
        if not alias:
            return None
        unit = _txt(1)
        type_widget = self.table.cellWidget(r, 2)
        dtype_widget = self.table.cellWidget(r, 5)
        type_text = type_widget.currentText() if isinstance(type_widget, QComboBox) else "Holding Register"
        dtype_text = dtype_widget.currentText() if isinstance(dtype_widget, QComboBox) else "Unsigned"
        try:
            address = int(float(_txt(3) or "0"))
        except Exception:
            address = 0
        try:
            length = max(1, int(float(_txt(4) or "1")))
        except Exception:
            length = 1
        try:
            gain = float(_txt(6) or "1.0")
        except Exception:
            gain = 1.0
        try:
            offset = float(_txt(7) or "0.0")
        except Exception:
            offset = 0.0

        fc = 3
        if type_text == "Coil":
            fc = 1
        elif type_text == "Discrete Input":
            fc = 2
        elif type_text == "Input Register":
            fc = 4

        modbus_type = "uint16"
        if fc in (1, 2):
            modbus_type = "coil"
            length = 1
        elif dtype_text == "Float":
            modbus_type = "float64" if length >= 4 else "float32"
            length = 4 if modbus_type == "float64" else 2
        elif dtype_text == "Signed":
            modbus_type = "int32" if length >= 2 else "int16"
            length = 2 if modbus_type == "int32" else 1
        else:
            modbus_type = "uint32" if length >= 2 else "uint16"
            length = 2 if modbus_type == "uint32" else 1

        return {
            "alias": alias,
            "fc": int(fc),
            "address": int(address),
            "length": int(length),
            "data_type_input": dtype_text.lower(),
            "type": modbus_type,
            "byte_order": "little" if word_order == "little" else "big",
            "word_order": "BA" if word_order == "little" else "AB",
            "scaling": {"m": gain, "b": offset, "unit": unit},
            "poll_hz": float(self._cfg.get("recording_rate_hz", 10.0)),
            "enabled": True,
        }

    def _build_doc(self) -> Dict[str, Any]:
        doc: Dict[str, Any] = dict(self._cfg)
        self._save_current_device_ui()
        doc["enabled"] = bool(doc.get("enabled", True))
        doc["mode"] = str(doc.get("mode", "real"))
        doc["recording_rate_hz"] = int(doc.get("recording_rate_hz", 10))
        doc["devices"] = copy.deepcopy(self._devices)
        # Legacy compatibility projection (flatten all reads and tcp servers).
        servers: List[Dict[str, Any]] = []
        reads: List[Dict[str, Any]] = []
        serial_devices: List[Dict[str, Any]] = []
        for dev in doc["devices"]:
            dev_name = str(dev.get("name", "Device"))
            conn = dev.get("connection") or {}
            iface = str(conn.get("interface_type", "TCP/IP"))
            if iface == "TCP/IP":
                servers.append(
                    {
                        "name": dev_name,
                        "host": str(conn.get("ip_address", "")),
                        "port": int(conn.get("network_port", 502)),
                        "unit_id": int(conn.get("unit_id", 1)),
                        "timeout_ms": 200,
                        "max_retries": 3,
                    }
                )
            else:
                serial_devices.append(
                    {
                        "name": dev_name,
                        "port": str(conn.get("com_port", "")),
                        "unit_id": int(conn.get("unit_id", 1)),
                        "baud_rate": int(conn.get("baud_rate", 115200)),
                        "serial_type": str(conn.get("serial_type", "RTU")),
                        "word_order": str(conn.get("word_order", "big")),
                    }
                )
            for read in dev.get("reads", []) or []:
                if not isinstance(read, dict):
                    continue
                item = copy.deepcopy(read)
                item["server"] = dev_name
                reads.append(item)
        doc["servers"] = servers
        doc["serial_devices"] = serial_devices
        doc["reads"] = reads
        if doc["devices"]:
            doc["connection"] = copy.deepcopy(doc["devices"][0].get("connection") or {})
        doc["writes"] = list(doc.get("writes") or [])
        return doc

    def _save_and_reload(self) -> bool:
        doc = self._build_doc()
        if not doc.get("reads"):
            QMessageBox.warning(self, "Missing channels", "Add at least one Modbus channel.")
            return False
        if str(doc.get("connection", {}).get("interface_type")) == "TCP/IP" and not str(doc.get("connection", {}).get("ip_address", "")).strip():
            QMessageBox.warning(self, "Missing IP", "IP Address is required for TCP/IP.")
            return False
        bad_aliases: list[str] = []
        for dev in doc.get("devices", []):
            for rd in dev.get("reads", []):
                alias = str(rd.get("alias", "")).strip()
                if alias and not validate_alias(alias):
                    bad_aliases.append(alias)
        if bad_aliases:
            QMessageBox.warning(
                self, "Invalid Alias",
                f"The following aliases are invalid:\n{', '.join(bad_aliases)}\n\n"
                "Aliases must match the standard naming pattern."
            )
            return False
        try:
            import yaml  # type: ignore
            self._cfg_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
            self._cfg = dict(doc)
        except Exception as e:
            QMessageBox.critical(self, "Write Error", f"Failed to save modbus.yaml: {e}")
            return False
        try:
            from src.core.ipc.bus import create_ui_control_push  # type: ignore
            ctrl = create_ui_control_push()
            if ctrl is not None:
                msg = json.dumps({"type": "reload_plugin", "plugin": "Modbus"}).encode("utf-8")
                ctrl["control_push"].send(msg)
        except Exception:
            pass
        return True

    def _run_test(self) -> None:
        if not self._save_and_reload():
            return
        self._testing = True
        self.lbl_test.setText("Test: running (watch Value column)")

    def _poll_telemetry(self) -> None:
        if self._sub is None or not self._testing:
            return
        try:
            import zmq
            latest_values: Dict[str, Any] = {}
            while True:
                try:
                    topic, payload = self._sub.recv_multipart(flags=zmq.NOBLOCK)
                except Exception:
                    break
                if topic != b"telemetry":
                    continue
                try:
                    msg = json.loads(payload.decode("utf-8"))
                    vals = msg.get("values") or {}
                    if isinstance(vals, dict):
                        latest_values = vals
                except Exception:
                    continue
            if not latest_values:
                return
            for r in range(self.table.rowCount()):
                alias_item = self.table.item(r, 0)
                if alias_item is None:
                    continue
                alias = alias_item.text().strip()
                if not alias or alias not in latest_values:
                    continue
                v = latest_values.get(alias)
                val_item = self.table.item(r, 8)
                if val_item is None:
                    val_item = QTableWidgetItem("")
                    val_item.setFlags(val_item.flags() & ~Qt.ItemIsEditable)
                    self.table.setItem(r, 8, val_item)
                try:
                    val_item.setText(f"{float(v):.6g}")
                except Exception:
                    val_item.setText(str(v))
        except Exception:
            pass

    def _on_accept(self) -> None:
        if not self._save_and_reload():
            return
        self.accept()

