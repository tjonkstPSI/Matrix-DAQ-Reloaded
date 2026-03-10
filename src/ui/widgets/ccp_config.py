from __future__ import annotations

import json
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (
        QDialog,
        QVBoxLayout,
        QHBoxLayout,
        QFormLayout,
        QLineEdit,
        QComboBox,
        QPushButton,
        QFileDialog,
        QLabel,
        QTextEdit,
        QDialogButtonBox,
        QMessageBox,
        QListWidget,
        QListWidgetItem,
    )
except Exception:
    raise


class CCPConfigDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure CCP")
        self.resize(900, 760)
        self._cfg_path = Path(__file__).resolve().parents[3] / "configs" / "ccp.yaml"
        self._cfg: Dict[str, Any] = {}
        self._channels: List[str] = []
        self._a2l_meta: Dict[str, Dict[str, Any]] = {}
        self._test_run_id: str = ""
        self._sub = None
        self._init_ui()
        self._load()
        self._init_status_subscriber()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        form = QFormLayout()

        self.txt_interface = QLineEdit(self)
        self.cmb_baudrate = QComboBox(self)
        self.cmb_baudrate.addItems(["125000", "250000", "500000", "1000000"])
        self.txt_access_key = QLineEdit(self)
        self.txt_access_key.setPlaceholderText("Hex key, e.g. ABCDEF0F")

        form.addRow("CAN interface", self.txt_interface)
        form.addRow("Baudrate", self.cmb_baudrate)
        form.addRow("Access key (hex)", self.txt_access_key)

        self.txt_a2l_path = QLineEdit(self)
        btn_browse = QPushButton("Browse A2L...", self)
        btn_browse.clicked.connect(self._browse_a2l)  # type: ignore
        btn_load_channels = QPushButton("Load Channels from A2L", self)
        btn_load_channels.clicked.connect(self._reload_channels_from_a2l)  # type: ignore
        root.addLayout(form)
        root.addWidget(QLabel("A2L path"))
        root.addWidget(self.txt_a2l_path)
        browse_row = QHBoxLayout()
        browse_row.addWidget(btn_browse)
        browse_row.addWidget(btn_load_channels)
        root.addLayout(browse_row)

        self.txt_prefix = QLineEdit(self)
        self.cmb_poll_interval = QComboBox(self)
        self.cmb_poll_interval.addItems(
            [
                "10 (High)",
                "50",
                "100 (Low)",
                "High",
                "Low",
            ]
        )

        form2 = QFormLayout()
        form2.addRow("Naming prefix", self.txt_prefix)
        form2.addRow("Poll interval", self.cmb_poll_interval)
        root.addLayout(form2)

        root.addWidget(QLabel("Channel filter"))
        self.txt_filter = QLineEdit(self)
        self.txt_filter.setPlaceholderText("Type to filter channel names...")
        self.txt_filter.textChanged.connect(self._apply_channel_filter)  # type: ignore
        root.addWidget(self.txt_filter)

        root.addWidget(QLabel("A2L channels (checkbox selection)"))
        self.list_channels = QListWidget(self)
        self.list_channels.setMinimumHeight(220)
        root.addWidget(self.list_channels)

        test_row = QHBoxLayout()
        self.btn_test = QPushButton("Test CCP Connection/Poll")
        self.btn_test.clicked.connect(self._run_test)  # type: ignore
        test_row.addWidget(self.btn_test)
        test_row.addStretch(1)
        root.addLayout(test_row)

        root.addWidget(QLabel("CCP test terminal"))
        self.txt_terminal = QTextEdit(self)
        self.txt_terminal.setReadOnly(True)
        self.txt_terminal.setMinimumHeight(170)
        root.addWidget(self.txt_terminal)

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
        session = self._cfg.get("session") or {}
        security = self._cfg.get("security") or {}
        a2l = self._cfg.get("a2l") or {}
        meas = self._cfg.get("measurements") or {}

        self.txt_interface.setText(str(session.get("interface", "CAN1")))
        baud = str(session.get("baudrate", "250000"))
        idx = self.cmb_baudrate.findText(baud)
        self.cmb_baudrate.setCurrentIndex(idx if idx >= 0 else 1)
        self.txt_access_key.setText(str(security.get("access_key", "")))
        self.txt_a2l_path.setText(str(a2l.get("path", "")))
        self.txt_prefix.setText(str(meas.get("naming_prefix", "CCP_")))
        poll_ms = int(self._cfg.get("poll_interval_ms", 100))
        if poll_ms <= 10:
            self.cmb_poll_interval.setCurrentText("10 (High)")
        elif poll_ms <= 50:
            self.cmb_poll_interval.setCurrentText("50")
        else:
            self.cmb_poll_interval.setCurrentText("100 (Low)")

        selected_names: List[str] = []
        for item in meas.get("list", []) or []:
            if isinstance(item, dict) and bool(item.get("enabled", True)) and item.get("name"):
                selected_names.append(str(item.get("name")))
        self._reload_channels_from_a2l(selected_names=selected_names)

    def _browse_a2l(self) -> None:
        start = self.txt_a2l_path.text().strip() or str(Path.cwd())
        path, _ = QFileDialog.getOpenFileName(self, "Select A2L file", start, "A2L files (*.a2l);;All files (*.*)")
        if path:
            self.txt_a2l_path.setText(path)
            self._reload_channels_from_a2l()

    def _parse_address(self, token: str) -> int | None:
        try:
            s = str(token).strip()
            if s.startswith(("0x", "0X")):
                return int(s, 16)
            return int(s, 10)
        except Exception:
            return None

    def _parse_a2l_channels(self, path: Path) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        if not path.exists():
            return out

        def _extract_quoted(text: str) -> List[str]:
            vals: List[str] = []
            s = text
            while '"' in s:
                try:
                    _, rest = s.split('"', 1)
                    q, s = rest.split('"', 1)
                    vals.append(q)
                except Exception:
                    break
            return vals
        data_types = {
            "UBYTE",
            "SBYTE",
            "UWORD",
            "SWORD",
            "ULONG",
            "SLONG",
            "FLOAT32_IEEE",
            "FLOAT64_IEEE",
        }
        compu_units: Dict[str, str] = {}

        # Pass 1: parse COMPU_METHOD units with explicit RAT_FUNC handling.
        in_compu = False
        compu_name: str | None = None
        rat_mode = False
        rat_q_count = 0
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if line.startswith("/begin COMPU_METHOD"):
                parts = line.split()
                compu_name = str(parts[2]).strip() if len(parts) > 2 else None
                in_compu = True
                rat_mode = False
                rat_q_count = 0
                continue
            if line.startswith("/end COMPU_METHOD"):
                in_compu = False
                compu_name = None
                rat_mode = False
                rat_q_count = 0
                continue
            if not in_compu or compu_name is None:
                continue
            if line.startswith("RAT_FUNC"):
                rat_mode = True
                rat_q_count = 0
                continue
            if not rat_mode:
                continue
            quoted = _extract_quoted(line)
            if not quoted:
                # non-quoted content exits RAT_FUNC quote sequence
                continue
            for q in quoted:
                rat_q_count += 1
                if rat_q_count == 2:
                    # RAT_FUNC expected sequence: 1=format, 2=unit
                    compu_units[compu_name] = str(q).strip()
                    rat_mode = False
                    break

        # Pass 2: parse MEASUREMENT/CHARACTERISTIC channel metadata.
        in_block = False
        cur_name: str | None = None
        cur_addr: int | None = None
        cur_type: str | None = None
        cur_compu_ref: str | None = None
        cur_limits: tuple[float, float] | None = None

        lines2 = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for raw in lines2:
            line = raw.strip()
            if line.startswith("/begin MEASUREMENT") or line.startswith("/begin CHARACTERISTIC"):
                parts = line.split()
                cur_name = str(parts[2]).strip() if len(parts) > 2 else None
                cur_addr = None
                cur_type = None
                cur_compu_ref = None
                cur_limits = None
                in_block = True
                continue
            if line.startswith("/end MEASUREMENT") or line.startswith("/end CHARACTERISTIC"):
                if in_block and cur_name:
                    dtype = str(cur_type or "")
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
                    size = int(size_map.get(dtype, 4))
                    if size > 5:
                        size = 5
                    out[cur_name] = {
                        "name": cur_name,
                        "address": cur_addr,
                        "address_extension": 0,
                        "data_type": dtype,
                        "size": size,
                        "limits": list(cur_limits) if cur_limits else None,
                        "unit": str(compu_units.get(str(cur_compu_ref or ""), "")).strip(),
                    }
                in_block = False
                cur_name = None
                continue
            if not in_block or cur_name is None:
                continue
            token = line.split()[0] if line else ""
            if cur_type is None and token in data_types:
                cur_type = token
                continue
            if cur_compu_ref is None and "/* Conversion */" in line and token:
                cur_compu_ref = token
                continue
            if cur_compu_ref is None and cur_type is not None and token.startswith("Compu_"):
                cur_compu_ref = token
                continue
            if line.startswith("ECU_ADDRESS") or line.startswith("ADDRESS"):
                parts = line.split()
                if len(parts) >= 2:
                    cur_addr = self._parse_address(parts[1])
                continue
            if line and line[0].isdigit():
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        candidate = (float(parts[0]), float(parts[1]))
                        if cur_limits is None or cur_limits == (0.0, 0.0):
                            cur_limits = candidate
                    except Exception:
                        pass
        return out

    def _reload_channels_from_a2l(self, selected_names: List[str] | None = None) -> None:
        selected = set(selected_names or self._checked_channels())
        self.list_channels.clear()
        self._channels = []
        self._a2l_meta = {}
        path = Path(self.txt_a2l_path.text().strip())
        if not path.exists():
            return
        try:
            self._a2l_meta = self._parse_a2l_channels(path)
            names = sorted(self._a2l_meta.keys())
        except Exception:
            names = []
        self._channels = names
        for name in names:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in selected else Qt.Unchecked)
            meta = self._a2l_meta.get(name, {})
            unit = str(meta.get("unit", "") or "").strip()
            display = f"{name}  |  {unit or '-'}"
            item.setText(display)
            item.setData(Qt.UserRole, {"name": name, "meta": meta})
            self.list_channels.addItem(item)
        self._apply_channel_filter()

    def _checked_channels(self) -> List[str]:
        out: List[str] = []
        for i in range(self.list_channels.count()):
            it = self.list_channels.item(i)
            if it is not None and it.checkState() == Qt.Checked:
                data = it.data(Qt.UserRole) or {}
                if isinstance(data, dict) and data.get("name"):
                    out.append(str(data.get("name")))
        return out

    def _checked_measurements(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for i in range(self.list_channels.count()):
            it = self.list_channels.item(i)
            if it is None or it.checkState() != Qt.Checked:
                continue
            data = it.data(Qt.UserRole) or {}
            name = str(data.get("name") or "").strip() if isinstance(data, dict) else ""
            meta = data.get("meta") if isinstance(data, dict) else {}
            if not name:
                continue
            entry: Dict[str, Any] = {
                "name": str(name),
                "unit_override": None,
                "enabled": True,
            }
            # Persist type/length/address metadata so mixed channel sets poll consistently.
            if isinstance(meta, dict):
                unit = str(meta.get("unit", "") or "").strip()
                if unit:
                    entry["unit_override"] = unit
                    entry["unit"] = unit
                else:
                    # Keep explicit empty strings instead of None for consistent plugin unit mapping.
                    entry["unit_override"] = ""
                    entry["unit"] = ""
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
        for i in range(self.list_channels.count()):
            it = self.list_channels.item(i)
            if it is None:
                continue
            data = it.data(Qt.UserRole) or {}
            base_name = str(data.get("name") or it.text()).lower() if isinstance(data, dict) else it.text().lower()
            if not q:
                visible = True
            elif "*" in q:
                # Wildcard mode when user supplies '*'
                visible = bool(fnmatch(base_name, q))
            else:
                # Default mode: prefix match
                visible = base_name.startswith(q)
            it.setHidden(not visible)

    def _as_bool(self, text: str) -> bool:
        return str(text).strip().lower() in {"1", "true", "yes", "on"}

    def _poll_interval_ms(self) -> int:
        t = self.cmb_poll_interval.currentText().strip().lower()
        if t.startswith("100") or t == "low":
            return 100
        if t.startswith("50"):
            return 50
        if t.startswith("10") or t == "high":
            return 10
        return 100

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
        ts = time.strftime("%H:%M:%S")
        self.txt_terminal.append(f"[{ts}] {line}")

    def _run_test(self) -> None:
        self._on_accept(save_only=True)
        try:
            from src.core.ipc.bus import create_ui_control_push
            ctrl = create_ui_control_push()
            if ctrl is None:
                self._append_terminal("ERROR: IPC control path unavailable.")
                return
            self._test_run_id = f"ccp_test_{int(time.time() * 1000)}"
            msg = json.dumps({"type": "ccp_test", "run_id": self._test_run_id}).encode("utf-8")
            ctrl["control_push"].send(msg)
            self._append_terminal("CCP test requested...")
        except Exception as e:
            self._append_terminal(f"ERROR: failed to request test: {e}")

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
                marker = "OK" if ok else "FAIL"
                self._append_terminal(f"{marker} [{step}] {detail}")
                if bool(msg.get("done", False)):
                    self._append_terminal("CCP test complete.")
        except Exception:
            pass

    def _on_accept(self, save_only: bool = False) -> None:
        mode = "real"
        interface = self.txt_interface.text().strip()
        a2l_path = self.txt_a2l_path.text().strip()
        if not interface:
            QMessageBox.warning(self, "Missing interface", "session.interface is required in real mode.")
            return
        if not a2l_path:
            QMessageBox.warning(self, "Missing A2L", "a2l.path is required in real mode.")
            return

        measurements = self._checked_measurements()
        if not measurements:
            QMessageBox.warning(self, "Missing channels", "Add at least one poll measurement name.")
            return

        doc: Dict[str, Any] = dict(self._cfg)
        doc["enabled"] = bool(doc.get("enabled", True))
        doc["mode"] = mode
        doc["recording_rate_hz"] = int(doc.get("recording_rate_hz", 10))
        doc["poll_interval_ms"] = self._poll_interval_ms()
        doc["poll_channels_per_tick"] = int(doc.get("poll_channels_per_tick", 1))
        doc["io_timeout_s"] = float(doc.get("io_timeout_s", 0.05))
        doc["poll_endian"] = "big"
        doc["mta_addr_endian"] = "big"
        doc["addr_ext_high"] = False
        doc["reconnect_interval_s"] = float(doc.get("reconnect_interval_s", 2.0))

        doc["session"] = {
            "interface": interface,
            "baudrate": int(self.cmb_baudrate.currentText().strip() or "250000"),
            "tx_id": doc.get("session", {}).get("tx_id", "0x0CFF50F9"),
            "rx_id": doc.get("session", {}).get("rx_id", "0x0CFF5100"),
            "station_address": doc.get("session", {}).get("station_address", "0x0"),
            "is_extended": bool(doc.get("session", {}).get("is_extended", True)),
        }

        doc["security"] = {
            "seed_resource": doc.get("security", {}).get("seed_resource", "0x01"),
            "seed_ctr": doc.get("security", {}).get("seed_ctr", "0x07"),
            "connect_ctr": doc.get("security", {}).get("connect_ctr", "0x19"),
            "unlock_ctr": doc.get("security", {}).get("unlock_ctr", "0x08"),
            "access_key": self.txt_access_key.text().strip(),
            "seed_endian": doc.get("security", {}).get("seed_endian", "big"),
            "sec_type": doc.get("security", {}).get("sec_type", "CAL"),
            "unlock_pad": doc.get("security", {}).get("unlock_pad", "0x55"),
            "force_unlock": bool(doc.get("security", {}).get("force_unlock", True)),
            "set_s_status": bool(doc.get("security", {}).get("set_s_status", True)),
            "s_status": doc.get("security", {}).get("s_status", "0x83"),
        }

        doc["a2l"] = {"path": a2l_path}
        doc["measurements"] = {
            "naming_prefix": self.txt_prefix.text().strip(),
            "list": measurements,
        }
        doc["writes"] = []

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
                import json as _json
                msg = _json.dumps({"type": "reload_plugin", "plugin": "CCP"}).encode("utf-8")
                ctrl["control_push"].send(msg)
        except Exception:
            pass
        if save_only:
            self._append_terminal("Config saved and CCP reload requested.")
            return
        self.accept()

