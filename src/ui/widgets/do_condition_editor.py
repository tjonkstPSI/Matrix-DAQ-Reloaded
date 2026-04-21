# Author: T. Onkst | Date: 04202026

from __future__ import annotations

import json
from typing import Any, Dict, Optional

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (
        QDialog,
        QVBoxLayout,
        QHBoxLayout,
        QFormLayout,
        QGroupBox,
        QLabel,
        QLineEdit,
        QComboBox,
        QPushButton,
        QDialogButtonBox,
    )
except Exception:
    raise


_OPERATORS = [">", ">=", "<", "<=", "==", "!=", "TRUE", "FALSE"]


class DOConditionEditorDialog(QDialog):
    """Editor for a single digital output condition with live output test."""

    def __init__(
        self,
        parent=None,
        *,
        do_alias: str = "",
        current_condition: Optional[Dict[str, Any]] = None,
        telemetry_getter=None,
        control_sender=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"DO Condition — {do_alias}" if do_alias else "DO Condition Editor")
        self.setMinimumWidth(420)
        self._do_alias = do_alias
        self._telemetry_getter = telemetry_getter
        self._control_sender = control_sender
        self._forced_state: Optional[bool] = None
        self.result_condition: Optional[Dict[str, Any]] = None

        self._init_ui()
        self._load_condition(current_condition)
        self._update_preview()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_telemetry)
        self._timer.start(250)

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)

        # --- Condition group ---
        cond_grp = QGroupBox("Condition", self)
        cond_lay = QFormLayout(cond_grp)

        self.lbl_source = QLabel(self._do_alias or "(no alias)", self)
        self.lbl_source.setStyleSheet("font-weight: bold;")
        cond_lay.addRow("Channel:", self.lbl_source)

        self.cmb_op = QComboBox(self)
        self.cmb_op.addItems(_OPERATORS)
        self.cmb_op.currentTextChanged.connect(self._on_op_changed)
        cond_lay.addRow("Operator:", self.cmb_op)

        self.txt_threshold = QLineEdit(self)
        self.txt_threshold.setPlaceholderText("e.g. 0.5")
        cond_lay.addRow("Threshold:", self.txt_threshold)

        self.lbl_live_val = QLabel("—", self)
        cond_lay.addRow("Live Value:", self.lbl_live_val)

        self.lbl_preview = QLabel("", self)
        self.lbl_preview.setStyleSheet("font-weight: bold;")
        cond_lay.addRow("Preview:", self.lbl_preview)

        self.cmb_op.currentTextChanged.connect(self._update_preview)
        self.txt_threshold.textChanged.connect(self._update_preview)

        root.addWidget(cond_grp)

        # --- Output Test group ---
        test_grp = QGroupBox("Output Test", self)
        test_lay = QVBoxLayout(test_grp)

        state_row = QHBoxLayout()
        self.lbl_do_state = QLabel("Current State: —", self)
        self.lbl_do_state.setStyleSheet("font-size: 14px;")
        state_row.addWidget(self.lbl_do_state)
        test_lay.addLayout(state_row)

        btn_row = QHBoxLayout()
        self.btn_high = QPushButton("Force HIGH", self)
        self.btn_high.setStyleSheet("background-color: #27ae60; color: white; padding: 6px 16px;")
        self.btn_high.clicked.connect(lambda: self._force_output(True))
        btn_row.addWidget(self.btn_high)

        self.btn_low = QPushButton("Force LOW", self)
        self.btn_low.setStyleSheet("background-color: #c0392b; color: white; padding: 6px 16px;")
        self.btn_low.clicked.connect(lambda: self._force_output(False))
        btn_row.addWidget(self.btn_low)

        self.btn_release = QPushButton("Release", self)
        self.btn_release.setToolTip("Return control to the condition evaluator")
        self.btn_release.clicked.connect(self._release_output)
        self.btn_release.setEnabled(False)
        btn_row.addWidget(self.btn_release)

        test_lay.addLayout(btn_row)
        root.addWidget(test_grp)

        # --- Clear / OK / Cancel ---
        bottom = QHBoxLayout()
        self.btn_clear = QPushButton("Clear Condition", self)
        self.btn_clear.clicked.connect(self._clear_condition)
        bottom.addWidget(self.btn_clear)
        bottom.addStretch()

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        bottom.addWidget(btns)
        root.addLayout(bottom)

    def _load_condition(self, cond: Optional[Dict[str, Any]]) -> None:
        if not cond:
            return
        op = str(cond.get("operator", "")).strip()
        threshold = cond.get("threshold", "")

        if op in _OPERATORS:
            self.cmb_op.setCurrentText(op)
        if op not in ("TRUE", "FALSE"):
            self.txt_threshold.setText(str(threshold))

    def _on_op_changed(self, text: str) -> None:
        is_bool = text in ("TRUE", "FALSE")
        self.txt_threshold.setEnabled(not is_bool)
        if is_bool:
            self.txt_threshold.clear()

    def _update_preview(self) -> None:
        op = self.cmb_op.currentText()
        src = self._do_alias
        if op == "TRUE":
            self.lbl_preview.setText("Always HIGH")
        elif op == "FALSE":
            self.lbl_preview.setText("Always LOW")
        else:
            thr = self.txt_threshold.text().strip()
            if src and thr:
                self.lbl_preview.setText(f"{src} {op} {thr}")
            else:
                self.lbl_preview.setText("(incomplete)")

    def _poll_telemetry(self) -> None:
        if self._telemetry_getter is None:
            return
        try:
            vals = self._telemetry_getter()
            if not isinstance(vals, dict):
                return
            if self._do_alias and self._do_alias in vals:
                v = vals[self._do_alias]
                self.lbl_live_val.setText(f"{float(v):.4f}" if isinstance(v, (int, float)) else str(v))

                state = bool(int(v))
                label = "HIGH" if state else "LOW"
                color = "#27ae60" if state else "#c0392b"
                self.lbl_do_state.setText(
                    f"Current State: <span style='color:{color};font-weight:bold;'>{label}</span>"
                )
            else:
                self.lbl_live_val.setText("—")
                self.lbl_do_state.setText("Current State: —")
        except Exception:
            pass

    def _force_output(self, high: bool) -> None:
        self._forced_state = high
        self.btn_release.setEnabled(True)
        state = 1 if high else 0
        if self._control_sender and self._do_alias:
            try:
                msg = json.dumps({"type": "do_write", "alias": self._do_alias, "state": state}).encode("utf-8")
                self._control_sender.send(msg)
            except Exception:
                pass

    def _release_output(self) -> None:
        self._forced_state = None
        self.btn_release.setEnabled(False)

    def _clear_condition(self) -> None:
        self.cmb_op.setCurrentIndex(0)
        self.txt_threshold.clear()

    def _on_accept(self) -> None:
        op = self.cmb_op.currentText().strip()
        source = self._do_alias

        if op == "TRUE":
            self.result_condition = {"source": source, "operator": "TRUE", "threshold": 0.0}
            self.accept()
            return
        if op == "FALSE":
            self.result_condition = {"source": source, "operator": "FALSE", "threshold": 0.0}
            self.accept()
            return

        threshold_text = self.txt_threshold.text().strip()
        if not threshold_text:
            self.result_condition = None
            self.accept()
            return

        try:
            threshold = float(threshold_text)
        except ValueError:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Invalid Threshold", "Threshold must be a number.")
            return

        self.result_condition = {"source": source, "operator": op, "threshold": threshold}
        self.accept()
