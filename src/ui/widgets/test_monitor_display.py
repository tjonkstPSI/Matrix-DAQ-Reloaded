# Author: T. Onkst | Date: 04292026

from __future__ import annotations

import collections
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from PySide6.QtCore import Qt, Signal
    from PySide6.QtGui import QColor, QFont, QBrush
    from PySide6.QtWidgets import (
        QCheckBox,
        QColorDialog,
        QComboBox,
        QDialog,
        QDoubleSpinBox,
        QFormLayout,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLayout,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
        QDialogButtonBox,
        QAbstractItemView,
    )
except Exception:
    raise

import inspect as _inspect

_orig_getsource = _inspect.getsource
def _safe_getsource(obj):
    try:
        return _orig_getsource(obj)
    except (TypeError, AttributeError, OSError):
        return ""
_inspect.getsource = _safe_getsource

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
import numpy as np

_inspect.getsource = _orig_getsource
del _orig_getsource, _safe_getsource

from .table_alarm_colors import apply_alarm_state_to_row
from .channels_table import _AOPanel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CFG_PATH = Path(__file__).resolve().parents[3] / "configs" / "test_monitor_display.yaml"
_ENGINE_TEST_PATH = Path(__file__).resolve().parents[3] / "configs" / "engine_test.yaml"

_TIME_WINDOWS: Dict[str, float] = {
    "10s": 10.0,
    "30s": 30.0,
    "60s": 60.0,
    "2.5min": 150.0,
    "5min": 300.0,
}

_DEFAULT_SPEED_ALIASES = ["cSP_Eng", "emasterrpm", "eslaverpm"]
_DEFAULT_POWER_ALIASES = ["xPO_GenAvg", "lPO_LdbAct"]

_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#17becf", "#aec7e8", "#ffbb78",
    "#98df8a", "#ff9896", "#c5b0d5", "#c49c94",
]

_MAX_POINTS = 6000  # 5min * 20Hz


# ---------------------------------------------------------------------------
# Configuration load / save
# ---------------------------------------------------------------------------

def _load_config() -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
        if _CFG_PATH.exists():
            return yaml.safe_load(_CFG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}


def _save_config(cfg: Dict[str, Any]) -> None:
    try:
        import yaml  # type: ignore
        _CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CFG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    except Exception:
        pass


def _load_engine_test_meta() -> Dict[str, str]:
    try:
        import yaml  # type: ignore
        if _ENGINE_TEST_PATH.exists():
            data = yaml.safe_load(_ENGINE_TEST_PATH.read_text(encoding="utf-8")) or {}
            req = data.get("required_fields") or {}
            return {k: str(v) for k, v in req.items()}
    except Exception:
        pass
    return {}


# ---------------------------------------------------------------------------
# Plot Channel Config Dialog
# ---------------------------------------------------------------------------

