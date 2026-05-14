# Author: T. Onkst | Date: 04202026

from __future__ import annotations

import json
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List, Optional

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
        QTabWidget,
        QWidget,
    )
except Exception:
    raise

from .nidaq_alias_picker import AliasPickerDialog
from .standard_channels import validate_alias
from .can_interfaces import discover_can_channels


class _BusTab(QWidget):
    """One tab per CAN bus — channel, baudrate, DBC, and signal table."""

    def __init__(self, available_channels: Optional[List[str]] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._dbc_signals: List[Dict[str, Any]] = []
        self._available_channels = list(available_channels or [])
        root = QVBoxLayout(self)

        form = QFormLayout()
        self.txt_name = QLineEdit(self)
        self.txt_name.setPlaceholderText("CAN Bus 1")
        self.cmb_channel = QComboBox(self)
        self.cmb_channel.setEditable(False)
        self.cmb_channel.addItem("")
        self.cmb_channel.addItems(self._available_channels)
        self.cmb_channel.setToolTip("Detected NI-XNET CAN interfaces. Select a discovered channel before saving.")
        self.cmb_baudrate = QComboBox(self)
        self.cmb_baudrate.addItems(["125000", "250000", "500000", "1000000"])
        form.addRow("Bus Name", self.txt_name)
        form.addRow("CAN Channel", self.cmb_channel)
        form.addRow("Baudrate", self.cmb_baudrate)
        root.addLayout(form)

        root.addWidget(QLabel("DBC Path"))
        self.txt_dbc_path = QLineEdit(self)
        root.addWidget(self.txt_dbc_path)
        row = QHBoxLayout()
        btn_browse = QPushButton("Browse DBC...", self)
        btn_browse.clicked.connect(self._browse_dbc)  # type: ignore
        btn_load = QPushButton("Load Signals from DBC", self)
        btn_load.clicked.connect(lambda: self._reload_signals_from_dbc())  # type: ignore
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
        self.tbl_signals.setMinimumHeight(250)
        self.tbl_signals.cellDoubleClicked.connect(self._on_cell_double_click)  # type: ignore
        root.addWidget(self.tbl_signals)

    # ------------------------------------------------------------------
    # Load / populate
    # ------------------------------------------------------------------

    def load_bus(self, bus_cfg: Dict[str, Any]) -> None:
        self.txt_name.setText(str(bus_cfg.get("name", "")))
        channel = str(bus_cfg.get("channel", "CAN1")).strip() or "CAN1"
        self._select_channel(channel)
        baud = str(bus_cfg.get("baudrate", "500000"))
        idx = self.cmb_baudrate.findText(baud)
        self.cmb_baudrate.setCurrentIndex(idx if idx >= 0 else 2)
        self.txt_dbc_path.setText(str(bus_cfg.get("dbc_path", "")))

        saved_map: Dict[tuple, str] = {}
        for sig in bus_cfg.get("signals", []) or []:
            if not isinstance(sig, dict):
                continue
            key = (str(sig.get("message", "")), str(sig.get("signal", "")))
            saved_map[key] = str(sig.get("alias", ""))
        self._reload_signals_from_dbc(saved_map=saved_map)

    def to_bus_dict(self) -> Dict[str, Any]:
        return {
            "name": self.txt_name.text().strip() or "CAN Bus",
            "channel": self.cmb_channel.currentText().strip(),
            "baudrate": int(self.cmb_baudrate.currentText().strip() or "500000"),
            "bustype": "nixnet",
            "dbc_path": self.txt_dbc_path.text().strip(),
            "signals": self._checked_signals(),
        }

    def _select_channel(self, channel: str) -> None:
        wanted = str(channel or "").strip().upper()
        for available in self._available_channels:
            if available.upper() == wanted:
                self.cmb_channel.setCurrentText(available)
                return
        self.cmb_channel.setCurrentIndex(0)

    # ------------------------------------------------------------------
    # DBC
    # ------------------------------------------------------------------

    def _browse_dbc(self) -> None:
        start = self.txt_dbc_path.text().strip() or str(Path.cwd())
        path, _ = QFileDialog.getOpenFileName(
            self, "Select DBC file", start, "DBC files (*.dbc);;All files (*.*)",
        )
        if path:
            self.txt_dbc_path.setText(path)
            self._reload_signals_from_dbc()

    def _reload_signals_from_dbc(self, saved_map: Optional[Dict[tuple, str]] = None) -> None:
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
                    sigs.append({
                        "message": str(msg.name),
                        "signal": str(sig.name),
                        "unit": str(sig.unit or ""),
                    })
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

    # ------------------------------------------------------------------
    # Signal helpers
    # ------------------------------------------------------------------

    def _current_signal_map(self) -> Dict[tuple, str]:
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
            out.append({
                "alias": alias,
                "message": msg,
                "signal": sig,
                "unit": unit,
                "enabled": True,
            })
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
        try:
            dlg = AliasPickerDialog(parent=self, current_alias=current)
            if dlg.exec() == QDialog.Accepted and dlg.selected_alias:
                self.tbl_signals.setItem(row, 4, QTableWidgetItem(dlg.selected_alias))
        except Exception as exc:
            QMessageBox.warning(self, "Alias Picker", f"Could not open alias picker: {exc}")


# ======================================================================
# Main CAN config dialog
# ======================================================================

class CANConfigDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure CAN")
        self.resize(950, 780)
        self._cfg_path = Path(__file__).resolve().parents[3] / "configs" / "can.yaml"
        self._cfg: Dict[str, Any] = {}
        self._available_can_channels = discover_can_channels()
        self._init_ui()
        self._load()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)

        bus_bar = QHBoxLayout()
        self.btn_add_bus = QPushButton("Add Bus", self)
        self.btn_add_bus.clicked.connect(self._add_bus)  # type: ignore
        self.btn_remove_bus = QPushButton("Remove Bus", self)
        self.btn_remove_bus.clicked.connect(self._remove_bus)  # type: ignore
        bus_bar.addWidget(self.btn_add_bus)
        bus_bar.addWidget(self.btn_remove_bus)
        bus_bar.addStretch(1)
        root.addLayout(bus_bar)

        self.tab_widget = QTabWidget(self)
        root.addWidget(self.tab_widget)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        btns.accepted.connect(self._on_accept)  # type: ignore
        btns.rejected.connect(self.reject)  # type: ignore
        root.addWidget(btns)

    # ------------------------------------------------------------------
    # Read / write YAML
    # ------------------------------------------------------------------

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
        buses = self._cfg.get("buses", [])
        if isinstance(buses, list) and buses:
            for bus_cfg in buses:
                if isinstance(bus_cfg, dict):
                    self._create_bus_tab(bus_cfg)
        elif self._cfg.get("session") or self._cfg.get("signals"):
            legacy = self._legacy_to_bus(self._cfg)
            self._create_bus_tab(legacy)
        else:
            self._create_bus_tab({"name": "CAN Bus 1", "channel": self._default_channel_for_index(1), "baudrate": 250000})
        self._update_remove_btn()

    def _legacy_to_bus(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        sess = cfg.get("session") or {}
        return {
            "name": "CAN Bus 1",
            "channel": str(sess.get("channel", "CAN1")),
            "baudrate": int(sess.get("baudrate", 500000)),
            "bustype": str(sess.get("bustype", "nixnet")),
            "dbc_path": str(cfg.get("dbc_path", "")),
            "signals": cfg.get("signals", []) or [],
        }

    # ------------------------------------------------------------------
    # Tab management
    # ------------------------------------------------------------------

    def _create_bus_tab(self, bus_cfg: Dict[str, Any]) -> _BusTab:
        tab = _BusTab(self._available_can_channels, self)
        tab.load_bus(bus_cfg)
        name = str(bus_cfg.get("name", "")).strip() or f"CAN Bus {self.tab_widget.count() + 1}"
        tab.txt_name.textChanged.connect(self._sync_tab_titles)  # type: ignore
        self.tab_widget.addTab(tab, name)
        return tab

    def _add_bus(self) -> None:
        idx = self.tab_widget.count() + 1
        bus_cfg = {"name": f"CAN Bus {idx}", "channel": self._default_channel_for_index(idx), "baudrate": 250000}
        tab = self._create_bus_tab(bus_cfg)
        self.tab_widget.setCurrentWidget(tab)
        self._update_remove_btn()

    def _used_channels(self) -> set[str]:
        used: set[str] = set()
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, _BusTab):
                channel = tab.cmb_channel.currentText().strip()
                if channel:
                    used.add(channel)
        return used

    def _default_channel_for_index(self, idx: int) -> str:
        used = self._used_channels()
        for channel in self._available_can_channels:
            if channel not in used:
                return channel
        return ""

    def _remove_bus(self) -> None:
        if self.tab_widget.count() <= 1:
            return
        idx = self.tab_widget.currentIndex()
        self.tab_widget.removeTab(idx)
        self._update_remove_btn()

    def _update_remove_btn(self) -> None:
        self.btn_remove_bus.setEnabled(self.tab_widget.count() > 1)

    def _sync_tab_titles(self) -> None:
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, _BusTab):
                name = tab.txt_name.text().strip() or f"CAN Bus {i + 1}"
                self.tab_widget.setTabText(i, name)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _on_accept(self) -> None:
        buses: List[Dict[str, Any]] = []
        all_aliases: List[str] = []
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if not isinstance(tab, _BusTab):
                continue
            bus = tab.to_bus_dict()
            if not bus.get("channel") and self._available_can_channels:
                QMessageBox.warning(self, "Missing channel", f"Bus tab '{bus.get('name')}': CAN channel is required.")
                return
            sigs = bus.get("signals", [])
            blank = [s for s in sigs if not s.get("alias")]
            if blank:
                names = [f"  {s['message']}.{s['signal']}" for s in blank[:10]]
                QMessageBox.warning(
                    self, "Missing Aliases",
                    f"Bus '{bus.get('name')}': these checked signals have no alias:\n\n" + "\n".join(names),
                )
                return
            bad = [s["alias"] for s in sigs if s.get("alias") and not validate_alias(s["alias"])]
            if bad:
                QMessageBox.warning(
                    self, "Invalid Alias",
                    f"Bus '{bus.get('name')}': invalid aliases:\n{', '.join(bad)}",
                )
                return
            for s in sigs:
                all_aliases.append(s.get("alias", ""))
            buses.append(bus)

        seen: Dict[str, str] = {}
        for a in all_aliases:
            if a in seen:
                QMessageBox.warning(
                    self, "Duplicate Alias",
                    f"Alias '{a}' is used on multiple buses. Aliases must be globally unique.",
                )
                return
            seen[a] = a

        doc: Dict[str, Any] = {
            "enabled": bool(self._cfg.get("enabled", True)),
            "mode": str(self._cfg.get("mode", "real")),
            "recording_rate_hz": int(self._cfg.get("recording_rate_hz", 10)),
            "buses": buses,
        }

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
