# Author: T. Onkst | Date: 03092026

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
	from PySide6.QtCore import Qt, QTimer
	from PySide6.QtWidgets import (
		QDialog,
		QVBoxLayout,
		QHBoxLayout,
		QFormLayout,
		QGroupBox,
		QRadioButton,
		QCheckBox,
		QDoubleSpinBox,
		QLineEdit,
		QLabel,
		QPushButton,
		QTableWidget,
		QTableWidgetItem,
		QHeaderView,
		QDialogButtonBox,
		QMessageBox,
		QListWidget,
		QListWidgetItem,
		QWidget,
	)
except Exception:
	raise


class ScalingEditorDialog(QDialog):

	def __init__(
		self,
		parent=None,
		current_scaling: dict | None = None,
		channel_alias: str = "",
		library_path: Path | None = None,
		telemetry_getter: Callable[[], Dict[str, Any]] | None = None,
	) -> None:
		super().__init__(parent)
		self._channel_alias = channel_alias
		self._telemetry_getter = telemetry_getter
		self._library_path = library_path or (
			Path(__file__).resolve().parents[3] / "configs" / "scale_library.yaml"
		)
		self.result_scaling: dict = {}
		self.setWindowTitle(f"Channel Scaling - {channel_alias}")
		self.resize(600, 550)
		self._init_ui()
		self._load_current(current_scaling)
		self._start_preview_timer()

	# ── UI construction ──────────────────────────────────────────────

	def _init_ui(self) -> None:
		layout = QVBoxLayout(self)

		radio_row = QHBoxLayout()
		self._rb_none = QRadioButton("No Scale")
		self._rb_linear = QRadioButton("Linear")
		self._rb_table = QRadioButton("Table")
		radio_row.addWidget(self._rb_none)
		radio_row.addWidget(self._rb_linear)
		radio_row.addWidget(self._rb_table)
		layout.addLayout(radio_row)

		self._grp_none = self._build_none_group()
		layout.addWidget(self._grp_none)

		self._grp_linear = self._build_linear_group()
		layout.addWidget(self._grp_linear)

		self._grp_table = self._build_table_group()
		layout.addWidget(self._grp_table)

		self._btn_import = QPushButton("Import from Library...")
		layout.addWidget(self._btn_import)

		layout.addStretch()

		bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
		layout.addWidget(bbox)

		self._rb_none.toggled.connect(self._on_type_changed)
		self._rb_linear.toggled.connect(self._on_type_changed)
		self._rb_table.toggled.connect(self._on_type_changed)
		self._lin_gain.valueChanged.connect(self._update_formula)
		self._lin_offset.valueChanged.connect(self._update_formula)
		self._btn_add_row.clicked.connect(self._add_table_row)
		self._btn_rm_row.clicked.connect(self._rm_table_row)
		self._btn_import.clicked.connect(self._import_from_library)
		bbox.accepted.connect(self._on_accept)
		bbox.rejected.connect(self.reject)

		self._rb_none.setChecked(True)
		self._on_type_changed()

	def _build_none_group(self) -> QWidget:
		w = QWidget()
		form = QFormLayout(w)
		form.setContentsMargins(0, 0, 0, 0)
		self._none_unit = QLineEdit("V")
		form.addRow("Unit:", self._none_unit)
		return w

	def _build_linear_group(self) -> QGroupBox:
		grp = QGroupBox("Linear Scaling")
		v = QVBoxLayout(grp)

		form = QFormLayout()
		self._lin_gain = QDoubleSpinBox()
		self._lin_gain.setRange(-1e6, 1e6)
		self._lin_gain.setDecimals(6)
		self._lin_gain.setValue(1.0)
		self._lin_offset = QDoubleSpinBox()
		self._lin_offset.setRange(-1e6, 1e6)
		self._lin_offset.setDecimals(6)
		self._lin_offset.setValue(0.0)
		self._lin_unit = QLineEdit("V")
		form.addRow("Gain:", self._lin_gain)
		form.addRow("Offset:", self._lin_offset)
		form.addRow("Unit:", self._lin_unit)
		v.addLayout(form)

		self._lin_formula = QLabel()
		self._update_formula()
		v.addWidget(self._lin_formula)

		preview = QHBoxLayout()
		self._lin_raw_lbl = QLabel("Raw: N/A")
		self._lin_scaled_lbl = QLabel("Scaled: N/A")
		preview.addWidget(self._lin_raw_lbl)
		preview.addWidget(self._lin_scaled_lbl)
		v.addLayout(preview)

		return grp

	def _build_table_group(self) -> QGroupBox:
		grp = QGroupBox("Table Scaling")
		v = QVBoxLayout(grp)

		self._tbl_table = QTableWidget(2, 2)
		self._tbl_table.setHorizontalHeaderLabels(["Raw (V)", "Scaled"])
		self._tbl_table.horizontalHeader().setStretchLastSection(True)
		for r in range(2):
			for c in range(2):
				self._tbl_table.setItem(r, c, QTableWidgetItem("0"))
		v.addWidget(self._tbl_table)

		btn_row = QHBoxLayout()
		self._btn_add_row = QPushButton("Add Row")
		self._btn_rm_row = QPushButton("Remove Row")
		self._btn_rm_row.setEnabled(False)
		btn_row.addWidget(self._btn_add_row)
		btn_row.addWidget(self._btn_rm_row)
		v.addLayout(btn_row)

		self._chk_extrapolate = QCheckBox("Extrapolate beyond table range")
		self._chk_extrapolate.setToolTip(
			"When enabled, values outside the table min/max are linearly extrapolated "
			"from the nearest two points instead of being clamped."
		)
		v.addWidget(self._chk_extrapolate)

		form = QFormLayout()
		self._tbl_unit = QLineEdit("V")
		form.addRow("Unit:", self._tbl_unit)
		v.addLayout(form)

		preview = QHBoxLayout()
		self._tbl_raw_lbl = QLabel("Raw: N/A")
		self._tbl_scaled_lbl = QLabel("Scaled: N/A")
		preview.addWidget(self._tbl_raw_lbl)
		preview.addWidget(self._tbl_scaled_lbl)
		v.addLayout(preview)

		return grp

	# ── Type switching ───────────────────────────────────────────────

	def _on_type_changed(self) -> None:
		self._grp_none.setVisible(self._rb_none.isChecked())
		self._grp_linear.setVisible(self._rb_linear.isChecked())
		self._grp_table.setVisible(self._rb_table.isChecked())

	# ── Formula label ────────────────────────────────────────────────

	def _update_formula(self) -> None:
		g = self._lin_gain.value()
		o = self._lin_offset.value()
		sign = "+" if o >= 0 else "-"
		self._lin_formula.setText(
			f"scaled = raw \u00d7 {g:.6f} {sign} {abs(o):.6f}"
		)

	# ── Load existing scaling dict ───────────────────────────────────

	def _load_current(self, scaling: dict | None) -> None:
		if not scaling:
			return
		stype = scaling.get("type", "none")
		if stype == "linear":
			self._rb_linear.setChecked(True)
			self._lin_gain.setValue(scaling.get("gain", 1.0))
			self._lin_offset.setValue(scaling.get("offset", 0.0))
			self._lin_unit.setText(scaling.get("unit", "V"))
			self._update_formula()
		elif stype == "table":
			self._rb_table.setChecked(True)
			self._tbl_unit.setText(scaling.get("unit", "V"))
			self._chk_extrapolate.setChecked(bool(scaling.get("extrapolate", False)))
			points = scaling.get("points", [])
			self._tbl_table.setRowCount(max(len(points), 2))
			for i, pt in enumerate(points):
				self._tbl_table.setItem(i, 0, QTableWidgetItem(str(pt[0])))
				self._tbl_table.setItem(i, 1, QTableWidgetItem(str(pt[1])))
			self._btn_rm_row.setEnabled(self._tbl_table.rowCount() > 2)
		else:
			self._rb_none.setChecked(True)
			self._none_unit.setText(scaling.get("unit", "V"))
		self._on_type_changed()

	# ── Table row management ─────────────────────────────────────────

	def _add_table_row(self) -> None:
		r = self._tbl_table.rowCount()
		self._tbl_table.insertRow(r)
		self._tbl_table.setItem(r, 0, QTableWidgetItem("0"))
		self._tbl_table.setItem(r, 1, QTableWidgetItem("0"))
		self._btn_rm_row.setEnabled(True)

	def _rm_table_row(self) -> None:
		if self._tbl_table.rowCount() <= 2:
			return
		row = self._tbl_table.currentRow()
		if row < 0:
			row = self._tbl_table.rowCount() - 1
		self._tbl_table.removeRow(row)
		self._btn_rm_row.setEnabled(self._tbl_table.rowCount() > 2)

	# ── Live telemetry preview ───────────────────────────────────────

	def _start_preview_timer(self) -> None:
		self._preview_timer = QTimer(self)
		self._preview_timer.setInterval(200)
		self._preview_timer.timeout.connect(self._update_preview)
		self._preview_timer.start()

	def _update_preview(self) -> None:
		raw_value: float | None = None
		if self._telemetry_getter is not None:
			try:
				data = self._telemetry_getter()
				if isinstance(data, dict) and self._channel_alias in data:
					raw_value = float(data[self._channel_alias])
			except Exception:
				pass

		if raw_value is None:
			self._lin_raw_lbl.setText("Raw: N/A")
			self._lin_scaled_lbl.setText("Scaled: N/A")
			self._tbl_raw_lbl.setText("Raw: N/A")
			self._tbl_scaled_lbl.setText("Scaled: N/A")
			return

		self._lin_raw_lbl.setText(f"Raw: {raw_value:.3f} V")
		self._tbl_raw_lbl.setText(f"Raw: {raw_value:.3f} V")

		if self._rb_linear.isChecked():
			g = self._lin_gain.value()
			o = self._lin_offset.value()
			u = self._lin_unit.text()
			scaled = raw_value * g + o
			self._lin_scaled_lbl.setText(f"Scaled: {scaled:.3f} {u}")

		if self._rb_table.isChecked():
			scaled = self._interpolate_table(raw_value)
			u = self._tbl_unit.text()
			if scaled is not None:
				self._tbl_scaled_lbl.setText(f"Scaled: {scaled:.3f} {u}")
			else:
				self._tbl_scaled_lbl.setText("Scaled: N/A")

	def _interpolate_table(self, raw: float) -> float | None:
		points = self._read_table_points()
		if len(points) < 2:
			return None
		points.sort(key=lambda p: p[0])
		extrap = self._chk_extrapolate.isChecked()
		if raw <= points[0][0]:
			if extrap and len(points) >= 2:
				x0, y0 = points[0]
				x1, y1 = points[1]
				if x1 != x0:
					return y0 + (raw - x0) * (y1 - y0) / (x1 - x0)
			return points[0][1]
		if raw >= points[-1][0]:
			if extrap and len(points) >= 2:
				x0, y0 = points[-2]
				x1, y1 = points[-1]
				if x1 != x0:
					return y1 + (raw - x1) * (y1 - y0) / (x1 - x0)
			return points[-1][1]
		for i in range(len(points) - 1):
			x0, y0 = points[i]
			x1, y1 = points[i + 1]
			if x0 <= raw <= x1:
				if x1 == x0:
					return y0
				t = (raw - x0) / (x1 - x0)
				return y0 + t * (y1 - y0)
		return None

	def _read_table_points(self) -> List[List[float]]:
		points: List[List[float]] = []
		for r in range(self._tbl_table.rowCount()):
			try:
				x = float(self._tbl_table.item(r, 0).text())
				y = float(self._tbl_table.item(r, 1).text())
				points.append([x, y])
			except (ValueError, AttributeError):
				continue
		return points

	# ── Library import ───────────────────────────────────────────────

	def _import_from_library(self) -> None:
		try:
			import yaml  # type: ignore
			with open(self._library_path, "r", encoding="utf-8") as f:
				lib = yaml.safe_load(f)
		except Exception as e:
			QMessageBox.warning(self, "Library Error", f"Could not load scale library:\n{e}")
			return

		scales = lib.get("scales", []) if isinstance(lib, dict) else []
		if not scales:
			QMessageBox.information(self, "Library", "No scales found in library.")
			return

		dlg = _LibraryPickerDialog(scales, self)
		if dlg.exec() != QDialog.Accepted or dlg.selected is None:
			return

		entry = dlg.selected
		stype = entry.get("type", "none")
		if stype == "linear":
			self._rb_linear.setChecked(True)
			self._lin_gain.setValue(entry.get("gain", 1.0))
			self._lin_offset.setValue(entry.get("offset", 0.0))
			self._lin_unit.setText(entry.get("unit", "V"))
			self._update_formula()
		elif stype == "table":
			self._rb_table.setChecked(True)
			self._tbl_unit.setText(entry.get("unit", "V"))
			self._chk_extrapolate.setChecked(bool(entry.get("extrapolate", False)))
			pts = entry.get("points", [])
			self._tbl_table.setRowCount(max(len(pts), 2))
			for i, pt in enumerate(pts):
				self._tbl_table.setItem(i, 0, QTableWidgetItem(str(pt[0])))
				self._tbl_table.setItem(i, 1, QTableWidgetItem(str(pt[1])))
			self._btn_rm_row.setEnabled(self._tbl_table.rowCount() > 2)
		else:
			self._rb_none.setChecked(True)
			self._none_unit.setText(entry.get("unit", "V"))
		self._on_type_changed()

	# ── Accept / validation ──────────────────────────────────────────

	def _on_accept(self) -> None:
		if self._rb_none.isChecked():
			self.result_scaling = {
				"type": "none",
				"unit": self._none_unit.text(),
			}
		elif self._rb_linear.isChecked():
			self.result_scaling = {
				"type": "linear",
				"gain": self._lin_gain.value(),
				"offset": self._lin_offset.value(),
				"unit": self._lin_unit.text(),
			}
		elif self._rb_table.isChecked():
			points = self._read_table_points()
			if len(points) < 2:
				QMessageBox.warning(
					self,
					"Validation Error",
					"Table mode requires at least 2 rows with valid numbers.",
				)
				return
			self.result_scaling = {
				"type": "table",
				"unit": self._tbl_unit.text(),
				"points": points,
				"extrapolate": self._chk_extrapolate.isChecked(),
			}
		self.accept()