class _PlotChannelConfigDialog(QDialog):
    """Dialog to select channels for the plot, assign Y axes, and pick colors."""

    def __init__(
        self,
        parent: QWidget | None,
        available_aliases: List[str],
        current_channels: List[Dict[str, Any]],
        axis_ranges: Dict[int, Dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure Plot Channels")
        self.resize(700, 600)
        self.result_channels: List[Dict[str, Any]] = []
        self.result_axis_ranges: Dict[int, Dict[str, Any]] = {}
        self._available = sorted(available_aliases)
        self._selected: List[Dict[str, Any]] = [dict(c) for c in current_channels]
        self._axis_ranges: Dict[int, Dict[str, Any]] = dict(axis_ranges or {})
        self._init_ui()
        self._refresh_selected_table()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        body = QHBoxLayout()

        # Left: available aliases with search filter
        left = QVBoxLayout()
        left.addWidget(QLabel("Available Channels:"))
        self._search_avail = QLineEdit()
        self._search_avail.setPlaceholderText("Filter...")
        self._search_avail.setClearButtonEnabled(True)
        self._search_avail.textChanged.connect(self._on_filter_avail)  # type: ignore
        left.addWidget(self._search_avail)
        self._list_avail = QListWidget()
        self._list_avail.setSelectionMode(QListWidget.ExtendedSelection)
        for a in self._available:
            self._list_avail.addItem(a)
        left.addWidget(self._list_avail)
        btn_add = QPushButton("Add >>")
        btn_add.clicked.connect(self._on_add)  # type: ignore
        left.addWidget(btn_add)
        body.addLayout(left)

        # Right: selected channels table
        right = QVBoxLayout()
        right.addWidget(QLabel("Plot Channels:"))
        self._tbl = QTableWidget(0, 4)
        self._tbl.setHorizontalHeaderLabels(["Alias", "Y Axis", "Color", ""])
        self._tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        right.addWidget(self._tbl)

        btn_row = QHBoxLayout()
        btn_remove = QPushButton("Remove")
        btn_remove.clicked.connect(self._on_remove)  # type: ignore
        btn_row.addWidget(btn_remove)
        btn_row.addStretch()
        right.addLayout(btn_row)
        body.addLayout(right)

        root.addLayout(body)

        # Y-Axis range configuration
        axis_group = QGroupBox("Y-Axis Settings")
        axis_lay = QGridLayout(axis_group)
        axis_lay.setContentsMargins(6, 12, 6, 6)
        axis_lay.addWidget(QLabel("<b>Axis</b>"), 0, 0)
        axis_lay.addWidget(QLabel("<b>Label</b>"), 0, 1)
        axis_lay.addWidget(QLabel("<b>Range</b>"), 0, 2)
        axis_lay.addWidget(QLabel(""), 0, 3)
        axis_lay.addWidget(QLabel(""), 0, 4)
        self._axis_widgets: Dict[int, Dict[str, Any]] = {}
        for ax_num in range(1, 5):
            row = ax_num
            saved = self._axis_ranges.get(ax_num, {})
            is_auto = saved.get("auto", True)

            lbl = QLabel(f"Axis {ax_num}:")
            edt_name = QLineEdit()
            edt_name.setPlaceholderText(f"Axis {ax_num}")
            edt_name.setText(str(saved.get("name", "")))
            edt_name.setMaximumWidth(140)
            chk = QCheckBox("Auto")
            chk.setChecked(is_auto)
            spn_min = QDoubleSpinBox()
            spn_min.setRange(-1e9, 1e9)
            spn_min.setDecimals(2)
            spn_min.setValue(float(saved.get("min", 0.0)))
            spn_min.setPrefix("Min: ")
            spn_min.setEnabled(not is_auto)
            spn_max = QDoubleSpinBox()
            spn_max.setRange(-1e9, 1e9)
            spn_max.setDecimals(2)
            spn_max.setValue(float(saved.get("max", 100.0)))
            spn_max.setPrefix("Max: ")
            spn_max.setEnabled(not is_auto)

            chk.toggled.connect(lambda on, mn=spn_min, mx=spn_max: (  # type: ignore
                mn.setEnabled(not on), mx.setEnabled(not on)
            ))

            axis_lay.addWidget(lbl, row, 0)
            axis_lay.addWidget(edt_name, row, 1)
            axis_lay.addWidget(chk, row, 2)
            axis_lay.addWidget(spn_min, row, 3)
            axis_lay.addWidget(spn_max, row, 4)
            self._axis_widgets[ax_num] = {
                "name": edt_name, "chk": chk, "min": spn_min, "max": spn_max,
            }

        root.addWidget(axis_group)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)  # type: ignore
        btns.rejected.connect(self.reject)  # type: ignore
        root.addWidget(btns)

    def _refresh_selected_table(self) -> None:
        self._tbl.setRowCount(len(self._selected))
        for row, ch in enumerate(self._selected):
            alias_item = QTableWidgetItem(ch["alias"])
            alias_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self._tbl.setItem(row, 0, alias_item)

            cb_axis = QComboBox()
            cb_axis.addItems(["1", "2", "3", "4"])
            cb_axis.setCurrentText(str(ch.get("y_axis", 1)))
            self._tbl.setCellWidget(row, 1, cb_axis)

            color = ch.get("color", _PALETTE[row % len(_PALETTE)])
            btn_color = QPushButton()
            btn_color.setFixedSize(28, 22)
            btn_color.setStyleSheet(f"background-color: {color}; border: 1px solid #555;")
            btn_color.clicked.connect(self._make_color_handler(row, btn_color))  # type: ignore
            self._tbl.setCellWidget(row, 2, btn_color)

            btn_del = QPushButton("X")
            btn_del.setFixedSize(24, 22)
            btn_del.clicked.connect(self._make_delete_handler(row))  # type: ignore
            self._tbl.setCellWidget(row, 3, btn_del)

    def _make_color_handler(self, row: int, btn: QPushButton) -> Callable:
        def _pick() -> None:
            cur = QColor(self._selected[row].get("color", "#ffffff"))
            color = QColorDialog.getColor(cur, self, "Pick Line Color")
            if color.isValid():
                self._selected[row]["color"] = color.name()
                btn.setStyleSheet(f"background-color: {color.name()}; border: 1px solid #555;")
        return _pick

    def _make_delete_handler(self, row: int) -> Callable:
        def _del() -> None:
            if row < len(self._selected):
                self._selected.pop(row)
                self._refresh_selected_table()
        return _del

    def _on_filter_avail(self, text: str) -> None:
        filt = text.strip().lower()
        for i in range(self._list_avail.count()):
            item = self._list_avail.item(i)
            item.setHidden(filt != "" and filt not in item.text().lower())

    def _on_add(self) -> None:
        existing = {c["alias"] for c in self._selected}
        for item in self._list_avail.selectedItems():
            alias = item.text()
            if alias not in existing:
                idx = len(self._selected)
                self._selected.append({
                    "alias": alias,
                    "y_axis": 1,
                    "color": _PALETTE[idx % len(_PALETTE)],
                })
        self._refresh_selected_table()

    def _on_remove(self) -> None:
        rows = sorted({idx.row() for idx in self._tbl.selectedIndexes()}, reverse=True)
        for r in rows:
            if r < len(self._selected):
                self._selected.pop(r)
        self._refresh_selected_table()

    def _on_accept(self) -> None:
        self.result_channels = []
        for row, ch in enumerate(self._selected):
            cb = self._tbl.cellWidget(row, 1)
            axis = int(cb.currentText()) if isinstance(cb, QComboBox) else 1
            self.result_channels.append({
                "alias": ch["alias"],
                "y_axis": axis,
                "color": ch.get("color", _PALETTE[row % len(_PALETTE)]),
            })
        self.result_axis_ranges = {}
        for ax_num, widgets in self._axis_widgets.items():
            is_auto = widgets["chk"].isChecked()
            self.result_axis_ranges[ax_num] = {
                "auto": is_auto,
                "min": widgets["min"].value(),
                "max": widgets["max"].value(),
                "name": widgets["name"].text().strip(),
            }
        self.accept()


# ---------------------------------------------------------------------------
# Watch Channel Picker Dialog
# ---------------------------------------------------------------------------

class _WatchPickerDialog(QDialog):
    """Simple checkbox list to pick which channels appear in the watch table."""

    def __init__(
        self,
        parent: QWidget | None,
        available_aliases: List[str],
        current_selection: List[str],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Watch Channels")
        self.resize(400, 500)
        self.result_aliases: List[str] = []
        cur = set(current_selection)
        root = QVBoxLayout(self)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_filter)  # type: ignore
        root.addWidget(self._search)
        self._list = QListWidget()
        for a in sorted(available_aliases):
            item = QListWidgetItem(a)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if a in cur else Qt.Unchecked)
            self._list.addItem(item)
        root.addWidget(self._list)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)  # type: ignore
        btns.rejected.connect(self.reject)  # type: ignore
        root.addWidget(btns)

    def _on_filter(self, text: str) -> None:
        filt = text.strip().lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setHidden(filt != "" and filt not in item.text().lower())

    def _on_accept(self) -> None:
        self.result_aliases = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.Checked:
                self.result_aliases.append(item.text())
        self.accept()


