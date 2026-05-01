# Author: T. Onkst | Date: 08182025
# Updated: 04212026 — fixed 7x3 grid layout with source-group panels

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QDoubleSpinBox,
        QGridLayout,
        QGroupBox,
        QHeaderView,
        QLabel,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except Exception:
    raise

from .table_alarm_colors import apply_alarm_state_to_row

# ---------------------------------------------------------------------------
# Grid placement spec:  (row, col, rowspan, colspan, source_group_key)
# ---------------------------------------------------------------------------

_GRID_SPEC: List[Tuple[int, int, int, int, str]] = [
    (0, 0, 1, 1, "System"),
    (0, 1, 1, 1, "Environment"),
    (0, 2, 2, 1, "NI Pressure"),
    (0, 3, 3, 1, "NI Temperature"),
    (0, 4, 3, 1, "CCP Primary"),
    (0, 5, 3, 1, "CCP Secondary"),
    (0, 6, 3, 1, "Modbus"),
    (1, 0, 1, 1, "NI Analog Out"),
    (1, 1, 1, 1, "Calculated"),
    (2, 0, 1, 1, "Other"),
    (2, 1, 1, 1, "CAN"),
    (2, 2, 1, 1, "NI Digital I/O"),
]

# Source map group names expected from orchestrator -> panel key mapping.
# Allows the orchestrator to emit "CCP Primary" or "CCP Secondary"
# while matching the panel key exactly.
_GROUP_ALIASES: Dict[str, str] = {
    "NI Temperature": "NI Temperature",
    "NI Pressure": "NI Pressure",
    "NI Digital I/O": "NI Digital I/O",
    "NI Analog Out": "NI Analog Out",
}


def _source_to_panel(source_group: str) -> str:
    """Map a source_map group name to the grid panel key."""
    if source_group in _GROUP_ALIASES:
        return _GROUP_ALIASES[source_group]
    if source_group.startswith("CCP "):
        role = source_group[4:].strip().lower()
        if "secondary" in role or role in ("1", "sec"):
            return "CCP Secondary"
        return "CCP Primary"
    for key in ("System", "Environment", "Modbus", "Calculated", "CAN", "Other"):
        if source_group == key:
            return key
    return "Other"


_FALLBACK_SYSTEM = {
    "Time_Relative_s", "iOT_Warning", "iOT_Alarm", "iOT_AlmSftSdn",
    "iOT_AlmEmgSdn", "iDG_EngRunStp",
}
_FALLBACK_PREFIXES = (
    ("EngineTest/", "System"),
    ("Cycle/", "System"),
    ("LoadBank/", "System"),
    ("CAN/", "CAN"),
    ("CCP/", "CCP Primary"),
    ("Modbus/", "Modbus"),
    ("Vaisala/", "Environment"),
    ("Omega/", "Environment"),
)


def _fallback_group(alias: str) -> str:
    """Best-effort grouping for aliases not in the source_map."""
    if alias in _FALLBACK_SYSTEM:
        return "System"
    for prefix, group in _FALLBACK_PREFIXES:
        if alias.startswith(prefix):
            return group
    return "Other"


# ---------------------------------------------------------------------------
# Source-group panel — read-only table with 3 columns
# ---------------------------------------------------------------------------

