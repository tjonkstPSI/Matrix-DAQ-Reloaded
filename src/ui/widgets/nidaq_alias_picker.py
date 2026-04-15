# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Dict

try:
	from PySide6.QtCore import Qt
	from PySide6.QtWidgets import (
		QDialog,
		QVBoxLayout,
		QHBoxLayout,
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

ALIAS_PATTERN = re.compile(
	r"(?:^[qcemixypvl](?:TP|PR|FL|VL|CT|PC|SP|FQ|DG|AC|DS|PW|MS|TM|TQ|PO|OT|DE|CN|HM|LA|PI|AF|VO|VS|DN)"
	r"_(?:Amb|Eng|Rad|Cac|Dyn|Cmp|Trb|Olc|Pmp|Pto|Thr|Ccs|Cat|Man|Mix|Vap|Reg|Blk|Hed|Ral|Xvr|Col|Alt"
	r"|Bat|Ign|Fan|Gen|Ldb|Bth|Epr|Ecm|Twg|Fac|Enc|Mfg|Tst|Loc|Vlv|Cyl|Fnt|Rer|Mst|Slv|Rgt|Lft|Clt|Ful"
	r"|Oil|Sld|Exh|Int|Gly|Ftr|Pan|Pdl|Spk|Trm|Air|Dew|Wet|Nag|Lpg|Phs|Cpl|Mil|Dtc|Shm|Lod|Hyd|Trn|Esp"
	r"|Emg|Std|Ssd|Flg|Fst|Bst|Pre|Pst|In|Out|Bby|Mid|Sfc|Dta|Stp|Act|Lng|Sht|Top|Bot|Nox|Oxy|Dpt|Vld"
	r"|Iso|Sae|Wat|Abs|Cnt|Cst|Gag|Avg|Roa|Ror|Lmt|[0-9]+)*$"
	r"|^[eiyx].+$)"
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def validate_alias(alias: str) -> bool:
	return bool(ALIAS_PATTERN.match(alias))


class AliasPickerDialog(QDialog):

	def __init__(
		self,
		parent=None,
		current_alias: str = "",
		library_path: Path | None = None,
	) -> None:
		super().__init__(parent)
		self.setWindowTitle("Select Channel Alias")
		self.resize(500, 500)

		self.selected_alias: str = ""
		self._current_alias = current_alias
		self._library_path = library_path or (_PROJECT_ROOT / "configs" / "alias_library.yaml")
		self._aliases: List[Dict[str, str]] = []

		self._load_library()
		self._init_ui()

	def _load_library(self) -> None:
		try:
			import yaml  # type: ignore
			text = self._library_path.read_text(encoding="utf-8")
			data = yaml.safe_load(text) or {}
			self._aliases = data.get("aliases", [])
		except Exception:
			self._aliases = []

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
		self._lib_table.setHorizontalHeaderLabels(["Alias", "Description"])
		self._lib_table.horizontalHeader().setStretchLastSection(True)
		self._lib_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
		self._lib_table.setSelectionBehavior(QAbstractItemView.SelectRows)
		self._lib_table.setSelectionMode(QAbstractItemView.SingleSelection)
		self._lib_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
		self._lib_table.verticalHeader().setVisible(False)
		self._lib_table.doubleClicked.connect(self._on_library_double_click)  # type: ignore
		self._lib_table.itemSelectionChanged.connect(self._on_library_selection_changed)  # type: ignore
		vbox.addWidget(self._lib_table)

		self._populate_library_table(self._aliases)
		self._tabs.addTab(tab, "Library")

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
			desc_item = QTableWidgetItem(entry.get("description", ""))
			alias_item.setFlags(alias_item.flags() & ~Qt.ItemIsEditable)
			desc_item.setFlags(desc_item.flags() & ~Qt.ItemIsEditable)
			self._lib_table.setItem(row, 0, alias_item)
			self._lib_table.setItem(row, 1, desc_item)

	def _filter_library(self, text: str) -> None:
		needle = text.strip().lower()
		if not needle:
			self._populate_library_table(self._aliases)
		else:
			filtered = [
				e for e in self._aliases
				if needle in e.get("alias", "").lower()
				or needle in e.get("description", "").lower()
			]
			self._populate_library_table(filtered)
		self._update_ok_state()

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
