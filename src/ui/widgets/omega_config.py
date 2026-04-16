# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QGroupBox,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMessageBox,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
    )
except Exception:
    raise

from src.plugins.omega import CHANNEL_MAP


class OmegaConfigDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure Omega Weather Station")
        self.resize(500, 340)
        self._cfg_path = Path(__file__).resolve().parents[3] / "configs" / "omega.yaml"
        self._cfg: Dict[str, Any] = {}
        self._init_ui()
        self._load()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)

        conn_box = QGroupBox("Connection")
        conn_form = QFormLayout(conn_box)
        self.txt_host = QLineEdit(self)
        self.txt_host.setPlaceholderText("192.168.76.45")
        conn_form.addRow("Host / IP", self.txt_host)
        self.spin_port = QSpinBox(self)
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(502)
        conn_form.addRow("Port", self.spin_port)
        root.addWidget(conn_box)

        ch_box = QGroupBox("Channels")
        ch_lay = QVBoxLayout(ch_box)
        self.tbl = QTableWidget(len(CHANNEL_MAP), 3, self)
        self.tbl.setHorizontalHeaderLabels(["ID", "Unit", "Alias"])
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.cellDoubleClicked.connect(self._on_cell_double_click)  # type: ignore

        for row, ch in enumerate(CHANNEL_MAP):
            id_item = QTableWidgetItem(ch["id"])
            id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)
            self.tbl.setItem(row, 0, id_item)

            unit_item = QTableWidgetItem(ch["unit"])
            unit_item.setFlags(unit_item.flags() & ~Qt.ItemIsEditable)
            self.tbl.setItem(row, 1, unit_item)

            alias_item = QTableWidgetItem("")
            alias_item.setFlags(alias_item.flags() & ~Qt.ItemIsEditable)
            self.tbl.setItem(row, 2, alias_item)

        ch_lay.addWidget(self.tbl)
        root.addWidget(ch_box)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        btns.accepted.connect(self._on_accept)  # type: ignore
        btns.rejected.connect(self.reject)  # type: ignore
        root.addWidget(btns)

    def _on_cell_double_click(self, row: int, col: int) -> None:
        if col != 2 or row < 0 or row >= len(CHANNEL_MAP):
            return
        current = (self.tbl.item(row, 2).text() or "").strip()
        try:
            from .nidaq_alias_picker import AliasPickerDialog
            dlg = AliasPickerDialog(current_alias=current, parent=self)
            if dlg.exec() == QDialog.Accepted:
                chosen = dlg.selected_alias
                if chosen:
                    self.tbl.item(row, 2).setText(chosen)
        except Exception as e:
            QMessageBox.warning(self, "Alias Picker", f"Could not open alias picker:\n{e}")

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
        conn = self._cfg.get("connection") or {}
        self.txt_host.setText(str(conn.get("host", "192.168.76.45")))
        try:
            self.spin_port.setValue(int(conn.get("port", 502)))
        except Exception:
            self.spin_port.setValue(502)

        channels_cfg: List[Dict[str, Any]] = self._cfg.get("channels") or []
        alias_map: Dict[str, str] = {}
        for item in channels_cfg:
            if isinstance(item, dict):
                alias_map[str(item.get("id", ""))] = str(item.get("alias", ""))

        for row, ch in enumerate(CHANNEL_MAP):
            saved = alias_map.get(ch["id"], "").strip()
            self.tbl.item(row, 2).setText(saved or ch["alias"])

    def _on_accept(self) -> None:
        channels_out: List[Dict[str, str]] = []
        for row, ch in enumerate(CHANNEL_MAP):
            alias = (self.tbl.item(row, 2).text() or "").strip()
            channels_out.append({"id": ch["id"], "alias": alias})

        has_blank = any(not c["alias"] for c in channels_out)
        if has_blank:
            ans = QMessageBox.warning(
                self, "Blank Aliases",
                "One or more channels have blank aliases.\n"
                "Channels without aliases will not appear in telemetry.\n\n"
                "Continue anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return

        doc: Dict[str, Any] = dict(self._cfg)
        doc["connection"] = {
            "host": self.txt_host.text().strip() or "192.168.76.45",
            "port": int(self.spin_port.value()),
            "timeout_ms": int((self._cfg.get("connection") or {}).get("timeout_ms", 2000)),
        }
        doc["channels"] = channels_out

        try:
            import yaml  # type: ignore
            self._cfg_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        except Exception as e:
            QMessageBox.critical(self, "Write Error", f"Failed to save omega.yaml: {e}")
            return

        try:
            from src.core.ipc.bus import create_ui_control_push  # type: ignore
            ctrl = create_ui_control_push()
            if ctrl is not None:
                msg = json.dumps({"type": "reload_plugin", "plugin": "Omega"}).encode("utf-8")
                ctrl["control_push"].send(msg)
        except Exception:
            pass
        self.accept()