class _SourcePanel(QGroupBox):
    """A fixed group-box containing a 3-column table for one source group."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self._aliases: List[str] = []
        self._prev_texts: List[str] = []
        self._prev_states: List[str] = []

        self._table = QTableWidget(0, 3, self)
        self._table.setHorizontalHeaderLabels(["Alias", "Value", "Unit"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._columns_sized = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(self._table)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumWidth(220)

    def set_channels(
        self,
        aliases: List[str],
        values: Dict[str, Any],
        units: Dict[str, str],
        states: Dict[str, str],
    ) -> None:
        if aliases != self._aliases:
            self._aliases = list(aliases)
            self._prev_texts = [""] * len(aliases)
            self._prev_states = [""] * len(aliases)
            self._table.setRowCount(len(aliases))
            for row, alias in enumerate(aliases):
                self._table.setItem(row, 0, QTableWidgetItem(str(alias)))
                self._table.setItem(row, 1, QTableWidgetItem(""))
                self._table.setItem(row, 2, QTableWidgetItem(str(units.get(alias, ""))))
            if not self._columns_sized:
                fm = self._table.fontMetrics()
                self._table.setColumnWidth(1, fm.horizontalAdvance("00000.00") + 16)
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
            if text != self._prev_texts[row]:
                self._prev_texts[row] = text
                item_v = self._table.item(row, 1)
                if item_v is not None:
                    item_v.setText(text)
            state = str(states.get(alias, "OK"))
            if state != self._prev_states[row]:
                self._prev_states[row] = state
                apply_alarm_state_to_row(self._table, row, state)


# ---------------------------------------------------------------------------
# Analog Outputs panel — editable spin boxes with write-back
# ---------------------------------------------------------------------------

class _AOPanel(QGroupBox):
    """Displays analog output channels in a table with embedded spin boxes."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("NI Analog Out", parent)
        self._aliases: List[str] = []
        self._widgets: Dict[str, Dict[str, Any]] = {}

        self._table = QTableWidget(0, 4, self)
        self._table.setHorizontalHeaderLabels(["Alias", "Value", "Unit", ""])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._columns_sized = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(self._table)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumWidth(220)

    def configure_channels(self, ao_channels: List[Dict[str, Any]]) -> None:
        current_aliases = [ch["alias"] for ch in ao_channels]
        if current_aliases == self._aliases:
            return

        self._table.setRowCount(0)
        self._widgets.clear()
        self._aliases = current_aliases

        if not ao_channels:
            return

        self._table.setRowCount(len(ao_channels))
        for i, ch in enumerate(ao_channels):
            alias = str(ch["alias"])
            unit = str(ch.get("unit", "V"))
            v_min = float(ch.get("min", 0.0))
            v_max = float(ch.get("max", 10.0))

            self._table.setItem(i, 0, QTableWidgetItem(alias))

            spin = QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setMinimum(v_min)
            spin.setMaximum(v_max)
            spin.setValue(0.0)
            spin.setFrame(False)
            spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
            self._table.setCellWidget(i, 1, spin)

            self._table.setItem(i, 2, QTableWidgetItem(unit if unit else "V"))

            btn = QPushButton("Set")
            btn.setFixedHeight(20)
            btn.clicked.connect(self._make_set_handler(alias, spin, unit))  # type: ignore
            self._table.setCellWidget(i, 3, btn)

            self._widgets[alias] = {"spin": spin, "btn": btn}

        if not self._columns_sized:
            fm = self._table.fontMetrics()
            self._table.setColumnWidth(1, fm.horizontalAdvance("00000.000") + 16)
            self._table.setColumnWidth(3, 36)
            self._columns_sized = True

    def update_readback(self, values: Dict[str, Any]) -> None:
        for alias, widgets in self._widgets.items():
            spin: QDoubleSpinBox = widgets["spin"]
            if spin.hasFocus():
                continue
            val = values.get(alias)
            if isinstance(val, (int, float)):
                try:
                    fval = float(val)
                    if abs(spin.value() - fval) > 1e-6:
                        spin.blockSignals(True)
                        spin.setValue(fval)
                        spin.blockSignals(False)
                except Exception:
                    pass

    def _make_set_handler(self, alias: str, spin: QDoubleSpinBox, unit: str) -> Callable:
        def _on_set() -> None:
            value = spin.value()
            display_unit = unit if unit else "V"
            reply = QMessageBox.question(
                self,
                "Confirm AO Write",
                f"Set {alias} to {value:.3f} {display_unit}?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            try:
                from src.core.ipc.bus import create_ui_control_push
                ctrl = create_ui_control_push()
                if ctrl is not None:
                    msg = json.dumps({"type": "ao_write", "alias": alias, "value": value}).encode("utf-8")
                    ctrl["control_push"].send(msg)
            except Exception:
                pass
        return _on_set

    @property
    def ao_aliases(self) -> set:
        return set(self._widgets.keys())


# ---------------------------------------------------------------------------
# Main widget — fixed 7-column x 3-row grid
# ---------------------------------------------------------------------------

class ChannelsTable(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._panels: Dict[str, _SourcePanel] = {}
        self._ao_panel: Optional[_AOPanel] = None
        self._prev_buckets: Dict[str, List[str]] = {}
        self._source_map: Dict[str, str] = {}
        self._init_ui()

    def _init_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        grid = QGridLayout()
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)

        for r in range(3):
            grid.setRowStretch(r, 1)
        for c in range(7):
            grid.setColumnStretch(c, 1)

        for row, col, rspan, cspan, key in _GRID_SPEC:
            if key == "NI Analog Out":
                panel = _AOPanel(self)
                self._ao_panel = panel
            else:
                panel = _SourcePanel(key, self)
                self._panels[key] = panel
            grid.addWidget(panel, row, col, rspan, cspan)

        outer.addLayout(grid)

    def update_data(
        self,
        values: Dict[str, Any] | None,
        units: Dict[str, Any] | None,
        states: Dict[str, Any] | None = None,
        ao_channels: List[Dict[str, Any]] | None = None,
        source_map: Dict[str, str] | None = None,
    ) -> None:
        if not isinstance(values, dict):
            return
        units = units if isinstance(units, dict) else {}
        states = states if isinstance(states, dict) else {}

        if source_map is not None:
            self._source_map = source_map

        ao_aliases: set = set()
        if ao_channels and self._ao_panel is not None:
            self._ao_panel.configure_channels(ao_channels)
            self._ao_panel.update_readback(values)
            ao_aliases = self._ao_panel.ao_aliases

        keys = set(values.keys())
        if isinstance(units, dict):
            keys |= set(units.keys())

        buckets: Dict[str, List[str]] = {spec[4]: [] for spec in _GRID_SPEC}
        for alias in sorted(keys):
            if alias.endswith("/health_ok") or alias.endswith("/conn_ok"):
                continue
            if alias in ao_aliases:
                continue
            if alias.startswith(("Core/", "NI_DAQ/")):
                continue
            group = self._source_map.get(alias)
            if group:
                panel_key = _source_to_panel(group)
            else:
                panel_key = _fallback_group(alias)
            if panel_key not in buckets:
                panel_key = "Other"
            buckets[panel_key].append(alias)

        for key, panel in self._panels.items():
            ch_list = buckets.get(key, [])
            panel.set_channels(ch_list, values, units, states)

        self._prev_buckets = buckets
