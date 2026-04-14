# Author: T. Onkst | Date: 03092026
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, List

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
    )
except Exception:
    raise


class CalculatedConfigDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure Calculated Channels")
        self.resize(1120, 760)
        self._cfg_path = Path(__file__).resolve().parents[3] / "configs" / "calculated_channels.yaml"
        self._cfg: Dict[str, Any] = {}
        self._active_row: int = -1
        self._init_ui()
        self._load()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        split = QHBoxLayout()

        # Left side: mapping editor for selected calculation.
        left_box = QGroupBox("Channel Mapping (Selected Calculation)")
        lv = QVBoxLayout(left_box)
        self.lbl_selected = QLabel("Select a calculation row to edit symbols.")
        lv.addWidget(self.lbl_selected)

        self.tbl_symbols = QTableWidget(self)
        self.tbl_symbols.setColumnCount(2)
        self.tbl_symbols.setHorizontalHeaderLabels(["Symbol", "Input Channel Alias or Constant"])
        self.tbl_symbols.horizontalHeader().setStretchLastSection(True)
        lv.addWidget(self.tbl_symbols, 1)

        sym_btns = QHBoxLayout()
        self.btn_add_symbol = QPushButton("Add Symbol")
        self.btn_add_symbol.clicked.connect(self._add_symbol_row)  # type: ignore
        self.btn_remove_symbol = QPushButton("Remove Symbol")
        self.btn_remove_symbol.clicked.connect(self._remove_selected_symbol_rows)  # type: ignore
        self.btn_apply_symbols = QPushButton("Apply Mapping to Selected Calculation")
        self.btn_apply_symbols.clicked.connect(self._save_current_mapping)  # type: ignore
        sym_btns.addWidget(self.btn_add_symbol)
        sym_btns.addWidget(self.btn_remove_symbol)
        sym_btns.addWidget(self.btn_apply_symbols)
        sym_btns.addStretch(1)
        lv.addLayout(sym_btns)

        helper = QLabel("Map symbols (example: x -> qPR_Amb, k -> 0.1450377).")
        helper.setWordWrap(True)
        lv.addWidget(helper)
        split.addWidget(left_box, 1)

        # Right side: calculations list + expressions + global update rate.
        right_box = QGroupBox("Calculations")
        rv = QVBoxLayout(right_box)

        rate_form = QFormLayout()
        self.cmb_rate = QComboBox(self)
        self.cmb_rate.setEditable(True)
        self.cmb_rate.addItems(["1", "5", "10", "20", "50", "100"])
        rate_form.addRow("Global update rate (Hz)", self.cmb_rate)
        rv.addLayout(rate_form)

        self.tbl_calcs = QTableWidget(self)
        self.tbl_calcs.setColumnCount(5)
        self.tbl_calcs.setHorizontalHeaderLabels(["Enabled", "Output Alias", "Expression", "Unit", "Symbols"])
        self.tbl_calcs.horizontalHeader().setStretchLastSection(True)
        self.tbl_calcs.currentCellChanged.connect(self._on_calc_selection_changed)  # type: ignore
        rv.addWidget(self.tbl_calcs, 1)

        calc_btns = QHBoxLayout()
        self.btn_add_calc = QPushButton("Add Calculation")
        self.btn_add_calc.clicked.connect(self._add_calc_row)  # type: ignore
        self.btn_remove_calc = QPushButton("Remove Selected")
        self.btn_remove_calc.clicked.connect(self._remove_selected_calc_rows)  # type: ignore
        self.btn_duplicate_calc = QPushButton("Duplicate Selected")
        self.btn_duplicate_calc.clicked.connect(self._duplicate_selected_calc_row)  # type: ignore
        calc_btns.addWidget(self.btn_add_calc)
        calc_btns.addWidget(self.btn_remove_calc)
        calc_btns.addWidget(self.btn_duplicate_calc)
        calc_btns.addStretch(1)
        rv.addLayout(calc_btns)
        split.addWidget(right_box, 2)

        root.addLayout(split, 1)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        btns.accepted.connect(self._on_accept)  # type: ignore
        btns.rejected.connect(self.reject)  # type: ignore
        root.addWidget(btns)

    def _read_yaml(self, path: Path) -> Dict[str, Any]:
        try:
            import yaml  # type: ignore
            if not path.exists():
                return {}
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

    def _load(self) -> None:
        self._cfg = self._read_yaml(self._cfg_path)
        hz = self._cfg.get("recording_rate_hz", 10)
        self.cmb_rate.setCurrentText(str(hz))
        self.tbl_calcs.setRowCount(0)
        for item in self._cfg.get("channels", []) or []:
            if not isinstance(item, dict):
                continue
            self._add_calc_row(
                {
                    "enabled": bool(item.get("enabled", True)),
                    "alias": str(item.get("alias", "")),
                    "expr": str(item.get("expr", "")),
                    "unit": str(item.get("unit", "")),
                    "symbols": dict(item.get("symbols") or {}),
                }
            )
        if self.tbl_calcs.rowCount() > 0:
            self.tbl_calcs.setCurrentCell(0, 1)
            self._load_mapping_from_calc_row(0)

    def _set_row_symbols(self, row: int, symbols: Dict[str, Any]) -> None:
        it = self.tbl_calcs.item(row, 4)
        if it is None:
            it = QTableWidgetItem("")
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.tbl_calcs.setItem(row, 4, it)
        safe = dict(symbols or {})
        it.setData(Qt.UserRole, safe)
        it.setText(self._symbols_summary(safe))

    def _get_row_symbols(self, row: int) -> Dict[str, Any]:
        it = self.tbl_calcs.item(row, 4)
        if it is None:
            return {}
        data = it.data(Qt.UserRole)
        return dict(data) if isinstance(data, dict) else {}

    def _symbols_summary(self, symbols: Dict[str, Any]) -> str:
        if not symbols:
            return "0 symbols"
        sample = ", ".join(f"{k}={v}" for k, v in list(symbols.items())[:2])
        if len(symbols) > 2:
            sample += ", ..."
        return f"{len(symbols)} symbols ({sample})"

    def _add_calc_row(self, seed: Dict[str, Any] | None = None) -> None:
        seed = seed or {"enabled": True, "alias": "", "expr": "", "unit": "", "symbols": {}}
        r = self.tbl_calcs.rowCount()
        self.tbl_calcs.insertRow(r)

        enabled_item = QTableWidgetItem("")
        enabled_item.setFlags(enabled_item.flags() | Qt.ItemIsUserCheckable)
        enabled_item.setCheckState(Qt.Checked if bool(seed.get("enabled", True)) else Qt.Unchecked)
        self.tbl_calcs.setItem(r, 0, enabled_item)
        self.tbl_calcs.setItem(r, 1, QTableWidgetItem(str(seed.get("alias", ""))))
        self.tbl_calcs.setItem(r, 2, QTableWidgetItem(str(seed.get("expr", ""))))
        self.tbl_calcs.setItem(r, 3, QTableWidgetItem(str(seed.get("unit", ""))))
        self._set_row_symbols(r, dict(seed.get("symbols") or {}))

    def _remove_selected_calc_rows(self) -> None:
        rows = sorted({i.row() for i in self.tbl_calcs.selectedIndexes()}, reverse=True)
        for r in rows:
            self.tbl_calcs.removeRow(r)
        self._active_row = -1
        self.tbl_symbols.setRowCount(0)
        self.lbl_selected.setText("Select a calculation row to edit symbols.")

    def _duplicate_selected_calc_row(self) -> None:
        row = self.tbl_calcs.currentRow()
        if row < 0:
            return
        seed = {
            "enabled": self.tbl_calcs.item(row, 0).checkState() == Qt.Checked if self.tbl_calcs.item(row, 0) else True,
            "alias": self.tbl_calcs.item(row, 1).text() if self.tbl_calcs.item(row, 1) else "",
            "expr": self.tbl_calcs.item(row, 2).text() if self.tbl_calcs.item(row, 2) else "",
            "unit": self.tbl_calcs.item(row, 3).text() if self.tbl_calcs.item(row, 3) else "",
            "symbols": self._get_row_symbols(row),
        }
        self._add_calc_row(seed)

    def _on_calc_selection_changed(self, current_row: int, _current_col: int, previous_row: int, _previous_col: int) -> None:
        if previous_row >= 0:
            self._save_current_mapping_for_row(previous_row)
        self._load_mapping_from_calc_row(current_row)

    def _load_mapping_from_calc_row(self, row: int) -> None:
        self.tbl_symbols.setRowCount(0)
        self._active_row = row
        if row < 0 or row >= self.tbl_calcs.rowCount():
            self.lbl_selected.setText("Select a calculation row to edit symbols.")
            return
        alias = self.tbl_calcs.item(row, 1).text().strip() if self.tbl_calcs.item(row, 1) else f"Row {row + 1}"
        self.lbl_selected.setText(f"Editing symbols for: {alias or '(unnamed output)'}")
        symbols = self._get_row_symbols(row)
        for k, v in symbols.items():
            self._add_symbol_row(str(k), str(v))

    def _add_symbol_row(self, sym: str = "", mapped: str = "") -> None:
        r = self.tbl_symbols.rowCount()
        self.tbl_symbols.insertRow(r)
        self.tbl_symbols.setItem(r, 0, QTableWidgetItem(sym))
        self.tbl_symbols.setItem(r, 1, QTableWidgetItem(mapped))

    def _remove_selected_symbol_rows(self) -> None:
        rows = sorted({i.row() for i in self.tbl_symbols.selectedIndexes()}, reverse=True)
        for r in rows:
            self.tbl_symbols.removeRow(r)

    def _parse_symbol_value(self, text: str) -> Any:
        t = str(text).strip()
        if not t:
            return ""
        try:
            if t.lower() in {"nan", "+nan", "-nan"}:
                return float("nan")
            return float(t)
        except Exception:
            return t

    def _collect_symbols_from_editor(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for r in range(self.tbl_symbols.rowCount()):
            sym_item = self.tbl_symbols.item(r, 0)
            val_item = self.tbl_symbols.item(r, 1)
            sym = sym_item.text().strip() if sym_item else ""
            val = val_item.text().strip() if val_item else ""
            if not sym:
                continue
            out[sym] = self._parse_symbol_value(val)
        return out

    def _save_current_mapping_for_row(self, row: int) -> None:
        if row < 0 or row >= self.tbl_calcs.rowCount():
            return
        symbols = self._collect_symbols_from_editor()
        self._set_row_symbols(row, symbols)

    def _save_current_mapping(self) -> None:
        if self._active_row >= 0:
            self._save_current_mapping_for_row(self._active_row)
            QMessageBox.information(self, "Mapping Saved", "Symbol mapping applied to selected calculation.")

    def _validate_before_save(self) -> str | None:
        if self._active_row >= 0:
            self._save_current_mapping_for_row(self._active_row)
        aliases: List[str] = []
        for r in range(self.tbl_calcs.rowCount()):
            alias = self.tbl_calcs.item(r, 1).text().strip() if self.tbl_calcs.item(r, 1) else ""
            expr = self.tbl_calcs.item(r, 2).text().strip() if self.tbl_calcs.item(r, 2) else ""
            symbols = self._get_row_symbols(r)
            if not alias:
                return f"Row {r + 1}: Output Alias is required."
            if not expr:
                return f"Row {r + 1}: Expression is required."
            try:
                ast.parse(expr, mode="eval")
            except Exception as e:
                return f"Row {r + 1}: Invalid expression syntax ({e})."
            if not isinstance(symbols, dict):
                return f"Row {r + 1}: Symbols must be a mapping."
            for key in symbols.keys():
                if not str(key).strip():
                    return f"Row {r + 1}: Symbol names cannot be empty."
            aliases.append(alias)
        if len(aliases) != len(set(aliases)):
            return "Duplicate output aliases are not allowed."
        try:
            hz = float(self.cmb_rate.currentText().strip())
            if hz <= 0.0:
                return "Global update rate must be > 0."
        except Exception:
            return "Global update rate must be numeric."
        return None

    def _build_doc(self) -> Dict[str, Any]:
        doc: Dict[str, Any] = dict(self._cfg)
        doc["enabled"] = bool(doc.get("enabled", True))
        doc["recording_rate_hz"] = float(self.cmb_rate.currentText().strip())
        channels: List[Dict[str, Any]] = []
        for r in range(self.tbl_calcs.rowCount()):
            enabled = self.tbl_calcs.item(r, 0).checkState() == Qt.Checked if self.tbl_calcs.item(r, 0) else True
            alias = self.tbl_calcs.item(r, 1).text().strip() if self.tbl_calcs.item(r, 1) else ""
            expr = self.tbl_calcs.item(r, 2).text().strip() if self.tbl_calcs.item(r, 2) else ""
            unit = self.tbl_calcs.item(r, 3).text().strip() if self.tbl_calcs.item(r, 3) else ""
            symbols = self._get_row_symbols(r)
            channels.append(
                {
                    "alias": alias,
                    "expr": expr,
                    "symbols": symbols,
                    "unit": unit,
                    "enabled": enabled,
                }
            )
        doc["channels"] = channels
        return doc

    def _on_accept(self) -> None:
        err = self._validate_before_save()
        if err:
            QMessageBox.warning(self, "Calculated Channels", err)
            return
        doc = self._build_doc()
        try:
            import yaml  # type: ignore
            self._cfg_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        except Exception as e:
            QMessageBox.critical(self, "Write Error", f"Failed to save calculated_channels.yaml: {e}")
            return
        try:
            from src.core.ipc.bus import create_ui_control_push  # type: ignore
            ctrl = create_ui_control_push()
            if ctrl is not None:
                msg = json.dumps({"type": "reload_plugin", "plugin": "Calculated_Channels"}).encode("utf-8")
                ctrl["control_push"].send(msg)
        except Exception:
            pass
        self.accept()

