# Author: T. Onkst | Date: 08182025
# Updated: 03092026 — grouped category panels with flow layout

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from PySide6.QtCore import QPoint, QRect, QSize, Qt
    from PySide6.QtWidgets import (
        QGroupBox,
        QHeaderView,
        QLayout,
        QLayoutItem,
        QScrollArea,
        QSizePolicy,
        QStyle,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
        QWidgetItem,
    )
except Exception:
    raise

from .table_alarm_colors import apply_alarm_state_to_row

# ---------------------------------------------------------------------------
# Category definitions
# ---------------------------------------------------------------------------

CATEGORY_ORDER: List[str] = [
    "Engine Conditions",
    "Temperatures",
    "Pressures",
    "ECU Data",
    "Facility",
    "Other",
]

_ALIAS_RE = re.compile(r"^([qcemixypvl])([A-Z]{2})_(.+)$")


def _categorize_channel(alias: str) -> str:
    m = _ALIAS_RE.match(alias)
    if m:
        source, code, desc = m.group(1), m.group(2), m.group(3)
        if code == "TP":
            return "Temperatures"
        if code == "PR":
            return "Pressures"
        if source in ("c", "e"):
            return "ECU Data"
        if "Ldb" in desc or "Alm" in desc or "Fan" in desc:
            return "Facility"
        if code == "HM":
            return "Facility"
        if source == "m":
            return "Engine Conditions"
        return "Other"
    if alias.startswith("e"):
        return "ECU Data"
    return "Other"


# ---------------------------------------------------------------------------
# FlowLayout — arranges children left-to-right, wrapping on overflow
# ---------------------------------------------------------------------------

class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, h_spacing: int = 8, v_spacing: int = 8) -> None:
        super().__init__(parent)
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._items: List[QLayoutItem] = []

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:  # noqa: N802
        return Qt.Orientations()  # type: ignore[return-value]

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        row_height = 0

        for item in self._items:
            wid = item.widget()
            if wid is not None and not wid.isVisible():
                continue
            sz = item.sizeHint()
            next_x = x + sz.width() + self._h_spacing
            if next_x - self._h_spacing > effective.right() and row_height > 0:
                x = effective.x()
                y = y + row_height + self._v_spacing
                next_x = x + sz.width() + self._h_spacing
                row_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), sz))
            x = next_x
            row_height = max(row_height, sz.height())

        return y + row_height - rect.y() + m.bottom()


# ---------------------------------------------------------------------------
# Category panel — one QGroupBox with a compact QTableWidget inside
# ---------------------------------------------------------------------------

class _CategoryPanel(QGroupBox):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self._aliases: List[str] = []
        self._table = QTableWidget(0, 3, self)
        self._table.setHorizontalHeaderLabels(["Alias", "Value", "Unit"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._columns_sized = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self._table)

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMinimumWidth(260)

    @property
    def aliases(self) -> List[str]:
        return list(self._aliases)

    def set_channels(
        self,
        aliases: List[str],
        values: Dict[str, Any],
        units: Dict[str, str],
        states: Dict[str, str],
    ) -> None:
        if aliases != self._aliases:
            self._aliases = aliases
            self._table.setRowCount(len(aliases))
            for row, alias in enumerate(aliases):
                self._table.setItem(row, 0, QTableWidgetItem(str(alias)))
                self._table.setItem(row, 1, QTableWidgetItem(""))
                self._table.setItem(row, 2, QTableWidgetItem(str(units.get(alias, ""))))
            if not self._columns_sized:
                fm = self._table.fontMetrics()
                self._table.setColumnWidth(1, fm.horizontalAdvance("0000000000.00") + 24)
                self._columns_sized = True

        for row, alias in enumerate(self._aliases):
            val = values.get(alias)
            if isinstance(val, (int, float)):
                try:
                    text = f"{float(val):.2f}"
                except Exception:
                    text = str(val)
            else:
                text = str(val) if val is not None else ""
            item_v = self._table.item(row, 1)
            if item_v is not None:
                item_v.setText(text)
            item_u = self._table.item(row, 2)
            if item_u is not None:
                item_u.setText(str(units.get(alias, "")))
            state = str(states.get(alias, "OK"))
            apply_alarm_state_to_row(self._table, row, state)

        self._resize_to_fit()

    def _resize_to_fit(self) -> None:
        row_count = self._table.rowCount()
        if row_count == 0:
            self.setFixedHeight(60)
            return
        header_h = self._table.horizontalHeader().height()
        row_h = self._table.verticalHeader().defaultSectionSize()
        table_h = header_h + row_h * row_count + 4
        lay_margins = self.layout().contentsMargins()
        title_h = self.fontMetrics().height() + 8
        total = table_h + lay_margins.top() + lay_margins.bottom() + title_h
        self._table.setFixedHeight(table_h)
        self.setFixedHeight(total)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(300, self.height() if self.height() > 0 else 200)


# ---------------------------------------------------------------------------
# Main widget — replaces the old flat ChannelsTable
# ---------------------------------------------------------------------------

class ChannelsTable(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._panels: Dict[str, _CategoryPanel] = {}
        self._prev_buckets: Dict[str, List[str]] = {}
        self._init_ui()

    def _init_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._container = QWidget()
        self._flow = FlowLayout(self._container, h_spacing=6, v_spacing=6)
        self._container.setLayout(self._flow)
        self._scroll.setWidget(self._container)
        outer.addWidget(self._scroll)

        for cat in CATEGORY_ORDER:
            panel = _CategoryPanel(cat, self._container)
            panel.setVisible(False)
            self._flow.addWidget(panel)
            self._panels[cat] = panel

    def update_data(
        self,
        values: Dict[str, Any] | None,
        units: Dict[str, Any] | None,
        states: Dict[str, Any] | None = None,
    ) -> None:
        if not isinstance(values, dict):
            return
        units = units if isinstance(units, dict) else {}
        states = states if isinstance(states, dict) else {}

        keys = set(values.keys())
        if isinstance(units, dict):
            keys |= set(units.keys())

        buckets: Dict[str, List[str]] = {cat: [] for cat in CATEGORY_ORDER}
        for alias in sorted(keys):
            cat = _categorize_channel(alias)
            if cat not in buckets:
                cat = "Other"
            buckets[cat].append(alias)

        layout_changed = buckets != self._prev_buckets

        for cat in CATEGORY_ORDER:
            panel = self._panels[cat]
            ch_list = buckets[cat]
            if ch_list:
                panel.setVisible(True)
                panel.set_channels(ch_list, values, units, states)
            else:
                panel.setVisible(False)

        if layout_changed:
            self._prev_buckets = buckets
            self._container.updateGeometry()
