from __future__ import annotations

import json
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QDialog,
        QVBoxLayout,
        QHBoxLayout,
        QFormLayout,
        QLineEdit,
        QComboBox,
        QPushButton,
        QFileDialog,
        QLabel,
        QDialogButtonBox,
        QMessageBox,
        QTableWidget,
        QTableWidgetItem,
        QHeaderView,
        QAbstractItemView,
    )
except Exception:
    raise

from .nidaq_alias_picker import AliasPickerDialog


class CANConfigDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure CAN")
        self.resize(900, 720)
        self._cfg_path = Path(__file__).resolve().parents[3] / "configs" / "can.yaml"
        self._cfg: Dict[str, Any] = {}
        self._dbc_signals: List[Dict[str, Any]] = []
        self._init_ui()
        self._load()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        form = QFormLayout()

        self.txt_channel = QLineEdit(self)
        self.txt_channel.setPlaceholderText("CAN channel, e.g. CAN1")
        self.cmb_baudrate = QComboBox(self)
        self.cmb_baudrate.addItems(["125000", "250000", "500000", "1000000"])
        self.cmb_mode = QComboBox(self)
        self.cmb_mode.addItems(["real", "sim"])
        form.addRow("Mode", self.cmb_mode)
        form.addRow("CAN channel", self.txt_channel)
        form.addRow("Baudrate", self.cmb_baudrate)
        root.addLayout(form)

        root.addWidget(QLabel("DBC path"))
        self.txt_dbc_path = QLineEdit(self)
        root.addWidget(self.txt_dbc_path)
        row = QHBoxLayout()
        btn_browse = QPushButton("Browse DBC...", self)
        btn_browse.clicked.connect(self._browse_dbc)  # type: ignore
        btn_load = QPushButton("Load Signals from DBC", self)
        btn_load.clicked.connect(self._reload_signals_from_dbc)  # type: ignore
        row.addWidget(btn_browse)
        row.addWidget(btn_load)
        row.addStretch(1)
        root.addLayout(row)

        root.addWidget(QLabel("Signal filter"))
        self.txt_filter = QLineEdit(self)
        self.txt_filter.setPlaceholderText("Prefix by default, wildcard if * is used")
        self.txt_filter.textChanged.connect(self._apply_signal_filter)  # type: ignore
        root.addWidget(self.txt_filter)

        root.addWidget(QLabel("DBC signals (check to enable, double-click Alias to set)"))
        self.tbl_signals = QTableWidget(0, 5, self)
        self.tbl_signals.setHorizontalHeaderLabels(["", "Message", "Signal", "Unit", "Alias"])
        self.tbl_signals.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tbl_signals.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_signals.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl_signals.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.tbl_signals.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.tbl_signals.verticalHeader().setVisible(False)
        self.tbl_signals.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_signals.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_signals.setMinimumHeight(300)
        self.tbl_signals.cellDoubleClicked.connect(self._on_cell_double_click)  # type: ignore
        root.addWidget(self.tbl_signals)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        btns.accepted.connect(self._on_accept)  # type: ignore
        btns.rejected.connect(self.reject)  # type: ignore
        root.addWidget(btns)

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
        mode = str(self._cfg.get("mode", "sim")).strip().lower()
        self.cmb_mode.setCurrentText("real" if mode == "real" else "sim")
        session = self._cfg.get("session") or {}
        self.txt_channel.setText(str(session.get("channel", "CAN1")))
        baud = str(session.get("baudrate", "500000"))
        idx = self.cmb_baudrate.findText(baud)
        self.cmb_baudrate.setCurrentIndex(idx if idx >= 0 else 2)
        self.txt_dbc_path.setText(str(self._cfg.get("dbc_path", "")))

        saved_map: Dict[tuple, str] = {}
        for it in (self._cfg.get("signals") or []):
            if not isinstance(it, dict) or not it.get("signal"):
                continue
            key = (str(it.get("message", "")), str(it.get("signal", "")))
            saved_map[key] = str(it.get("alias", ""))
        self._reload_signals_from_dbc(saved_map=saved_map)

    def _browse_dbc(self) -> None:
        start = self.txt_dbc_path.text().strip() or str(Path.cwd())
        path, _ = QFileDialog.getOpenFileName(self, "Select DBC file", start, "DBC files (*.dbc);;All files (*.*)")
        if path:
            self.txt_dbc_path.setText(path)
            self._reload_signals_from_dbc()

    def _reload_signals_from_dbc(self, saved_map: Dict[tuple, str] | None = None) -> None:
        if saved_map is None:
            saved_map = self._current_signal_map()
        self.tbl_signals.setRowCount(0)
        self._dbc_signals = []
        dbc_path = Path(self.txt_dbc_path.text().strip())
        if not dbc_path.exists():
            return
        try:
            import cantools  # type: ignore
        except Exception:
            QMessageBox.warning(self, "cantools missing", "cantools package is required to load DBC signals.")
            return
        try:
            db = cantools.database.load_file(str(dbc_path))
            sigs: List[Dict[str, Any]] = []
            for msg in db.messages:
                for sig in msg.signals:
                    sigs.append(
                        {
                            "message": str(msg.name),
                            "signal": str(sig.name),
                            "unit": str(sig.unit or ""),
                        }
                    )
            self._dbc_signals = sorted(sigs, key=lambda x: (x["message"], x["signal"]))
        except Exception as e:
            QMessageBox.warning(self, "DBC parse error", f"Failed to parse DBC: {e}")
            self._dbc_signals = []

        self.tbl_signals.setRowCount(len(self._dbc_signals))
        for row, item_data in enumerate(self._dbc_signals):
            msg = item_data["message"]
            sig = item_data["signal"]
            unit = item_data["unit"] or ""
            key = (msg, sig)
            is_selected = key in saved_map
            saved_alias = saved_map.get(key, "")

            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk_item.setCheckState(Qt.Checked if is_selected else Qt.Unchecked)
            self.tbl_signals.setItem(row, 0, chk_item)

            msg_item = QTableWidgetItem(msg)
            msg_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.tbl_signals.setItem(row, 1, msg_item)

            sig_item = QTableWidgetItem(sig)
            sig_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.tbl_signals.setItem(row, 2, sig_item)

            unit_item = QTableWidgetItem(unit)
            unit_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.tbl_signals.setItem(row, 3, unit_item)

            alias_item = QTableWidgetItem(saved_alias)
            alias_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.tbl_signals.setItem(row, 4, alias_item)

        self._apply_signal_filter()

    def _current_signal_map(self) -> Dict[tuple, str]:
        """Build a (message, signal) -> alias map from current table state."""
        out: Dict[tuple, str] = {}
        for r in range(self.tbl_signals.rowCount()):
            chk = self.tbl_signals.item(r, 0)
            if chk is None or chk.checkState() != Qt.Checked:
                continue
            msg = (self.tbl_signals.item(r, 1).text().strip()
                   if self.tbl_signals.item(r, 1) else "")
            sig = (self.tbl_signals.item(r, 2).text().strip()
                   if self.tbl_signals.item(r, 2) else "")
            alias = (self.tbl_signals.item(r, 4).text().strip()
                     if self.tbl_signals.item(r, 4) else "")
            if sig:
                out[(msg, sig)] = alias
        return out

    def _checked_signal_keys(self) -> list:
        out: list = []
        for r in range(self.tbl_signals.rowCount()):
            chk = self.tbl_signals.item(r, 0)
            if chk is None or chk.checkState() != Qt.Checked:
                continue
            msg = (self.tbl_signals.item(r, 1).text().strip()
                   if self.tbl_signals.item(r, 1) else "")
            sig = (self.tbl_signals.item(r, 2).text().strip()
                   if self.tbl_signals.item(r, 2) else "")
            if sig:
                out.append((msg, sig))
        return out

    def _checked_signals(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for r in range(self.tbl_signals.rowCount()):
            chk = self.tbl_signals.item(r, 0)
            if chk is None or chk.checkState() != Qt.Checked:
                continue
            msg = (self.tbl_signals.item(r, 1).text().strip()
                   if self.tbl_signals.item(r, 1) else "")
            sig = (self.tbl_signals.item(r, 2).text().strip()
                   if self.tbl_signals.item(r, 2) else "")
            unit = (self.tbl_signals.item(r, 3).text().strip()
                    if self.tbl_signals.item(r, 3) else "")
            alias = (self.tbl_signals.item(r, 4).text().strip()
                     if self.tbl_signals.item(r, 4) else "")
            if not msg or not sig:
                continue
            out.append(
                {
                    "alias": alias,
                    "message": msg,
                    "signal": sig,
                    "unit": unit,
                    "enabled": True,
                }
            )
        return out

    def _apply_signal_filter(self) -> None:
        q = self.txt_filter.text().strip().lower()
        for r in range(self.tbl_signals.rowCount()):
            msg = (self.tbl_signals.item(r, 1).text().lower()
                   if self.tbl_signals.item(r, 1) else "")
            sig = (self.tbl_signals.item(r, 2).text().lower()
                   if self.tbl_signals.item(r, 2) else "")
            key = f"{msg}.{sig}"
            if not q:
                visible = True
            elif "*" in q:
                visible = bool(fnmatch(key, q))
            else:
                visible = key.startswith(q)
            self.tbl_signals.setRowHidden(r, not visible)

    def _on_cell_double_click(self, row: int, col: int) -> None:
        if col != 4:
            return
        current = (self.tbl_signals.item(row, 4).text().strip()
                   if self.tbl_signals.item(row, 4) else "")
        dlg = AliasPickerDialog(parent=self, current_alias=current)
        if dlg.exec() == QDialog.Accepted and dlg.selected_alias:
            self.tbl_signals.setItem(row, 4, QTableWidgetItem(dlg.selected_alias))

    def _on_accept(self) -> None:
        channel = self.txt_channel.text().strip()
        if not channel:
            QMessageBox.warning(self, "Missing channel", "CAN channel is required.")
            return
        dbc_path = self.txt_dbc_path.text().strip()
        mode = self.cmb_mode.currentText().strip().lower()
        if mode == "real" and not dbc_path:
            QMessageBox.warning(self, "Missing DBC", "DBC path is required in real mode.")
            return
        signals = self._checked_signals()
        if not signals:
            QMessageBox.warning(self, "Missing signals", "Select at least one signal from DBC.")
            return

        blank = [s for s in signals if not s.get("alias")]
        if blank:
            names = [f"  {s['message']}.{s['signal']}" for s in blank[:10]]
            QMessageBox.warning(
                self, "Missing Aliases",
                "The following checked signals have no alias assigned. "
                "Double-click the Alias column to set one:\n\n" + "\n".join(names),
            )
            return

        alias_counts: Dict[str, list] = {}
        for s in signals:
            a = s.get("alias", "")
            alias_counts.setdefault(a, []).append(s.get("message", ""))
        dupes = {a: msgs for a, msgs in alias_counts.items() if len(msgs) > 1}
        if dupes:
            lines = [f"  {a}  (messages: {', '.join(msgs)})" for a, msgs in dupes.items()]
            QMessageBox.warning(
                self, "Duplicate Aliases",
                "The following aliases are used by multiple checked signals. "
                "Deselect one of each pair to continue:\n\n" + "\n".join(lines),
            )
            return

        doc: Dict[str, Any] = dict(self._cfg)
        doc["enabled"] = bool(doc.get("enabled", True))
        doc["mode"] = mode
        doc["recording_rate_hz"] = int(doc.get("recording_rate_hz", 10))
        doc["session"] = {
            "channel": channel,
            "baudrate": int(self.cmb_baudrate.currentText().strip() or "500000"),
            "bustype": "nixnet",
        }
        doc["dbc_path"] = dbc_path
        doc["signals"] = signals
        doc["buses"] = doc.get("buses", [])
        doc["databases"] = doc.get("databases", [])

        try:
            import yaml  # type: ignore
            self._cfg_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        except Exception as e:
            QMessageBox.critical(self, "Write Error", f"Failed to save can.yaml: {e}")
            return

        try:
            from src.core.ipc.bus import create_ui_control_push  # type: ignore
            ctrl = create_ui_control_push()
            if ctrl is not None:
                msg = json.dumps({"type": "reload_plugin", "plugin": "CAN"}).encode("utf-8")
                ctrl["control_push"].send(msg)
        except Exception:
            pass
        self.accept()