# ── Library picker sub-dialog ────────────────────────────────────────

class _LibraryPickerDialog(QDialog):

	def __init__(self, scales: List[dict], parent=None) -> None:
		super().__init__(parent)
		self.setWindowTitle("Import Scale from Library")
		self.resize(400, 350)
		self.selected: dict | None = None
		self._scales = scales

		layout = QVBoxLayout(self)
		self._list = QListWidget()
		for s in scales:
			self._list.addItem(s.get("name", "Unnamed"))
		layout.addWidget(self._list)

		bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
		layout.addWidget(bbox)
		bbox.accepted.connect(self._on_accept)
		bbox.rejected.connect(self.reject)
		self._list.doubleClicked.connect(self._on_accept)

	def _on_accept(self) -> None:
		row = self._list.currentRow()
		if row >= 0:
			self.selected = self._scales[row]
		self.accept()


# ── Temperature unit picker ──────────────────────────────────────────

class TempUnitPickerDialog(QDialog):

	def __init__(self, parent=None, current_unit: str = "C") -> None:
		super().__init__(parent)
		self.setWindowTitle("Temperature Unit")
		self.resize(300, 150)
		self.selected_unit: str = current_unit

		layout = QVBoxLayout(self)
		self._rb_c = QRadioButton("Celsius (C)")
		self._rb_f = QRadioButton("Fahrenheit (F)")
		self._rb_k = QRadioButton("Kelvin (K)")
		layout.addWidget(self._rb_c)
		layout.addWidget(self._rb_f)
		layout.addWidget(self._rb_k)

		if current_unit == "F":
			self._rb_f.setChecked(True)
		elif current_unit == "K":
			self._rb_k.setChecked(True)
		else:
			self._rb_c.setChecked(True)

		bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
		layout.addWidget(bbox)
		bbox.accepted.connect(self._on_accept)
		bbox.rejected.connect(self.reject)

	def _on_accept(self) -> None:
		if self._rb_f.isChecked():
			self.selected_unit = "F"
		elif self._rb_k.isChecked():
			self.selected_unit = "K"
		else:
			self.selected_unit = "C"
		self.accept()
