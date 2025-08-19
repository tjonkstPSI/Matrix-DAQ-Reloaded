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
		self._fields: Dict[str, QLineEdit] = {}
		self._comments: QTextEdit | None = None
		self._init_ui()

	def _init_ui(self) -> None:
		root = QVBoxLayout(self)
		form = QFormLayout()
		# Create required field inputs (keys mirror engine_test.yaml)
		for key, label in [
			("engine_type", "Engine Type"),
			("engine_serial_number", "Engine Serial Number"),
			("test_type", "Test Type"),
			("test_operator", "Test Operator"),
			("project_number", "Project Number"),
		]:
			le = QLineEdit(self)
			le.setObjectName(key)
			self._fields[key] = le
			form.addRow(label + ":", le)
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
		missing = [k for k, w in self._fields.items() if not w.text().strip()]
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
				req[k] = w.text().strip()
			data["required_fields"] = req
			if self._comments is not None:
				data["pre_test_comments"] = self._comments.toPlainText()
			# Preserve other keys; write file
			self._cfg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
		except Exception as e:
			QMessageBox.critical(self, "Write Error", f"Failed to save engine_test.yaml: {e}")
			return
		self.accept()


