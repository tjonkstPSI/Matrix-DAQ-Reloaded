# Author: T. Onkst | Date: 03092026

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, List, Tuple

try:
	from PySide6.QtCore import Qt
	from PySide6.QtWidgets import (
		QDialog,
		QVBoxLayout,
		QTabWidget,
		QWidget,
		QTableWidget,
		QTableWidgetItem,
		QHeaderView,
		QAbstractItemView,
		QComboBox,
		QCheckBox,
		QHBoxLayout,
		QFormLayout,
		QLineEdit,
		QDialogButtonBox,
		QMessageBox,
	)
except Exception:
	raise

from .nidaq_alias_picker import AliasPickerDialog, validate_alias
from .nidaq_scaling_editor import ScalingEditorDialog, TempUnitPickerDialog

try:
	from src.plugins._nidaq_scaling import scaling_summary
except Exception:
	def scaling_summary(s: dict) -> str:
		return str(s.get("type", "none"))


class NiDaqConfigDialog(QDialog):
	"""Configurator for NI_DAQ plugin. Reads/writes configs/ni_daq.yaml.

	Inventory is sourced from configs/ni_daq.generated.yaml when present,
	falling back to currently configured channels in configs/ni_daq.yaml.
	"""

	AI_COLS: List[str] = ["Enabled", "Hardware", "Alias", "Unit", "Measurement", "Scaling"]
	DIG_COLS: List[str] = ["Enabled", "Hardware", "Alias"]
	AO_COLS: List[str] = ["Enabled", "Hardware", "Alias", "Unit"]

	def __init__(self, parent=None) -> None:
		super().__init__(parent)
		self.setWindowTitle("Configure NI DAQ Channels")
		self.resize(900, 600)
		self._root = Path(__file__).resolve().parents[3]
		self._cfg_dir = self._root / "configs"
		self._cfg_path = self._cfg_dir / "ni_daq.yaml"
		self._inv_path = self._cfg_dir / "ni_daq.generated.yaml"
		self._cfg: Dict[str, Any] = {}
		self._inventory: Dict[str, List[str]] = {"ai": [], "di": [], "do": [], "ao": []}
		self._ai_scaling: Dict[int, Dict[str, Any]] = {}
		self._telemetry_getter = None
		try:
			from src.core.ipc.bus import create_ui_subscriber  # type: ignore
			sockets = create_ui_subscriber()
			if sockets is not None:
				sub = sockets.telemetry_sub
				self._telem_sub = sub
				self._telem_cache: Dict[str, Any] = {}
				def _getter() -> Dict[str, Any]:
					import zmq  # type: ignore
					try:
						while True:
							parts = self._telem_sub.recv_multipart(zmq.NOBLOCK)
							if len(parts) >= 2:
								import json
								self._telem_cache = json.loads(parts[1]).get("values", {})
					except Exception:
						pass
					return dict(self._telem_cache)
				self._telemetry_getter = _getter
		except Exception:
			pass
		self._init_ui()
		self._load()

	def _init_ui(self) -> None:
		v = QVBoxLayout(self)
		self.tabs = QTabWidget(self)
		v.addWidget(self.tabs)
		# Tables per type
		self.tbl_ai = self._make_table(self.AI_COLS)
		self.tbl_di = self._make_table(self.DIG_COLS)
		self.tbl_do = self._make_table(self.DIG_COLS)
		self.tbl_ao = self._make_table(self.AO_COLS)
		self.tabs.addTab(self._wrap(self.tbl_ai), "Analog Input")
		self.tabs.addTab(self._wrap(self.tbl_di), "Digital Input")
		self.tabs.addTab(self._wrap(self.tbl_do), "Digital Output")
		self.tabs.addTab(self._wrap(self.tbl_ao), "Analog Output")
		# Sampling tab
		samp = QWidget(self)
		fl = QFormLayout(samp)
		self.txt_rate = QLineEdit(samp)
		self.chk_wd = QCheckBox("Enable Watchdog", samp)
		self.cmb_wd_mode = QComboBox(samp); self.cmb_wd_mode.addItems(["driver", "digital_loopback"]) 
		self.txt_wd_timeout = QLineEdit(samp)
		fl.addRow("Recording rate (Hz)", self.txt_rate)
		fl.addRow("Watchdog", self.chk_wd)
		fl.addRow("Watchdog mode", self.cmb_wd_mode)
		fl.addRow("Watchdog timeout (ms)", self.txt_wd_timeout)
		self.tabs.addTab(samp, "Sampling")
		# Buttons
		btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
		btns.accepted.connect(self._on_accept)  # type: ignore
		btns.rejected.connect(self.reject)  # type: ignore
		v.addWidget(btns)

	def _wrap(self, w: QWidget) -> QWidget:
		box = QWidget(self)
		lay = QVBoxLayout(box)
		lay.addWidget(w)
		return box

	def _make_table(self, cols: List[str]) -> QTableWidget:
		tbl = QTableWidget(0, len(cols), self)
		tbl.setHorizontalHeaderLabels(cols)
		h = tbl.horizontalHeader()
		h.setSectionResizeMode(QHeaderView.Stretch)
		tbl.verticalHeader().setVisible(False)
		tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
		tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
		tbl.cellDoubleClicked.connect(lambda r, c, t=tbl: self._on_cell_double_click(t, r, c))  # type: ignore
		return tbl

	# Data I/O
	def _load(self) -> None:
		self._cfg = self._read_yaml(self._cfg_path)
		# Prefer live discovery first
		live_inv: Dict[str, Any] = {}
		try:
			from src.tools.nidaq_discover import discover_system  # type: ignore
			live_inv = discover_system() or {}
		except Exception:
			live_inv = {}
		if isinstance(live_inv, dict) and live_inv.get("devices"):
			inv = live_inv
		else:
			# Fallback to generated snapshot if present
			inv = self._read_yaml(self._inv_path)
		# If we have a structured inventory, use it to populate tables
		if isinstance(inv, dict) and inv.get("devices"):
			ai: List[str] = []; di: List[str] = []; do: List[str] = []; ao: List[str] = []
			for d in inv.get("devices", []):
				ai.extend([str(x) for x in (d.get("ai") or [])])
				di.extend([str(x) for x in (d.get("di") or [])])
				do.extend([str(x) for x in (d.get("do") or [])])
				ao.extend([str(x) for x in (d.get("ao") or [])])
			self._inventory = {"ai": ai, "di": di, "do": do, "ao": ao}
		else:
			# Last resort: build inventory from current config channels
			ai = [str(c.get("phys")) for c in (self._cfg.get("channels", {}).get("ai_voltage") or []) if c.get("phys")]
			ai.extend([str(c.get("phys")) for c in (self._cfg.get("channels", {}).get("ai_temp") or []) if c.get("phys")])
			di = [str(c.get("phys")) for c in (self._cfg.get("channels", {}).get("di") or []) if c.get("phys")]
			do = [str(c.get("phys")) for c in (self._cfg.get("channels", {}).get("do") or []) if c.get("phys")]
			ao = [str(c.get("phys")) for c in (self._cfg.get("channels", {}).get("ao") or []) if c.get("phys")]
			self._inventory = {"ai": ai, "di": di, "do": do, "ao": ao}
		# Compare inventory to current config; if mismatch, prompt to regenerate defaults
		if not self._inventory_matches_current_cfg():
			resp = QMessageBox.question(self, "NI DAQ Inventory Changed", "Detected hardware inventory mismatch with current ni_daq.yaml. Regenerate default config from inventory?", QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
			if resp == QMessageBox.Yes:
				self._regenerate_defaults_from_inventory()
				self._cfg = self._read_yaml(self._cfg_path)
		self._populate_tables()
		# Populate sampling/watchdog defaults
		try:
			rate = float(self._cfg.get("recording_rate_hz", 10.0))
			self.txt_rate.setText(str(rate))
		except Exception:
			self.txt_rate.setText("10")
		wd = self._cfg.get("watchdog") or {}
		self.chk_wd.setChecked(bool(wd.get("enabled", False)))
		self.cmb_wd_mode.setCurrentText(str(wd.get("mode", "driver")))
		self.txt_wd_timeout.setText(str(wd.get("timeout_ms", "1000")))

	def _populate_tables(self) -> None:
		# Helper maps from current config
		ch = self._cfg.get("channels", {}) or {}
		ai_cfg = {str(c.get("phys")): c for c in (ch.get("ai_voltage") or []) if c.get("phys")}
		ai_cfg.update({str(c.get("phys")): c for c in (ch.get("ai_temp") or []) if c.get("phys")})
		di_cfg = {str(c.get("phys")): c for c in (ch.get("di") or []) if c.get("phys")}
		do_cfg = {str(c.get("phys")): c for c in (ch.get("do") or []) if c.get("phys")}
		ao_cfg = {str(c.get("phys")): c for c in (ch.get("ao") or []) if c.get("phys")}
		self._ai_scaling.clear()
		self.tbl_ai.setRowCount(len(self._inventory["ai"]))
		for row, phys in enumerate(self._inventory["ai"]):
			cfg = ai_cfg.get(phys, {})
			enabled = bool(cfg.get("enabled", False))
			alias = str(cfg.get("alias", "")) if enabled else ""
			sc = dict(cfg.get("scaling") or {})
			unit = (sc.get("unit") or cfg.get("unit", "")) if enabled else ""
			meas = "Voltage"
			if cfg.get("sensor"):
				stype = str((cfg.get("sensor") or {}).get("type", "TC")).upper()
				meas = "RTD" if stype == "RTD" else "TC"
				if not sc.get("type"):
					sc = {"type": "none", "unit": unit or "C"}
			else:
				if not sc.get("type"):
					sc["type"] = "none"
			self._ai_scaling[row] = sc
			self._set_checkbox(self.tbl_ai, row, 0, enabled)
			hw_item = QTableWidgetItem(phys)
			hw_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
			self.tbl_ai.setItem(row, 1, hw_item)
			self.tbl_ai.setItem(row, 2, QTableWidgetItem(alias))
			self.tbl_ai.setItem(row, 3, QTableWidgetItem(unit))
			cb = QComboBox(self.tbl_ai); cb.addItems(["Voltage", "TC", "RTD", "Current"]); cb.setCurrentText(meas)
			self.tbl_ai.setCellWidget(row, 4, cb)
			scale_item = QTableWidgetItem(scaling_summary(sc))
			scale_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
			self.tbl_ai.setItem(row, 5, scale_item)
		# DI
		self.tbl_di.setRowCount(len(self._inventory["di"]))
		for row, phys in enumerate(self._inventory["di"]):
			cfg = di_cfg.get(phys, {})
			enabled = bool(cfg.get("enabled", False))
			alias = str(cfg.get("alias", "")) if enabled else ""
			self._set_checkbox(self.tbl_di, row, 0, enabled)
			self.tbl_di.setItem(row, 1, QTableWidgetItem(phys)); self.tbl_di.item(row, 1).setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
			self.tbl_di.setItem(row, 2, QTableWidgetItem(alias))
		# DO
		self.tbl_do.setRowCount(len(self._inventory["do"]))
		for row, phys in enumerate(self._inventory["do"]):
			cfg = do_cfg.get(phys, {})
			enabled = bool(cfg.get("enabled", False))
			alias = str(cfg.get("alias", "")) if enabled else ""
			self._set_checkbox(self.tbl_do, row, 0, enabled)
			self.tbl_do.setItem(row, 1, QTableWidgetItem(phys)); self.tbl_do.item(row, 1).setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
			self.tbl_do.setItem(row, 2, QTableWidgetItem(alias))
		# AO
		self.tbl_ao.setRowCount(len(self._inventory["ao"]))
		for row, phys in enumerate(self._inventory["ao"]):
			cfg = ao_cfg.get(phys, {})
			enabled = bool(cfg.get("enabled", False))
			alias = str(cfg.get("alias", "")) if enabled else ""
			unit = ((cfg.get("scaling") or {}).get("unit") or cfg.get("unit", "")) if enabled else ""
			self._set_checkbox(self.tbl_ao, row, 0, enabled)
			self.tbl_ao.setItem(row, 1, QTableWidgetItem(phys)); self.tbl_ao.item(row, 1).setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
			self.tbl_ao.setItem(row, 2, QTableWidgetItem(alias))
			self.tbl_ao.setItem(row, 3, QTableWidgetItem(unit))

	def _on_cell_double_click(self, table: QTableWidget, row: int, col: int) -> None:
		if table is self.tbl_ai:
			if col == 2:
				self._open_alias_picker(table, row, col)
			elif col == 5:
				self._open_scaling_editor(row)
			elif col == 3:
				pass
		elif table in (self.tbl_di, self.tbl_do, self.tbl_ao):
			if col == 2:
				self._open_alias_picker(table, row, col)

	def _open_alias_picker(self, table: QTableWidget, row: int, col: int) -> None:
		current = table.item(row, col).text().strip() if table.item(row, col) else ""
		dlg = AliasPickerDialog(parent=self, current_alias=current)
		if dlg.exec() == QDialog.Accepted and dlg.selected_alias:
			table.setItem(row, col, QTableWidgetItem(dlg.selected_alias))

	def _open_scaling_editor(self, row: int) -> None:
		meas_cb = self.tbl_ai.cellWidget(row, 4)
		meas = meas_cb.currentText() if isinstance(meas_cb, QComboBox) else "Voltage"
		alias = self.tbl_ai.item(row, 2).text().strip() if self.tbl_ai.item(row, 2) else ""
		if meas in ("TC", "RTD"):
			current_unit = self.tbl_ai.item(row, 3).text().strip() if self.tbl_ai.item(row, 3) else "C"
			dlg = TempUnitPickerDialog(parent=self, current_unit=current_unit or "C")
			if dlg.exec() == QDialog.Accepted:
				self.tbl_ai.setItem(row, 3, QTableWidgetItem(dlg.selected_unit))
				sc = {"type": "none", "unit": dlg.selected_unit}
				self._ai_scaling[row] = sc
				self.tbl_ai.setItem(row, 5, QTableWidgetItem(scaling_summary(sc)))
		else:
			current_sc = dict(self._ai_scaling.get(row) or {"type": "none", "unit": "V"})
			dlg = ScalingEditorDialog(
				parent=self,
				current_scaling=current_sc,
				channel_alias=alias,
				telemetry_getter=self._telemetry_getter,
			)
			if dlg.exec() == QDialog.Accepted and dlg.result_scaling:
				sc = dlg.result_scaling
				self._ai_scaling[row] = sc
				self.tbl_ai.setItem(row, 3, QTableWidgetItem(sc.get("unit", "")))
				scale_item = QTableWidgetItem(scaling_summary(sc))
				scale_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
				self.tbl_ai.setItem(row, 5, scale_item)

	def _set_checkbox(self, table: QTableWidget, row: int, col: int, checked: bool) -> None:
		item = QTableWidgetItem()
		item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
		item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
		table.setItem(row, col, item)

	def _read_yaml(self, path: Path) -> Dict[str, Any]:
		try:
			import yaml  # type: ignore
			if not path.exists():
				return {}
			return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
		except Exception:
			return {}

	def _inventory_matches_current_cfg(self) -> bool:
		try:
			ch = (self._cfg.get("channels") or {})
			cfg_ai = set([str(c.get("phys")) for c in (ch.get("ai_voltage") or []) if c.get("phys")]) | set([str(c.get("phys")) for c in (ch.get("ai_temp") or []) if c.get("phys")])
			cfg_di = set([str(c.get("phys")) for c in (ch.get("di") or []) if c.get("phys")])
			cfg_do = set([str(c.get("phys")) for c in (ch.get("do") or []) if c.get("phys")])
			cfg_ao = set([str(c.get("phys")) for c in (ch.get("ao") or []) if c.get("phys")])
			inv_ai = set(self._inventory.get("ai", []))
			inv_di = set(self._inventory.get("di", []))
			inv_do = set(self._inventory.get("do", []))
			inv_ao = set(self._inventory.get("ao", []))
			return cfg_ai == inv_ai and cfg_di == inv_di and cfg_do == inv_do and cfg_ao == inv_ao
		except Exception:
			return True

	def _regenerate_defaults_from_inventory(self) -> None:
		# Use discover template generator to produce defaults that include ALL inventory
		try:
			from src.tools.nidaq_discover import discover_system, generate_yaml_template  # type: ignore
		except Exception:
			return
		try:
			import yaml  # type: ignore
			inv = {"devices": []}
			# Prefer live discovery first; fall back to generated snapshot
			live = {}
			try:
				live = discover_system() or {}
			except Exception:
				live = {}
			if isinstance(live, dict) and live.get("devices"):
				inv = live
			else:
				gen = self._read_yaml(self._inv_path)
				if isinstance(gen, dict) and gen.get("devices"):
					inv = gen
			text = generate_yaml_template(inv)
			new_cfg = yaml.safe_load(text) or {}
			# Merge preserved top-level config blocks from existing YAML
			for key in ("acquisition", "health", "decimation", "watchdog"):
				if key in (self._cfg or {}):
					new_cfg[key] = self._cfg.get(key)
			# Preserve existing recording_rate_hz if present
			if isinstance(self._cfg, dict) and "recording_rate_hz" in self._cfg:
				new_cfg["recording_rate_hz"] = self._cfg.get("recording_rate_hz")
			# Merge per-channel details (aliases, enable flags, units, and sensor for TC/RTD)
			old_ch = (self._cfg.get("channels") or {})
			old_ai_v = {str(c.get("phys")): c for c in (old_ch.get("ai_voltage") or []) if c.get("phys")}
			old_ai_t = {str(c.get("phys")): c for c in (old_ch.get("ai_temp") or []) if c.get("phys")}
			old_di = {str(c.get("phys")): c for c in (old_ch.get("di") or []) if c.get("phys")}
			old_do = {str(c.get("phys")): c for c in (old_ch.get("do") or []) if c.get("phys")}
			old_ao = {str(c.get("phys")): c for c in (old_ch.get("ao") or []) if c.get("phys")}
			new_ch = new_cfg.get("channels") or {}
			for key in ("ai_voltage", "ai_temp", "di", "do", "ao"):
				if key not in new_ch:
					new_ch[key] = []
			# Merge AI Voltage
			merged_ai_v = []
			for c in (new_ch.get("ai_voltage") or []):
				phys = str(c.get("phys", ""))
				oc = old_ai_v.get(phys)
				if oc:
					alias = oc.get("alias", c.get("alias"))
					scaling = oc.get("scaling") or c.get("scaling") or {"unit": (oc.get("unit") or c.get("unit") or "")}
					enabled = oc.get("enabled", c.get("enabled"))
					c.update({"alias": alias, "enabled": enabled, "scaling": scaling})
				merged_ai_v.append(c)
			new_ch["ai_voltage"] = merged_ai_v
			# Merge AI Temp
			merged_ai_t = []
			for c in (new_ch.get("ai_temp") or []):
				phys = str(c.get("phys", ""))
				oc = old_ai_t.get(phys)
				if oc:
					alias = oc.get("alias", c.get("alias"))
					unit = oc.get("unit", c.get("unit", "C"))
					sensor = dict(oc.get("sensor") or {})
					# Ensure required fields and defaults
					stype = str(sensor.get("type", c.get("sensor", {}).get("type", "TC")).upper())
					sensor["type"] = stype
					if stype == "RTD":
						sensor.setdefault("subtype", "PT100")
						sensor.setdefault("wires", 3)
						sensor.setdefault("excitation_current_a", 0.001)
					else:
						sensor.setdefault("subtype", "K")
					c.update({"alias": alias, "unit": unit, "sensor": sensor, "enabled": oc.get("enabled", c.get("enabled", False))})
				else:
					# If template provided temp entries without sensor, add safe defaults
					s = dict(c.get("sensor") or {})
					stype = str(s.get("type", "TC")).upper()
					s["type"] = stype
					if stype == "RTD":
						s.setdefault("subtype", "PT100"); s.setdefault("wires", 3); s.setdefault("excitation_current_a", 0.001)
					else:
						s.setdefault("subtype", "K")
					c["sensor"] = s
				merged_ai_t.append(c)
			new_ch["ai_temp"] = merged_ai_t
			# Merge DI/DO/AO aliases and enabled
			def _merge_simple(lst, old_map):
				res = []
				for c in (lst or []):
					phys = str(c.get("phys", ""))
					oc = old_map.get(phys)
					if oc:
						c.update({"alias": oc.get("alias", c.get("alias")), "enabled": oc.get("enabled", c.get("enabled"))})
					res.append(c)
				return res
			new_ch["di"] = _merge_simple(new_ch.get("di"), old_di)
			new_ch["do"] = _merge_simple(new_ch.get("do"), old_do)
			new_ch["ao"] = _merge_simple(new_ch.get("ao"), old_ao)
			new_cfg["channels"] = new_ch
			self._cfg_path.write_text(yaml.safe_dump(new_cfg, sort_keys=False), encoding="utf-8")
		except Exception:
			pass

	def _on_accept(self) -> None:
		def _collect_all(table: QTableWidget, alias_col: int) -> List[Tuple[str, str, bool, str, str]]:
			rows: List[Tuple[str, str, bool, str, str]] = []
			for r in range(table.rowCount()):
				it = table.item(r, 0)
				en = bool(it and it.checkState() == Qt.Checked)
				phys = table.item(r, 1).text().strip() if table.item(r, 1) else ""
				alias = table.item(r, alias_col).text().strip() if table.item(r, alias_col) else ""
				unit = table.item(r, 3).text().strip() if table.columnCount() > 3 and table.item(r, 3) else ""
				meas = ""
				if table is self.tbl_ai:
					cb = table.cellWidget(r, 4)
					if isinstance(cb, QComboBox):
						meas = cb.currentText()
				rows.append((phys, alias, en, unit, meas))
			return rows
		ai_rows = _collect_all(self.tbl_ai, 2)
		di_rows = _collect_all(self.tbl_di, 2)
		do_rows = _collect_all(self.tbl_do, 2)
		ao_rows = _collect_all(self.tbl_ao, 2)
		missing = [phys for (phys, alias, en, _, _) in (ai_rows + di_rows + do_rows + ao_rows) if en and not alias]
		if missing:
			QMessageBox.warning(self, "Missing Aliases", f"Please set an alias for: {', '.join(missing[:10])}{'...' if len(missing)>10 else ''}")
			return
		invalid = [
			f"{alias} ({phys})"
			for (phys, alias, en, _, _) in (ai_rows + di_rows + do_rows + ao_rows)
			if en and alias and not validate_alias(alias)
		]
		if invalid:
			QMessageBox.warning(
				self, "Invalid Aliases",
				f"The following aliases do not match the naming convention:\n{chr(10).join(invalid[:10])}{'...' if len(invalid)>10 else ''}"
			)
			return
		# Build updated blocks to merge back into existing YAML (preserve unknown/top-level keys)
		updated: Dict[str, Any] = {}
		# Mode (preserve existing)
		updated["mode"] = str(self._cfg.get("mode", "real"))
		# Recording rate
		try:
			updated["recording_rate_hz"] = float(self.txt_rate.text().strip())
		except Exception:
			updated["recording_rate_hz"] = self._cfg.get("recording_rate_hz", 10.0)
		# Channels with merge-on-save for existing RTD/TC sensor fields
		chs: Dict[str, Any] = {"ai_voltage": [], "ai_temp": [], "di": [], "do": [], "ao": []}
		old_ch = (self._cfg.get("channels") or {})
		old_ai_v = {str(c.get("phys")): c for c in (old_ch.get("ai_voltage") or []) if c.get("phys")}
		old_ai_t = {str(c.get("phys")): c for c in (old_ch.get("ai_temp") or []) if c.get("phys")}
		for row_idx, (phys, alias, en, unit, meas) in enumerate(ai_rows):
			m = meas or "Voltage"
			if m == "RTD":
				base = dict(old_ai_t.get(phys) or {})
				sensor = dict(base.get("sensor") or {})
				sensor["type"] = "RTD"
				sensor.setdefault("subtype", "PT100")
				sensor.setdefault("wires", 3)
				sensor.setdefault("excitation_current_a", 0.001)
				sc = dict(self._ai_scaling.get(row_idx) or {})
				item = {"phys": phys, "alias": alias or base.get("alias", ""), "enabled": en if en is not None else base.get("enabled", False), "unit": sc.get("unit") or unit or base.get("unit", "C"), "sensor": sensor}
				chs["ai_temp"].append(item)
			elif m == "TC":
				base = dict(old_ai_t.get(phys) or {})
				sensor = dict(base.get("sensor") or {})
				sensor["type"] = "TC"
				sensor.setdefault("subtype", "K")
				sc = dict(self._ai_scaling.get(row_idx) or {})
				item = {"phys": phys, "alias": alias or base.get("alias", ""), "enabled": en if en is not None else base.get("enabled", False), "unit": sc.get("unit") or unit or base.get("unit", "C"), "sensor": sensor}
				chs["ai_temp"].append(item)
			else:
				base = dict(old_ai_v.get(phys) or {})
				sc = dict(self._ai_scaling.get(row_idx) or base.get("scaling") or {})
				if unit and not sc.get("unit"):
					sc["unit"] = unit
				if not sc.get("type"):
					sc["type"] = "none"
				item = {"phys": phys, "alias": alias or base.get("alias", ""), "enabled": en if en is not None else base.get("enabled", False), "scaling": sc}
				chs["ai_voltage"].append(item)
		for phys, alias, en, _, _ in di_rows:
			chs["di"].append({"phys": phys, "alias": alias, "enabled": en, "initial": 0})
		for phys, alias, en, _, _ in do_rows:
			chs["do"].append({"phys": phys, "alias": alias, "enabled": en, "initial": 0})
		for phys, alias, en, unit, _ in ao_rows:
			chs["ao"].append({"phys": phys, "alias": alias, "enabled": en, "scaling": {"unit": unit}, "range_v": {"min": 0.0, "max": 10.0}})
		updated["channels"] = chs
		# Watchdog (merge with existing so optional keys like expir_states persist)
		wd: Dict[str, Any] = {"enabled": bool(self.chk_wd.isChecked()), "mode": self.cmb_wd_mode.currentText()}
		try:
			wd["timeout_ms"] = int(float(self.txt_wd_timeout.text().strip()))
		except Exception:
			pass
		old_wd = dict(self._cfg.get("watchdog") or {})
		old_wd.update(wd)
		updated["watchdog"] = old_wd
		# Merge into existing YAML, preserving acquisition/health/decimation and any other unknown keys
		new_cfg = dict(self._cfg)
		for k, v in updated.items():
			new_cfg[k] = v
		# Write YAML
		try:
			import yaml  # type: ignore
			self._cfg_path.write_text(yaml.safe_dump(new_cfg, sort_keys=False), encoding="utf-8")
		except Exception as e:
			QMessageBox.critical(self, "Write Error", f"Failed to save ni_daq.yaml: {e}")
			return
		# Notify Core to reload NI_DAQ so UI can reflect updated channels
		try:
			from src.core.ipc.bus import create_ui_control_push  # type: ignore
			ctrl = create_ui_control_push()
			if ctrl is not None:
				import json as _json
				msg = _json.dumps({"type": "reload_plugin", "plugin": "NI_DAQ"}).encode("utf-8")
				ctrl["control_push"].send(msg)
		except Exception:
			pass
		self.accept()


