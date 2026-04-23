# Author: T. Onkst | Date: 04212026

from __future__ import annotations

import json
import socket
import struct
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from PySide6.QtWidgets import (
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QTextEdit,
        QVBoxLayout,
    )
except Exception:
    raise


class LoadBankConfigDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure Load Bank")
        self.resize(760, 560)
        self._cfg_path = Path(__file__).resolve().parents[3] / "configs" / "loadbank.yaml"
        self._maps_dir = Path(__file__).resolve().parents[3] / "configs" / "loadbanks"
        self._cfg: Dict[str, Any] = {}
        self._models: List[Tuple[str, str]] = []  # [(display_name, map_file_path), ...]
        self._init_ui()
        self._load_models()
        self._load()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)

        form = QFormLayout()

        self.cmb_primary_model = QComboBox(self)
        self.cmb_primary_model.setEditable(False)
        self.txt_primary_ip = QLineEdit(self)
        self.txt_primary_ip.setPlaceholderText("192.168.100.1")

        self.cmb_secondary_model = QComboBox(self)
        self.cmb_secondary_model.setEditable(False)
        self.txt_secondary_ip = QLineEdit(self)
        self.txt_secondary_ip.setPlaceholderText("192.168.100.2")

        self.txt_voltage = QLineEdit(self)
        self.txt_voltage.setPlaceholderText("480")
        self.cmb_phase = QComboBox(self)
        self.cmb_phase.addItems(["1 phase", "3 phase"])

        form.addRow("Primary Load Bank", self.cmb_primary_model)
        form.addRow("Primary Load Bank IP", self.txt_primary_ip)
        form.addRow("Secondary Load Bank", self.cmb_secondary_model)
        form.addRow("Secondary Load Bank IP", self.txt_secondary_ip)
        form.addRow("Voltage", self.txt_voltage)
        form.addRow("Phase", self.cmb_phase)
        root.addLayout(form)

        row = QHBoxLayout()
        self.btn_test = QPushButton("Test Connection", self)
        self.btn_test.clicked.connect(self._run_test_connection)  # type: ignore
        row.addWidget(self.btn_test)
        row.addStretch(1)
        root.addLayout(row)

        root.addWidget(QLabel("Connection test log"))
        self.txt_log = QTextEdit(self)
        self.txt_log.setReadOnly(True)
        self.txt_log.setMinimumHeight(220)
        root.addWidget(self.txt_log)

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

    def _load_models(self) -> None:
        self._models = []
        files = sorted(self._maps_dir.glob("*.yaml"))
        for p in files:
            model_name = p.stem
            try:
                data = self._read_yaml(p)
                model_name = str(data.get("model") or model_name)
            except Exception:
                pass
            self._models.append((model_name, str(p.as_posix())))

        # Keep UI usable even if no map files are present.
        if not self._models:
            self._models = [("Acme-LB100", "configs/loadbanks/Acme-LB100.yaml")]

        self.cmb_primary_model.clear()
        self.cmb_secondary_model.clear()
        self.cmb_secondary_model.addItem("None", "")
        for model_name, map_path in self._models:
            self.cmb_primary_model.addItem(model_name, map_path)
            self.cmb_secondary_model.addItem(model_name, map_path)

    def _load(self) -> None:
        self._cfg = self._read_yaml(self._cfg_path)
        primary_model = ""
        primary_ip = ""
        secondary_model = ""
        secondary_ip = ""
        voltage = ""
        phase = "3 phase"

        lb_block = self._cfg.get("load_banks") or {}
        if isinstance(lb_block, dict):
            p = lb_block.get("primary") or {}
            s = lb_block.get("secondary") or {}
            if isinstance(p, dict):
                primary_model = str(p.get("model") or "")
                primary_ip = str(p.get("ip_address") or "")
            if isinstance(s, dict):
                secondary_model = str(s.get("model") or "")
                secondary_ip = str(s.get("ip_address") or "")

        # Backward-compatibility fallback from legacy shape.
        if not primary_model:
            primary_model = str(((self._cfg.get("model") or {}).get("selected")) or "")
        if not primary_ip:
            primary_ip = str(((self._cfg.get("connection") or {}).get("host")) or "")

        electrical = self._cfg.get("electrical") or {}
        if isinstance(electrical, dict):
            v = electrical.get("voltage")
            if v is not None:
                voltage = str(v)
            ph = str(electrical.get("phase") or "").strip()
            if ph == "1":
                phase = "1 phase"
            elif ph == "3":
                phase = "3 phase"

        self._set_model_combo(self.cmb_primary_model, primary_model)
        self._set_model_combo(self.cmb_secondary_model, secondary_model)
        self.txt_primary_ip.setText(primary_ip)
        self.txt_secondary_ip.setText(secondary_ip)
        self.txt_voltage.setText(voltage or "480")
        self.cmb_phase.setCurrentText(phase)

    def _set_model_combo(self, combo: QComboBox, model_name: str) -> None:
        model_name = (model_name or "").strip()
        if not model_name:
            idx = combo.findText("None")
            if idx >= 0:
                combo.setCurrentIndex(idx)
            return
        idx = combo.findText(model_name)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _model_info(self, combo: QComboBox) -> Tuple[str, str]:
        name = combo.currentText().strip()
        map_file = str(combo.currentData() or "").strip()
        return name, map_file

    def _build_doc(self) -> Dict[str, Any]:
        doc: Dict[str, Any] = dict(self._cfg)
        primary_model, primary_map = self._model_info(self.cmb_primary_model)
        secondary_model, secondary_map = self._model_info(self.cmb_secondary_model)
        primary_ip = self.txt_primary_ip.text().strip()
        secondary_ip = self.txt_secondary_ip.text().strip()

        # "None" selection means no secondary loadbank.
        if secondary_model == "None" or not secondary_map:
            secondary_model = ""
            secondary_map = ""
            secondary_ip = ""

        try:
            voltage = int(float(self.txt_voltage.text().strip() or "480"))
        except Exception:
            voltage = 480
        phase = 1 if self.cmb_phase.currentText().startswith("1") else 3

        doc["enabled"] = bool(doc.get("enabled", True))
        doc["mode"] = str(doc.get("mode", "sim"))
        doc["recording_rate_hz"] = int(doc.get("recording_rate_hz", 10))

        doc["electrical"] = {"voltage": voltage, "phase": phase}
        doc["load_banks"] = {
            "primary": {
                "model": primary_model,
                "map_file": primary_map,
                "ip_address": primary_ip,
                "port": 502,
                "unit_id": 1,
                "enabled": bool(primary_ip),
            },
            "secondary": {
                "model": secondary_model,
                "map_file": secondary_map,
                "ip_address": secondary_ip,
                "port": 502,
                "unit_id": 1,
                "enabled": False if not secondary_model else bool(secondary_ip),
            },
        }

        # Future runtime-oriented shape for multi-device support.
        devices = [
            {
                "role": "primary",
                "name": "Primary",
                "model": primary_model,
                "map_file": primary_map,
                "connection": {"host": primary_ip, "port": 502, "unit_id": 1},
                "enabled": bool(primary_ip),
            },
        ]
        if secondary_model:
            devices.append({
                "role": "secondary",
                "name": "Secondary",
                "model": secondary_model,
                "map_file": secondary_map,
                "connection": {"host": secondary_ip, "port": 502, "unit_id": 1},
                "enabled": bool(secondary_ip),
            })
        doc["devices"] = devices

        # Backward-compatible legacy keys expected by current runtime.
        doc["connection"] = {"host": primary_ip or "127.0.0.1", "port": 502, "unit_id": 1}
        doc["model"] = {"selected": primary_model or None, "map_file": primary_map}
        return doc

    def _save_and_reload(self) -> bool:
        doc = self._build_doc()
        primary_ip = str((((doc.get("load_banks") or {}).get("primary") or {}).get("ip_address") or "")).strip()
        if not primary_ip:
            QMessageBox.warning(self, "Missing Primary IP", "Primary Load Bank IP is required.")
            return False

        try:
            import yaml  # type: ignore
            self._cfg_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
            self._cfg = dict(doc)
        except Exception as e:
            QMessageBox.critical(self, "Write Error", f"Failed to save loadbank.yaml: {e}")
            return False

        try:
            from src.core.ipc.bus import create_ui_control_push  # type: ignore
            ctrl = create_ui_control_push()
            if ctrl is not None:
                msg = json.dumps({"type": "reload_plugin", "plugin": "LoadBank"}).encode("utf-8")
                ctrl["control_push"].send(msg)
        except Exception:
            pass
        return True

    def _append_log(self, text: str) -> None:
        self.txt_log.append(text)

    def _modbus_probe(self, host: str, port: int, unit_id: int = 1, timeout_s: float = 1.5) -> Tuple[bool, str]:
        """
        Minimal Modbus/TCP probe:
        send Read Holding Registers (0x03) for address 0, quantity 1 and check for response.
        """
        tid = 1
        req = struct.pack(">HHHBBHH", tid, 0, 6, unit_id, 0x03, 0, 1)
        try:
            with socket.create_connection((host, int(port)), timeout=timeout_s) as sock:
                sock.settimeout(timeout_s)
                sock.sendall(req)
                resp = sock.recv(260)
        except Exception as e:
            return False, f"socket error: {e}"

        if len(resp) < 9:
            return False, f"short response ({len(resp)} bytes)"
        try:
            r_tid, _pid, _length, r_uid = struct.unpack(">HHHB", resp[:7])
            fc = resp[7]
        except Exception as e:
            return False, f"decode error: {e}"

        if r_tid != tid:
            return False, f"transaction mismatch (tx={tid}, rx={r_tid})"
        if r_uid != unit_id:
            return False, f"unit-id mismatch (tx={unit_id}, rx={r_uid})"

        # Exception response still proves request/response path.
        if fc & 0x80:
            exc = resp[8] if len(resp) > 8 else -1
            return True, f"modbus exception response (code={exc})"
        return True, "modbus response received"

    def _run_test_connection(self) -> None:
        doc = self._build_doc()
        lbs = doc.get("load_banks") or {}
        primary = lbs.get("primary") or {}
        secondary = lbs.get("secondary") or {}

        self.txt_log.clear()
        self._append_log("Starting load bank connectivity test...")

        for role, lb in (("Primary", primary), ("Secondary", secondary)):
            if not isinstance(lb, dict):
                continue
            host = str(lb.get("ip_address") or "").strip()
            if not host:
                self._append_log(f"[{role}] skipped (no IP configured)")
                continue
            model = str(lb.get("model") or "-")
            port = int(lb.get("port", 502))
            unit_id = int(lb.get("unit_id", 1))

            self._append_log(f"[{role}] model={model}, target={host}:{port}, unit_id={unit_id}")
            self._append_log(f"[{role}] sending Modbus/TCP probe...")
            ok, detail = self._modbus_probe(host, port=port, unit_id=unit_id)
            if ok:
                self._append_log(f"[{role}] PASS: {detail}")
            else:
                self._append_log(f"[{role}] FAIL: {detail}")

        self._append_log("Connectivity test complete.")

    def _on_accept(self) -> None:
        if not self._save_and_reload():
            return
        self.accept()

