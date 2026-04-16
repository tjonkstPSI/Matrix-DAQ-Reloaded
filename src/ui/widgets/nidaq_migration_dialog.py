# Author: T. Onkst | Date: 03092026

from __future__ import annotations

from typing import Any, Dict, List

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QHeaderView,
        QLabel,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
    )
except Exception:
    raise


_SKIP_LABEL = "(skip — use defaults)"


class HardwareMigrationDialog(QDialog):
    """Lets the user remap old NI DAQ modules to newly discovered modules."""

    def __init__(self, diff: Dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("NI DAQ Hardware Migration")
        self.resize(700, 400)
        self._diff = diff
        self._combos: List[QComboBox] = []
        self._init_ui()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)

        missing = self._diff.get("missing", [])
        new_devs = self._diff.get("new", [])
        unchanged = self._diff.get("unchanged", [])
        suggested = {m["old"]: m["new"] for m in self._diff.get("suggested_mappings", [])}

        summary = (
            f"{len(unchanged)} module(s) unchanged, "
            f"{len(missing)} module(s) need remapping, "
            f"{len(new_devs)} new module(s) available"
        )
        root.addWidget(QLabel(summary))
        root.addWidget(QLabel(
            "For each missing module, choose which new module should inherit its configuration.\n"
            "Chassis are excluded. Only modules with compatible I/O type (AI voltage, AI thermocouple, "
            "Digital, AO) and enough channels are offered. Matching product types listed first."
        ))

        self._table = QTableWidget(len(missing), 4)
        self._table.setHorizontalHeaderLabels(["Old Module", "Product Type", "Channels", "Map To"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QTableWidget.NoSelection)

        for row, m in enumerate(missing):
            name = str(m.get("name", ""))
            ptype = str(m.get("product_type", ""))
            capability = str(m.get("capability", ""))
            ai_subtype = str(m.get("ai_subtype", ""))
            old_ct = int(m.get("ch_count", 0) or 0)

            if ptype:
                type_label = ptype
            else:
                if capability == "ai" and ai_subtype:
                    type_label = f"(unknown, AI {ai_subtype})"
                elif capability:
                    type_label = f"(unknown, {capability})"
                else:
                    type_label = "(unknown)"

            name_item = QTableWidgetItem(name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(row, 0, name_item)

            type_item = QTableWidgetItem(type_label)
            type_item.setFlags(type_item.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(row, 1, type_item)

            count_item = QTableWidgetItem(str(old_ct))
            count_item.setFlags(count_item.flags() & ~Qt.ItemIsEditable)
            count_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, 2, count_item)

            cmb = QComboBox()
            cmb.addItem(_SKIP_LABEL)

            compatible = [
                d for d in new_devs
                if str(d.get("capability", "")) == capability
                and int(d.get("ch_count", 0) or 0) >= old_ct
            ]

            if capability == "ai" and ai_subtype:
                sub_match = [d for d in compatible if str(d.get("ai_subtype", "")) == ai_subtype]
                sub_other = [d for d in compatible if d not in sub_match]
            else:
                sub_match = compatible
                sub_other = []

            def _label(d):
                d_name = str(d.get("name", ""))
                d_type = str(d.get("product_type", ""))
                d_ct = int(d.get("ch_count", 0) or 0)
                if d_type:
                    return f"{d_name}  ({d_type}, {d_ct}ch)"
                return f"{d_name}  ({d_ct}ch)"

            preferred: List[str] = []
            fallback: List[str] = []
            for d in sub_match:
                lbl = _label(d)
                if ptype and str(d.get("product_type", "")) == ptype:
                    preferred.append(lbl)
                else:
                    fallback.append(lbl)

            other: List[str] = [_label(d) for d in sub_other]

            for lbl in preferred:
                cmb.addItem(lbl)
            if preferred and fallback:
                cmb.insertSeparator(cmb.count())
            for lbl in fallback:
                cmb.addItem(lbl)
            if (preferred or fallback) and other:
                cmb.insertSeparator(cmb.count())
            for lbl in other:
                cmb.addItem(lbl)

            if not compatible:
                cmb.setEnabled(False)
                if capability == "ai" and ai_subtype:
                    tip = f"No AI {ai_subtype} modules with at least {old_ct} channels available"
                else:
                    tip = f"No {capability or 'compatible'} modules with at least {old_ct} channels available"
                cmb.setToolTip(tip)

            suggestion = suggested.get(name, "")
            if suggestion:
                for i in range(cmb.count()):
                    text = cmb.itemText(i)
                    if text == suggestion or text.startswith(suggestion + "  ("):
                        cmb.setCurrentIndex(i)
                        break

            self._table.setCellWidget(row, 3, cmb)
            self._combos.append(cmb)

        root.addWidget(self._table)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        btns.accepted.connect(self.accept)  # type: ignore
        btns.rejected.connect(self.reject)  # type: ignore
        root.addWidget(btns)

    def confirmed_mappings(self) -> List[Dict[str, str]]:
        """Return the user-confirmed mappings as [{old, new}, ...]."""
        missing = self._diff.get("missing", [])
        result: List[Dict[str, str]] = []
        for row, m in enumerate(missing):
            cmb = self._combos[row]
            text = cmb.currentText().strip()
            if text == _SKIP_LABEL or not text:
                continue
            new_name = text.split("  (")[0].strip()
            result.append({"old": str(m.get("name", "")), "new": new_name})
        return result
