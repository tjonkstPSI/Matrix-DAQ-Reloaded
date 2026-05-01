# Author: T. Onkst | Date: 08182025

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QCheckBox,
        QDialog,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QLineEdit,
        QPushButton,
        QFileDialog,
        QWidget,
        QMessageBox,
    )
except Exception:
    raise


@dataclass
class LaunchSelections:
    selected_plugins: List[str]
    selected_displays: List[str]
    data_root: str
    test_cell: str
    data_mode: str  # 'real' | 'sim'
    imported_paths: List[str]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _app_registry_path() -> Path:
    return _project_root() / "configs" / "app_registry.yaml"


def _load_available_plugins() -> List[str]:
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(_app_registry_path().read_text(encoding="utf-8")) or {}
        out = [str(p) for p in (data.get("available_plugins") or []) if p]
        if out:
            return out
    except Exception:
        pass
    return [
        "NI_DAQ",
        "CAN",
        "CCP",
        "Calculated_Channels",
        "Cycle",
        "LoadBank",
        "Modbus",
        "Omega",
        "Statistics",
        "Vaisala",
    ]


def _load_available_displays() -> List[str]:
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(_app_registry_path().read_text(encoding="utf-8")) or {}
        out = [str(d) for d in (data.get("available_displays") or []) if d]
        if out:
            return out
    except Exception:
        pass
    return [
        "All Channels Table",
        "Main Test Monitor",
    ]


def _plugins_yaml_path() -> Path:
    return _project_root() / "configs" / "plugins.yaml"


def load_previous_selections() -> Dict[str, Any]:
    path = _plugins_yaml_path()
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def save_selections(sel: LaunchSelections) -> None:
    path = _plugins_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    ALWAYS_ON = {"EngineTest", "Channel_Manager"}
    selected_with_required = list({*sel.selected_plugins, *ALWAYS_ON})
    doc = {
        "selected_plugins": selected_with_required,
        "selected_displays": list(sel.selected_displays or []),
        "data_root": str(sel.data_root),
        "test_cell": str(sel.test_cell),
        # Persist data_mode selection for convenience; runtime will override per-plugin without writing plugin YAMLs
        "data_mode": str(sel.data_mode),
        "imported_paths": list(sel.imported_paths or []),
    }
    try:
        import yaml  # type: ignore
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    except Exception:
        # Best effort
        pass


class LaunchDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Launch Configuration")
        self.resize(720, 520)
        self._imported: List[str] = []
        self._plugin_ids = _load_available_plugins()
        self._display_templates = _load_available_displays()
        self._init_ui()
        self._load_previous()

    def _init_ui(self) -> None:
        v = QVBoxLayout(self)
        # Plugins
        v.addWidget(QLabel("Select Plugins:"))
        self.list_plugins = QListWidget()
        self.list_plugins.setSelectionMode(QListWidget.MultiSelection)
        for pid in self._plugin_ids:
            item = QListWidgetItem(pid)
            self.list_plugins.addItem(item)
        v.addWidget(self.list_plugins)
        # Displays multi-select (up to two) — directly below plugins
        v.addWidget(QLabel("Select Data Displays (up to 2):"))
        self.list_displays = QListWidget()
        self.list_displays.setSelectionMode(QListWidget.MultiSelection)
        for name in self._display_templates:
            self.list_displays.addItem(QListWidgetItem(name))
        v.addWidget(self.list_displays)
        # Data root + browse
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Data Root:"))
        self.edit_data_root = QLineEdit()
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._choose_data_root)  # type: ignore
        row1.addWidget(self.edit_data_root, 1)
        row1.addWidget(btn_browse)
        v.addLayout(row1)
        # Test Cell
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Test Cell:"))
        self.edit_test_cell = QLineEdit()
        row2.addWidget(self.edit_test_cell, 1)
        v.addLayout(row2)
        self.chk_offline = QCheckBox("Offline Mode (simulated data)")
        v.addWidget(self.chk_offline)
        # Import old configs
        row4 = QHBoxLayout()
        self.lbl_import = QLabel("Imported: 0 file(s)")
        btn_import = QPushButton("Import Configs…")
        btn_import.clicked.connect(self._import_configs)  # type: ignore
        row4.addWidget(self.lbl_import)
        row4.addStretch(1)
        row4.addWidget(btn_import)
        v.addLayout(row4)
        # Actions
        rowA = QHBoxLayout()
        rowA.addStretch(1)
        btn_exit = QPushButton("Exit")
        btn_launch = QPushButton("Launch")
        btn_exit.clicked.connect(self.reject)  # type: ignore
        btn_launch.clicked.connect(self._on_launch)  # type: ignore
        rowA.addWidget(btn_exit)
        rowA.addWidget(btn_launch)
        v.addLayout(rowA)

    def _load_previous(self) -> None:
        prev = load_previous_selections()
        if not prev:
            return
        try:
            pr = str(prev.get("data_root", ""))
            tc = str(prev.get("test_cell", ""))
            dm = str(prev.get("data_mode", "real")).lower()
            selected = set([str(x) for x in (prev.get("selected_plugins") or [])])
            disp_sel = set([str(x) for x in (prev.get("selected_displays") or [])])
        except Exception:
            pr, tc, dm, selected, disp_sel = "", "", "real", set(), set()
        self.edit_data_root.setText(pr)
        self.edit_test_cell.setText(tc)
        self.chk_offline.setChecked(dm == "sim")
        for i in range(self.list_plugins.count()):
            it = self.list_plugins.item(i)
            if it.text() in selected:
                it.setSelected(True)
        for i in range(self.list_displays.count()):
            it = self.list_displays.item(i)
            if it.text() in disp_sel:
                it.setSelected(True)

    def _choose_data_root(self) -> None:
        start = self.edit_data_root.text().strip() or str(_project_root())
        path = QFileDialog.getExistingDirectory(self, "Select Data Root", start)
        if path:
            self.edit_data_root.setText(path)

    def _import_configs(self) -> None:
        # Stub: let user select multiple YAML files; we do not copy here
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Import Config YAMLs",
            str(_project_root() / "configs"),
            "YAML Files (*.yaml *.yml)"
        )
        if files:
            self._imported = files
            self.lbl_import.setText(f"Imported: {len(files)} file(s)")

    def _on_launch(self) -> None:
        selected: List[str] = []
        for it in self.list_plugins.selectedItems():
            selected.append(it.text())
        selected_displays: List[str] = []
        for it in self.list_displays.selectedItems():
            selected_displays.append(it.text())
        data_root = self.edit_data_root.text().strip()
        test_cell = self.edit_test_cell.text().strip()
        data_mode = "sim" if self.chk_offline.isChecked() else "real"
        if not selected:
            QMessageBox.warning(self, "Missing selection", "Please select at least one plugin.")
            return
        if len(selected_displays) > 2:
            QMessageBox.warning(self, "Too many displays", "Select up to two data displays.")
            return
        if not data_root:
            QMessageBox.warning(self, "Missing data root", "Please choose a data root folder.")
            return
        save_selections(LaunchSelections(
            selected_plugins=selected,
            selected_displays=selected_displays,
            data_root=data_root,
            test_cell=test_cell,
            data_mode=data_mode,
            imported_paths=self._imported,
        ))
        self.accept()