# ---------------------------------------------------------------------------
# _PlotPanel — live rolling matplotlib plot with multi-Y-axis support
# ---------------------------------------------------------------------------

class _PlotPanel(QWidget):
    """Live rolling plot with up to 4 Y axes using matplotlib FigureCanvasQTAgg.

    Axis 1 is the primary left axis.  Axes 2-4 are created via twinx() and
    offset to the right.  On reconfigure the figure is rebuilt from scratch
    to avoid stale axis artifacts.
    """

    _AXIS_COLORS = ["#cccccc", "#f0c050", "#50c0f0", "#f07050"]
    _BG_COLOR = "#1e1e1e"
    _GRID_ALPHA = 0.25
    _REDRAW_INTERVAL_S = 0.10  # throttle redraws to ~10 Hz

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._channels: List[Dict[str, Any]] = []
        self._window_s: float = 30.0
        self._buffers: Dict[str, collections.deque] = {}
        self._time_buf: Dict[str, collections.deque] = {}
        self._lines: Dict[str, Line2D] = {}
        self._line_axis: Dict[str, int] = {}
        self._axes: Dict[int, Any] = {}  # ax_num -> matplotlib Axes
        self._axis_ranges: Dict[int, Dict[str, Any]] = {}
        self._known_aliases: List[str] = []
        self._t0: float = 0.0
        self._last_draw: float = 0.0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Time Window:"))
        self._cmb_window = QComboBox()
        self._cmb_window.addItems(list(_TIME_WINDOWS.keys()))
        self._cmb_window.setCurrentText("30s")
        self._cmb_window.currentTextChanged.connect(self._on_window_changed)  # type: ignore
        toolbar.addWidget(self._cmb_window)
        toolbar.addStretch()
        self._btn_cfg = QPushButton("Configure Channels")
        self._btn_cfg.clicked.connect(self._on_configure)  # type: ignore
        toolbar.addWidget(self._btn_cfg)
        lay.addLayout(toolbar)

        self._fig = Figure(dpi=100, facecolor=self._BG_COLOR)
        self._fig.subplots_adjust(left=0.08, right=0.88, top=0.95, bottom=0.10)
        self._canvas = FigureCanvas(self._fig)
        self._canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay.addWidget(self._canvas)

        self._ax1 = self._fig.add_subplot(111)
        self._style_axis(self._ax1, self._AXIS_COLORS[0], "Axis 1", left=True)
        self._ax1.set_xlabel("Elapsed Time (s)", color="#aaaaaa", fontsize=9)
        self._axes[1] = self._ax1

    # -- public api ----------------------------------------------------------

    def set_config(
        self,
        channels: List[Dict[str, Any]],
        window_key: str,
        axis_ranges: Dict[int, Dict[str, Any]] | None = None,
    ) -> None:
        self._cmb_window.setCurrentText(window_key if window_key in _TIME_WINDOWS else "30s")
        self._window_s = _TIME_WINDOWS.get(window_key, 30.0)
        if axis_ranges:
            self._axis_ranges = dict(axis_ranges)
        self._apply_channels(channels)

    def update_known_aliases(self, aliases: List[str]) -> None:
        if aliases and not self._known_aliases:
            self._known_aliases = sorted(aliases)
        elif aliases:
            merged = sorted(set(self._known_aliases) | set(aliases))
            if merged != self._known_aliases:
                self._known_aliases = merged

    def append_data(self, values: Dict[str, Any]) -> None:
        if not self._channels or not values:
            return

        now = time.time()
        if self._t0 == 0.0:
            self._t0 = now
        elapsed = now - self._t0
        cutoff = elapsed - self._window_s

        for ch in self._channels:
            alias = ch["alias"]
            val = values.get(alias)
            if val is None or not isinstance(val, (int, float)):
                continue
            tbuf = self._time_buf.get(alias)
            vbuf = self._buffers.get(alias)
            if tbuf is None or vbuf is None:
                continue
            tbuf.append(elapsed)
            vbuf.append(float(val))

        for alias, line in self._lines.items():
            tbuf = self._time_buf.get(alias)
            vbuf = self._buffers.get(alias)
            if not tbuf or not vbuf:
                continue
            while tbuf and tbuf[0] < cutoff:
                tbuf.popleft()
                vbuf.popleft()
            if len(tbuf) >= 2:
                line.set_data(np.array(tbuf), np.array(vbuf))

        x_min = max(0.0, elapsed - self._window_s)
        x_max = elapsed
        for ax_num, ax in self._axes.items():
            ax.set_xlim(x_min, x_max)
            rng = self._axis_ranges.get(ax_num, {})
            if rng.get("auto", True):
                ax.relim()
                ax.autoscale_view(scalex=False, scaley=True)
            else:
                ax.set_ylim(float(rng.get("min", 0)), float(rng.get("max", 100)))

        if now - self._last_draw >= self._REDRAW_INTERVAL_S:
            self._canvas.draw_idle()
            self._last_draw = now

    def get_config(self) -> Tuple[List[Dict[str, Any]], str, Dict[int, Dict[str, Any]]]:
        window_key = self._cmb_window.currentText()
        return list(self._channels), window_key, dict(self._axis_ranges)

    # -- private -------------------------------------------------------------

    def _style_axis(self, ax: Any, color: str, label: str, left: bool = False) -> None:
        ax.set_facecolor(self._BG_COLOR)
        ax.tick_params(colors="#aaaaaa", labelsize=8)
        ax.grid(True, alpha=self._GRID_ALPHA, color="#555555")
        if left:
            ax.set_ylabel(label, color=color, fontsize=9)
            ax.tick_params(axis="y", colors=color)
        else:
            ax.set_ylabel(label, color=color, fontsize=9)
            ax.tick_params(axis="y", colors=color)
        for spine in ax.spines.values():
            spine.set_color("#444444")

    def _on_window_changed(self, text: str) -> None:
        self._window_s = _TIME_WINDOWS.get(text, 30.0)

    def _on_configure(self) -> None:
        dlg = _PlotChannelConfigDialog(
            self, self._known_aliases, self._channels, self._axis_ranges
        )
        if dlg.exec() == QDialog.Accepted:
            self._axis_ranges = dlg.result_axis_ranges
            self._apply_channels(dlg.result_channels)
            self._save_to_parent()

    def _save_to_parent(self) -> None:
        parent = self.parent()
        while parent and not isinstance(parent, TestMonitorDisplay):
            parent = parent.parent()
        if isinstance(parent, TestMonitorDisplay):
            parent._persist_config()

    def _apply_channels(self, channels: List[Dict[str, Any]]) -> None:
        self._fig.clear()
        self._lines.clear()
        self._line_axis.clear()
        self._axes.clear()

        self._channels = list(channels)
        self._buffers.clear()
        self._time_buf.clear()

        self._ax1 = self._fig.add_subplot(111)
        ax1_name = self._axis_ranges.get(1, {}).get("name", "") or "Axis 1"
        self._style_axis(self._ax1, self._AXIS_COLORS[0], ax1_name, left=True)
        self._ax1.set_xlabel("Elapsed Time (s)", color="#aaaaaa", fontsize=9)
        self._axes[1] = self._ax1

        if not self._channels:
            self._canvas.draw_idle()
            return

        used_axes = sorted({int(ch.get("y_axis", 1)) for ch in self._channels})

        right_offset = 0
        for ax_num in used_axes:
            if ax_num >= 2:
                twin = self._ax1.twinx()
                if right_offset > 0:
                    twin.spines["right"].set_position(("axes", 1.0 + right_offset * 0.09))
                color = self._AXIS_COLORS[min(ax_num - 1, len(self._AXIS_COLORS) - 1)]
                ax_name = self._axis_ranges.get(ax_num, {}).get("name", "") or f"Axis {ax_num}"
                self._style_axis(twin, color, ax_name)
                self._axes[ax_num] = twin
                right_offset += 1

        for ch in self._channels:
            alias = ch["alias"]
            color = ch.get("color", "#ffffff")
            ax_num = int(ch.get("y_axis", 1))
            self._buffers[alias] = collections.deque(maxlen=_MAX_POINTS)
            self._time_buf[alias] = collections.deque(maxlen=_MAX_POINTS)

            ax = self._axes.get(ax_num, self._ax1)
            (line,) = ax.plot([], [], color=color, linewidth=2, label=alias)
            self._lines[alias] = line
            self._line_axis[alias] = ax_num

        all_handles: List[Line2D] = []
        all_labels: List[str] = []
        for ax in self._axes.values():
            h, l = ax.get_legend_handles_labels()
            all_handles.extend(h)
            all_labels.extend(l)
        if all_handles:
            self._ax1.legend(
                all_handles, all_labels,
                loc="upper right",
                facecolor="#2e2e2e",
                labelcolor="white",
                edgecolor="#555555",
                fontsize=8,
            )

        for ax_num, ax in self._axes.items():
            rng = self._axis_ranges.get(ax_num, {})
            if not rng.get("auto", True):
                ax.set_ylim(float(rng.get("min", 0)), float(rng.get("max", 100)))

        right_margin = 0.92 - right_offset * 0.045
        self._fig.subplots_adjust(left=0.08, right=max(right_margin, 0.65))
        self._canvas.draw_idle()


