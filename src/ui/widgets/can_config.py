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
        QListWidget,
        QListWidgetItem,
    )
except Exception:
    raise


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

        root.addWidget(QLabel("DBC signals (checkbox selection)"))
        self.list_signals = QListWidget(self)
        self.list_signals.setMinimumHeight(300)
        root.addWidget(self.list_signals)

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

        selected = [str(it.get("signal")) for it in (self._cfg.get("signals") or []) if isinstance(it, dict) and it.get("signal")]
        self._reload_signals_from_dbc(selected_names=selected)

    def _browse_dbc(self) -> None:
        start = self.txt_dbc_path.text().strip() or str(Path.cwd())
        path, _ = QFileDialog.getOpenFileName(self, "Select DBC file", start, "DBC files (*.dbc);;All files (*.*)")
        if path:
            self.txt_dbc_path.setText(path)
            self._reload_signals_from_dbc()

    def _reload_signals_from_dbc(self, selected_names: List[str] | None = None) -> None:
        selected = set(selected_names or self._checked_signal_names())
        self.list_signals.clear()
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
                            "alias": str(sig.name),
                        }
                    )
            self._dbc_signals = sorted(sigs, key=lambda x: (x["message"], x["signal"]))
        except Exception as e:
            QMessageBox.warning(self, "DBC parse error", f"Failed to parse DBC: {e}")
            self._dbc_signals = []

        for item_data in self._dbc_signals:
            msg = item_data["message"]
            sig = item_data["signal"]
            unit = item_data["unit"] or "-"
            label = f"{msg}.{sig}  |  {unit}"
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setData(Qt.UserRole, dict(item_data))
            item.setCheckState(Qt.Checked if sig in selected else Qt.Unchecked)
            self.list_signals.addItem(item)
        self._apply_signal_filter()

    def _checked_signal_names(self) -> List[str]:
        out: List[str] = []
        for i in range(self.list_signals.count()):
            it = self.list_signals.item(i)
            if it is None or it.checkState() != Qt.Checked:
                continue
            data = it.data(Qt.UserRole) or {}
            sig = str(data.get("signal") or "").strip() if isinstance(data, dict) else ""
            if sig:
                out.append(sig)
        return out

    def _checked_signals(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for i in range(self.list_signals.count()):
            it = self.list_signals.item(i)
            if it is None or it.checkState() != Qt.Checked:
                continue
            data = it.data(Qt.UserRole) or {}
            if not isinstance(data, dict):
                continue
            message = str(data.get("message") or "").strip()
            signal = str(data.get("signal") or "").strip()
            if not message or not signal:
                continue
            out.append(
                {
                    "alias": str(data.get("alias") or signal),
                    "message": message,
                    "signal": signal,
                    "unit": str(data.get("unit") or ""),
                    "enabled": True,
                }
            )
        return out

    def _apply_signal_filter(self) -> None:
        q = self.txt_filter.text().strip().lower()
        for i in range(self.list_signals.count()):
            it = self.list_signals.item(i)
            if it is None:
                continue
            data = it.data(Qt.UserRole) or {}
            key = ""
            if isinstance(data, dict):
                key = f"{str(data.get('message','')).lower()}.{str(data.get('signal','')).lower()}"
            if not q:
                visible = True
            elif "*" in q:
                visible = bool(fnmatch(key, q))
            else:
                visible = key.startswith(q)
            it.setHidden(not visible)

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

