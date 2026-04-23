# Author: T. Onkst | Date: 04212026

from __future__ import annotations

from typing import List, Optional, Tuple

try:
    from PySide6.QtCore import Qt, QRectF
    from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QPainterPath
    from PySide6.QtWidgets import QWidget
except Exception:
    raise


class CycleChartWidget(QWidget):
    """Lightweight step-schedule chart drawn with QPainter.

    Displays the load profile as a step line with axis labels and a
    vertical marker showing the current time position.
    """

    _BG = QColor(30, 30, 30)
    _GRID = QColor(60, 60, 60)
    _AXIS_TEXT = QColor(180, 180, 180)
    _STEP_LINE = QColor(52, 152, 219)
    _STEP_FILL = QColor(52, 152, 219, 40)
    _MARKER = QColor(231, 76, 60)
    _MARGIN_L = 48
    _MARGIN_R = 12
    _MARGIN_T = 10
    _MARGIN_B = 24

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._schedule: List[Tuple[float, float]] = []
        self._max_kw: float = 100.0
        self._duration_s: float = 1.0
        self._position_s: float = 0.0
        self.setMinimumHeight(100)

    def set_schedule(self, schedule: List[Tuple[float, float]], duration_s: float) -> None:
        self._schedule = list(schedule)
        self._duration_s = max(1.0, duration_s)
        self._max_kw = max((v for _, v in schedule), default=100.0) * 1.1
        if self._max_kw < 1.0:
            self._max_kw = 100.0
        self.update()

    def set_position(self, position_s: float) -> None:
        self._position_s = max(0.0, position_s)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._schedule:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        ml, mr, mt, mb = self._MARGIN_L, self._MARGIN_R, self._MARGIN_T, self._MARGIN_B
        plot_w = w - ml - mr
        plot_h = h - mt - mb
        if plot_w < 10 or plot_h < 10:
            p.end()
            return

        p.fillRect(0, 0, w, h, self._BG)

        def tx(t: float) -> float:
            return ml + (t / self._duration_s) * plot_w

        def ty(v: float) -> float:
            return mt + plot_h - (v / self._max_kw) * plot_h

        # Grid lines (3 horizontal)
        pen_grid = QPen(self._GRID, 1, Qt.PenStyle.DotLine)
        p.setPen(pen_grid)
        for i in range(1, 4):
            gy = mt + plot_h * (1.0 - i / 4.0)
            p.drawLine(int(ml), int(gy), int(ml + plot_w), int(gy))

        # Axis labels
        font = QFont("Segoe UI", 7)
        p.setFont(font)
        p.setPen(QPen(self._AXIS_TEXT))
        for i in range(5):
            frac = i / 4.0
            val = self._max_kw * frac
            label = f"{val:.0f}"
            yl = mt + plot_h * (1.0 - frac)
            p.drawText(QRectF(0, yl - 8, ml - 4, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)

        # Time labels along bottom
        n_labels = min(5, max(2, int(self._duration_s / 60) + 1))
        for i in range(n_labels + 1):
            frac = i / n_labels
            t_val = self._duration_s * frac
            xl = tx(t_val)
            if t_val >= 3600:
                lbl = f"{t_val/3600:.1f}h"
            elif t_val >= 60:
                lbl = f"{t_val/60:.0f}m"
            else:
                lbl = f"{t_val:.0f}s"
            p.drawText(QRectF(xl - 20, mt + plot_h + 2, 40, 18), Qt.AlignmentFlag.AlignCenter, lbl)

        # Step line + fill
        path = QPainterPath()
        fill_path = QPainterPath()
        baseline_y = ty(0.0)
        first = True
        for t, v in self._schedule:
            x = tx(t)
            y = ty(v)
            if first:
                path.moveTo(ml if t > 0 else x, baseline_y if t > 0 else y)
                fill_path.moveTo(ml, baseline_y)
                if t > 0:
                    path.lineTo(x, baseline_y)
                    fill_path.lineTo(x, baseline_y)
                fill_path.lineTo(x, y)
                if t > 0:
                    path.moveTo(x, y)
                first = False
            else:
                path.lineTo(x, path.currentPosition().y())
                fill_path.lineTo(x, fill_path.currentPosition().y())
                path.lineTo(x, y)
                fill_path.lineTo(x, y)

        end_x = tx(self._duration_s)
        if self._schedule:
            path.lineTo(end_x, path.currentPosition().y())
            fill_path.lineTo(end_x, fill_path.currentPosition().y())
            fill_path.lineTo(end_x, baseline_y)
            fill_path.lineTo(ml, baseline_y)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self._STEP_FILL))
        p.drawPath(fill_path)

        p.setPen(QPen(self._STEP_LINE, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        # Position marker
        mx = tx(min(self._position_s, self._duration_s))
        pen_marker = QPen(self._MARKER, 2)
        p.setPen(pen_marker)
        p.drawLine(int(mx), int(mt), int(mx), int(mt + plot_h))

        # Axes border
        p.setPen(QPen(self._AXIS_TEXT, 1))
        p.drawLine(int(ml), int(mt), int(ml), int(mt + plot_h))
        p.drawLine(int(ml), int(mt + plot_h), int(ml + plot_w), int(mt + plot_h))

        p.end()