# ---------------------------------------------------------------------------
# _StandardInfoPanel — fixed fields for engine/test metadata
# ---------------------------------------------------------------------------

class _StandardInfoPanel(QGroupBox):
    """Displays Engine Speed, Power (live), and test metadata (from YAML)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Test Info", parent)
        self._speed_aliases = list(_DEFAULT_SPEED_ALIASES)
        self._power_aliases = list(_DEFAULT_POWER_ALIASES)
        self._resolved_speed: Optional[str] = None
        self._resolved_power: Optional[str] = None
        self._meta_cache: Optional[Dict[str, str]] = None
        self._meta_loaded = False

        form = QFormLayout(self)
        form.setContentsMargins(6, 12, 6, 6)

        self._lbl_speed = QLabel("--")
        self._lbl_power = QLabel("--")
        self._lbl_engine_type = QLabel("--")
        self._lbl_serial = QLabel("--")
        self._lbl_operator = QLabel("--")
        self._lbl_test_type = QLabel("--")

        for lbl in (self._lbl_speed, self._lbl_power, self._lbl_engine_type,
                     self._lbl_serial, self._lbl_operator, self._lbl_test_type):
            lbl.setTextFormat(Qt.PlainText)
            f = lbl.font()
            f.setPointSize(10)
            lbl.setFont(f)

        form.addRow("Engine Speed:", self._lbl_speed)
        form.addRow("Power:", self._lbl_power)
        form.addRow("Engine Type:", self._lbl_engine_type)
        form.addRow("Engine Serial:", self._lbl_serial)
        form.addRow("Operator:", self._lbl_operator)
        form.addRow("Test Type:", self._lbl_test_type)

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def set_alias_overrides(
        self,
        speed: Optional[List[str]] = None,
        power: Optional[List[str]] = None,
    ) -> None:
        if speed:
            self._speed_aliases = list(speed)
        if power:
            self._power_aliases = list(power)

    def _resolve_alias(
        self,
        candidates: List[str],
        values: Dict[str, Any],
        cached: Optional[str],
    ) -> Tuple[Optional[str], Any]:
        if cached and cached in values:
            return cached, values[cached]
        for a in candidates:
            if a in values:
                return a, values[a]
        return None, None

    def update(
        self,
        values: Dict[str, Any],
        units: Dict[str, Any] | None,
        states: Dict[str, str] | None,
    ) -> None:
        self._resolved_speed, spd_val = self._resolve_alias(
            self._speed_aliases, values, self._resolved_speed
        )
        self._resolved_power, pwr_val = self._resolve_alias(
            self._power_aliases, values, self._resolved_power
        )

        if spd_val is not None and isinstance(spd_val, (int, float)):
            unit = ""
            if units and self._resolved_speed:
                unit = str(units.get(self._resolved_speed, ""))
            self._lbl_speed.setText(f"{float(spd_val):.1f} {unit}".strip())
        else:
            self._lbl_speed.setText("N/A")

        if pwr_val is not None and isinstance(pwr_val, (int, float)):
            unit = ""
            if units and self._resolved_power:
                unit = str(units.get(self._resolved_power, ""))
            self._lbl_power.setText(f"{float(pwr_val):.1f} {unit}".strip())
        else:
            self._lbl_power.setText("N/A")

        # Alarm coloring for speed/power labels
        if states:
            self._apply_label_alarm(self._lbl_speed, states.get(self._resolved_speed or "", "OK"))
            self._apply_label_alarm(self._lbl_power, states.get(self._resolved_power or "", "OK"))

        # Metadata (reload periodically)
        if not self._meta_loaded:
            self._meta_cache = _load_engine_test_meta()
            self._meta_loaded = True

        if self._meta_cache:
            self._lbl_engine_type.setText(self._meta_cache.get("engine_type", "--") or "--")
            self._lbl_serial.setText(self._meta_cache.get("engine_serial_number", "--") or "--")
            self._lbl_operator.setText(self._meta_cache.get("test_operator", "--") or "--")
            self._lbl_test_type.setText(self._meta_cache.get("test_type", "--") or "--")

    def invalidate_meta(self) -> None:
        self._meta_loaded = False

    @staticmethod
    def _apply_label_alarm(lbl: QLabel, state: str) -> None:
        s = str(state or "").strip().upper()
        if s == "WARN":
            lbl.setStyleSheet("background-color: #FFEB3B; color: #000; padding: 2px;")
        elif s in ("SHUT", "ALARM"):
            lbl.setStyleSheet("background-color: #F44336; color: #FFF; padding: 2px;")
        else:
            lbl.setStyleSheet("")


# ---------------------------------------------------------------------------
# _WatchPanel — user-configurable channel watch table
# ---------------------------------------------------------------------------

class _WatchPanel(QGroupBox):
    """Displays a user-selected set of channels with live values and alarm coloring."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Watch Channels", parent)
        self._aliases: List[str] = []
        self._known_aliases: List[str] = []
        self._prev_states: Dict[str, str] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 12, 4, 4)

        btn = QPushButton("Edit Channels")
        btn.clicked.connect(self._on_edit)  # type: ignore
        lay.addWidget(btn)

        self._tbl = QTableWidget(0, 3)
        self._tbl.setHorizontalHeaderLabels(["Alias", "Value", "Unit"])
        self._tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        lay.addWidget(self._tbl)

    def set_channels(self, aliases: List[str]) -> None:
        self._aliases = list(aliases)
        self._rebuild_table()

    def update_known_aliases(self, aliases: List[str]) -> None:
        if aliases:
            merged = sorted(set(self._known_aliases) | set(aliases))
            if merged != self._known_aliases:
                self._known_aliases = merged

    def _rebuild_table(self) -> None:
        self._tbl.setRowCount(len(self._aliases))
        self._prev_states.clear()
        for row, alias in enumerate(self._aliases):
            self._tbl.setItem(row, 0, QTableWidgetItem(alias))
            self._tbl.setItem(row, 1, QTableWidgetItem("--"))
            self._tbl.setItem(row, 2, QTableWidgetItem(""))

    def update(
        self,
        values: Dict[str, Any],
        units: Dict[str, Any] | None,
        states: Dict[str, str] | None,
    ) -> None:
        for row, alias in enumerate(self._aliases):
            val = values.get(alias)
            if val is not None and isinstance(val, (int, float)):
                self._tbl.item(row, 1).setText(f"{float(val):.4f}")
            elif val is not None:
                self._tbl.item(row, 1).setText(str(val))

            if units:
                u = units.get(alias, "")
                self._tbl.item(row, 2).setText(str(u))

            state = "OK"
            if states:
                state = str(states.get(alias, "OK"))
            prev = self._prev_states.get(alias, "OK")
            if state != prev:
                apply_alarm_state_to_row(self._tbl, row, state)
                self._prev_states[alias] = state

    def _on_edit(self) -> None:
        dlg = _WatchPickerDialog(self, self._known_aliases, self._aliases)
        if dlg.exec() == QDialog.Accepted:
            self._aliases = dlg.result_aliases
            self._rebuild_table()
            self._save_to_parent()

    def _save_to_parent(self) -> None:
        parent = self.parent()
        while parent and not isinstance(parent, TestMonitorDisplay):
            parent = parent.parent()
        if isinstance(parent, TestMonitorDisplay):
            parent._persist_config()

    def get_aliases(self) -> List[str]:
        return list(self._aliases)


