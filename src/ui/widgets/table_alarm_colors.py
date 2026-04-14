from __future__ import annotations

from typing import Optional, Tuple

try:
    from PySide6.QtGui import QColor, QBrush
    from PySide6.QtWidgets import QTableWidget
except Exception:
    raise


def alarm_state_colors(state: str) -> Tuple[Optional[QBrush], Optional[QBrush]]:
    """Return (background, foreground) brushes for an alarm state."""
    s = str(state or "").strip().upper()
    if s == "WARN":
        return (QBrush(QColor(255, 235, 59)), QBrush(QColor(0, 0, 0)))
    if s in {"SHUT", "ALARM"}:
        return (QBrush(QColor(244, 67, 54)), QBrush(QColor(255, 255, 255)))
    return (None, None)


def apply_alarm_state_to_row(table: QTableWidget, row: int, state: str) -> None:
    """Apply row coloring for a specific alarm state."""
    bg, fg = alarm_state_colors(state)
    for col in range(table.columnCount()):
        it = table.item(row, col)
        if it is None:
            continue
        if bg is None:
            it.setData(8, None)  # clear Qt.BackgroundRole without importing Qt
            it.setData(9, None)  # clear Qt.ForegroundRole
        else:
            it.setBackground(bg)
            if fg is not None:
                it.setForeground(fg)

