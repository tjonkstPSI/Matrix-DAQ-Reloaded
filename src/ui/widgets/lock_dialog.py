# Author: T. Onkst | Date: 08192025

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any

try:
	from PySide6.QtWidgets import (
		QDialog,
		QVBoxLayout,
		QFormLayout,
		QLineEdit,
		QComboBox,
		QTextEdit,
		QDialogButtonBox,
		QMessageBox,
	)
except Exception:
	raise


class LockDialog(QDialog):
	"""Dialog to collect EngineTest required fields and Pre Test Comments.

	Reads and writes `configs/engine_test.yaml`.
	"""

	def __init__(self, parent=None) -> None:
		super().__init__(parent)
		self.setWindowTitle("Lock Test — EngineTest Metadata")
		self._cfg_path = Path(__file__).resolve().parents[3] / "configs" / "engine_test.yaml"
		self._fields: Dict[str, Any] = {}
		self._comments: QTextEdit | None = None
		self._init_ui()

	def _init_ui(self) -> None:
		root = QVBoxLayout(self)
		form = QFormLayout()
		# Create required field inputs (keys mirror engine_test.yaml)
		# Engine Type (dropdown)
		engine_types = [
			"", "0.97L", "0.998L", "2L", "2.4L", "3L", "4X", "5.7L", "6L", "8.8L",
			"11L", "14L", "17L", "20L", "22L", "32L", "40L", "53L", "65L", "88L", "110L",
		]
		cb_engine = QComboBox(self)
		cb_engine.addItems(engine_types)
		cb_engine.setObjectName("engine_type")
		self._fields["engine_type"] = cb_engine
		form.addRow("Engine Type:", cb_engine)
		# Engine Serial Number (text)
		le_esn = QLineEdit(self); le_esn.setObjectName("engine_serial_number")
		self._fields["engine_serial_number"] = le_esn
		form.addRow("Engine Serial Number:", le_esn)
		# Test Type (dropdown)
		test_types = [
			"",
			"Air-To-Boil Testing",
			"BSFC Mapping",
			"Camshaft Testing",
			"Engine Health Check",
			"Engine Map",
			"Engine Start Testing",
			"Heat Rejection",
			"Load Step Testing",
			"Other Testing",
			"Spark Sweep",
			"Standard Break-In",
			"Steady State Full Load",
			"Torque Curve",
			"Vibration Testing",
		]
		cb_test = QComboBox(self)
		cb_test.addItems(test_types)
		cb_test.setObjectName("test_type")
		self._fields["test_type"] = cb_test
		form.addRow("Test Type:", cb_test)
		# Test Operator (text)
		le_op = QLineEdit(self); le_op.setObjectName("test_operator")
		self._fields["test_operator"] = le_op
		form.addRow("Test Operator:", le_op)
		# Project Number (text)
		le_proj = QLineEdit(self); le_proj.setObjectName("project_number")
		self._fields["project_number"] = le_proj
		form.addRow("Project Number:", le_proj)
		root.addLayout(form)
		# Pre Test Comments (multi-line)
		self._comments = QTextEdit(self)
		self._comments.setPlaceholderText("Pre Test Comments…")
		self._comments.setFixedHeight(120)
		root.addWidget(self._comments)
		# Buttons
		btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
		btns.accepted.connect(self._on_accept)  # type: ignore
		btns.rejected.connect(self.reject)  # type: ignore
		root.addWidget(btns)

	# Intentionally do not preload values from existing config; fields start empty each time

	def _on_accept(self) -> None:
		# Validate required fields are non-empty
		missing = []
		for k, w in self._fields.items():
			val = ""
			try:
				# QComboBox has currentText, QLineEdit has text
				if hasattr(w, "currentText"):
					val = str(w.currentText()).strip()
				elif hasattr(w, "text"):
					val = str(w.text()).strip()
			except Exception:
				val = ""
			if not val:
				missing.append(k)
		if missing:
			QMessageBox.warning(self, "Missing Fields", f"Please fill: {', '.join(missing)}")
			return
		# Write back to YAML
		try:
			import yaml  # type: ignore
			data: Dict[str, Any]
			try:
				data = yaml.safe_load(self._cfg_path.read_text(encoding="utf-8")) or {}
			except Exception:
				data = {}
			req = dict(data.get("required_fields") or {})
			for k, w in self._fields.items():
				try:
					val = str(w.currentText()).strip() if hasattr(w, "currentText") else str(w.text()).strip()
				except Exception:
					val = ""
				req[k] = val
			data["required_fields"] = req
			if self._comments is not None:
				data["pre_test_comments"] = self._comments.toPlainText()
			# Preserve other keys; write file
			self._cfg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
		except Exception as e:
			QMessageBox.critical(self, "Write Error", f"Failed to save engine_test.yaml: {e}")
			return
		self.accept()


