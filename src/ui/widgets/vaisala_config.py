# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

try:
    from PySide6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
    )
except Exception:
    raise


class VaisalaConfigDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure Vaisala")
        self.resize(640, 560)
        self._cfg_path = Path(__file__).resolve().parents[3] / "configs" / "vaisala.yaml"
        self._cfg: Dict[str, Any] = {}
        self._init_ui()
        self._load()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)

        self.chk_enabled = QCheckBox("Plugin enabled")
        self.chk_enabled.setChecked(True)
        root.addWidget(self.chk_enabled)

        form = QFormLayout()
        self.cmb_mode = QComboBox(self)
        self.cmb_mode.addItems(["sim", "real"])
        self.spin_rate = QSpinBox(self)
        self.spin_rate.setRange(1, 1000)
        self.spin_rate.setValue(10)
        form.addRow("Mode", self.cmb_mode)
        form.addRow("Recording rate (Hz)", self.spin_rate)
        root.addLayout(form)

        conn = QGroupBox("Connection (Modbus/TCP)")
        cf = QFormLayout(conn)
        self.txt_host = QLineEdit(self)
        self.txt_host.setPlaceholderText("127.0.0.1")
        self.spin_port = QSpinBox(self)
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(502)
        self.spin_unit = QSpinBox(self)
        self.spin_unit.setRange(1, 255)
        self.spin_unit.setValue(1)
        cf.addRow("Host / IP", self.txt_host)
        cf.addRow("Port", self.spin_port)
        cf.addRow("Unit ID", self.spin_unit)
        root.addWidget(conn)

        mod = QGroupBox("Model")
        mf = QFormLayout(mod)
        self.txt_model = QLineEdit(self)
        self.txt_map = QLineEdit(self)
        self.txt_map.setPlaceholderText("optional map file path")
        mf.addRow("Selected model", self.txt_model)
        mf.addRow("Register map file", self.txt_map)
        root.addWidget(mod)

        poll = QFormLayout()
        self.txt_poll_hz = QLineEdit(self)
        self.txt_poll_hz.setPlaceholderText("empty = use default")
        poll.addRow("Override poll (Hz)", self.txt_poll_hz)
        root.addLayout(poll)

        root.addWidget(QLabel("Channel aliases & units"))
        self.tbl_ch = QTableWidget(self)
        self.tbl_ch.setColumnCount(2)
        self.tbl_ch.setHorizontalHeaderLabels(["Alias", "Unit"])
        self.tbl_ch.horizontalHeader().setStretchLastSection(True)
        self.tbl_ch.setMinimumHeight(140)
        root.addWidget(self.tbl_ch)

        root.addWidget(QLabel("Calibration offsets (alias → offset)"))
        self.tbl_off = QTableWidget(self)
        self.tbl_off.setColumnCount(2)
        self.tbl_off.setHorizontalHeaderLabels(["Channel alias", "Offset"])
        self.tbl_off.horizontalHeader().setStretchLastSection(True)
        self.tbl_off.setMinimumHeight(100)
        root.addWidget(self.tbl_off)

        btns_row = QHBoxLayout()
        self.btn_add_ch = QPushButton("Add channel")
        self.btn_add_ch.clicked.connect(self._add_ch_row)  # type: ignore
        self.btn_del_ch = QPushButton("Remove channel")
        self.btn_del_ch.clicked.connect(self._del_ch_rows)  # type: ignore
        self.btn_add_off = QPushButton("Add offset")
        self.btn_add_off.clicked.connect(self._add_off_row)  # type: ignore
        self.btn_del_off = QPushButton("Remove offset")
        self.btn_del_off.clicked.connect(self._del_off_rows)  # type: ignore
        btns_row.addWidget(self.btn_add_ch)
        btns_row.addWidget(self.btn_del_ch)
        btns_row.addWidget(self.btn_add_off)
        btns_row.addWidget(self.btn_del_off)
        btns_row.addStretch(1)
        root.addLayout(btns_row)

        dlg_btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        dlg_btns.accepted.connect(self._on_accept)  # type: ignore
        dlg_btns.rejected.connect(self.reject)  # type: ignore
        root.addWidget(dlg_btns)

    def _add_ch_row(self) -> None:
        r = self.tbl_ch.rowCount()
        self.tbl_ch.insertRow(r)
        self.tbl_ch.setItem(r, 0, QTableWidgetItem(""))
        self.tbl_ch.setItem(r, 1, QTableWidgetItem(""))

    def _del_ch_rows(self) -> None:
        for r in sorted({i.row() for i in self.tbl_ch.selectedIndexes()}, reverse=True):
            self.tbl_ch.removeRow(r)

    def _add_off_row(self) -> None:
        r = self.tbl_off.rowCount()
        self.tbl_off.insertRow(r)
        self.tbl_off.setItem(r, 0, QTableWidgetItem(""))
        self.tbl_off.setItem(r, 1, QTableWidgetItem("0"))

    def _del_off_rows(self) -> None:
        for r in sorted({i.row() for i in self.tbl_off.selectedIndexes()}, reverse=True):
            self.tbl_off.removeRow(r)

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
        c = self._cfg
        self.chk_enabled.setChecked(bool(c.get("enabled", True)))
        mode = str(c.get("mode", "sim"))
        mi = self.cmb_mode.findText(mode)
        self.cmb_mode.setCurrentIndex(mi if mi >= 0 else 0)
        try:
            self.spin_rate.setValue(int(float(c.get("recording_rate_hz", 10))))
        except Exception:
            self.spin_rate.setValue(10)

        conn = (c.get("connection") or {}) if isinstance(c.get("connection"), dict) else {}
        self.txt_host.setText(str(conn.get("host", "127.0.0.1")))
        try:
            self.spin_port.setValue(int(conn.get("port", 502)))
        except Exception:
            self.spin_port.setValue(502)
        try:
            self.spin_unit.setValue(int(conn.get("unit_id", 1)))
        except Exception:
            self.spin_unit.setValue(1)

        model = (c.get("model") or {}) if isinstance(c.get("model"), dict) else {}
        self.txt_model.setText(str(model.get("selected", "") or ""))
        mf = model.get("map_file")
        self.txt_map.setText("" if mf is None else str(mf))

        pol = (c.get("polling") or {}) if isinstance(c.get("polling"), dict) else {}
        ov = pol.get("override_poll_hz")
        self.txt_poll_hz.setText("" if ov is None else str(ov))

        self.tbl_ch.setRowCount(0)
        chs = c.get("channels") or []
        if isinstance(chs, list):
            for item in chs:
                if not isinstance(item, dict):
                    continue
                al = str(item.get("alias", "") or "")
                if not al:
                    continue
                r = self.tbl_ch.rowCount()
                self.tbl_ch.insertRow(r)
                self.tbl_ch.setItem(r, 0, QTableWidgetItem(al))
                self.tbl_ch.setItem(r, 1, QTableWidgetItem(str(item.get("unit", ""))))

        self.tbl_off.setRowCount(0)
        offs = c.get("calibration_offsets") or {}
        if isinstance(offs, dict):
            for k, v in offs.items():
                r = self.tbl_off.rowCount()
                self.tbl_off.insertRow(r)
                self.tbl_off.setItem(r, 0, QTableWidgetItem(str(k)))
                self.tbl_off.setItem(r, 1, QTableWidgetItem(str(v)))

    def _build_doc(self) -> Dict[str, Any]:
        doc: Dict[str, Any] = dict(self._cfg)
        doc["enabled"] = self.chk_enabled.isChecked()
        doc["mode"] = self.cmb_mode.currentText()
        doc["recording_rate_hz"] = int(self.spin_rate.value())
        doc["connection"] = {
            "host": self.txt_host.text().strip() or "127.0.0.1",
            "port": int(self.spin_port.value()),
            "unit_id": int(self.spin_unit.value()),
        }
        map_raw = self.txt_map.text().strip()
        doc["model"] = {
            "selected": self.txt_model.text().strip() or "HMT330",
            "map_file": None if not map_raw else map_raw,
        }
        pol_raw = self.txt_poll_hz.text().strip()
        override = None
        if pol_raw:
            try:
                override = float(pol_raw)
            except Exception:
                override = None
        doc["polling"] = {"override_poll_hz": override}

        chans: List[Dict[str, str]] = []
        for r in range(self.tbl_ch.rowCount()):
            a = self.tbl_ch.item(r, 0)
            u = self.tbl_ch.item(r, 1)
            alias = (a.text().strip() if a else "").strip()
            if not alias:
                continue
            chans.append({"alias": alias, "unit": (u.text().strip() if u else "")})
        doc["channels"] = chans

        off: Dict[str, float] = {}
        for r in range(self.tbl_off.rowCount()):
            k_it = self.tbl_off.item(r, 0)
            v_it = self.tbl_off.item(r, 1)
            key = (k_it.text().strip() if k_it else "").strip()
            if not key:
                continue
            try:
                off[key] = float((v_it.text().strip() if v_it else "0") or "0")
            except Exception:
                off[key] = 0.0
        doc["calibration_offsets"] = off
        return doc

    def _save_and_reload(self) -> bool:
        doc = self._build_doc()
        try:
            import yaml  # type: ignore

            self._cfg_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
            self._cfg = dict(doc)
        except Exception as e:
            QMessageBox.critical(self, "Write Error", f"Failed to save vaisala.yaml: {e}")
            return False

        try:
            from src.core.ipc.bus import create_ui_control_push  # type: ignore

            ctrl = create_ui_control_push()
            if ctrl is not None:
                msg = json.dumps({"type": "reload_plugin", "plugin": "Vaisala"}).encode("utf-8")
                ctrl["control_push"].send(msg)
        except Exception:
            pass
        return True

    def _on_accept(self) -> None:
        if not self._save_and_reload():
            return
        self.accept()