# ---------------------------------------------------------------------------
# _AlarmTerminal — scrolling alarm/warning message log
# ---------------------------------------------------------------------------

class _AlarmTerminal(QWidget):

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("<b>Alarm / Warning Log</b>")
        lay.addWidget(lbl)
        self._txt = QPlainTextEdit()
        self._txt.setReadOnly(True)
        self._txt.setMaximumBlockCount(500)
        mono = QFont("Consolas", 9)
        mono.setStyleHint(QFont.Monospace)
        self._txt.setFont(mono)
        self._txt.setStyleSheet(
            "QPlainTextEdit { background-color: #1a1a2e; color: #e0e0e0; }"
        )
        lay.addWidget(self._txt)

    def process_events(self, alarm_events: list | None) -> None:
        if not alarm_events:
            return
        for ev in alarm_events:
            if not isinstance(ev, dict):
                continue
            alias = ev.get("alias", "?")
            new_state = str(ev.get("new_state", "")).upper()
            old_state = str(ev.get("old_state", "")).upper()
            trigger = ev.get("trigger", "")
            ts = ev.get("ts")
            try:
                ts_str = datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S") if ts else "--:--:--"
            except Exception:
                ts_str = "--:--:--"

            if new_state == "WARN":
                self._txt.appendPlainText(
                    f"[{ts_str}] WARNING: {alias} — {trigger}"
                )
            elif new_state in ("SHUT", "ALARM"):
                self._txt.appendPlainText(
                    f"[{ts_str}] ALARM: {alias} — {trigger}"
                )
            elif new_state == "OK" and old_state in ("WARN", "SHUT", "ALARM"):
                self._txt.appendPlainText(
                    f"[{ts_str}] CLEARED: {alias} returned to OK"
                )


