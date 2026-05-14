# Author: T. Onkst | Date: 03092026
from __future__ import annotations

import json
import math
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List

import sys

from .can_interfaces import discover_can_channels

try:
    from ...plugins._ccp_a2l import parse_a2l_daq_lists, _canonical_poll_tier, is_daq_tier
except Exception:
    parse_a2l_daq_lists = None  # type: ignore[assignment]
    _canonical_poll_tier = None  # type: ignore[assignment]
    is_daq_tier = None  # type: ignore[assignment]


def _session_key_store() -> dict:
    """Process-global session access-key store (cleared on app exit)."""
    if not hasattr(sys, "_matrix_ccp_session_keys"):
        sys._matrix_ccp_session_keys = {}  # type: ignore[attr-defined]
    return sys._matrix_ccp_session_keys  # type: ignore[attr-defined]

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QGroupBox,
        QHeaderView,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QSpinBox,
        QStyledItemDelegate,
        QTabBar,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QVBoxLayout,
    )
except Exception:
    raise


_TIER_OPTIONS = ["High Poll", "Low Poll", "DAQ 1ms", "DAQ 10ms", "DAQ 50ms", "DAQ 100ms"]
_SHORT_UP_DISPLAY = {"high": "High Poll", "low": "Low Poll"}
_DAQ_DISPLAY = {"1ms": "DAQ 1ms", "10ms": "DAQ 10ms", "50ms": "DAQ 50ms", "100ms": "DAQ 100ms"}
_DISPLAY_TO_CANONICAL = {
    "High Poll": "high", "Low Poll": "low",
    "DAQ 1ms": "1ms", "DAQ 10ms": "10ms", "DAQ 50ms": "50ms", "DAQ 100ms": "100ms",
}
_CANONICAL_TO_DISPLAY = {v: k for k, v in _DISPLAY_TO_CANONICAL.items()}


class TierDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):  # type: ignore
        combo = QComboBox(parent)
        combo.addItems(_TIER_OPTIONS)
        combo.setFrame(False)
        return combo

    def setEditorData(self, editor, index):  # type: ignore
        value = str(index.data(Qt.DisplayRole) or "High Poll").strip()
        idx = editor.findText(value, Qt.MatchFixedString)
        if idx < 0:
            display = _CANONICAL_TO_DISPLAY.get(value, "High Poll")
            idx = editor.findText(display, Qt.MatchFixedString)
        editor.setCurrentIndex(idx if idx >= 0 else 0)

    def setModelData(self, editor, model, index):  # type: ignore
        model.setData(index, editor.currentText(), Qt.EditRole)

    def updateEditorGeometry(self, editor, option, index):  # type: ignore
        editor.setGeometry(option.rect)


class CCPTestDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CCP Connection Test")
        self.resize(600, 350)
        layout = QVBoxLayout(self)
        self.txt_output = QTextEdit(self)
        self.txt_output.setReadOnly(True)
        layout.addWidget(self.txt_output)
        btns = QDialogButtonBox(QDialogButtonBox.Ok, parent=self)
        btns.accepted.connect(self.accept)  # type: ignore
        layout.addWidget(btns)

    def append(self, line: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.txt_output.append(f"[{ts}] {line}")


class CCPConfigDialog(QDialog):
    _DEFAULT_PRIORITY = "Low Poll"
    _MAX_DISPLAYED_A2L_ROWS = 5000
    _A2L_NAME_CACHE: Dict[tuple[str, int, int], Dict[str, Dict[str, Any]]] = {}
    _DAQ_DTO_PAYLOAD_BYTES = 7
    _MAX_ODT_UTILIZATION_PCT = 90

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure CCP")
        self.resize(620, 680)
        self._cfg_path = Path(__file__).resolve().parents[3] / "configs" / "ccp.yaml"
        self._cfg: Dict[str, Any] = {}
        self._devices: List[Dict[str, Any]] = []
        self._active_device_idx: int = -1
        self._test_run_id: str = ""
        self._available_can_channels = discover_can_channels()
        self._sub = None
        self._init_ui()
        self._load()
        self._init_status_subscriber()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        tabs_row = QHBoxLayout()
        self.tabs = QTabBar(self)
        self.tabs.currentChanged.connect(self._on_tab_changed)  # type: ignore
        self.btn_add_device = QPushButton("Add Device", self)
        self.btn_add_device.clicked.connect(self._add_device)  # type: ignore
        self.btn_remove_device = QPushButton("Remove Device", self)
        self.btn_remove_device.clicked.connect(self._remove_device)  # type: ignore
        tabs_row.addWidget(self.tabs, 1)
        tabs_row.addWidget(self.btn_add_device)
        tabs_row.addWidget(self.btn_remove_device)
        root.addLayout(tabs_row)

        form = QFormLayout()
        form.setVerticalSpacing(4)
        self.txt_device_name = QLineEdit(self)
        self.txt_device_name.textEdited.connect(self._on_device_name_changed)  # type: ignore
        self.cmb_role = QComboBox(self)
        self.cmb_role.addItems(["Primary", "Secondary"])
        self.cmb_interface = QComboBox(self)
        self.cmb_interface.setEditable(False)
        self.cmb_interface.addItem("")
        self.cmb_interface.addItems(self._available_can_channels)
        self.cmb_interface.setToolTip("Detected NI-XNET CAN interfaces. Select a discovered interface before saving.")
        self.cmb_baudrate = QComboBox(self)
        self.cmb_baudrate.addItems(["125000", "250000", "500000", "1000000"])
        self.txt_access_key = QLineEdit(self)
        self.txt_access_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_access_key.setPlaceholderText("Hex key (session-only, not saved to disk)")
        self.txt_prefix = QLineEdit(self)
        form.addRow("Device Name", self.txt_device_name)
        form.addRow("ECM Role", self.cmb_role)
        form.addRow("CAN interface", self.cmb_interface)
        form.addRow("Baudrate", self.cmb_baudrate)
        form.addRow("Access key (hex)", self.txt_access_key)
        form.addRow("Naming prefix", self.txt_prefix)
        root.addLayout(form)

        self.txt_a2l_path = QLineEdit(self)
        btn_browse = QPushButton("Browse A2L...", self)
        btn_browse.clicked.connect(self._browse_a2l)  # type: ignore
        btn_load_channels = QPushButton("Load Channels from A2L", self)
        btn_load_channels.clicked.connect(lambda *_: self._reload_channels_from_a2l(force_refresh=True))  # type: ignore
        root.addWidget(QLabel("A2L path"))
        root.addWidget(self.txt_a2l_path)
        browse_row = QHBoxLayout()
        browse_row.addWidget(btn_browse)
        browse_row.addWidget(btn_load_channels)
        root.addLayout(browse_row)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Channel filter"))
        self.chk_show_selected = QCheckBox("Show selected channels only", self)
        self.chk_show_selected.toggled.connect(self._apply_channel_filter)  # type: ignore
        filter_row.addStretch(1)
        filter_row.addWidget(self.chk_show_selected)
        root.addLayout(filter_row)
        self.txt_filter = QLineEdit(self)
        self.txt_filter.setPlaceholderText("Type to filter channel names...")
        self.txt_filter.textChanged.connect(self._apply_channel_filter)  # type: ignore
        root.addWidget(self.txt_filter)

        root.addWidget(QLabel("A2L channels"))
        self.table_channels = QTableWidget(self)
        self.table_channels.setColumnCount(5)
        self.table_channels.setHorizontalHeaderLabels(["Use", "Measurement", "Unit", "Tier", "Bytes"])
        self.table_channels.setMinimumHeight(200)
        self.table_channels.itemChanged.connect(self._on_channel_item_changed)  # type: ignore
        header = self.table_channels.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.resizeSection(1, 200)
        header.resizeSection(3, 90)
        header.setStretchLastSection(False)
        self._tier_delegate = TierDelegate(self.table_channels)
        self.table_channels.setItemDelegateForColumn(3, self._tier_delegate)
        root.addWidget(self.table_channels)
        self.lbl_channel_status = QLabel("", self)
        root.addWidget(self.lbl_channel_status)

        cap_group = QGroupBox("Channel Allocation", self)
        self._cap_group = cap_group
        self._cap_layout = QVBoxLayout(cap_group)
        self._cap_layout.setContentsMargins(6, 6, 6, 6)
        self._cap_layout.setSpacing(3)
        self._sup_summary = QLabel("", self)
        self._cap_layout.addWidget(self._sup_summary)
        self._daq_header = QLabel("DAQ Tier Capacity:", self)
        self._daq_header.setStyleSheet("font-weight: bold; margin-top: 4px;")
        self._cap_layout.addWidget(self._daq_header)
        self._tier_bars: Dict[str, QProgressBar] = {}
        self._tier_labels: Dict[str, QLabel] = {}
        self._tier_stats: Dict[str, QLabel] = {}
        for tier in ["1ms", "10ms", "50ms", "100ms"]:
            row = QHBoxLayout()
            row.setSpacing(4)
            lbl = QLabel(tier, self)
            lbl.setFixedWidth(50)
            bar = QProgressBar(self)
            bar.setMinimum(0)
            bar.setMaximum(100)
            bar.setValue(0)
            bar.setTextVisible(False)
            bar.setFixedHeight(16)
            stats = QLabel("", self)
            stats.setFixedWidth(180)
            row.addWidget(lbl)
            row.addWidget(bar, 1)
            row.addWidget(stats)
            self._tier_bars[tier] = bar
            self._tier_labels[tier] = lbl
            self._tier_stats[tier] = stats
            self._cap_layout.addLayout(row)
            lbl.setVisible(False)
            bar.setVisible(False)
            stats.setVisible(False)
        self._cap_hint = QLabel("", self)
        self._cap_hint.setWordWrap(True)
        self._cap_layout.addWidget(self._cap_hint)
        root.addWidget(cap_group)

        hz_row = QHBoxLayout()
        hz_row.setSpacing(6)
        hz_row.addWidget(QLabel("Target Poll Rate", self))
        self.spn_target_hz = QSpinBox(self)
        self.spn_target_hz.setRange(1, 50)
        self.spn_target_hz.setValue(10)
        self.spn_target_hz.setSuffix(" Hz")
        self.spn_target_hz.setToolTip("Target update rate per SHORT_UP channel")
        self.spn_target_hz.valueChanged.connect(self._update_budget_estimate)  # type: ignore
        hz_row.addWidget(self.spn_target_hz)
        hz_row.addStretch(1)
        self._budget_label = QLabel("", self)
        hz_row.addWidget(self._budget_label)
        root.addLayout(hz_row)

        test_row = QHBoxLayout()
        self.btn_test = QPushButton("Test Connection...")
        self.btn_test.clicked.connect(self._run_test)  # type: ignore
        test_row.addWidget(self.btn_test)
        test_row.addStretch(1)
        root.addLayout(test_row)

        self._test_dialog: CCPTestDialog | None = None

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

    def _blank_device(self, idx: int, role: str = "primary") -> Dict[str, Any]:
        role = str(role).strip().lower()
        if role not in {"primary", "secondary"}:
            role = "primary"
        default_prefix = "CCP_P_" if role == "primary" else "CCP_S_"
        return {
            "name": f"CCP {role.title()}",
            "role": role,
            "session": {
                "interface": self._default_interface_for_device(),
                "baudrate": 250000,
                "tx_id": "0x0CFF50F9",
                "rx_id": "0x0CFF5100",
                "station_address": "0x0" if role == "primary" else "0x1",
                "is_extended": True,
            },
            "security": {
                "seed_resource": "0x01",
                "seed_ctr": "0x07",
                "connect_ctr": "0x19",
                "unlock_ctr": "0x08",
                "access_key": "",
                "seed_endian": "big",
                "sec_type": "CAL",
                "unlock_pad": "0x55",
                "force_unlock": True,
                "set_s_status": True,
                "s_status": "0x83",
            },
            "a2l": {"path": ""},
            "poll_interval_ms": 10,
            "poll_default_priority": self._DEFAULT_PRIORITY,
            "acquisition_mode": "short_up",
            "fallback_short_up": False,
            "acquisition": {
                "mode": "short_up",
                "fallback_short_up": False,
                "seed_resource": "0x02",
                "sec_type": "DAQ",
                "tier": "100ms",
                "prescaler": 1,
            },
            "measurements": {"naming_prefix": default_prefix, "list": []},
            "_a2l_meta": {},
        }

    def _load_devices_from_cfg(self, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
        devices = cfg.get("devices")
        out: List[Dict[str, Any]] = []
        if isinstance(devices, list) and devices:
            for i, dev in enumerate(devices[:2]):
                if not isinstance(dev, dict):
                    continue
                role = str(dev.get("role") or ("secondary" if i == 1 else "primary")).strip().lower()
                base = self._blank_device(i + 1, role)
                base["name"] = str(dev.get("name") or base["name"])
                base["session"].update(dev.get("session") or {})
                base["security"].update(dev.get("security") or {})
                base["a2l"].update(dev.get("a2l") or {})
                base["poll_interval_ms"] = int(dev.get("poll_interval_ms", cfg.get("poll_interval_ms", 100)))
                base["poll_default_priority"] = self._canonical_priority(
                    dev.get("poll_default_priority") or dev.get("poll_default_tier")
                    or cfg.get("poll_default_priority") or cfg.get("poll_default_tier")
                    or base.get("poll_default_priority")
                )
                acq = dict(base.get("acquisition") or {})
                acq.update(cfg.get("acquisition") or {})
                acq.update(dev.get("acquisition") or {})
                mode = str(dev.get("acquisition_mode") or acq.get("mode") or cfg.get("acquisition_mode") or "short_up").lower()
                base["acquisition_mode"] = "short_up" if mode in {"short_up", "shortup"} else "daq"
                base["fallback_short_up"] = bool(dev.get("fallback_short_up", acq.get("fallback_short_up", True)))
                acq["mode"] = base["acquisition_mode"]
                acq["fallback_short_up"] = base["fallback_short_up"]
                base["acquisition"] = acq
                base["measurements"] = dict(base["measurements"])
                base["measurements"].update(dev.get("measurements") or {})
                out.append(base)
            if out:
                return out

        # Legacy single-device fallback.
        d = self._blank_device(1, "primary")
        d["session"].update(cfg.get("session") or {})
        d["security"].update(cfg.get("security") or {})
        d["a2l"].update(cfg.get("a2l") or {})
        d["measurements"] = dict(cfg.get("measurements") or d["measurements"])
        d["poll_interval_ms"] = int(cfg.get("poll_interval_ms", 100))
        d["poll_default_priority"] = self._canonical_priority(
            cfg.get("poll_default_priority") or cfg.get("poll_default_tier") or d.get("poll_default_priority")
        )
        acq = dict(d.get("acquisition") or {})
        acq.update(cfg.get("acquisition") or {})
        mode = str(cfg.get("acquisition_mode") or acq.get("mode") or "short_up").lower()
        d["acquisition_mode"] = "short_up" if mode in {"short_up", "shortup"} else "daq"
        d["fallback_short_up"] = bool(cfg.get("fallback_short_up", acq.get("fallback_short_up", True)))
        acq["mode"] = d["acquisition_mode"]
        acq["fallback_short_up"] = d["fallback_short_up"]
        d["acquisition"] = acq
        return [d]

    def _load(self) -> None:
        self._cfg = self._read_yaml(self._cfg_path)
        self._devices = self._load_devices_from_cfg(self._cfg)
        for device in self._devices:
            session = device.get("session") or {}
            session["interface"] = self._matched_interface(str(session.get("interface") or ""))
            device["session"] = session
        if hasattr(self, "spn_target_hz"):
            self.spn_target_hz.setValue(int(self._cfg.get("target_poll_hz", 10)))
        self.tabs.blockSignals(True)
        while self.tabs.count() > 0:
            self.tabs.removeTab(0)
        for d in self._devices:
            self.tabs.addTab(str(d.get("name") or "CCP Device"))
        self.tabs.blockSignals(False)
        if self._devices:
            self._active_device_idx = 0
            self.tabs.setCurrentIndex(0)
            self._load_device_ui(0)
        self._update_device_buttons()

    def _save_current_device_ui(self) -> None:
        idx = self._active_device_idx
        if idx < 0 or idx >= len(self._devices):
            return
        d = self._devices[idx]
        d["name"] = self.txt_device_name.text().strip() or f"CCP Device {idx+1}"
        role = self.cmb_role.currentText().strip().lower()
        d["role"] = "secondary" if role == "secondary" else "primary"
        d["session"] = {
            **(d.get("session") or {}),
            "interface": self.cmb_interface.currentText().strip(),
            "baudrate": int(self.cmb_baudrate.currentText().strip() or "250000"),
            "station_address": "0x0" if d["role"] == "primary" else "0x1",
        }
        key_text = self.txt_access_key.text().strip()
        if key_text:
            _session_key_store()[d.get("name") or f"CCP Device {idx+1}"] = key_text
        d["security"] = {**(d.get("security") or {}), "access_key": ""}
        d["a2l"] = {"path": self.txt_a2l_path.text().strip()}
        d["poll_interval_ms"] = int(d.get("poll_interval_ms", 10))
        mode = str(d.get("acquisition_mode") or "short_up").lower()
        if mode not in {"short_up", "shortup", "daq"}:
            mode = "short_up"
        d["acquisition_mode"] = mode
        d["fallback_short_up"] = bool(d.get("fallback_short_up", False))
        acq = dict(d.get("acquisition") or {})
        acq.setdefault("seed_resource", "0x02")
        acq.setdefault("sec_type", "DAQ")
        acq.setdefault("tier", "100ms")
        acq.setdefault("prescaler", 1)
        acq["mode"] = mode
        acq["fallback_short_up"] = True
        d["acquisition"] = acq
        d["measurements"] = {
            **(d.get("measurements") or {}),
            "naming_prefix": self.txt_prefix.text().strip(),
            "list": self._checked_measurements(),
        }

    def _load_device_ui(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._devices):
            return
        d = self._devices[idx]
        session = d.get("session") or {}
        security = d.get("security") or {}
        a2l = d.get("a2l") or {}
        meas = d.get("measurements") or {}
        self.txt_device_name.setText(str(d.get("name") or f"CCP Device {idx+1}"))
        self.cmb_role.setCurrentText("Secondary" if str(d.get("role") or "").lower() == "secondary" else "Primary")
        self._select_interface(str(session.get("interface", "")))
        baud = str(session.get("baudrate", "250000"))
        bidx = self.cmb_baudrate.findText(baud)
        self.cmb_baudrate.setCurrentIndex(bidx if bidx >= 0 else 1)
        device_name = str(d.get("name") or f"CCP Device {idx+1}")
        self.txt_access_key.setText(_session_key_store().get(device_name, ""))
        self.txt_a2l_path.setText(str(a2l.get("path", "")))
        self.txt_prefix.setText(str(meas.get("naming_prefix", "CCP_")))
        selected_names: List[str] = []
        selected_priorities: Dict[str, str] = {}
        selected_meta: Dict[str, Dict[str, Any]] = {}
        for item in meas.get("list", []) or []:
            if isinstance(item, dict) and bool(item.get("enabled", True)) and item.get("name"):
                name = str(item.get("name"))
                selected_names.append(name)
                selected_priorities[name] = self._canonical_tier(
                    item.get("priority") or item.get("poll_tier") or item.get("daq_list")
                )
                selected_meta[name] = dict(item)
        self._reload_channels_from_a2l(
            selected_names=selected_names,
            selected_priorities=selected_priorities,
            selected_meta=selected_meta,
        )

    def _on_tab_changed(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._devices):
            return
        if self._active_device_idx >= 0:
            self._save_current_device_ui()
        self._active_device_idx = idx
        self._load_device_ui(idx)

    def _on_device_name_changed(self, text: str) -> None:
        idx = self._active_device_idx
        if idx < 0 or idx >= self.tabs.count():
            return
        self.tabs.setTabText(idx, text.strip() or f"CCP Device {idx+1}")

    def _add_device(self) -> None:
        if len(self._devices) >= 2:
            QMessageBox.information(self, "CCP Devices", "CCP currently supports up to two ECM devices.")
            return
        self._save_current_device_ui()
        role = "secondary" if len(self._devices) == 1 else "primary"
        d = self._blank_device(len(self._devices) + 1, role)
        self._devices.append(d)
        self.tabs.addTab(str(d.get("name")))
        self.tabs.setCurrentIndex(self.tabs.count() - 1)
        self._update_device_buttons()

    def _matched_interface(self, interface: str) -> str:
        wanted = str(interface or "").strip().upper()
        for available in self._available_can_channels:
            if available.upper() == wanted:
                return available
        return ""

    def _select_interface(self, interface: str) -> None:
        matched = self._matched_interface(interface)
        if matched:
            self.cmb_interface.setCurrentText(matched)
            return
        self.cmb_interface.setCurrentIndex(0)

    def _used_interfaces(self) -> set[str]:
        used: set[str] = set()
        for i, device in enumerate(self._devices):
            if i == self._active_device_idx:
                current = self.cmb_interface.currentText().strip()
                if current:
                    used.add(current)
                continue
            session = device.get("session") or {}
            interface = str(session.get("interface") or "").strip()
            if interface:
                used.add(interface)
        return used

    def _default_interface_for_device(self) -> str:
        used = self._used_interfaces()
        for interface in self._available_can_channels:
            if interface not in used:
                return interface
        return ""

    def _remove_device(self) -> None:
        if not self._devices:
            return
        if len(self._devices) <= 1:
            QMessageBox.information(self, "CCP Devices", "At least one CCP device is required.")
            return
        idx = self.tabs.currentIndex()
        if idx < 0 or idx >= len(self._devices):
            return
        self._devices.pop(idx)
        self.tabs.removeTab(idx)
        new_idx = max(0, min(idx, len(self._devices) - 1))
        self.tabs.setCurrentIndex(new_idx)
        self._active_device_idx = new_idx
        self._load_device_ui(new_idx)
        self._update_device_buttons()

    def _update_device_buttons(self) -> None:
        self.btn_add_device.setEnabled(len(self._devices) < 2)
        self.btn_remove_device.setEnabled(len(self._devices) > 1)

    def _browse_a2l(self) -> None:
        start = self.txt_a2l_path.text().strip() or str(Path.cwd())
        path, _ = QFileDialog.getOpenFileName(self, "Select A2L file", start, "A2L files (*.a2l);;All files (*.*)")
        if path:
            self.txt_a2l_path.setText(path)
            self._reload_channels_from_a2l(force_refresh=True)

    def _parse_address(self, token: str) -> int | None:
        try:
            s = str(token).strip()
            if s.startswith(("0x", "0X")):
                return int(s, 16)
            return int(s, 10)
        except Exception:
            return None

    def _parse_a2l_channels(self, path: Path, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        if not path.exists():
            return out
        try:
            stat = path.stat()
            cache_key = (str(path.resolve()), int(stat.st_size), int(stat.st_mtime_ns))
            if not force_refresh and cache_key in self._A2L_NAME_CACHE:
                return dict(self._A2L_NAME_CACHE[cache_key])
        except Exception:
            cache_key = ("", 0, 0)
        data_types = {"UBYTE", "SBYTE", "UWORD", "SWORD", "ULONG", "SLONG", "FLOAT32_IEEE", "FLOAT64_IEEE"}
        in_block = False
        cur_name: str | None = None
        cur_type: str | None = None
        size_map = {
            "UBYTE": 1,
            "SBYTE": 1,
            "UWORD": 2,
            "SWORD": 2,
            "ULONG": 4,
            "SLONG": 4,
            "FLOAT32_IEEE": 4,
            "FLOAT64_IEEE": 8,
        }
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for raw in fh:
                line = raw.strip()
                if line.startswith("/begin MEASUREMENT"):
                    parts = line.split()
                    cur_name = str(parts[2]).strip() if len(parts) > 2 else None
                    cur_type = None
                    in_block = True
                    continue
                if line.startswith("/end MEASUREMENT"):
                    if in_block and cur_name:
                        dtype = str(cur_type or "")
                        size = max(1, min(8, int(size_map.get(dtype, 4))))
                        out[cur_name] = {
                            "name": cur_name,
                            "data_type": dtype,
                            "size": size,
                            "unit": "",
                        }
                    in_block = False
                    cur_name = None
                    continue
                if not in_block or cur_name is None:
                    continue
                token = line.split()[0] if line else ""
                if cur_type is None and token in data_types:
                    cur_type = token
        if cache_key[0]:
            self._A2L_NAME_CACHE[cache_key] = dict(out)
        return out

    def _canonical_tier(self, value: Any = None) -> str:
        text = str(value or "").strip().lower().replace(" ", "")
        aliases = {
            "high": "high", "hi": "high", "h": "high",
            "highpoll": "high", "high_poll": "high",
            "low": "low", "lo": "low", "l": "low",
            "lowpoll": "low", "low_poll": "low",
            "1": "1ms", "1ms": "1ms",
            "10": "10ms", "10ms": "10ms",
            "50": "50ms", "50ms": "50ms",
            "100": "100ms", "100ms": "100ms",
            "daq1ms": "1ms", "daq10ms": "10ms",
            "daq50ms": "50ms", "daq100ms": "100ms",
        }
        return aliases.get(text, "high")

    _canonical_priority = _canonical_tier

    def _tier_display(self, value: Any = None) -> str:
        canon = self._canonical_tier(value)
        return _CANONICAL_TO_DISPLAY.get(canon, "High Poll")

    def _checked_channels(self) -> List[str]:
        out: List[str] = []
        for row in range(self.table_channels.rowCount()):
            it = self.table_channels.item(row, 0)
            name_item = self.table_channels.item(row, 1)
            if it is not None and name_item is not None and it.checkState() == Qt.Checked:
                data = name_item.data(Qt.UserRole) or {}
                if isinstance(data, dict) and data.get("name"):
                    out.append(str(data.get("name")))
        return out

    def _checked_priorities(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for row in range(self.table_channels.rowCount()):
            name_item = self.table_channels.item(row, 1)
            if name_item is None:
                continue
            data = name_item.data(Qt.UserRole) or {}
            name = str(data.get("name") or "").strip() if isinstance(data, dict) else ""
            priority_item = self.table_channels.item(row, 3)
            if name and priority_item is not None:
                out[name] = priority_item.text().strip()
        return out

    def _checked_meta_by_name(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for row in range(self.table_channels.rowCount()):
            use_item = self.table_channels.item(row, 0)
            name_item = self.table_channels.item(row, 1)
            if use_item is None or name_item is None or use_item.checkState() != Qt.Checked:
                continue
            data = name_item.data(Qt.UserRole) or {}
            if not isinstance(data, dict):
                continue
            name = str(data.get("name") or "").strip()
            meta = data.get("meta")
            if name and isinstance(meta, dict):
                out[name] = dict(meta)
        return out

    def _reload_channels_from_a2l(
        self,
        selected_names: List[str] | None = None,
        selected_priorities: Dict[str, str] | None = None,
        selected_meta: Dict[str, Dict[str, Any]] | None = None,
        force_refresh: bool = False,
    ) -> None:
        selected = set(selected_names if selected_names is not None else self._checked_channels())
        priorities = dict(selected_priorities if selected_priorities is not None else self._checked_priorities())
        saved_meta = dict(selected_meta if selected_meta is not None else self._checked_meta_by_name())
        self.table_channels.blockSignals(True)
        self.table_channels.setUpdatesEnabled(False)
        self.table_channels.setRowCount(0)
        idx = self._active_device_idx
        if idx < 0 or idx >= len(self._devices):
            self.table_channels.setUpdatesEnabled(True)
            self.table_channels.blockSignals(False)
            return
        d = self._devices[idx]
        path = Path(self.txt_a2l_path.text().strip())
        if not path.exists():
            meta = {name: dict(saved_meta.get(name, {"name": name})) for name in selected}
            d["_a2l_meta"] = meta
            self._populate_channel_table(meta, selected, priorities, saved_meta)
            self.table_channels.setUpdatesEnabled(True)
            self.table_channels.blockSignals(False)
            self._apply_channel_filter()
            self._update_priority_summary()
            return
        try:
            meta = self._parse_a2l_channels(path, force_refresh=force_refresh)
        except Exception:
            meta = {}
        for name, m in saved_meta.items():
            merged = dict(meta.get(name, {"name": name}))
            merged.update(m)
            meta[name] = merged
        d["_a2l_meta"] = meta
        self._populate_channel_table(meta, selected, priorities, saved_meta)
        self.table_channels.setUpdatesEnabled(True)
        self.table_channels.blockSignals(False)
        self._apply_channel_filter()
        self._update_priority_summary()

    def _populate_channel_table(
        self,
        meta: Dict[str, Dict[str, Any]],
        selected: set[str],
        priorities: Dict[str, str],
        saved_meta: Dict[str, Dict[str, Any]],
    ) -> None:
        names = sorted(meta.keys())
        total = len(names)
        cap = int(self._MAX_DISPLAYED_A2L_ROWS)
        if total > cap:
            selected_names = [name for name in names if name in selected]
            visible: List[str] = []
            seen: set[str] = set()
            for name in selected_names + names:
                if name in seen:
                    continue
                visible.append(name)
                seen.add(name)
                if len(visible) >= cap:
                    break
            names = visible
            self.lbl_channel_status.setText(
                f"Showing {len(names)} of {total} measurements. Use the filter to narrow large A2L files."
            )
        else:
            self.lbl_channel_status.setText(f"Showing {total} measurements.")
        self.table_channels.setRowCount(len(names))
        for row, name in enumerate(names):
            m = dict(meta.get(name, {}))
            if name in saved_meta:
                m.update(saved_meta[name])
            unit = str(m.get("unit", "") or "").strip()
            use_item = QTableWidgetItem("")
            use_item.setFlags(use_item.flags() | Qt.ItemIsUserCheckable)
            use_item.setCheckState(Qt.Checked if name in selected else Qt.Unchecked)
            self.table_channels.setItem(row, 0, use_item)
            name_item = QTableWidgetItem(name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            name_item.setData(Qt.UserRole, {"name": name, "meta": m})
            self.table_channels.setItem(row, 1, name_item)
            unit_item = QTableWidgetItem(unit or "-")
            unit_item.setFlags(unit_item.flags() & ~Qt.ItemIsEditable)
            self.table_channels.setItem(row, 2, unit_item)
            priority_item = QTableWidgetItem(self._tier_display(priorities.get(name, self._DEFAULT_PRIORITY)))
            self.table_channels.setItem(row, 3, priority_item)
            size_item = QTableWidgetItem(str(int(m.get("size") or 0)))
            size_item.setFlags(size_item.flags() & ~Qt.ItemIsEditable)
            self.table_channels.setItem(row, 4, size_item)

    def _checked_measurements(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for row in range(self.table_channels.rowCount()):
            use_item = self.table_channels.item(row, 0)
            name_item = self.table_channels.item(row, 1)
            if use_item is None or name_item is None or use_item.checkState() != Qt.Checked:
                continue
            data = name_item.data(Qt.UserRole) or {}
            name = str(data.get("name") or "").strip() if isinstance(data, dict) else ""
            meta = data.get("meta") if isinstance(data, dict) else {}
            if not name:
                continue
            entry: Dict[str, Any] = {"name": name, "unit_override": None, "enabled": True}
            priority_item = self.table_channels.item(row, 3)
            display_text = priority_item.text().strip() if priority_item is not None else self._DEFAULT_PRIORITY
            entry["priority"] = _DISPLAY_TO_CANONICAL.get(display_text, self._canonical_tier(display_text))
            if isinstance(meta, dict):
                unit = str(meta.get("unit", "") or "").strip()
                entry["unit_override"] = unit
                entry["unit"] = unit
                if meta.get("address") is not None:
                    entry["address"] = int(meta.get("address"))
                if meta.get("address_extension") is not None:
                    entry["address_extension"] = int(meta.get("address_extension"))
                if meta.get("data_type"):
                    entry["data_type"] = str(meta.get("data_type"))
                if meta.get("size") is not None:
                    entry["size"] = int(meta.get("size"))
                lim = meta.get("limits")
                if isinstance(lim, (list, tuple)) and len(lim) == 2:
                    try:
                        entry["limits"] = [float(lim[0]), float(lim[1])]
                    except Exception:
                        pass
            out.append(entry)
        return out

    def _apply_channel_filter(self) -> None:
        q = self.txt_filter.text().strip().lower()
        show_selected = bool(self.chk_show_selected.isChecked()) if hasattr(self, "chk_show_selected") else False
        for row in range(self.table_channels.rowCount()):
            use_item = self.table_channels.item(row, 0)
            name_item = self.table_channels.item(row, 1)
            if name_item is None:
                continue
            data = name_item.data(Qt.UserRole) or {}
            name = str(data.get("name") or name_item.text()).lower() if isinstance(data, dict) else name_item.text().lower()
            if not q:
                visible = True
            elif "*" in q:
                visible = bool(fnmatch(name, q))
            else:
                visible = name.startswith(q)
            if show_selected:
                visible = visible and use_item is not None and use_item.checkState() == Qt.Checked
            self.table_channels.setRowHidden(row, not visible)
        self._update_priority_summary()

    def _on_channel_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            if hasattr(self, "chk_show_selected") and self.chk_show_selected.isChecked():
                self._apply_channel_filter()
            self._update_priority_summary()
        elif item.column() == 3:
            display = self._tier_display(item.text())
            if item.text() != display:
                self.table_channels.blockSignals(True)
                item.setText(display)
                self.table_channels.blockSignals(False)
            self._update_priority_summary()

    def _tier_counts(self) -> Dict[str, Dict[str, int]]:
        counts: Dict[str, Dict[str, int]] = {}
        for row in range(self.table_channels.rowCount()):
            use_item = self.table_channels.item(row, 0)
            if use_item is None or use_item.checkState() != Qt.Checked:
                continue
            priority_item = self.table_channels.item(row, 3)
            tier = self._canonical_tier(
                priority_item.text() if priority_item is not None else self._DEFAULT_PRIORITY
            )
            name_item = self.table_channels.item(row, 1)
            size = 2
            if name_item is not None:
                data = name_item.data(Qt.UserRole) or {}
                if isinstance(data, dict):
                    meta = data.get("meta") or {}
                    if isinstance(meta, dict) and meta.get("size"):
                        size = int(meta.get("size", 2))
            if tier not in counts:
                counts[tier] = {"channels": 0, "odts": 0, "_offset": 0}
            counts[tier]["channels"] += 1
            offset = counts[tier]["_offset"]
            if offset + size > self._DAQ_DTO_PAYLOAD_BYTES:
                counts[tier]["odts"] += 1
                offset = 0
            offset += size
            counts[tier]["_offset"] = offset
        for tc in counts.values():
            if tc["_offset"] > 0:
                tc["odts"] += 1
            del tc["_offset"]
        return counts

    def _daq_list_capacities(self) -> Dict[str, int]:
        caps: Dict[str, int] = {}
        path = Path(self.txt_a2l_path.text().strip())
        if parse_a2l_daq_lists is not None and path.exists():
            try:
                daq_lists = parse_a2l_daq_lists(path)
                for tier, meta in daq_lists.items():
                    caps[tier] = int(meta.odt_count) if meta.odt_count else 10
            except Exception:
                pass
        return caps

    def _update_priority_summary(self) -> None:
        if not hasattr(self, "_tier_bars"):
            return
        tier_counts = self._tier_counts()
        caps = self._daq_list_capacities()
        any_over_cap = False
        cap_pct = self._MAX_ODT_UTILIZATION_PCT
        high_ch = tier_counts.get("high", {}).get("channels", 0)
        low_ch = tier_counts.get("low", {}).get("channels", 0)
        sup_total = high_ch + low_ch
        if sup_total > 0:
            self._sup_summary.setText(
                f"SHORT_UP: {sup_total} channels ({high_ch} high, {low_ch} low)"
            )
            self._sup_summary.setVisible(True)
        else:
            self._sup_summary.setVisible(False)
        any_daq = any(
            tier_counts.get(t, {}).get("channels", 0) > 0
            for t in ["1ms", "10ms", "50ms", "100ms"]
        )
        self._daq_header.setVisible(any_daq)
        for tier in ["1ms", "10ms", "50ms", "100ms"]:
            bar = self._tier_bars.get(tier)
            lbl = self._tier_labels.get(tier)
            stats = self._tier_stats.get(tier)
            if bar is None or lbl is None or stats is None:
                continue
            tc = tier_counts.get(tier)
            if not tc or tc["channels"] == 0:
                bar.setVisible(False)
                lbl.setVisible(False)
                stats.setVisible(False)
                continue
            bar.setVisible(True)
            lbl.setVisible(True)
            stats.setVisible(True)
            odts = tc["odts"]
            max_odts = caps.get(tier, 10)
            usable_odts = max(1, int(max_odts * cap_pct / 100.0))
            pct = int(min(100, (odts / max(1, max_odts)) * 100))
            bar.setValue(pct)
            stats.setText(f"{pct}%  ({odts}/{max_odts} ODTs, {tc['channels']}ch)")
            bar.setToolTip(f"DAQ {tier}: {odts} of {max_odts} ODTs used ({tc['channels']} channels)")
            if odts > max_odts:
                lbl.setText(f"DAQ {tier} OVER")
                lbl.setStyleSheet("font-weight: bold; color: red;")
                stats.setStyleSheet("font-weight: bold; color: red;")
                bar.setStyleSheet("QProgressBar::chunk { background-color: #cc3333; }")
                any_over_cap = True
            elif odts > usable_odts:
                lbl.setText(f"DAQ {tier}")
                lbl.setStyleSheet("font-weight: bold; color: #cc3333;")
                stats.setText(f"{pct}%  ({odts}/{max_odts} ODTs, {tc['channels']}ch) — OVER {cap_pct}% LIMIT")
                stats.setStyleSheet("font-weight: bold; color: #cc3333;")
                bar.setStyleSheet("QProgressBar::chunk { background-color: #cc3333; }")
                any_over_cap = True
            else:
                lbl.setText(f"DAQ {tier}")
                lbl.setStyleSheet("")
                stats.setStyleSheet("")
                bar.setStyleSheet("")
        if any_over_cap:
            self._cap_hint.setText(
                f"One or more DAQ tiers exceed the {cap_pct}% ODT limit. "
                f"Move channels to SHORT_UP (High/Low Poll) or a less-loaded DAQ tier. "
                f"Save is blocked until all DAQ tiers are within the limit."
            )
            self._cap_hint.setStyleSheet("color: #cc3333; font-weight: bold;")
        elif any_daq:
            self._cap_hint.setText("DAQ tiers must be within capacity to save. Use High/Low Poll for SHORT_UP.")
            self._cap_hint.setStyleSheet("")
        else:
            self._cap_hint.setText("")
            self._cap_hint.setStyleSheet("")
        self._update_budget_estimate()

    def _update_budget_estimate(self) -> None:
        if not hasattr(self, "_budget_label"):
            return
        tier_counts = self._tier_counts()
        high_ch = tier_counts.get("high", {}).get("channels", 0)
        low_ch = tier_counts.get("low", {}).get("channels", 0)
        sup_total = high_ch + low_ch
        if sup_total == 0:
            self._budget_label.setText("")
            return
        target_hz = self.spn_target_hz.value()
        assumed_capacity = 200.0
        target_rps = float(target_hz * sup_total)
        budget_pct = min(999, int(target_rps / assumed_capacity * 100))
        if low_ch == 0:
            high_hz = min(target_hz, assumed_capacity / max(1, high_ch))
            low_hz = 0.0
        elif high_ch == 0:
            high_hz = 0.0
            low_hz = min(target_hz, assumed_capacity / max(1, low_ch))
        else:
            achievable_rps = min(target_rps, assumed_capacity)
            high_hz = achievable_rps * 0.75 / max(1, high_ch)
            low_hz = achievable_rps * 0.25 / max(1, low_ch)
        parts: list[str] = []
        if high_ch:
            parts.append(f"HIGH {high_ch}ch @ ~{high_hz:.1f} Hz")
        if low_ch:
            parts.append(f"LOW {low_ch}ch @ ~{low_hz:.1f} Hz")
        parts.append(f"Budget: ~{budget_pct}%")
        text = " | ".join(parts)
        if budget_pct > 100:
            self._budget_label.setText(f"{text}  (target may not be achievable)")
            self._budget_label.setStyleSheet("color: #cc3333;")
        else:
            self._budget_label.setText(text)
            self._budget_label.setStyleSheet("")

    def _poll_interval_ms(self) -> int:
        idx = self._active_device_idx
        if 0 <= idx < len(self._devices):
            return int(self._devices[idx].get("poll_interval_ms", 10))
        return 10

    def _build_doc(self) -> Dict[str, Any]:
        self._save_current_device_ui()
        doc: Dict[str, Any] = dict(self._cfg)
        doc["enabled"] = bool(doc.get("enabled", True))
        doc["mode"] = "real"
        doc["recording_rate_hz"] = int(doc.get("recording_rate_hz", 10))
        doc["poll_channels_per_tick"] = int(doc.get("poll_channels_per_tick", 1))
        doc["io_timeout_s"] = float(doc.get("io_timeout_s", 0.05))
        doc["poll_default_priority"] = "low"
        doc["target_poll_hz"] = self.spn_target_hz.value() if hasattr(self, "spn_target_hz") else 10
        doc["acquisition_mode"] = "short_up"
        doc["fallback_short_up"] = False
        top_acq = dict(doc.get("acquisition") or {})
        top_acq.setdefault("seed_resource", "0x02")
        top_acq.setdefault("sec_type", "DAQ")
        top_acq.setdefault("tier", "100ms")
        top_acq.setdefault("prescaler", 1)
        top_acq["mode"] = "short_up"
        top_acq["fallback_short_up"] = False
        doc["acquisition"] = top_acq
        doc.pop("poll_tiers", None)
        doc.pop("poll_default_tier", None)
        doc["poll_endian"] = "big"
        doc["mta_addr_endian"] = "big"
        doc["addr_ext_high"] = False
        doc["reconnect_interval_s"] = float(doc.get("reconnect_interval_s", 2.0))
        devices: List[Dict[str, Any]] = []
        for d in self._devices[:2]:
            role = str(d.get("role") or "primary").lower()
            session = dict(d.get("session") or {})
            session["station_address"] = "0x1" if role == "secondary" else "0x0"
            acq = dict(d.get("acquisition") or {})
            mode = str(d.get("acquisition_mode") or acq.get("mode") or "short_up").lower()
            mode = "daq" if mode in {"daq", "daq_stream", "stream"} else "short_up"
            acq["mode"] = mode
            acq["fallback_short_up"] = bool(d.get("fallback_short_up", acq.get("fallback_short_up", True)))
            acq.setdefault("seed_resource", "0x02")
            acq.setdefault("sec_type", "DAQ")
            acq.setdefault("tier", "100ms")
            acq.setdefault("prescaler", 1)
            devices.append(
                {
                    "name": str(d.get("name") or f"CCP {role.title()}"),
                    "role": role,
                    "session": session,
                    "security": dict(d.get("security") or {}),
                    "a2l": dict(d.get("a2l") or {}),
                    "poll_interval_ms": int(d.get("poll_interval_ms", 100)),
                    "acquisition_mode": mode,
                    "fallback_short_up": bool(acq.get("fallback_short_up", True)),
                    "acquisition": acq,
                    "poll_default_priority": self._canonical_priority(
                        d.get("poll_default_priority") or self._DEFAULT_PRIORITY
                    ),
                    "measurements": dict(d.get("measurements") or {}),
                }
            )
        doc["devices"] = devices

        # Legacy/top-level compatibility mirrors first device.
        first = devices[0]
        doc["session"] = dict(first.get("session") or {})
        doc["security"] = dict(first.get("security") or {})
        doc["a2l"] = dict(first.get("a2l") or {})
        doc["poll_interval_ms"] = int(first.get("poll_interval_ms", 100))
        doc["acquisition_mode"] = str(first.get("acquisition_mode") or "short_up")
        doc["fallback_short_up"] = bool(first.get("fallback_short_up", True))
        doc["acquisition"] = dict(first.get("acquisition") or doc.get("acquisition") or {})
        doc["measurements"] = dict(first.get("measurements") or {})
        doc["writes"] = []
        return doc

    def _validate_devices(self, devices: List[Dict[str, Any]]) -> str | None:
        if not devices:
            return "At least one CCP device is required."
        roles = [str(d.get("role") or "").lower() for d in devices]
        if len(set(roles)) != len(roles):
            return "Primary/Secondary role must be unique per device."
        for d in devices:
            session = d.get("session") or {}
            if not str(session.get("interface") or "").strip() and self._available_can_channels:
                return f"{d.get('name','CCP device')}: CAN interface is required."
            a2l = d.get("a2l") or {}
            if not str(a2l.get("path") or "").strip():
                return f"{d.get('name','CCP device')}: A2L path is required."
            meas = d.get("measurements") or {}
            items = [x for x in (meas.get("list") or []) if isinstance(x, dict) and bool(x.get("enabled", True))]
            if not items:
                return f"{d.get('name','CCP device')}: select at least one measurement."
        return None

    def _init_status_subscriber(self) -> None:
        try:
            from src.core.ipc.bus import create_ui_subscriber
            sockets = create_ui_subscriber()
            if sockets is not None:
                self._sub = sockets.telemetry_sub
        except Exception:
            self._sub = None
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(120)
        self._status_timer.timeout.connect(self._poll_status)  # type: ignore
        self._status_timer.start()

    def _append_terminal(self, line: str) -> None:
        dlg = self._test_dialog
        if dlg is not None and dlg.isVisible():
            dlg.append(line)

    def _run_test(self) -> None:
        self._on_accept(save_only=True)
        dlg = CCPTestDialog(self)
        self._test_dialog = dlg
        dlg.show()
        try:
            from src.core.ipc.bus import create_ui_control_push
            ctrl = create_ui_control_push()
            if ctrl is None:
                dlg.append("ERROR: IPC control path unavailable.")
                return
            self._test_run_id = f"ccp_test_{int(time.time() * 1000)}"
            msg = json.dumps({"type": "ccp_test", "run_id": self._test_run_id}).encode("utf-8")
            ctrl["control_push"].send(msg)
            dlg.append("CCP test requested...")
        except Exception as e:
            dlg.append(f"ERROR: failed to request test: {e}")

    def _poll_status(self) -> None:
        if self._sub is None:
            return
        try:
            import zmq
            while True:
                try:
                    topic, payload = self._sub.recv_multipart(flags=zmq.NOBLOCK)
                except Exception:
                    break
                if topic != b"status":
                    continue
                try:
                    msg = json.loads(payload.decode("utf-8"))
                except Exception:
                    continue
                if msg.get("type") != "ccp_test":
                    continue
                if self._test_run_id and str(msg.get("run_id", "")) != self._test_run_id:
                    continue
                step = str(msg.get("step", "step"))
                ok = bool(msg.get("ok", False))
                detail = str(msg.get("detail", ""))
                self._append_terminal(f"{'OK' if ok else 'FAIL'} [{step}] {detail}")
                if bool(msg.get("done", False)):
                    self._append_terminal("CCP test complete.")
        except Exception:
            pass

    def _validate_tier_capacity(self) -> str | None:
        tier_counts = self._tier_counts()
        caps = self._daq_list_capacities()
        cap_pct = self._MAX_ODT_UTILIZATION_PCT
        over: List[str] = []
        daq_tiers = {"1ms", "10ms", "50ms", "100ms"}
        for tier, tc in tier_counts.items():
            if tier not in daq_tiers:
                continue
            max_odts = caps.get(tier, 10)
            usable_odts = max(1, int(max_odts * cap_pct / 100.0))
            if tc["odts"] > max_odts:
                over.append(f"DAQ {tier}: needs {tc['odts']} ODTs, max {max_odts} ({tc['channels']} channels)")
            elif tc["odts"] > usable_odts:
                pct = int((tc["odts"] / max(1, max_odts)) * 100)
                over.append(
                    f"DAQ {tier}: {pct}% full ({tc['odts']}/{max_odts} ODTs, "
                    f"{tc['channels']} channels) — exceeds {cap_pct}% limit"
                )
        if over:
            return (
                "DAQ tier capacity exceeded:\n" + "\n".join(over)
                + f"\n\nMax allowed is {cap_pct}% per tier for reliable data quality. "
                "Move channels to HIGH/LOW Poll (SHORT_UP) or a less-loaded DAQ tier."
            )
        return None

    def _on_accept(self, save_only: bool = False) -> None:
        doc = self._build_doc()
        devices = [d for d in (doc.get("devices") or []) if isinstance(d, dict)]
        err = self._validate_devices(devices)
        if err:
            QMessageBox.warning(self, "CCP Configuration", err)
            return
        mode = str(doc.get("acquisition_mode") or "short_up").lower()
        if mode == "daq":
            tier_err = self._validate_tier_capacity()
            if tier_err:
                QMessageBox.warning(self, "DAQ Capacity", tier_err)
                return
        try:
            import yaml  # type: ignore
            self._cfg_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        except Exception as e:
            QMessageBox.critical(self, "Write Error", f"Failed to save ccp.yaml: {e}")
            return

        try:
            from src.core.ipc.bus import create_ui_control_push  # type: ignore
            ctrl = create_ui_control_push()
            if ctrl is not None:
                msg = json.dumps({
                    "type": "reload_plugin",
                    "plugin": "CCP",
                    "session_keys": _session_key_store(),
                }).encode("utf-8")
                ctrl["control_push"].send(msg)
        except Exception:
            pass
        if save_only:
            return
        self.accept()

