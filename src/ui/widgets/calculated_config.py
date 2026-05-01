# Author: T. Onkst | Date: 04292026
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from PySide6.QtCore import Qt, QSize
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except Exception:
    raise


class CalculatedConfigDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure Calculated Channels")
        self.resize(1200, 800)
        self._cfg_path = Path(__file__).resolve().parents[3] / "configs" / "calculated_channels.yaml"
        self._cfg: Dict[str, Any] = {}
        self._blocks: List[Dict[str, Any]] = []
        self._active_idx: int = -1
        self._init_ui()
        self._load()

    # ── UI setup ─────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)

        splitter = QSplitter(Qt.Horizontal, self)

        # Left panel: block list
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lbl_blocks = QLabel("Calculation Blocks")
        lbl_blocks.setStyleSheet("font-weight: 600; font-size: 13px;")
        lv.addWidget(lbl_blocks)

        self.lst_blocks = QListWidget(self)
        self.lst_blocks.currentRowChanged.connect(self._on_block_selected)  # type: ignore
        lv.addWidget(self.lst_blocks, 1)

        list_btns = QHBoxLayout()
        self.btn_add_block = QPushButton("Add")
        self.btn_add_block.clicked.connect(self._add_block)  # type: ignore
        self.btn_remove_block = QPushButton("Remove")
        self.btn_remove_block.clicked.connect(self._remove_block)  # type: ignore
        self.btn_dup_block = QPushButton("Duplicate")
        self.btn_dup_block.clicked.connect(self._duplicate_block)  # type: ignore
        list_btns.addWidget(self.btn_add_block)
        list_btns.addWidget(self.btn_remove_block)
        list_btns.addWidget(self.btn_dup_block)
        lv.addLayout(list_btns)

        recipe_btns = QHBoxLayout()
        self.btn_export = QPushButton("Export Recipe")
        self.btn_export.clicked.connect(self._export_recipe)  # type: ignore
        self.btn_import = QPushButton("Import Recipe")
        self.btn_import.clicked.connect(self._import_recipe)  # type: ignore
        recipe_btns.addWidget(self.btn_export)
        recipe_btns.addWidget(self.btn_import)
        lv.addLayout(recipe_btns)

        splitter.addWidget(left)

        # Right panel: block detail editor
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)

        # Header: name + enabled
        header = QHBoxLayout()
        header.addWidget(QLabel("Name:"))
        self.txt_name = QLineEdit()
        self.txt_name.setPlaceholderText("Block name (e.g. Estop Logic)")
        self.txt_name.textChanged.connect(self._on_name_changed)  # type: ignore
        header.addWidget(self.txt_name, 1)
        self.chk_enabled = QCheckBox("Enabled")
        self.chk_enabled.setChecked(True)
        header.addWidget(self.chk_enabled)
        rv.addLayout(header)

        # Input symbols
        sym_box = QGroupBox("Input Symbols")
        sv = QVBoxLayout(sym_box)
        self.tbl_symbols = QTableWidget(self)
        self.tbl_symbols.setColumnCount(2)
        self.tbl_symbols.setHorizontalHeaderLabels(["Symbol", "Input Channel Alias or Constant"])
        self.tbl_symbols.horizontalHeader().setStretchLastSection(True)
        self.tbl_symbols.setMaximumHeight(160)
        sv.addWidget(self.tbl_symbols)
        sym_btns = QHBoxLayout()
        btn_add_sym = QPushButton("Add Symbol")
        btn_add_sym.clicked.connect(lambda: self._add_symbol_row())  # type: ignore
        btn_rm_sym = QPushButton("Remove Symbol")
        btn_rm_sym.clicked.connect(self._remove_selected_symbol_rows)  # type: ignore
        sym_btns.addWidget(btn_add_sym)
        sym_btns.addWidget(btn_rm_sym)
        sym_btns.addStretch(1)
        sv.addLayout(sym_btns)
        rv.addWidget(sym_box)

        # Expression body
        body_box = QGroupBox("Expression Body")
        bv = QVBoxLayout(body_box)
        self.txt_body = QPlainTextEdit()
        mono = QFont("Consolas", 10)
        mono.setStyleHint(QFont.Monospace)
        self.txt_body.setFont(mono)
        self.txt_body.setPlaceholderText(
            "# One assignment per line: var = expression\n"
            "SoftShutdown = 1.0 if (softalarm == 1 and rpm == 0) else 0.0\n"
            "FuelLockoff = 0 if (Estop == 0) else 1"
        )
        self.txt_body.setMinimumHeight(150)
        bv.addWidget(self.txt_body, 1)
        rv.addWidget(body_box, 1)

        # Exposed outputs
        out_box = QGroupBox("Exposed Outputs")
        ov = QVBoxLayout(out_box)
        self.tbl_outputs = QTableWidget(self)
        self.tbl_outputs.setColumnCount(3)
        self.tbl_outputs.setHorizontalHeaderLabels(["Variable Name", "Output Alias", "Unit"])
        self.tbl_outputs.horizontalHeader().setStretchLastSection(True)
        self.tbl_outputs.setMaximumHeight(140)
        ov.addWidget(self.tbl_outputs)
        out_btns = QHBoxLayout()
        btn_add_out = QPushButton("Add Output")
        btn_add_out.clicked.connect(lambda: self._add_output_row())  # type: ignore
        btn_rm_out = QPushButton("Remove Output")
        btn_rm_out.clicked.connect(self._remove_selected_output_rows)  # type: ignore
        out_btns.addWidget(btn_add_out)
        out_btns.addWidget(btn_rm_out)
        out_btns.addStretch(1)
        ov.addLayout(out_btns)
        rv.addWidget(out_box)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        root.addWidget(splitter, 1)

        # Bottom: global rate + OK/Cancel
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Global update rate (Hz):"))
        self.cmb_rate = QComboBox(self)
        self.cmb_rate.setEditable(True)
        self.cmb_rate.addItems(["1", "5", "10", "20", "50", "100"])
        self.cmb_rate.setFixedWidth(80)
        bottom.addWidget(self.cmb_rate)
        bottom.addStretch(1)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        btns.accepted.connect(self._on_accept)  # type: ignore
        btns.rejected.connect(self.reject)  # type: ignore
        bottom.addWidget(btns)
        root.addLayout(bottom)

        self._set_detail_enabled(False)

    def _set_detail_enabled(self, enabled: bool) -> None:
        self.txt_name.setEnabled(enabled)
        self.chk_enabled.setEnabled(enabled)
        self.tbl_symbols.setEnabled(enabled)
        self.txt_body.setEnabled(enabled)
        self.tbl_outputs.setEnabled(enabled)

    # ── Load / save YAML ─────────────────────────────────────────────

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
        self._blocks = []
        self.lst_blocks.clear()
        for item in self._cfg.get("channels", []) or []:
            if not isinstance(item, dict):
                continue
            if "body" in item:
                blk = {
                    "name": str(item.get("name", "")),
                    "enabled": bool(item.get("enabled", True)),
                    "symbols": dict(item.get("symbols") or {}),
                    "body": str(item.get("body", "")),
                    "outputs": list(item.get("outputs") or []),
                }
            elif "expr" in item:
                from src.plugins.calculated import _migrate_legacy_channel
                blk = _migrate_legacy_channel(item)
            else:
                continue
            self._blocks.append(blk)
            self._add_list_item(blk)
        if self.lst_blocks.count() > 0:
            self.lst_blocks.setCurrentRow(0)

    def _add_list_item(self, blk: Dict[str, Any]) -> None:
        name = blk.get("name") or "(unnamed)"
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if blk.get("enabled", True) else Qt.Unchecked)
        self.lst_blocks.addItem(item)

    # ── Block selection ──────────────────────────────────────────────

    def _save_current_block(self) -> None:
        """Persist the right-panel state back into self._blocks."""
        idx = self._active_idx
        if idx < 0 or idx >= len(self._blocks):
            return
        blk = self._blocks[idx]
        blk["name"] = self.txt_name.text().strip()
        blk["enabled"] = self.chk_enabled.isChecked()
        blk["symbols"] = self._collect_symbols()
        blk["body"] = self.txt_body.toPlainText()
        blk["outputs"] = self._collect_outputs()
        li = self.lst_blocks.item(idx)
        if li is not None:
            li.setText(blk["name"] or "(unnamed)")
            li.setCheckState(Qt.Checked if blk["enabled"] else Qt.Unchecked)

    def _on_block_selected(self, row: int) -> None:
        if self._active_idx >= 0:
            self._save_current_block()
        self._active_idx = row
        if row < 0 or row >= len(self._blocks):
            self._clear_detail()
            self._set_detail_enabled(False)
            return
        self._set_detail_enabled(True)
        blk = self._blocks[row]
        self.txt_name.blockSignals(True)
        self.txt_name.setText(blk.get("name", ""))
        self.txt_name.blockSignals(False)
        self.chk_enabled.setChecked(blk.get("enabled", True))
        self._load_symbols(blk.get("symbols") or {})
        self.txt_body.setPlainText(blk.get("body", ""))
        self._load_outputs(blk.get("outputs") or [])

    def _clear_detail(self) -> None:
        self.txt_name.clear()
        self.chk_enabled.setChecked(True)
        self.tbl_symbols.setRowCount(0)
        self.txt_body.clear()
        self.tbl_outputs.setRowCount(0)

    def _on_name_changed(self, text: str) -> None:
        idx = self._active_idx
        if idx < 0 or idx >= self.lst_blocks.count():
            return
        li = self.lst_blocks.item(idx)
        if li is not None:
            li.setText(text.strip() or "(unnamed)")

    # ── Block list buttons ───────────────────────────────────────────

    def _add_block(self) -> None:
        blk: Dict[str, Any] = {
            "name": "New Block",
            "enabled": True,
            "symbols": {},
            "body": "",
            "outputs": [],
        }
        self._blocks.append(blk)
        self._add_list_item(blk)
        self.lst_blocks.setCurrentRow(self.lst_blocks.count() - 1)

    def _remove_block(self) -> None:
        idx = self.lst_blocks.currentRow()
        if idx < 0:
            return
        self._active_idx = -1
        self._blocks.pop(idx)
        self.lst_blocks.takeItem(idx)
        if self.lst_blocks.count() > 0:
            self.lst_blocks.setCurrentRow(min(idx, self.lst_blocks.count() - 1))
        else:
            self._clear_detail()
            self._set_detail_enabled(False)

    def _duplicate_block(self) -> None:
        idx = self.lst_blocks.currentRow()
        if idx < 0 or idx >= len(self._blocks):
            return
        self._save_current_block()
        import copy
        blk = copy.deepcopy(self._blocks[idx])
        blk["name"] = blk.get("name", "") + " (copy)"
        self._blocks.append(blk)
        self._add_list_item(blk)
        self.lst_blocks.setCurrentRow(self.lst_blocks.count() - 1)

    # ── Symbol table helpers ─────────────────────────────────────────

    def _load_symbols(self, symbols: Dict[str, Any]) -> None:
        self.tbl_symbols.setRowCount(0)
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

    def _collect_symbols(self) -> Dict[str, Any]:
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

    @staticmethod
    def _parse_symbol_value(text: str) -> Any:
        t = str(text).strip()
        if not t:
            return ""
        try:
            if t.lower() in {"nan", "+nan", "-nan"}:
                return float("nan")
            return float(t)
        except Exception:
            return t

    # ── Output table helpers ─────────────────────────────────────────

    def _load_outputs(self, outputs: List[Dict[str, Any]]) -> None:
        self.tbl_outputs.setRowCount(0)
        for o in outputs:
            if not isinstance(o, dict):
                continue
            self._add_output_row(
                str(o.get("var", "")),
                str(o.get("alias", "")),
                str(o.get("unit", "")),
            )

    def _add_output_row(self, var: str = "", alias: str = "", unit: str = "") -> None:
        r = self.tbl_outputs.rowCount()
        self.tbl_outputs.insertRow(r)
        self.tbl_outputs.setItem(r, 0, QTableWidgetItem(var))
        self.tbl_outputs.setItem(r, 1, QTableWidgetItem(alias))
        self.tbl_outputs.setItem(r, 2, QTableWidgetItem(unit))

    def _remove_selected_output_rows(self) -> None:
        rows = sorted({i.row() for i in self.tbl_outputs.selectedIndexes()}, reverse=True)
        for r in rows:
            self.tbl_outputs.removeRow(r)

    def _collect_outputs(self) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        for r in range(self.tbl_outputs.rowCount()):
            var_item = self.tbl_outputs.item(r, 0)
            alias_item = self.tbl_outputs.item(r, 1)
            unit_item = self.tbl_outputs.item(r, 2)
            var = var_item.text().strip() if var_item else ""
            alias = alias_item.text().strip() if alias_item else ""
            unit = unit_item.text().strip() if unit_item else ""
            if var or alias:
                out.append({"var": var, "alias": alias, "unit": unit})
        return out

    # ── Import / Export recipe ───────────────────────────────────────

    def _export_recipe(self) -> None:
        idx = self.lst_blocks.currentRow()
        if idx < 0 or idx >= len(self._blocks):
            QMessageBox.warning(self, "Export", "Select a block to export.")
            return
        self._save_current_block()
        blk = self._blocks[idx]
        recipe = {
            "name": blk.get("name", ""),
            "description": "",
            "version": "1.0",
            "symbols": blk.get("symbols", {}),
            "body": blk.get("body", ""),
            "outputs": blk.get("outputs", []),
        }
        suggested = (blk.get("name") or "recipe").replace(" ", "_") + ".json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Recipe", suggested, "JSON Files (*.json)"
        )
        if not path:
            return
        try:
            Path(path).write_text(
                json.dumps(recipe, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            QMessageBox.information(self, "Export", f"Recipe exported to {Path(path).name}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export: {e}")

    def _import_recipe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Recipe", "", "JSON Files (*.json);;All Files (*)"
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Failed to read recipe: {e}")
            return
        if not isinstance(data, dict):
            QMessageBox.warning(self, "Import Error", "Recipe file must contain a JSON object.")
            return
        blk: Dict[str, Any] = {
            "name": str(data.get("name", Path(path).stem)),
            "enabled": True,
            "symbols": dict(data.get("symbols") or {}),
            "body": str(data.get("body", "")),
            "outputs": list(data.get("outputs") or []),
        }
        self._blocks.append(blk)
        self._add_list_item(blk)
        self.lst_blocks.setCurrentRow(self.lst_blocks.count() - 1)

    # ── Validation ───────────────────────────────────────────────────

    def _validate_before_save(self) -> Optional[str]:
        if self._active_idx >= 0:
            self._save_current_block()
        all_aliases: List[str] = []
        for bi, blk in enumerate(self._blocks):
            name = blk.get("name", "").strip()
            if not name:
                return f"Block {bi + 1}: Name is required."
            body = str(blk.get("body", "")).strip()
            if not body:
                return f"Block '{name}': Expression body is empty."
            symbols = blk.get("symbols")
            if not isinstance(symbols, dict):
                return f"Block '{name}': Symbols must be a mapping."
            for key in (symbols or {}).keys():
                sk = str(key).strip()
                if not sk:
                    return f"Block '{name}': Symbol names cannot be empty."
                if not sk.isidentifier():
                    return f"Block '{name}': Symbol '{sk}' is not a valid Python identifier."
            assigned_vars: set = set()
            for line_num, line in enumerate(body.splitlines(), 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                varname, sep, rhs = line.partition("=")
                if not sep:
                    return f"Block '{name}' line {line_num}: expected 'var = expr' format."
                varname = varname.strip()
                rhs = rhs.strip()
                if not varname or not varname.isidentifier():
                    return f"Block '{name}' line {line_num}: '{varname}' is not a valid variable name."
                if not rhs:
                    return f"Block '{name}' line {line_num}: expression is empty."
                try:
                    ast.parse(rhs, mode="eval")
                except Exception as e:
                    return f"Block '{name}' line {line_num}: syntax error: {e}"
                assigned_vars.add(varname)
            outputs = blk.get("outputs") or []
            if not outputs:
                return f"Block '{name}': At least one exposed output is required."
            for oi, o in enumerate(outputs):
                if not isinstance(o, dict):
                    return f"Block '{name}' output {oi + 1}: invalid format."
                var = str(o.get("var", "")).strip()
                alias = str(o.get("alias", "")).strip()
                if not var:
                    return f"Block '{name}' output {oi + 1}: Variable Name is required."
                if not alias:
                    return f"Block '{name}' output {oi + 1}: Output Alias is required."
                if var not in assigned_vars:
                    return f"Block '{name}' output {oi + 1}: variable '{var}' is not assigned in the body."
                all_aliases.append(alias)
        if len(all_aliases) != len(set(all_aliases)):
            return "Duplicate output aliases across blocks are not allowed."
        try:
            hz = float(self.cmb_rate.currentText().strip())
            if hz <= 0.0:
                return "Global update rate must be > 0."
        except Exception:
            return "Global update rate must be numeric."
        return None

    # ── Build doc and save ───────────────────────────────────────────

    def _build_doc(self) -> Dict[str, Any]:
        doc: Dict[str, Any] = {}
        doc["enabled"] = bool(self._cfg.get("enabled", True))
        doc["recording_rate_hz"] = float(self.cmb_rate.currentText().strip())
        channels: List[Dict[str, Any]] = []
        for bi, blk in enumerate(self._blocks):
            li = self.lst_blocks.item(bi)
            enabled = li.checkState() == Qt.Checked if li is not None else blk.get("enabled", True)
            channels.append({
                "name": blk.get("name", ""),
                "enabled": enabled,
                "symbols": blk.get("symbols", {}),
                "body": blk.get("body", ""),
                "outputs": blk.get("outputs", []),
            })
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
