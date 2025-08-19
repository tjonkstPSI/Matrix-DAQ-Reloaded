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
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
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
            # After initial population, set fixed width for Value column (10 digits + 2 decimals)
            if not self._columns_fixed:
                fm = self.table.fontMetrics()
                padding = 24
                template = "0000000000.00"  # 10 digits + decimal + 2 decimals
                w_val = fm.horizontalAdvance(template) + padding
                self.table.setColumnWidth(1, w_val)
                # Keep Alias column stretch and Unit autosizing
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
            # Value column width is fixed; keep alias stretch and unit auto


