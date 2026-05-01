# Author: T. Onkst | Date: 04302026

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QButtonGroup,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMessageBox,
        QPushButton,
        QRadioButton,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except Exception:
    raise


# ---------------------------------------------------------------------------
# Channel Picker Dialog (reusable for trigger channel + channel table)
# ---------------------------------------------------------------------------

class _ChannelPickerDialog(QDialog):
    """Searchable list picker for telemetry channel aliases."""

    def __init__(
        self,
        parent: QWidget | None,
        available: List[str],
        current: str = "",
        title: str = "Select Channel",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(400, 480)
        self.selected: str = ""
        self._available = sorted(available)

        root = QVBoxLayout(self)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_filter)  # type: ignore
        root.addWidget(self._search)

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SingleSelection)
        for a in self._available:
            item = QListWidgetItem(a)
            if a == current:
                item.setSelected(True)
            self._list.addItem(item)
        self._list.itemDoubleClicked.connect(self._on_double_click)  # type: ignore
        root.addWidget(self._list)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)  # type: ignore
        btns.rejected.connect(self.reject)  # type: ignore
        root.addWidget(btns)

    def _on_filter(self, text: str) -> None:
        filt = text.strip().lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setHidden(filt != "" and filt not in item.text().lower())

    def _on_double_click(self, item: QListWidgetItem) -> None:
        self.selected = item.text()
        self.accept()

    def _on_accept(self) -> None:
        items = self._list.selectedItems()
        if items:
            self.selected = items[0].text()
        self.accept()


# ---------------------------------------------------------------------------
# Statistics Config Dialog
# ---------------------------------------------------------------------------

_ALL_METRICS = [
    ("mean", "Mean"),
    ("stdev", "Std Dev"),
    ("min", "Min"),
    ("max", "Max"),
    ("p2p", "Peak-to-Peak"),
]