# ---------------------------------------------------------------------------
# TestMonitorDisplay — main composite widget
# ---------------------------------------------------------------------------

class TestMonitorDisplay(QWidget):
    """Main Test Monitor Display: live plot, AO controls, watch table,
    standard info, and alarm terminal."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cfg = _load_config()
        self._perf_diag_enabled = str(os.environ.get("MATRIX_UI_PERF_DIAG", "")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self._perf_diag: Dict[str, Any] = {"start": time.perf_counter(), "count": 0, "samples": []}
        self._init_ui()
        self._apply_saved_config()

    def _record_perf_diag(
        self,
        *,
        elapsed_ms: float,
        alias_ms: float,
        plot_ms: float,
        standard_ms: float,
        watch_ms: float,
        ao_ms: float,
        alarm_ms: float,
        value_count: int,
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
                        "alias_ms": float(alias_ms),
                        "plot_ms": float(plot_ms),
                        "standard_ms": float(standard_ms),
                        "watch_ms": float(watch_ms),
                        "ao_ms": float(ao_ms),
                        "alarm_ms": float(alarm_ms),
                        "value_count": float(value_count),
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
                "[UI_PERF] test_monitor "
                f"count={count} rate={count / max(0.001, now - start):.1f}/s "
                f"elapsed_ms_avg={_avg('elapsed_ms'):.2f} elapsed_ms_max={_max('elapsed_ms'):.2f} "
                f"alias_ms_avg={_avg('alias_ms'):.2f} alias_ms_max={_max('alias_ms'):.2f} "
                f"plot_ms_avg={_avg('plot_ms'):.2f} plot_ms_max={_max('plot_ms'):.2f} "
                f"standard_ms_avg={_avg('standard_ms'):.2f} standard_ms_max={_max('standard_ms'):.2f} "
                f"watch_ms_avg={_avg('watch_ms'):.2f} watch_ms_max={_max('watch_ms'):.2f} "
                f"ao_ms_avg={_avg('ao_ms'):.2f} ao_ms_max={_max('ao_ms'):.2f} "
                f"alarm_ms_avg={_avg('alarm_ms'):.2f} alarm_ms_max={_max('alarm_ms'):.2f} "
                f"value_count_max={_max('value_count'):.0f}",
                flush=True,
            )
            self._perf_diag = {"start": now, "count": 0, "samples": []}
        except Exception:
            pass

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # Main vertical splitter: top content vs alarm terminal
        self._vsplit = QSplitter(Qt.Vertical)

        # Top horizontal splitter: plot vs right sidebar
        self._hsplit = QSplitter(Qt.Horizontal)

        # Left: plot panel
        self._plot_panel = _PlotPanel()
        self._hsplit.addWidget(self._plot_panel)

        # Right: sidebar with standard info, watch table, AO panel
        right_container = QWidget()
        right_lay = QVBoxLayout(right_container)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(4)

        self._standard_panel = _StandardInfoPanel()
        right_lay.addWidget(self._standard_panel)

        self._watch_panel = _WatchPanel()
        right_lay.addWidget(self._watch_panel, stretch=1)

        self._ao_panel = _AOPanel()
        self._ao_panel.setVisible(False)
        right_lay.addWidget(self._ao_panel)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setWidget(right_container)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._hsplit.addWidget(right_scroll)

        self._hsplit.setSizes([1300, 580])
        self._hsplit.setStretchFactor(0, 3)
        self._hsplit.setStretchFactor(1, 1)

        self._vsplit.addWidget(self._hsplit)

        # Bottom: alarm terminal
        self._alarm_terminal = _AlarmTerminal()
        self._vsplit.addWidget(self._alarm_terminal)

        self._vsplit.setSizes([780, 200])
        self._vsplit.setStretchFactor(0, 4)
        self._vsplit.setStretchFactor(1, 1)

        root.addWidget(self._vsplit)

    def _apply_saved_config(self) -> None:
        plot_cfg = self._cfg.get("plot") or {}
        channels = plot_cfg.get("channels") or []
        window_key = str(plot_cfg.get("time_window", "30s"))
        raw_ranges = plot_cfg.get("axis_ranges") or {}
        axis_ranges = {int(k): v for k, v in raw_ranges.items()}
        if channels:
            self._plot_panel.set_config(channels, window_key, axis_ranges)

        watch = self._cfg.get("watch_channels") or []
        if watch:
            self._watch_panel.set_channels(watch)

        overrides = self._cfg.get("standard_overrides") or {}
        self._standard_panel.set_alias_overrides(
            speed=overrides.get("speed_aliases"),
            power=overrides.get("power_aliases"),
        )

    def _persist_config(self) -> None:
        channels, window_key, axis_ranges = self._plot_panel.get_config()
        self._cfg["plot"] = {
            "time_window": window_key,
            "channels": channels,
            "axis_ranges": {str(k): v for k, v in axis_ranges.items()},
        }
        self._cfg["watch_channels"] = self._watch_panel.get_aliases()
        if "standard_overrides" not in self._cfg:
            self._cfg["standard_overrides"] = {
                "speed_aliases": list(_DEFAULT_SPEED_ALIASES),
                "power_aliases": list(_DEFAULT_POWER_ALIASES),
            }
        _save_config(self._cfg)

    def update_data(
        self,
        values: Dict[str, Any] | None,
        units: Dict[str, Any] | None,
        states: Dict[str, str] | None,
        alarm_events: list | None = None,
        ao_channels: List[Dict[str, Any]] | None = None,
    ) -> None:
        if values is None:
            values = {}
        diag_enabled = self._perf_diag_enabled
        diag_start = time.perf_counter() if diag_enabled else 0.0
        alias_ms = 0.0
        plot_ms = 0.0
        standard_ms = 0.0
        watch_ms = 0.0
        ao_ms = 0.0
        alarm_ms = 0.0

        call_start = time.perf_counter() if diag_enabled else 0.0
        all_aliases = list(values.keys())
        self._plot_panel.update_known_aliases(all_aliases)
        self._watch_panel.update_known_aliases(all_aliases)
        if diag_enabled:
            alias_ms = (time.perf_counter() - call_start) * 1000.0

        try:
            call_start = time.perf_counter() if diag_enabled else 0.0
            self._plot_panel.append_data(values)
            if diag_enabled:
                plot_ms = (time.perf_counter() - call_start) * 1000.0
        except Exception:
            pass

        call_start = time.perf_counter() if diag_enabled else 0.0
        self._standard_panel.update(values, units, states)
        if diag_enabled:
            standard_ms = (time.perf_counter() - call_start) * 1000.0
        call_start = time.perf_counter() if diag_enabled else 0.0
        self._watch_panel.update(values, units, states)
        if diag_enabled:
            watch_ms = (time.perf_counter() - call_start) * 1000.0

        call_start = time.perf_counter() if diag_enabled else 0.0
        if ao_channels is not None:
            self._ao_panel.configure_channels(ao_channels)
            self._ao_panel.update_readback(values)
        elif self._ao_panel.isVisible():
            self._ao_panel.update_readback(values)
        if diag_enabled:
            ao_ms = (time.perf_counter() - call_start) * 1000.0

        call_start = time.perf_counter() if diag_enabled else 0.0
        self._alarm_terminal.process_events(alarm_events)
        if diag_enabled:
            alarm_ms = (time.perf_counter() - call_start) * 1000.0
            self._record_perf_diag(
                elapsed_ms=(time.perf_counter() - diag_start) * 1000.0,
                alias_ms=alias_ms,
                plot_ms=plot_ms,
                standard_ms=standard_ms,
                watch_ms=watch_ms,
                ao_ms=ao_ms,
                alarm_ms=alarm_ms,
                value_count=len(values),
            )
