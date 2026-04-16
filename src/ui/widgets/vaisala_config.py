# Author: T. Onkst | Date: 03092026
# Updated: 03092026 — pressure mode, filtering, dynamic channel picker

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
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
        QWidget,
    )
except Exception:
    raise

from src.plugins.vaisala import REGISTER_MAP
from .nidaq_alias_picker import AliasPickerDialog

MODEL_UNIT_IDS = {
    "HMT330": 1,
    "Indigo510": 241,
}


class VaisalaConfigDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure Vaisala")
        self.resize(780, 700)
        self._cfg_path = Path(__file__).resolve().parents[3] / "configs" / "vaisala.yaml"
        self._cfg: Dict[str, Any] = {}
        self._init_ui()
        self._load()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)

        top_form = QFormLayout()
        self.cmb_model = QComboBox(self)
        self.cmb_model.addItems(["HMT330", "Indigo510"])
        top_form.addRow("Model", self.cmb_model)
        root.addLayout(top_form)

        conn_grp = QGroupBox("Connection (Modbus TCP)")
        cf = QFormLayout(conn_grp)
        self.txt_host = QLineEdit(self)
        self.txt_host.setPlaceholderText("192.168.1.100")
        self.spin_port = QSpinBox(self)
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(502)
        cf.addRow("Host / IP", self.txt_host)
        cf.addRow("Port", self.spin_port)
        root.addWidget(conn_grp)

        # --- Pressure group ---
        prs_grp = QGroupBox("Pressure Compensation")
        prs_layout = QVBoxLayout(prs_grp)
        prs_top = QFormLayout()
        self.cmb_pressure_mode = QComboBox(self)
        self.cmb_pressure_mode.addItems(["Fixed", "Dynamic"])
        prs_top.addRow("Mode", self.cmb_pressure_mode)
        prs_layout.addLayout(prs_top)

        self._prs_fixed_widget = QWidget(self)
        prs_fixed_form = QFormLayout(self._prs_fixed_widget)
        prs_fixed_form.setContentsMargins(0, 0, 0, 0)
        self.spin_pressure_hpa = QDoubleSpinBox(self)
        self.spin_pressure_hpa.setRange(0.0, 9999.0)
        self.spin_pressure_hpa.setDecimals(2)
        self.spin_pressure_hpa.setValue(1013.25)
        self.spin_pressure_hpa.setSuffix(" hPa")
        prs_fixed_form.addRow("Fixed value", self.spin_pressure_hpa)
        prs_layout.addWidget(self._prs_fixed_widget)

        self._prs_dyn_widget = QWidget(self)
        prs_dyn_form = QFormLayout(self._prs_dyn_widget)
        prs_dyn_form.setContentsMargins(0, 0, 0, 0)
        self.cmb_dyn_channel = QComboBox(self)
        self.cmb_dyn_channel.setEditable(True)
        self.cmb_dyn_channel.setInsertPolicy(QComboBox.NoInsert)
        self.cmb_dyn_channel.lineEdit().setPlaceholderText("Channel alias")
        prs_dyn_form.addRow("Source channel", self.cmb_dyn_channel)
        self.txt_dyn_unit = QLineEdit(self)
        self.txt_dyn_unit.setPlaceholderText("hPa")
        prs_dyn_form.addRow("Source unit", self.txt_dyn_unit)
        self.spin_dyn_gain = QDoubleSpinBox(self)
        self.spin_dyn_gain.setRange(-1e6, 1e6)
        self.spin_dyn_gain.setDecimals(6)
        self.spin_dyn_gain.setValue(1.0)
        prs_dyn_form.addRow("Gain", self.spin_dyn_gain)
        self.spin_dyn_offset = QDoubleSpinBox(self)
        self.spin_dyn_offset.setRange(-1e6, 1e6)
        self.spin_dyn_offset.setDecimals(6)
        self.spin_dyn_offset.setValue(0.0)
        prs_dyn_form.addRow("Offset", self.spin_dyn_offset)
        prs_layout.addWidget(self._prs_dyn_widget)

        self.cmb_pressure_mode.currentTextChanged.connect(self._on_pressure_mode_changed)  # type: ignore
        self._on_pressure_mode_changed(self.cmb_pressure_mode.currentText())
        root.addWidget(prs_grp)

        # --- Filtering group ---
        filt_grp = QGroupBox("Filtering")
        filt_form = QFormLayout(filt_grp)
        self.cmb_filtering = QComboBox(self)
        self.cmb_filtering.addItems(["None", "Standard", "Extended"])
        filt_form.addRow("Mode", self.cmb_filtering)
        root.addWidget(filt_grp)

        root.addWidget(QLabel("Channels (check to enable, edit alias as needed)"))
        self.tbl_ch = QTableWidget(len(REGISTER_MAP), 5, self)
        self.tbl_ch.setHorizontalHeaderLabels(["", "ID", "Description", "Unit", "Alias"])
        self.tbl_ch.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tbl_ch.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tbl_ch.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl_ch.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.tbl_ch.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.tbl_ch.verticalHeader().setVisible(False)
        self.tbl_ch.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_ch.setMinimumHeight(300)

        for row, reg in enumerate(REGISTER_MAP):
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk_item.setCheckState(Qt.Unchecked)
            self.tbl_ch.setItem(row, 0, chk_item)

            id_item = QTableWidgetItem(reg["id"])
            id_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.tbl_ch.setItem(row, 1, id_item)

            desc_item = QTableWidgetItem(reg["description"])
            desc_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.tbl_ch.setItem(row, 2, desc_item)

            unit_item = QTableWidgetItem(reg["unit"])
            unit_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.tbl_ch.setItem(row, 3, unit_item)

            alias_item = QTableWidgetItem("")
            alias_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.tbl_ch.setItem(row, 4, alias_item)

        self.tbl_ch.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_ch.cellDoubleClicked.connect(self._on_ch_double_click)  # type: ignore
        root.addWidget(self.tbl_ch)

        dlg_btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        dlg_btns.accepted.connect(self._on_accept)  # type: ignore
        dlg_btns.rejected.connect(self.reject)  # type: ignore
        root.addWidget(dlg_btns)

    # ------------------------------------------------------------------
    # Pressure mode toggle
    # ------------------------------------------------------------------

    def _on_pressure_mode_changed(self, text: str) -> None:
        is_fixed = text == "Fixed"
        self._prs_fixed_widget.setVisible(is_fixed)
        self._prs_dyn_widget.setVisible(not is_fixed)

    # ------------------------------------------------------------------
    # Channel alias picker (double-click)
    # ------------------------------------------------------------------

    def _on_ch_double_click(self, row: int, col: int) -> None:
        if col != 4:
            return
        current = (self.tbl_ch.item(row, 4).text().strip()
                   if self.tbl_ch.item(row, 4) else "")
        dlg = AliasPickerDialog(parent=self, current_alias=current)
        if dlg.exec() == QDialog.Accepted and dlg.selected_alias:
            self.tbl_ch.item(row, 4).setText(dlg.selected_alias)

    # ------------------------------------------------------------------
    # Channel alias scanner for dynamic pressure picker
    # ------------------------------------------------------------------

    def _collect_available_channels(self) -> List[str]:
        """Scan plugin YAML configs and return a sorted list of known aliases."""
        configs_dir = self._cfg_path.parent
        aliases: List[str] = []

        def _safe_yaml(path: Path) -> dict:
            try:
                import yaml  # type: ignore
                if not path.exists():
                    return {}
                return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:
                return {}

        ni = _safe_yaml(configs_dir / "ni_daq.yaml")
        for ch in (ni.get("channels") or []):
            if isinstance(ch, dict) and ch.get("alias"):
                aliases.append(str(ch["alias"]))

        can = _safe_yaml(configs_dir / "can.yaml")
        for sig in (can.get("signals") or []):
            if isinstance(sig, dict) and sig.get("alias") and sig.get("enabled", True):
                aliases.append(str(sig["alias"]))

        ccp = _safe_yaml(configs_dir / "ccp.yaml")
        for ch in (ccp.get("channels") or []):
            if isinstance(ch, dict) and ch.get("name"):
                aliases.append(str(ch["name"]))

        modbus = _safe_yaml(configs_dir / "modbus.yaml")
        for rd in (modbus.get("reads") or []):
            if isinstance(rd, dict) and rd.get("alias"):
                aliases.append(str(rd["alias"]))

        return sorted(set(a for a in aliases if a.strip()))

    # ------------------------------------------------------------------
    # Load / save
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
        c = self._cfg

        model = (c.get("model") or {}) if isinstance(c.get("model"), dict) else {}
        model_name = str(model.get("selected", "HMT330") or "HMT330")
        mi_model = self.cmb_model.findText(model_name)
        self.cmb_model.setCurrentIndex(mi_model if mi_model >= 0 else 0)

        conn = (c.get("connection") or {}) if isinstance(c.get("connection"), dict) else {}
        self.txt_host.setText(str(conn.get("host", "192.168.1.100")))
        try:
            self.spin_port.setValue(int(conn.get("port", 502)))
        except Exception:
            self.spin_port.setValue(502)
        ch_cfg_by_id: Dict[str, Dict[str, Any]] = {}
        for item in (c.get("channels") or []):
            if isinstance(item, dict) and item.get("id"):
                ch_cfg_by_id[str(item["id"])] = item

        for row, reg in enumerate(REGISTER_MAP):
            saved = ch_cfg_by_id.get(reg["id"])
            chk = self.tbl_ch.item(row, 0)
            alias_item = self.tbl_ch.item(row, 4)
            if saved is not None:
                chk.setCheckState(Qt.Checked if bool(saved.get("enabled", False)) else Qt.Unchecked)
                alias_item.setText(str(saved.get("alias") or ""))
            else:
                chk.setCheckState(Qt.Unchecked)
                alias_item.setText("")

        # Pressure
        prs = c.get("pressure") or {}
        prs_mode = str(prs.get("mode", "fixed")).strip().lower()
        idx_pm = self.cmb_pressure_mode.findText("Dynamic" if prs_mode == "dynamic" else "Fixed")
        self.cmb_pressure_mode.setCurrentIndex(idx_pm if idx_pm >= 0 else 0)
        try:
            self.spin_pressure_hpa.setValue(float(prs.get("fixed_value_hpa", 1013.25)))
        except Exception:
            self.spin_pressure_hpa.setValue(1013.25)

        dyn = prs.get("dynamic") or {}
        available = self._collect_available_channels()
        self.cmb_dyn_channel.clear()
        self.cmb_dyn_channel.addItems(available)
        saved_ch = str(dyn.get("source_channel", "")).strip()
        if saved_ch:
            idx_ch = self.cmb_dyn_channel.findText(saved_ch)
            if idx_ch >= 0:
                self.cmb_dyn_channel.setCurrentIndex(idx_ch)
            else:
                self.cmb_dyn_channel.setEditText(saved_ch)
        self.txt_dyn_unit.setText(str(dyn.get("source_unit", "hPa")))
        try:
            self.spin_dyn_gain.setValue(float(dyn.get("gain", 1.0)))
        except Exception:
            self.spin_dyn_gain.setValue(1.0)
        try:
            self.spin_dyn_offset.setValue(float(dyn.get("offset", 0.0)))
        except Exception:
            self.spin_dyn_offset.setValue(0.0)

        # Filtering
        filt = str(c.get("filtering", "none")).strip().lower()
        filt_map = {"none": "None", "std": "Standard", "ext": "Extended"}
        idx_f = self.cmb_filtering.findText(filt_map.get(filt, "None"))
        self.cmb_filtering.setCurrentIndex(idx_f if idx_f >= 0 else 0)

    def _on_accept(self) -> None:
        channels: List[Dict[str, Any]] = []
        enabled_aliases: List[str] = []

        for row, reg in enumerate(REGISTER_MAP):
            chk = self.tbl_ch.item(row, 0)
            alias_item = self.tbl_ch.item(row, 4)
            enabled = chk is not None and chk.checkState() == Qt.Checked
            alias = (alias_item.text().strip() if alias_item else "")
            channels.append({
                "id": reg["id"],
                "alias": alias,
                "enabled": enabled,
            })
            if enabled:
                if not alias:
                    QMessageBox.warning(self, "Empty Alias", f"Channel {reg['id']} is enabled but has no alias.")
                    return
                enabled_aliases.append(alias)

        if len(enabled_aliases) != len(set(enabled_aliases)):
            seen: Dict[str, int] = {}
            dupes: List[str] = []
            for a in enabled_aliases:
                seen[a] = seen.get(a, 0) + 1
                if seen[a] == 2:
                    dupes.append(a)
            QMessageBox.warning(
                self, "Duplicate Aliases",
                "The following aliases are used by multiple enabled channels:\n\n"
                + "\n".join(f"  {d}" for d in dupes),
            )
            return

        selected_model = self.cmb_model.currentText().strip() or "HMT330"
        unit_id = MODEL_UNIT_IDS.get(selected_model, 1)

        prs_mode_text = self.cmb_pressure_mode.currentText()
        prs_mode_val = "dynamic" if prs_mode_text == "Dynamic" else "fixed"

        filt_text = self.cmb_filtering.currentText()
        filt_map = {"None": "none", "Standard": "std", "Extended": "ext"}
        filt_val = filt_map.get(filt_text, "none")

        doc: Dict[str, Any] = dict(self._cfg)
        doc["connection"] = {
            "host": self.txt_host.text().strip() or "192.168.1.100",
            "port": int(self.spin_port.value()),
            "unit_id": unit_id,
            "timeout_ms": int((self._cfg.get("connection") or {}).get("timeout_ms", 1000)),
            "poll_rate_hz": int((self._cfg.get("connection") or {}).get("poll_rate_hz", 1)),
        }
        doc["model"] = {
            "selected": selected_model,
        }
        doc["pressure"] = {
            "mode": prs_mode_val,
            "fixed_value_hpa": round(self.spin_pressure_hpa.value(), 2),
            "dynamic": {
                "source_channel": self.cmb_dyn_channel.currentText().strip(),
                "source_unit": self.txt_dyn_unit.text().strip() or "hPa",
                "gain": round(self.spin_dyn_gain.value(), 6),
                "offset": round(self.spin_dyn_offset.value(), 6),
            },
        }
        doc["filtering"] = filt_val
        doc["channels"] = channels

        try:
            import yaml  # type: ignore
            self._cfg_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
            self._cfg = dict(doc)
        except Exception as e:
            QMessageBox.critical(self, "Write Error", f"Failed to save vaisala.yaml: {e}")
            return

        try:
            from src.core.ipc.bus import create_ui_control_push  # type: ignore
            ctrl = create_ui_control_push()
            if ctrl is not None:
                msg = json.dumps({"type": "reload_plugin", "plugin": "Vaisala"}).encode("utf-8")
                ctrl["control_push"].send(msg)
        except Exception:
            pass
        self.accept()