class StatisticsConfigDialog(QDialog):
    def __init__(self, parent=None, telemetry_aliases: List[str] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure Statistics")
        self.resize(680, 560)
        self._cfg_path = Path(__file__).resolve().parents[3] / "configs" / "statistics.yaml"
        self._cfg: Dict[str, Any] = {}
        self._telemetry_aliases: List[str] = sorted(telemetry_aliases or [])
        self._init_ui()
        self._load()

    # -- UI construction -----------------------------------------------------

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)

        # Snapshot window
        snap_box = QGroupBox("Snapshot Window")
        snap_lay = QFormLayout(snap_box)
        row_win = QHBoxLayout()
        self.cmb_win_type = QComboBox()
        self.cmb_win_type.addItems(["Seconds", "Samples"])
        self.cmb_win_type.currentTextChanged.connect(self._on_win_type_changed)  # type: ignore
        row_win.addWidget(self.cmb_win_type)
        self.spin_win_sec = QDoubleSpinBox()
        self.spin_win_sec.setRange(0.1, 3600.0)
        self.spin_win_sec.setDecimals(1)
        self.spin_win_sec.setValue(5.0)
        self.spin_win_sec.setSuffix(" s")
        row_win.addWidget(self.spin_win_sec)
        self.spin_win_samp = QSpinBox()
        self.spin_win_samp.setRange(1, 1_000_000)
        self.spin_win_samp.setValue(100)
        self.spin_win_samp.setSuffix(" samples")
        self.spin_win_samp.setVisible(False)
        row_win.addWidget(self.spin_win_samp)
        snap_lay.addRow("Window:", row_win)
        self.cmb_capture = QComboBox()
        self.cmb_capture.addItems(["forward", "backward"])
        snap_lay.addRow("Capture mode:", self.cmb_capture)
        root.addWidget(snap_box)

        # Metrics
        met_box = QGroupBox("Metrics")
        met_lay = QHBoxLayout(met_box)
        self._metric_checks: Dict[str, QCheckBox] = {}
        for key, label in _ALL_METRICS:
            chk = QCheckBox(label)
            chk.setChecked(True)
            met_lay.addWidget(chk)
            self._metric_checks[key] = chk
        root.addWidget(met_box)

        # Manual logging
        self.chk_manual = QCheckBox("Enable manual logging (Log Statistics button)")
        self.chk_manual.setChecked(True)
        root.addWidget(self.chk_manual)

        # Auto trigger (checkable group -- hidden when unchecked)
        self.grp_auto = QGroupBox("Automatic Trigger")
        self.grp_auto.setCheckable(True)
        self.grp_auto.setChecked(False)
        auto_lay = QFormLayout(self.grp_auto)
        row_trig = QHBoxLayout()
        self.lbl_trig_ch = QLineEdit()
        self.lbl_trig_ch.setReadOnly(True)
        self.lbl_trig_ch.setPlaceholderText("(none)")
        row_trig.addWidget(self.lbl_trig_ch, 1)
        self.btn_pick_trig = QPushButton("Pick...")
        self.btn_pick_trig.clicked.connect(self._pick_trigger_channel)  # type: ignore
        row_trig.addWidget(self.btn_pick_trig)
        auto_lay.addRow("Trigger channel:", row_trig)
        self.cmb_cmp = QComboBox()
        self.cmb_cmp.addItems([">", ">=", "<", "<="])
        auto_lay.addRow("Comparator:", self.cmb_cmp)
        self.spin_thr = QDoubleSpinBox()
        self.spin_thr.setRange(-1e9, 1e9)
        self.spin_thr.setDecimals(4)
        auto_lay.addRow("Threshold:", self.spin_thr)
        self.cmb_edge = QComboBox()
        self.cmb_edge.addItems(["rising", "falling"])
        auto_lay.addRow("Edge:", self.cmb_edge)
        root.addWidget(self.grp_auto)

        # Channel selection mode
        ch_box = QGroupBox("Channel Selection")
        ch_lay = QVBoxLayout(ch_box)
        self.rb_all = QRadioButton("All channels (dynamic — stats for every numeric channel)")
        self.rb_selected = QRadioButton("Selected channels only")
        self.rb_all.setChecked(True)
        self._ch_mode_group = QButtonGroup(self)
        self._ch_mode_group.addButton(self.rb_all)
        self._ch_mode_group.addButton(self.rb_selected)
        self.rb_all.toggled.connect(self._on_channel_mode_changed)  # type: ignore
        ch_lay.addWidget(self.rb_all)
        ch_lay.addWidget(self.rb_selected)

        self._ch_table_container = QWidget()
        tbl_lay = QVBoxLayout(self._ch_table_container)
        tbl_lay.setContentsMargins(0, 4, 0, 0)
        self.tbl_ch = QTableWidget(0, 2)
        self.tbl_ch.setHorizontalHeaderLabels(["Enabled", "Alias"])
        h = self.tbl_ch.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        self.tbl_ch.setColumnWidth(0, 55)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_ch.verticalHeader().setVisible(False)
        self.tbl_ch.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_ch.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_ch.cellDoubleClicked.connect(self._on_ch_table_dbl_click)  # type: ignore
        tbl_lay.addWidget(self.tbl_ch)
        btn_row = QHBoxLayout()
        btn_add = QPushButton("Add Channel")
        btn_add.clicked.connect(self._add_channel)  # type: ignore
        btn_del = QPushButton("Remove Selected")
        btn_del.clicked.connect(self._remove_channels)  # type: ignore
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_del)
        btn_row.addStretch()
        tbl_lay.addLayout(btn_row)
        self._ch_table_container.setVisible(False)
        ch_lay.addWidget(self._ch_table_container)
        root.addWidget(ch_box)

        # OK / Cancel
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)  # type: ignore
        btns.rejected.connect(self.reject)  # type: ignore
        root.addWidget(btns)

    # -- Slot handlers -------------------------------------------------------

    def _on_win_type_changed(self, text: str) -> None:
        is_sec = text == "Seconds"
        self.spin_win_sec.setVisible(is_sec)
        self.spin_win_samp.setVisible(not is_sec)

    def _on_channel_mode_changed(self, checked: bool) -> None:
        self._ch_table_container.setVisible(self.rb_selected.isChecked())

    def _pick_trigger_channel(self) -> None:
        aliases = self._telemetry_aliases or self._get_aliases_from_parent()
        dlg = _ChannelPickerDialog(
            self, aliases, self.lbl_trig_ch.text().strip(), "Select Trigger Channel"
        )
        if dlg.exec() == QDialog.Accepted and dlg.selected:
            self.lbl_trig_ch.setText(dlg.selected)

    def _add_channel(self) -> None:
        aliases = self._telemetry_aliases or self._get_aliases_from_parent()
        existing = set()
        for r in range(self.tbl_ch.rowCount()):
            it = self.tbl_ch.item(r, 1)
            if it:
                existing.add(it.text())
        available = [a for a in aliases if a not in existing]
        dlg = _ChannelPickerDialog(self, available, "", "Add Channel")
        if dlg.exec() == QDialog.Accepted and dlg.selected:
            row = self.tbl_ch.rowCount()
            self.tbl_ch.insertRow(row)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked)
            self.tbl_ch.setItem(row, 0, chk)
            self.tbl_ch.setItem(row, 1, QTableWidgetItem(dlg.selected))

    def _remove_channels(self) -> None:
        rows = sorted({idx.row() for idx in self.tbl_ch.selectedIndexes()}, reverse=True)
        for r in rows:
            self.tbl_ch.removeRow(r)

    def _on_ch_table_dbl_click(self, row: int, col: int) -> None:
        if col == 1:
            aliases = self._telemetry_aliases or self._get_aliases_from_parent()
            current = self.tbl_ch.item(row, 1).text() if self.tbl_ch.item(row, 1) else ""
            dlg = _ChannelPickerDialog(self, aliases, current, "Change Channel")
            if dlg.exec() == QDialog.Accepted and dlg.selected:
                self.tbl_ch.setItem(row, 1, QTableWidgetItem(dlg.selected))

    def _get_aliases_from_parent(self) -> List[str]:
        try:
            p = self.parent()
            if p and hasattr(p, "_last_payload"):
                vals = p._last_payload.get("values")
                if isinstance(vals, dict):
                    return sorted(vals.keys())
        except Exception:
            pass
        return []

    # -- Load / Save ---------------------------------------------------------

    def _load(self) -> None:
        self._cfg = self._read_yaml(self._cfg_path)
        c = self._cfg

        # Snapshot window
        snap = c.get("snapshot") or {}
        wtype = str(snap.get("window_type", "seconds")).lower()
        if wtype == "samples":
            self.cmb_win_type.setCurrentText("Samples")
            try:
                self.spin_win_samp.setValue(int(snap.get("window_value", 100)))
            except Exception:
                self.spin_win_samp.setValue(100)
        else:
            self.cmb_win_type.setCurrentText("Seconds")
            try:
                self.spin_win_sec.setValue(float(snap.get("window_value", 5.0)))
            except Exception:
                self.spin_win_sec.setValue(5.0)
        # Legacy support: old YAML with window.seconds / window.samples
        if "window_type" not in snap and isinstance(snap.get("window"), dict):
            old_win = snap["window"]
            if old_win.get("samples") is not None:
                self.cmb_win_type.setCurrentText("Samples")
                self.spin_win_samp.setValue(int(old_win["samples"]))
            elif old_win.get("seconds") is not None:
                self.cmb_win_type.setCurrentText("Seconds")
                self.spin_win_sec.setValue(float(old_win["seconds"]))
        self._on_win_type_changed(self.cmb_win_type.currentText())

        cm = str(snap.get("capture_mode", "forward"))
        idx = self.cmb_capture.findText(cm)
        self.cmb_capture.setCurrentIndex(idx if idx >= 0 else 0)

        # Metrics
        met = c.get("metrics") or {}
        sel = met.get("selected") or [k for k, _ in _ALL_METRICS]
        sel_set = set(sel) if isinstance(sel, list) else set()
        for key, chk in self._metric_checks.items():
            chk.setChecked(key in sel_set)

        # Manual logging
        man = c.get("manual_logging") or {}
        self.chk_manual.setChecked(bool(man.get("enabled", True)))

        # Auto trigger
        aut = c.get("automatic_logging") or {}
        self.grp_auto.setChecked(bool(aut.get("enabled", False)))
        trig = aut.get("trigger") or {}
        self.lbl_trig_ch.setText(str(trig.get("channel", "") or ""))
        cmpv = str(trig.get("comparator", ">"))
        i = self.cmb_cmp.findText(cmpv)
        self.cmb_cmp.setCurrentIndex(i if i >= 0 else 0)
        try:
            self.spin_thr.setValue(float(trig.get("threshold", 0.0)))
        except Exception:
            self.spin_thr.setValue(0.0)
        ed = str(trig.get("edge", "rising"))
        ei = self.cmb_edge.findText(ed)
        self.cmb_edge.setCurrentIndex(ei if ei >= 0 else 0)

        # Channel mode
        ch_mode = str(c.get("channel_mode", "all")).lower()
        chans = c.get("channels") or []
        if ch_mode == "selected" or (isinstance(chans, list) and len(chans) > 0):
            self.rb_selected.setChecked(True)
        else:
            self.rb_all.setChecked(True)
        self._on_channel_mode_changed(True)

        # Channel table
        self.tbl_ch.setRowCount(0)
        if isinstance(chans, list):
            for item in chans:
                if not isinstance(item, dict):
                    continue
                alias = str(item.get("alias", ""))
                if not alias:
                    continue
                row = self.tbl_ch.rowCount()
                self.tbl_ch.insertRow(row)
                chk = QTableWidgetItem()
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                chk.setCheckState(Qt.Checked if bool(item.get("enabled", True)) else Qt.Unchecked)
                self.tbl_ch.setItem(row, 0, chk)
                self.tbl_ch.setItem(row, 1, QTableWidgetItem(alias))

    def _build_doc(self) -> Dict[str, Any]:
        is_sec = self.cmb_win_type.currentText() == "Seconds"
        doc: Dict[str, Any] = {}
        doc["snapshot"] = {
            "window_type": "seconds" if is_sec else "samples",
            "window_value": float(self.spin_win_sec.value()) if is_sec else int(self.spin_win_samp.value()),
            "capture_mode": self.cmb_capture.currentText(),
        }
        doc["metrics"] = {
            "selected": [k for k, chk in self._metric_checks.items() if chk.isChecked()],
        }
        doc["manual_logging"] = {"enabled": self.chk_manual.isChecked()}
        doc["automatic_logging"] = {
            "enabled": self.grp_auto.isChecked(),
            "trigger": {
                "channel": self.lbl_trig_ch.text().strip() or None,
                "comparator": self.cmb_cmp.currentText(),
                "threshold": float(self.spin_thr.value()),
                "edge": self.cmb_edge.currentText(),
            },
        }
        doc["channel_mode"] = "selected" if self.rb_selected.isChecked() else "all"

        chans: List[Dict[str, Any]] = []
        if self.rb_selected.isChecked():
            for r in range(self.tbl_ch.rowCount()):
                it1 = self.tbl_ch.item(r, 1)
                alias = it1.text().strip() if it1 else ""
                if not alias:
                    continue
                it0 = self.tbl_ch.item(r, 0)
                enabled = it0.checkState() == Qt.Checked if it0 else True
                chans.append({"alias": alias, "enabled": enabled})
        doc["channels"] = chans
        return doc

    def _read_yaml(self, path: Path) -> Dict[str, Any]:
        try:
            import yaml  # type: ignore
            if not path.exists():
                return {}
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

    def _save_and_reload(self) -> bool:
        doc = self._build_doc()
        try:
            import yaml  # type: ignore
            self._cfg_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
            self._cfg = dict(doc)
        except Exception as e:
            QMessageBox.critical(self, "Write Error", f"Failed to save statistics.yaml: {e}")
            return False
        try:
            from src.core.ipc.bus import create_ui_control_push  # type: ignore
            ctrl = create_ui_control_push()
            if ctrl is not None:
                msg = json.dumps({"type": "reload_plugin", "plugin": "Statistics"}).encode("utf-8")
                ctrl["control_push"].send(msg)
        except Exception:
            pass
        return True

    def _on_accept(self) -> None:
        selected_metrics = [k for k, chk in self._metric_checks.items() if chk.isChecked()]
        if not selected_metrics:
            QMessageBox.warning(self, "No Metrics", "Select at least one metric.")
            return
        if not self._save_and_reload():
            return
        self.accept()
