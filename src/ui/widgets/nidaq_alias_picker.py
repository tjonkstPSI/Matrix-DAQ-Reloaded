# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import os
import time
from typing import Any, List, Dict

try:
	from PySide6.QtCore import Qt
	from PySide6.QtWidgets import (
		QDialog,
		QVBoxLayout,
		QTabWidget,
		QWidget,
		QLineEdit,
		QLabel,
		QTableWidget,
		QTableWidgetItem,
		QHeaderView,
		QAbstractItemView,
		QDialogButtonBox,
	)
except Exception:
	raise

from .standard_channels import ALIAS_PATTERN, validate_alias, load_standard_channels  # noqa: F401


class AliasPickerDialog(QDialog):

	def __init__(
		self,
		parent=None,
		current_alias: str = "",
		**_kwargs,
	) -> None:
		super().__init__(parent)
		self.setWindowTitle("Select Channel Alias")
		self.resize(500, 500)

		self.selected_alias: str = ""
		self._current_alias = current_alias
		self._channels: List[Dict[str, str]] = []
		self._perf_diag_enabled = str(os.environ.get("MATRIX_UI_PERF_DIAG", "")).strip().lower() in {
			"1",
			"true",
			"yes",
			"on",
		}
		self._perf_diag: Dict[str, Any] = {"start": time.perf_counter(), "count": 0, "samples": []}

		self._load_channels()
		self._init_ui()

	def _record_perf_diag(
		self,
		*,
		elapsed_ms: float,
		populate_ms: float,
		match_count: int,
		total_count: int,
		query_len: int,
	) -> None:
		if not self._perf_diag_enabled:
			return
		try:
			self._perf_diag["count"] = int(self._perf_diag.get("count", 0)) + 1
			samples = self._perf_diag.setdefault("samples", [])
			if isinstance(samples, list):
				samples.append(
					{
						"elapsed_ms": float(elapsed_ms),
						"populate_ms": float(populate_ms),
						"match_count": float(match_count),
						"total_count": float(total_count),
						"query_len": float(query_len),
					}
				)
			now = time.perf_counter()
			start = float(self._perf_diag.get("start", now))
			if now - start < 5.0:
				return

			def _avg(key: str) -> float:
				return sum(float(s.get(key, 0.0)) for s in samples) / float(len(samples)) if samples else 0.0

			def _max(key: str) -> float:
				return max((float(s.get(key, 0.0)) for s in samples), default=0.0)

			count = int(self._perf_diag.get("count", 0))
			print(
				"[UI_PERF] alias_picker "
				f"count={count} rate={count / max(0.001, now - start):.1f}/s "
				f"elapsed_ms_avg={_avg('elapsed_ms'):.2f} elapsed_ms_max={_max('elapsed_ms'):.2f} "
				f"populate_ms_avg={_avg('populate_ms'):.2f} populate_ms_max={_max('populate_ms'):.2f} "
				f"match_count_max={_max('match_count'):.0f} total_count_max={_max('total_count'):.0f} "
				f"query_len_max={_max('query_len'):.0f}",
				flush=True,
			)
			self._perf_diag = {"start": now, "count": 0, "samples": []}
		except Exception:
			pass

	def _load_channels(self) -> None:
		self._channels = load_standard_channels()

	def _init_ui(self) -> None:
		layout = QVBoxLayout(self)

		self._button_box = QDialogButtonBox(
			QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
			parent=self,
		)
		self._ok_btn = self._button_box.button(QDialogButtonBox.Ok)
		self._ok_btn.setEnabled(False)
		self._button_box.accepted.connect(self._on_accept)  # type: ignore
		self._button_box.rejected.connect(self.reject)  # type: ignore

		self._tabs = QTabWidget()
		layout.addWidget(self._tabs)

		self._build_library_tab()
		self._build_custom_tab()

		layout.addWidget(self._button_box)

		self._tabs.currentChanged.connect(self._on_tab_changed)  # type: ignore

	def _build_library_tab(self) -> None:
		tab = QWidget()
		vbox = QVBoxLayout(tab)

		self._lib_search = QLineEdit()
		self._lib_search.setPlaceholderText("Search aliases...")
		self._lib_search.textChanged.connect(self._filter_library)  # type: ignore
		vbox.addWidget(self._lib_search)

		self._lib_table = QTableWidget(0, 2)
		self._lib_table.setHorizontalHeaderLabels(["Alias", "Unit"])
		self._lib_table.horizontalHeader().setStretchLastSection(True)
		self._lib_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
		self._lib_table.setSelectionBehavior(QAbstractItemView.SelectRows)
		self._lib_table.setSelectionMode(QAbstractItemView.SingleSelection)
		self._lib_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
		self._lib_table.verticalHeader().setVisible(False)
		self._lib_table.doubleClicked.connect(self._on_library_double_click)  # type: ignore
		self._lib_table.itemSelectionChanged.connect(self._on_library_selection_changed)  # type: ignore
		vbox.addWidget(self._lib_table)

		self._populate_library_table(self._channels)
		self._tabs.addTab(tab, "Standard Channels")

	def _build_custom_tab(self) -> None:
		tab = QWidget()
		vbox = QVBoxLayout(tab)

		self._custom_edit = QLineEdit()
		self._custom_edit.setPlaceholderText("Enter custom alias...")
		if self._current_alias:
			self._custom_edit.setText(self._current_alias)
		self._custom_edit.textChanged.connect(self._on_custom_text_changed)  # type: ignore
		vbox.addWidget(self._custom_edit)

		self._valid_label = QLabel("")
		vbox.addWidget(self._valid_label)
		vbox.addStretch()

		self._tabs.addTab(tab, "Custom")

		if self._current_alias:
			self._on_custom_text_changed(self._current_alias)

	def _populate_library_table(self, entries: List[Dict[str, str]]) -> None:
		self._lib_table.setRowCount(len(entries))
		for row, entry in enumerate(entries):
			alias_item = QTableWidgetItem(entry.get("alias", ""))
			unit_item = QTableWidgetItem(entry.get("unit", ""))
			alias_item.setFlags(alias_item.flags() & ~Qt.ItemIsEditable)
			unit_item.setFlags(unit_item.flags() & ~Qt.ItemIsEditable)
			self._lib_table.setItem(row, 0, alias_item)
			self._lib_table.setItem(row, 1, unit_item)

	def _filter_library(self, text: str) -> None:
		diag_enabled = self._perf_diag_enabled
		diag_start = time.perf_counter() if diag_enabled else 0.0
		populate_ms = 0.0
		needle = text.strip().lower()
		if not needle:
			entries = self._channels
		else:
			entries = [
				e for e in self._channels
				if needle in e.get("alias", "").lower()
				or needle in e.get("unit", "").lower()
			]
		populate_start = time.perf_counter() if diag_enabled else 0.0
		self._populate_library_table(entries)
		if diag_enabled:
			populate_ms = (time.perf_counter() - populate_start) * 1000.0
		self._update_ok_state()
		if diag_enabled:
			self._record_perf_diag(
				elapsed_ms=(time.perf_counter() - diag_start) * 1000.0,
				populate_ms=populate_ms,
				match_count=len(entries),
				total_count=len(self._channels),
				query_len=len(needle),
			)

	def _on_library_selection_changed(self) -> None:
		self._update_ok_state()

	def _on_library_double_click(self) -> None:
		rows = self._lib_table.selectionModel().selectedRows()
		if rows:
			item = self._lib_table.item(rows[0].row(), 0)
			if item:
				self.selected_alias = item.text()
				self.accept()

	def _on_custom_text_changed(self, text: str) -> None:
		alias = text.strip()
		if not alias:
			self._valid_label.setText("")
			self._valid_label.setStyleSheet("")
		elif validate_alias(alias):
			self._valid_label.setText("Valid")
			self._valid_label.setStyleSheet("color: green;")
		else:
			self._valid_label.setText("Invalid - must match naming convention")
			self._valid_label.setStyleSheet("color: red;")
		self._update_ok_state()

	def _on_tab_changed(self, _index: int) -> None:
		self._update_ok_state()

	def _update_ok_state(self) -> None:
		if self._tabs.currentIndex() == 0:
			has_selection = bool(self._lib_table.selectionModel().selectedRows())
			self._ok_btn.setEnabled(has_selection)
		else:
			alias = self._custom_edit.text().strip()
			self._ok_btn.setEnabled(bool(alias) and validate_alias(alias))

	def _on_accept(self) -> None:
		if self._tabs.currentIndex() == 0:
			rows = self._lib_table.selectionModel().selectedRows()
			if rows:
				item = self._lib_table.item(rows[0].row(), 0)
				if item:
					self.selected_alias = item.text()
		else:
			self.selected_alias = self._custom_edit.text().strip()
		self.accept()
