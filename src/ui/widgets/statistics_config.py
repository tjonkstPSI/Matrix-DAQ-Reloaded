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
        QDoubleSpinBox,
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


class StatisticsConfigDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure Statistics")
        self.resize(720, 640)
        self._cfg_path = Path(__file__).resolve().parents[3] / "configs" / "statistics.yaml"
        self._cfg: Dict[str, Any] = {}
        self._init_ui()
        self._load()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)

        top = QFormLayout()
        self.chk_enabled = QCheckBox("Plugin enabled")
        self.chk_enabled.setChecked(True)
        self.spin_rate_hz = QSpinBox(self)
        self.spin_rate_hz.setRange(1, 1000)
        self.spin_rate_hz.setValue(10)
        top.addRow(self.chk_enabled)
        top.addRow("Recording rate (Hz)", self.spin_rate_hz)
        root.addLayout(top)

        snap_box = QGroupBox("Snapshot window")
        snap_form = QFormLayout(snap_box)
        self.spin_win_sec = QDoubleSpinBox(self)
        self.spin_win_sec.setRange(0.1, 3600.0)
        self.spin_win_sec.setDecimals(3)
        self.txt_win_samples = QLineEdit(self)
        self.txt_win_samples.setPlaceholderText("empty = not used")
        self.cmb_capture = QComboBox(self)
        self.cmb_capture.addItems(["backward", "forward"])
        self.chk_notify_skip = QCheckBox("Notify when snapshot skipped (insufficient window)")
        self.chk_notify_skip.setChecked(True)
        snap_form.addRow("Window (seconds)", self.spin_win_sec)
        snap_form.addRow("Window (samples)", self.txt_win_samples)
        snap_form.addRow("Capture mode", self.cmb_capture)
        snap_form.addRow(self.chk_notify_skip)
        root.addWidget(snap_box)

        met_box = QGroupBox("Metrics & logging")
        met_form = QFormLayout(met_box)
        self.txt_metrics = QLineEdit(self)
        self.txt_metrics.setPlaceholderText("mean, stdev, min, max, p2p")
        self.chk_manual = QCheckBox("Manual logging enabled")
        self.chk_manual.setChecked(True)
        self.chk_auto = QCheckBox("Automatic logging enabled")
        self.txt_trig_ch = QLineEdit(self)
        self.cmb_cmp = QComboBox(self)
        self.cmb_cmp.addItems([">", ">=", "<", "<="])
        self.spin_thr = QDoubleSpinBox(self)
        self.spin_thr.setRange(-1e9, 1e9)
        self.spin_thr.setDecimals(6)
        self.cmb_edge = QComboBox(self)
        self.cmb_edge.addItems(["rising", "falling"])
        self.spin_holdoff = QDoubleSpinBox(self)
        self.spin_holdoff.setRange(0.0, 3600.0)
        self.spin_holdoff.setDecimals(3)
        met_form.addRow("Default metrics", self.txt_metrics)
        met_form.addRow(self.chk_manual)
        met_form.addRow(self.chk_auto)
        met_form.addRow("Trigger channel", self.txt_trig_ch)
        met_form.addRow("Comparator", self.cmb_cmp)
        met_form.addRow("Threshold", self.spin_thr)
        met_form.addRow("Edge", self.cmb_edge)
        met_form.addRow("Holdoff (s)", self.spin_holdoff)
        root.addWidget(met_box)

        out_box = QGroupBox("Output")
        out_form = QFormLayout(out_box)
        self.cmb_fmt = QComboBox(self)
        self.cmb_fmt.addItems(["wide", "long"])
        self.chk_excel = QCheckBox("Enable Excel export")
        self.chk_per_sheet = QCheckBox("One stat per Excel sheet")
        self.chk_per_sheet.setChecked(True)
        out_form.addRow("Format", self.cmb_fmt)
        out_form.addRow(self.chk_excel)
        out_form.addRow(self.chk_per_sheet)
        root.addWidget(out_box)

        root.addWidget(QLabel("Channels (leave empty for dynamic mode: all numeric channels)"))
        self.tbl_ch = QTableWidget(self)
        self.tbl_ch.setColumnCount(3)
        self.tbl_ch.setHorizontalHeaderLabels(["Enabled", "Alias", "Stats (comma-separated)"])
        self.tbl_ch.horizontalHeader().setStretchLastSection(True)
        self.tbl_ch.setMinimumHeight(160)
        root.addWidget(self.tbl_ch)

        row_btns = QHBoxLayout()
        self.btn_add_row = QPushButton("Add row")
        self.btn_add_row.clicked.connect(self._add_channel_row)  # type: ignore
        self.btn_del_row = QPushButton("Remove selected")
        self.btn_del_row.clicked.connect(self._remove_channel_rows)  # type: ignore
        row_btns.addWidget(self.btn_add_row)
        row_btns.addWidget(self.btn_del_row)
        row_btns.addStretch(1)
        root.addLayout(row_btns)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        btns.accepted.connect(self._on_accept)  # type: ignore
        btns.rejected.connect(self.reject)  # type: ignore
        root.addWidget(btns)

    def _add_channel_row(self) -> None:
        r = self.tbl_ch.rowCount()
        self.tbl_ch.insertRow(r)
        self.tbl_ch.setItem(r, 0, QTableWidgetItem("true"))
        self.tbl_ch.setItem(r, 1, QTableWidgetItem(""))
        self.tbl_ch.setItem(r, 2, QTableWidgetItem("mean, stdev, min, max, p2p"))

    def _remove_channel_rows(self) -> None:
        rows = sorted({i.row() for i in self.tbl_ch.selectedIndexes()}, reverse=True)
        for r in rows:
            self.tbl_ch.removeRow(r)

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
        try:
            self.spin_rate_hz.setValue(int(float(c.get("recording_rate_hz", 10))))
        except Exception:
            self.spin_rate_hz.setValue(10)

        snap = (c.get("snapshot") or {}) if isinstance(c.get("snapshot"), dict) else {}
        win = snap.get("window") or {}
        try:
            self.spin_win_sec.setValue(float(win.get("seconds", 5.0)))
        except Exception:
            self.spin_win_sec.setValue(5.0)
        sam = win.get("samples")
        self.txt_win_samples.setText("" if sam is None else str(sam))
        cm = str(snap.get("capture_mode", "backward"))
        idx = self.cmb_capture.findText(cm)
        self.cmb_capture.setCurrentIndex(idx if idx >= 0 else 0)
        self.chk_notify_skip.setChecked(bool(snap.get("notify_on_skip", True)))

        met = (c.get("metrics") or {}) if isinstance(c.get("metrics"), dict) else {}
        sel = met.get("selected") or ["mean", "stdev", "min", "max", "p2p"]
        if isinstance(sel, list):
            self.txt_metrics.setText(", ".join(str(x) for x in sel))
        else:
            self.txt_metrics.setText(str(sel))

        man = (c.get("manual_logging") or {}) if isinstance(c.get("manual_logging"), dict) else {}
        self.chk_manual.setChecked(bool(man.get("enabled", True)))

        aut = (c.get("automatic_logging") or {}) if isinstance(c.get("automatic_logging"), dict) else {}
        self.chk_auto.setChecked(bool(aut.get("enabled", False)))
        trig = aut.get("trigger") or {}
        if isinstance(trig, dict):
            self.txt_trig_ch.setText(str(trig.get("channel", "") or ""))
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
            try:
                self.spin_holdoff.setValue(float(trig.get("holdoff_s", 0.0)))
            except Exception:
                self.spin_holdoff.setValue(0.0)

        out = (c.get("output") or {}) if isinstance(c.get("output"), dict) else {}
        fmt = str(out.get("format", "wide"))
        fi = self.cmb_fmt.findText(fmt)
        self.cmb_fmt.setCurrentIndex(fi if fi >= 0 else 0)
        self.chk_excel.setChecked(bool(out.get("enable_excel_export", False)))
        self.chk_per_sheet.setChecked(bool(out.get("excel_per_stat_sheet", True)))

        self.tbl_ch.setRowCount(0)
        chans = c.get("channels") or []
        if isinstance(chans, list):
            for item in chans:
                if not isinstance(item, dict):
                    continue
                alias = str(item.get("alias", "") or "")
                if not alias:
                    continue
                r = self.tbl_ch.rowCount()
                self.tbl_ch.insertRow(r)
                en = bool(item.get("enabled", True))
                self.tbl_ch.setItem(r, 0, QTableWidgetItem("true" if en else "false"))
                self.tbl_ch.setItem(r, 1, QTableWidgetItem(alias))
                stats = item.get("stats") or []
                if isinstance(stats, list):
                    self.tbl_ch.setItem(r, 2, QTableWidgetItem(", ".join(str(s) for s in stats)))
                else:
                    self.tbl_ch.setItem(r, 2, QTableWidgetItem(str(stats)))

    def _parse_metrics_line(self) -> List[str]:
        raw = self.txt_metrics.text().strip()
        if not raw:
            return ["mean", "stdev", "min", "max", "p2p"]
        parts = [p.strip() for p in raw.replace(";", ",").split(",")]
        return [p for p in parts if p]

    def _build_doc(self) -> Dict[str, Any]:
        doc: Dict[str, Any] = dict(self._cfg)
        doc["enabled"] = self.chk_enabled.isChecked()
        doc["recording_rate_hz"] = int(self.spin_rate_hz.value())

        sam_raw = self.txt_win_samples.text().strip()
        samples_val = None
        if sam_raw:
            try:
                samples_val = int(sam_raw)
            except Exception:
                samples_val = None

        doc["snapshot"] = {
            "window": {"seconds": float(self.spin_win_sec.value()), "samples": samples_val},
            "capture_mode": self.cmb_capture.currentText(),
            "notify_on_skip": self.chk_notify_skip.isChecked(),
        }
        doc["metrics"] = {"selected": self._parse_metrics_line()}
        doc["manual_logging"] = {"enabled": self.chk_manual.isChecked()}
        doc["automatic_logging"] = {
            "enabled": self.chk_auto.isChecked(),
            "trigger": {
                "channel": self.txt_trig_ch.text().strip() or None,
                "comparator": self.cmb_cmp.currentText(),
                "threshold": float(self.spin_thr.value()),
                "edge": self.cmb_edge.currentText(),
                "holdoff_s": float(self.spin_holdoff.value()),
            },
        }
        doc["output"] = {
            "format": self.cmb_fmt.currentText(),
            "enable_excel_export": self.chk_excel.isChecked(),
            "excel_per_stat_sheet": self.chk_per_sheet.isChecked(),
        }

        chans: List[Dict[str, Any]] = []
        for r in range(self.tbl_ch.rowCount()):
            it0 = self.tbl_ch.item(r, 0)
            it1 = self.tbl_ch.item(r, 1)
            it2 = self.tbl_ch.item(r, 2)
            alias = (it1.text().strip() if it1 else "").strip()
            if not alias:
                continue
            en_s = (it0.text().strip().lower() if it0 else "true")
            enabled = en_s not in ("0", "false", "no", "")
            stats_raw = (it2.text().strip() if it2 else "") if it2 else ""
            stats_list = [s.strip() for s in stats_raw.replace(";", ",").split(",") if s.strip()]
            if not stats_list:
                stats_list = self._parse_metrics_line()
            chans.append({"alias": alias, "stats": stats_list, "enabled": enabled})
        doc["channels"] = chans
        return doc

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
        if not self._save_and_reload():
            return
        self.accept()
