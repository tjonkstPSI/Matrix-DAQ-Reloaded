# Author: T. Onkst | Date: 08182025

from __future__ import annotations

from typing import Dict, Any, List

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView
except Exception:
    raise


class ChannelsTable(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._aliases: List[str] = []
        self._init_ui()

    def _init_ui(self) -> None:
        v = QVBoxLayout(self)
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["Alias", "Value", "Unit"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        v.addWidget(self.table)
        self._columns_fixed = False
        self._max_col_px = [0, 0, 0]

    def update_data(self, values: Dict[str, Any] | None, units: Dict[str, Any] | None) -> None:
        if not isinstance(values, dict):
            return
        units = units if isinstance(units, dict) else {}
        aliases = sorted(values.keys())
        if aliases != self._aliases:
            # Rebuild table rows
            self._aliases = aliases
            self.table.setRowCount(len(aliases))
            for row, alias in enumerate(aliases):
                self.table.setItem(row, 0, QTableWidgetItem(str(alias)))
                self.table.setItem(row, 1, QTableWidgetItem(""))
                self.table.setItem(row, 2, QTableWidgetItem(str(units.get(alias, ""))))
            # After initial population, autosize and then fix widths
            self.table.resizeColumnsToContents()
            if not self._columns_fixed:
                for col in range(3):
                    w = self.table.columnWidth(col)
                    self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.Fixed)
                    self.table.setColumnWidth(col, w)
                    self._max_col_px[col] = max(self._max_col_px[col], w)
                self._columns_fixed = True
        # Update values and units
        for row, alias in enumerate(self._aliases):
            try:
                val = values.get(alias)
                if isinstance(val, (int, float)):
                    try:
                        fval = float(val)
                        text = f"{fval:.2f}"
                    except Exception:
                        text = str(val)
                else:
                    text = str(val)
            except Exception:
                text = ""
            self.table.item(row, 1).setText(text)
            try:
                u = units.get(alias, "")
                self.table.item(row, 2).setText(str(u))
            except Exception:
                pass
            # Dynamically grow column widths if content is wider than current
            try:
                fm = self.table.fontMetrics()
                padding = 24
                # Alias
                w0 = fm.horizontalAdvance(str(alias)) + padding
                if w0 > self._max_col_px[0]:
                    self._max_col_px[0] = w0
                    self.table.setColumnWidth(0, w0)
                # Value
                w1 = fm.horizontalAdvance(text) + padding
                if w1 > self._max_col_px[1]:
                    self._max_col_px[1] = w1
                    self.table.setColumnWidth(1, w1)
                # Unit
                w2 = fm.horizontalAdvance(str(units.get(alias, ""))) + padding
                if w2 > self._max_col_px[2]:
                    self._max_col_px[2] = w2
                    self.table.setColumnWidth(2, w2)
            except Exception:
                pass


