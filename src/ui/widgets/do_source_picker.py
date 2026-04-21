# Author: T. Onkst | Date: 04202026

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QDialog,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QDialogButtonBox,
    )
except Exception:
    raise


class DOSourcePickerDialog(QDialog):
    """Lets the user pick from currently active telemetry channels as a DO source."""

    def __init__(
        self,
        parent=None,
        *,
        current_source: str = "",
        telemetry_getter: Optional[Callable[[], Dict[str, Any]]] = None,
        extra_aliases: Optional[List[str]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select DO Source Channel")
        self.setMinimumSize(400, 500)
        self.selected_source: str = ""

        sources = self._collect_sources(telemetry_getter, extra_aliases)
        self._all_sources = sources

        self._init_ui(current_source)

    @staticmethod
    def _collect_sources(
        telemetry_getter: Optional[Callable[[], Dict[str, Any]]],
        extra_aliases: Optional[List[str]],
    ) -> List[str]:
        keys: set[str] = set()
        if telemetry_getter is not None:
            try:
                vals = telemetry_getter()
                if isinstance(vals, dict):
                    for k in vals:
                        if not k.endswith("/health_ok") and not k.endswith("/conn_ok"):
                            keys.add(str(k))
            except Exception:
                pass
        if extra_aliases:
            keys.update(extra_aliases)
        return sorted(keys)

    def _init_ui(self, current: str) -> None:
        root = QVBoxLayout(self)

        root.addWidget(QLabel("Search or select a telemetry channel to map to this DO:"))

        self.txt_filter = QLineEdit(self)
        self.txt_filter.setPlaceholderText("Type to filter...")
        self.txt_filter.setClearButtonEnabled(True)
        self.txt_filter.textChanged.connect(self._apply_filter)
        root.addWidget(self.txt_filter)

        self.lst = QListWidget(self)
        self.lst.setAlternatingRowColors(True)
        for src in self._all_sources:
            item = QListWidgetItem(src)
            self.lst.addItem(item)
            if src == current:
                item.setSelected(True)
                self.lst.setCurrentItem(item)
        self.lst.itemDoubleClicked.connect(self._on_item_double_click)
        root.addWidget(self.lst)

        self.txt_custom = QLineEdit(self)
        self.txt_custom.setPlaceholderText("Or type a custom source alias...")
        if current and current not in self._all_sources:
            self.txt_custom.setText(current)
        root.addWidget(self.txt_custom)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _apply_filter(self, text: str) -> None:
        filt = text.strip().lower()
        for i in range(self.lst.count()):
            item = self.lst.item(i)
            if item is not None:
                item.setHidden(filt not in item.text().lower())

    def _on_item_double_click(self, item: QListWidgetItem) -> None:
        self.selected_source = item.text()
        self.accept()

    def _on_accept(self) -> None:
        custom = self.txt_custom.text().strip()
        if custom:
            self.selected_source = custom
        else:
            sel = self.lst.currentItem()
            if sel and not sel.isHidden():
                self.selected_source = sel.text()
        self.accept()
