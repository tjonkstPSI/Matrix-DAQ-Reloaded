# Author: T. Onkst | Date: 03092026
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Set

try:
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import (
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QHeaderView,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QDoubleSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except Exception:
    raise


_COND_OPTIONS = [
    "Always Enabled",
    "Engine Running",
    "Engine Run time",
    "Test Time",
]

_ACTION_OPTIONS = [
    "Visible Alert",
    "Visible Alert + Shutdown",
]

_SHUTDOWN_TYPE_OPTIONS = [
    "Hard",
    "Soft",
]

_COLS = [
    "Channel",
    "Warn\nLow",
    "Warn Low\nX / Y",
    "Warn\nHigh",
    "Warn High\nX / Y",
    "Warn\nAction",
    "Alarm\nLow",
    "Alarm Low\nX / Y",
    "Alarm\nHigh",
    "Alarm High\nX / Y",
    "Alarm\nAction",
    "Shutdown\nType",
    "Enabling\nCond",
    "Enable\nThres",
]

_LATCH_COLS = {
    2: "Warn Low X / Y",
    4: "Warn High X / Y",
    7: "Alarm Low X / Y",
    9: "Alarm High X / Y",
}

_THRESHOLD_COLS = {
    1: "Warn Low",
    3: "Warn High",
    6: "Alarm Low",
    8: "Alarm High",
}


class _DelayPairDialog(QDialog):
    def __init__(self, title: str, enter_s: float, clear_s: float, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)

        root = QVBoxLayout(self)
        root.addWidget(QLabel("Set the latch and unlatch delay in seconds. Values must be zero or greater."))

        form = QFormLayout()
        self.spn_enter = QDoubleSpinBox(self)
        self.spn_enter.setRange(0.0, 1_000_000.0)
        self.spn_enter.setDecimals(3)
        self.spn_enter.setSingleStep(0.1)
        self.spn_enter.setValue(max(0.0, float(enter_s)))
        self.spn_enter.setToolTip("Latch time (X): seconds the condition must be true before the alert activates.")

        self.spn_clear = QDoubleSpinBox(self)
        self.spn_clear.setRange(0.0, 1_000_000.0)
        self.spn_clear.setDecimals(3)
        self.spn_clear.setSingleStep(0.1)
        self.spn_clear.setValue(max(0.0, float(clear_s)))
        self.spn_clear.setToolTip("Unlatch time (Y): seconds the condition must be clear before the alert resets.")

        form.addRow("Latch time (X)", self.spn_enter)
        form.addRow("Unlatch time (Y)", self.spn_clear)
        root.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        btns.accepted.connect(self.accept)  # type: ignore
        btns.rejected.connect(self.reject)  # type: ignore
        root.addWidget(btns)

    def values(self) -> tuple[float, float]:
        return (float(self.spn_enter.value()), float(self.spn_clear.value()))


class ChannelManagerConfigDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure Channel Manager")
        self.resize(1300, 760)
        self._cfg_path = Path(__file__).resolve().parents[3] / "configs" / "channel_manager.yaml"
        self._cfg: Dict[str, Any] = {}
        self._sub = None
        self._active_aliases: Set[str] = set()
        self._active_units: Dict[str, str] = {}
        self._init_ui()
        self._load()
        self._init_subscriber()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)

        # Logging controls.
        top = QWidget(self)
        form = QFormLayout(top)
        self.cmb_rate = QComboBox(self)
        self.cmb_rate.setEditable(True)
        self.cmb_rate.addItems(["1", "5", "10", "20", "50", "100"])
        self.txt_seg_time = QLineEdit(self)
        self.txt_seg_size = QLineEdit(self)
        form.addRow("Sample rate (Hz)", self.cmb_rate)
        form.addRow("Log size by time (s)", self.txt_seg_time)
        form.addRow("Log size by file size (MB)", self.txt_seg_size)
        root.addWidget(top)

        # Engine-running controls.
        eng = QWidget(self)
        eng_form = QFormLayout(eng)
        self.cmb_engine_alias = QComboBox(self)
        self.cmb_engine_alias.setEditable(False)
        self.txt_engine_rpm_threshold = QLineEdit(self)
        self.txt_engine_rpm_threshold.setText("0")
        eng_form.addRow("Engine speed source alias", self.cmb_engine_alias)
        eng_form.addRow("Engine running RPM threshold", self.txt_engine_rpm_threshold)
        root.addWidget(eng)

        root.addWidget(QLabel("Alarm table (active channels)"))
        self.table = QTableWidget(self)
        self.table.setColumnCount(len(_COLS))
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.cellDoubleClicked.connect(self._edit_delay_pair)  # type: ignore
        self._apply_table_column_layout()
        root.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        self.btn_import = QPushButton("Import")
        self.btn_import.clicked.connect(self._import_doc)  # type: ignore
        self.btn_export = QPushButton("Export")
        self.btn_export.clicked.connect(self._export_doc)  # type: ignore
        self.btn_add_active = QPushButton("Add active channels")
        self.btn_add_active.clicked.connect(self._add_active_channels)  # type: ignore
        self.btn_remove_extra = QPushButton("Remove extra channels")
        self.btn_remove_extra.clicked.connect(self._remove_extra_channels)  # type: ignore
        btn_row.addWidget(self.btn_import)
        btn_row.addWidget(self.btn_export)
        btn_row.addWidget(self.btn_add_active)
        btn_row.addWidget(self.btn_remove_extra)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

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
        self._load_doc(self._cfg)

    def _load_doc(self, doc: Dict[str, Any]) -> None:
        self.cmb_rate.setCurrentText(str(doc.get("recording_rate_hz", 10)))
        storage = doc.get("storage") or {}
        self.txt_seg_time.setText(str(storage.get("segment_time_limit_s", 14400)))
        self.txt_seg_size.setText(str(storage.get("segment_size_limit_mb", 100)))
        er = doc.get("engine_running") or {}
        self.txt_engine_rpm_threshold.setText(str(er.get("rpm_threshold", 0)))
        src_alias = str(er.get("source_alias", "")).strip()
        if src_alias:
            if self.cmb_engine_alias.findText(src_alias) < 0:
                self.cmb_engine_alias.addItem(src_alias)
            self.cmb_engine_alias.setCurrentText(src_alias)

        self.table.setRowCount(0)
        for item in doc.get("channels", []) or []:
            if isinstance(item, dict):
                self._add_row(self._row_from_item(item))
        self._apply_table_column_layout()

    def _parse_delay_pair(self, text: str) -> tuple[float, float]:
        t = str(text or "").strip()
        if not t:
            return (0.0, 0.0)
        parts = [p.strip() for p in t.split("/") if p.strip()]
        if len(parts) == 2:
            try:
                return (float(parts[0]), float(parts[1]))
            except Exception:
                return (0.0, 0.0)
        try:
            v = float(t)
            return (v, v)
        except Exception:
            return (0.0, 0.0)

    def _pair_to_text(self, enter_s: Any, clear_s: Any) -> str:
        try:
            return f"{float(enter_s):g} / {float(clear_s):g}"
        except Exception:
            return "0 / 0"

    def _row_from_item(self, item: Dict[str, Any]) -> Dict[str, str]:
        warn = item.get("warning") or {}
        alarm = item.get("alarm") or item.get("shutdown") or {}
        # Backward compatibility for legacy keys.
        warn_low = warn.get("low", item.get("low_warning", ""))
        warn_high = warn.get("high", item.get("high_warning", ""))
        alarm_low = alarm.get("low", item.get("low_shutdown", ""))
        alarm_high = alarm.get("high", item.get("high_shutdown", ""))
        warn_low_pair = self._pair_to_text(
            warn.get("low_enter_delay_s", item.get("enter_delay_s", 0.0)),
            warn.get("low_clear_delay_s", item.get("clear_delay_s", 0.0)),
        )
        warn_high_pair = self._pair_to_text(
            warn.get("high_enter_delay_s", item.get("enter_delay_s", 0.0)),
            warn.get("high_clear_delay_s", item.get("clear_delay_s", 0.0)),
        )
        alarm_low_pair = self._pair_to_text(
            alarm.get("low_enter_delay_s", item.get("enter_delay_s", 0.0)),
            alarm.get("low_clear_delay_s", item.get("clear_delay_s", 0.0)),
        )
        alarm_high_pair = self._pair_to_text(
            alarm.get("high_enter_delay_s", item.get("enter_delay_s", 0.0)),
            alarm.get("high_clear_delay_s", item.get("clear_delay_s", 0.0)),
        )
        warn_action = str(warn.get("action", "visible_alert")).strip().lower()
        alarm_action = str(alarm.get("action", "visible_alert")).strip().lower()
        raw_stype = str(alarm.get("shutdown_type", item.get("shutdown_type", "hard")) or "hard").strip().lower()
        stype_display = "Soft" if raw_stype == "soft" else "Hard"
        return {
            "Channel": str(item.get("alias", "")),
            "Warn Low": str(warn_low if warn_low is not None else ""),
            "Warn Low X / Y": warn_low_pair,
            "Warn High": str(warn_high if warn_high is not None else ""),
            "Warn High X / Y": warn_high_pair,
            "Warn Action": "Visible Alert + Shutdown" if warn_action == "visible_alert_shutdown" else "Visible Alert",
            "Alarm Low": str(alarm_low if alarm_low is not None else ""),
            "Alarm Low X / Y": alarm_low_pair,
            "Alarm High": str(alarm_high if alarm_high is not None else ""),
            "Alarm High X / Y": alarm_high_pair,
            "Alarm Action": "Visible Alert + Shutdown" if alarm_action == "visible_alert_shutdown" else "Visible Alert",
            "Shutdown Type": stype_display,
            "Enabling Cond": str(item.get("enabling_condition", "always_enabled")),
            "Enable Thres": str(item.get("enable_threshold", 0)),
        }

    def _add_row(self, values: Dict[str, str]) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        channel_item = QTableWidgetItem(values.get("Channel", ""))
        channel_item.setFlags(channel_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(r, 0, channel_item)
        self.table.setItem(r, 1, QTableWidgetItem(values.get("Warn Low", "")))
        self.table.setItem(r, 2, self._latch_item(values.get("Warn Low X / Y", "0 / 0")))
        self.table.setItem(r, 3, QTableWidgetItem(values.get("Warn High", "")))
        self.table.setItem(r, 4, self._latch_item(values.get("Warn High X / Y", "0 / 0")))
        warn_act = QComboBox(self.table)
        warn_act.addItems(_ACTION_OPTIONS)
        warn_act.setCurrentText(values.get("Warn Action", _ACTION_OPTIONS[0]))
        self.table.setCellWidget(r, 5, warn_act)
        self.table.setItem(r, 6, QTableWidgetItem(values.get("Alarm Low", "")))
        self.table.setItem(r, 7, self._latch_item(values.get("Alarm Low X / Y", "0 / 0")))
        self.table.setItem(r, 8, QTableWidgetItem(values.get("Alarm High", "")))
        self.table.setItem(r, 9, self._latch_item(values.get("Alarm High X / Y", "0 / 0")))
        alarm_act = QComboBox(self.table)
        alarm_act.addItems(_ACTION_OPTIONS)
        alarm_act.setCurrentText(values.get("Alarm Action", _ACTION_OPTIONS[0]))
        self.table.setCellWidget(r, 10, alarm_act)
        stype = QComboBox(self.table)
        stype.addItems(_SHUTDOWN_TYPE_OPTIONS)
        stype.setCurrentText(values.get("Shutdown Type", _SHUTDOWN_TYPE_OPTIONS[0]))
        self.table.setCellWidget(r, 11, stype)
        cond = QComboBox(self.table)
        cond.addItems(_COND_OPTIONS)
        cond.setCurrentText(self._cond_display(values.get("Enabling Cond", "always_enabled")))
        self.table.setCellWidget(r, 12, cond)
        self.table.setItem(r, 13, QTableWidgetItem(values.get("Enable Thres", "0")))

    def _cond_display(self, key: str) -> str:
        k = str(key).strip().lower()
        if k in {"engine_running", "engine running"}:
            return "Engine Running"
        if k in {"engine_run_time", "engine run time"}:
            return "Engine Run time"
        if k in {"test_time", "test time"}:
            return "Test Time"
        return "Always Enabled"

    def _cond_key(self, text: str) -> str:
        t = str(text).strip().lower()
        if t == "engine running":
            return "engine_running"
        if t == "engine run time":
            return "engine_run_time"
        if t == "test time":
            return "test_time"
        return "always_enabled"

    def _table_aliases(self) -> Set[str]:
        out: Set[str] = set()
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            alias = it.text().strip() if it else ""
            if alias:
                out.add(alias)
        return out

    def _add_active_channels(self) -> None:
        existing = self._table_aliases()
        for alias in sorted(self._active_aliases):
            if alias in existing:
                continue
            self._add_row(
                {
                    "Channel": alias,
                    "Warn Low": "",
                    "Warn Low X / Y": "0 / 0",
                    "Warn High": "",
                    "Warn High X / Y": "0 / 0",
                    "Warn Action": "Visible Alert",
                    "Alarm Low": "",
                    "Alarm Low X / Y": "0 / 0",
                    "Alarm High": "",
                    "Alarm High X / Y": "0 / 0",
                    "Alarm Action": "Visible Alert",
                    "Shutdown Type": "Hard",
                    "Enabling Cond": "always_enabled",
                    "Enable Thres": "0",
                }
            )
        self._apply_table_column_layout()

    def _latch_item(self, text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setToolTip("Double-click to set latch time (X) and unlatch time (Y).")
        return item

    def _apply_table_column_layout(self) -> None:
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setMinimumHeight(46)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(0, QHeaderView.Stretch)

        fixed_widths = {
            1: 64,
            2: 72,
            3: 64,
            4: 72,
            5: 125,
            6: 64,
            7: 72,
            8: 64,
            9: 72,
            10: 125,
            11: 90,
            12: 115,
            13: 78,
        }
        for col, width in fixed_widths.items():
            header.setSectionResizeMode(col, QHeaderView.Interactive)
            self.table.setColumnWidth(col, width)

    def _edit_delay_pair(self, row: int, col: int) -> None:
        if col not in _LATCH_COLS:
            return
        item = self.table.item(row, col)
        if item is None:
            item = self._latch_item("0 / 0")
            self.table.setItem(row, col, item)
        enter_s, clear_s = self._parse_delay_pair(item.text())
        dialog = _DelayPairDialog(_LATCH_COLS[col], enter_s, clear_s, self)
        if dialog.exec() != QDialog.Accepted:
            return
        enter_s, clear_s = dialog.values()
        item.setText(self._pair_to_text(enter_s, clear_s))

    def _remove_extra_channels(self) -> None:
        for r in range(self.table.rowCount() - 1, -1, -1):
            it = self.table.item(r, 0)
            alias = it.text().strip() if it else ""
            if alias and alias not in self._active_aliases:
                self.table.removeRow(r)

    def _init_subscriber(self) -> None:
        try:
            from src.core.ipc.bus import create_ui_subscriber
            sockets = create_ui_subscriber()
            if sockets is not None:
                self._sub = sockets.telemetry_sub
        except Exception:
            self._sub = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(200)
        self._poll_timer.timeout.connect(self._poll_telemetry)  # type: ignore
        self._poll_timer.start()

    def _refresh_engine_alias_options(self) -> None:
        current = self.cmb_engine_alias.currentText().strip()
        opts = sorted(
            [
                a for a in self._active_aliases
                if (("rpm" in a.lower()) or (a == "cSP_Eng"))
            ]
        )
        self.cmb_engine_alias.blockSignals(True)
        self.cmb_engine_alias.clear()
        for a in opts:
            self.cmb_engine_alias.addItem(a)
        if current in opts:
            self.cmb_engine_alias.setCurrentText(current)
        elif opts:
            self.cmb_engine_alias.setCurrentIndex(0)
        self.cmb_engine_alias.blockSignals(False)

    def _poll_telemetry(self) -> None:
        if self._sub is None:
            return
        try:
            import zmq
            got = False
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
                    units = msg.get("units") or {}
                    if isinstance(vals, dict):
                        for k in vals.keys():
                            alias = str(k)
                            if "/" in alias or alias == "Time_Relative_s":
                                continue
                            self._active_aliases.add(alias)
                            got = True
                    if isinstance(units, dict):
                        for k, v in units.items():
                            alias = str(k)
                            if "/" in alias or alias == "Time_Relative_s":
                                continue
                            self._active_units[alias] = str(v or "")
                except Exception:
                    continue
            if got:
                self._refresh_engine_alias_options()
        except Exception:
            pass

    def _float_or_none(self, text: str) -> float | None:
        t = str(text or "").strip()
        if not t:
            return None
        try:
            return float(t)
        except Exception:
            return None

    def _cell_text(self, row: int, col: int) -> str:
        item = self.table.item(row, col)
        return item.text().strip() if item else ""

    def _validate_table_inputs(self) -> str | None:
        numeric_cols = dict(_THRESHOLD_COLS)
        numeric_cols[13] = "Enable Thres"
        for r in range(self.table.rowCount()):
            alias = self._cell_text(r, 0) or f"row {r + 1}"
            for col, label in numeric_cols.items():
                text = self._cell_text(r, col)
                if not text:
                    continue
                try:
                    value = float(text)
                except Exception:
                    return f"Row {r + 1} ({alias}): {label} must be a number or empty."
                if not math.isfinite(value):
                    return f"Row {r + 1} ({alias}): {label} must be a finite number."
        return None

    def _action_key(self, text: str) -> str:
        return "visible_alert_shutdown" if "shutdown" in str(text).lower() else "visible_alert"

    def _build_doc(self) -> Dict[str, Any]:
        doc: Dict[str, Any] = dict(self._cfg)
        doc["enabled"] = bool(doc.get("enabled", True))
        doc["recording_rate_hz"] = float(self.cmb_rate.currentText().strip())
        storage = dict(doc.get("storage") or {})
        if "commit_interval_s" not in storage:
            storage["commit_interval_s"] = 2
        storage["segment_time_limit_s"] = float(self.txt_seg_time.text().strip() or "14400")
        storage["segment_size_limit_mb"] = float(self.txt_seg_size.text().strip() or "100")
        doc["storage"] = storage
        doc["engine_running"] = {
            "source_alias": self.cmb_engine_alias.currentText().strip(),
            "rpm_threshold": float(self.txt_engine_rpm_threshold.text().strip() or "0"),
        }

        channels: List[Dict[str, Any]] = []
        for r in range(self.table.rowCount()):
            alias_item = self.table.item(r, 0)
            alias = alias_item.text().strip() if alias_item else ""
            if not alias:
                continue
            wl = self._float_or_none(self.table.item(r, 1).text() if self.table.item(r, 1) else "")
            wl_en, wl_cl = self._parse_delay_pair(self.table.item(r, 2).text() if self.table.item(r, 2) else "")
            wh = self._float_or_none(self.table.item(r, 3).text() if self.table.item(r, 3) else "")
            wh_en, wh_cl = self._parse_delay_pair(self.table.item(r, 4).text() if self.table.item(r, 4) else "")
            warn_action_widget = self.table.cellWidget(r, 5)
            warn_action_text = warn_action_widget.currentText() if isinstance(warn_action_widget, QComboBox) else _ACTION_OPTIONS[0]
            sl = self._float_or_none(self.table.item(r, 6).text() if self.table.item(r, 6) else "")
            sl_en, sl_cl = self._parse_delay_pair(self.table.item(r, 7).text() if self.table.item(r, 7) else "")
            sh = self._float_or_none(self.table.item(r, 8).text() if self.table.item(r, 8) else "")
            sh_en, sh_cl = self._parse_delay_pair(self.table.item(r, 9).text() if self.table.item(r, 9) else "")
            alarm_action_widget = self.table.cellWidget(r, 10)
            alarm_action_text = alarm_action_widget.currentText() if isinstance(alarm_action_widget, QComboBox) else _ACTION_OPTIONS[0]
            stype_widget = self.table.cellWidget(r, 11)
            stype_text = stype_widget.currentText() if isinstance(stype_widget, QComboBox) else _SHUTDOWN_TYPE_OPTIONS[0]
            shutdown_type = "soft" if stype_text.strip().lower() == "soft" else "hard"
            cond_widget = self.table.cellWidget(r, 12)
            cond_key = self._cond_key(cond_widget.currentText() if isinstance(cond_widget, QComboBox) else _COND_OPTIONS[0])
            thr = self._float_or_none(self.table.item(r, 13).text() if self.table.item(r, 13) else "") or 0.0

            channels.append(
                {
                    "alias": alias,
                    "warning": {
                        "low": wl,
                        "low_enter_delay_s": wl_en,
                        "low_clear_delay_s": wl_cl,
                        "high": wh,
                        "high_enter_delay_s": wh_en,
                        "high_clear_delay_s": wh_cl,
                        "action": self._action_key(warn_action_text),
                    },
                    "alarm": {
                        "low": sl,
                        "low_enter_delay_s": sl_en,
                        "low_clear_delay_s": sl_cl,
                        "high": sh,
                        "high_enter_delay_s": sh_en,
                        "high_clear_delay_s": sh_cl,
                        "action": self._action_key(alarm_action_text),
                        "shutdown_type": shutdown_type,
                    },
                    "enabling_condition": cond_key,
                    "enable_threshold": float(thr),
                }
            )
        doc["channels"] = channels
        # Keep alarm events config if present.
        out_cfg = doc.get("output") or {}
        if "alarm_events" not in out_cfg:
            out_cfg["alarm_events"] = {
                "enabled": True,
                "format": "jsonl",
                "file_name": "alarm_events.jsonl",
                "fields": ["ts_hms", "alias", "from", "to", "value"],
            }
        doc["output"] = out_cfg
        return doc

    def _validate_doc(self, doc: Dict[str, Any]) -> str | None:
        try:
            hz = float(doc.get("recording_rate_hz", 0))
            if hz <= 0.0:
                return "Sample rate must be > 0."
        except Exception:
            return "Sample rate must be numeric."
        try:
            stor = doc.get("storage") or {}
            seg_t = float(stor.get("segment_time_limit_s", 0))
            seg_mb = float(stor.get("segment_size_limit_mb", 0))
            commit_s = float(stor.get("commit_interval_s", 2))
            if seg_t <= 0.0:
                return "Log time limit must be > 0."
            if seg_mb <= 0.0:
                return "Log size limit must be > 0."
            if commit_s < 0.1:
                return "Commit interval must be >= 0.1 s."
        except Exception:
            return "Invalid storage settings."
        aliases = [str(c.get("alias")) for c in (doc.get("channels") or []) if isinstance(c, dict) and c.get("alias")]
        if len(aliases) != len(set(aliases)):
            return "Duplicate channel rows in alarm table."
        return None

    def _save_doc(self, doc: Dict[str, Any], path: Path) -> bool:
        try:
            import yaml  # type: ignore
            path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
            return True
        except Exception as e:
            QMessageBox.critical(self, "Write Error", f"Failed to save YAML: {e}")
            return False

    def _import_doc(self) -> None:
        start = str(self._cfg_path.parent)
        p, _ = QFileDialog.getOpenFileName(self, "Import Channel Manager YAML", start, "YAML (*.yaml *.yml)")
        if not p:
            return
        doc = self._read_yaml(Path(p))
        if not isinstance(doc, dict) or not doc:
            QMessageBox.warning(self, "Import", "Invalid YAML file.")
            return
        self._cfg = dict(doc)
        self._load_doc(doc)

    def _export_doc(self) -> None:
        err = self._validate_table_inputs()
        if err:
            QMessageBox.warning(self, "Channel Manager", err)
            return
        doc = self._build_doc()
        err = self._validate_doc(doc)
        if err:
            QMessageBox.warning(self, "Channel Manager", err)
            return
        start = str(self._cfg_path.parent / "channel_manager_export.yaml")
        p, _ = QFileDialog.getSaveFileName(self, "Export Channel Manager YAML", start, "YAML (*.yaml *.yml)")
        if not p:
            return
        if self._save_doc(doc, Path(p)):
            QMessageBox.information(self, "Export", "Channel Manager YAML exported.")

    def _on_accept(self) -> None:
        err = self._validate_table_inputs()
        if err:
            QMessageBox.warning(self, "Channel Manager", err)
            return
        doc = self._build_doc()
        err = self._validate_doc(doc)
        if err:
            QMessageBox.warning(self, "Channel Manager", err)
            return
        if not self._save_doc(doc, self._cfg_path):
            return
        try:
            from src.core.ipc.bus import create_ui_control_push  # type: ignore
            ctrl = create_ui_control_push()
            if ctrl is not None:
                msg = json.dumps({"type": "reload_plugin", "plugin": "Channel_Manager"}).encode("utf-8")
                ctrl["control_push"].send(msg)
        except Exception:
            pass
        self.accept()

