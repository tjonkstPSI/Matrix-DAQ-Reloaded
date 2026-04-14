# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from PySide6.QtWidgets import (
        QDoubleSpinBox,
        QFormLayout,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )
except Exception:
    raise


def _coerce_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _pick_value(vals: Dict[str, Any], exact: List[str], substr: List[str]) -> Optional[float]:
    for k in exact:
        if k in vals:
            f = _coerce_float(vals[k])
            if f is not None:
                return f
    kl = {str(k).lower(): k for k in vals.keys()}
    for sub in substr:
        s = sub.lower()
        for lk, orig in kl.items():
            if s in lk:
                f = _coerce_float(vals[orig])
                if f is not None:
                    return f
    return None


class LoadBankControlPanel(QWidget):
    """Embeddable operator panel for LoadBank: status, controls, and metering readback."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._bus: Any = None
        self._cfg: Dict[str, Any] = {}
        self._primary_model = "—"
        self._secondary_model = "—"
        self._sp_max = 2000.0
        self._load_config_meta()
        self._build_ui()

    def _load_config_meta(self) -> None:
        cfg_path = Path(__file__).resolve().parents[3] / "configs" / "loadbank.yaml"
        try:
            import yaml  # type: ignore

            if cfg_path.exists():
                self._cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:
            self._cfg = {}
        lbs = self._cfg.get("load_banks") or {}
        if isinstance(lbs, dict):
            prim = lbs.get("primary") or {}
            sec = lbs.get("secondary") or {}
            if isinstance(prim, dict) and prim.get("model"):
                self._primary_model = str(prim.get("model"))
            if isinstance(sec, dict) and sec.get("model"):
                self._secondary_model = str(sec.get("model"))
        safety = self._cfg.get("safety") or {}
        lim = (safety.get("setpoint_limits_percent") or {}) if isinstance(safety, dict) else {}
        try:
            self._sp_max = float(lim.get("max", self._sp_max))
        except Exception:
            pass

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        # Status
        gb_status = QGroupBox("Status")
        gs = QVBoxLayout(gb_status)
        row_conn = QHBoxLayout()
        self._dot = QLabel("●")
        self._dot.setStyleSheet("color: #e74c3c; font-size: 16px;")
        self._lbl_conn = QLabel("Disconnected")
        self._lbl_conn.setStyleSheet("color: #e74c3c;")
        row_conn.addWidget(self._dot)
        row_conn.addWidget(self._lbl_conn)
        row_conn.addStretch(1)
        gs.addLayout(row_conn)
        self._lbl_primary = QLabel(f"Primary: {self._primary_model}")
        self._lbl_secondary = QLabel(f"Secondary: {self._secondary_model}")
        gs.addWidget(self._lbl_primary)
        gs.addWidget(self._lbl_secondary)
        root.addWidget(gb_status)

        # Control
        gb_ctl = QGroupBox("Control")
        gcv = QVBoxLayout(gb_ctl)
        row_tc = QHBoxLayout()
        self._btn_take = QPushButton("Take Control")
        self._btn_take.setCheckable(True)
        self._btn_take.clicked.connect(self._on_take_control)  # type: ignore
        self._btn_fan = QPushButton("Fan Power")
        self._btn_fan.setCheckable(True)
        self._btn_fan.clicked.connect(self._on_fan_power)  # type: ignore
        row_tc.addWidget(self._btn_take)
        row_tc.addWidget(self._btn_fan)
        gcv.addLayout(row_tc)

        form = QFormLayout()
        self._spin_kw = QDoubleSpinBox()
        self._spin_kw.setRange(0.0, max(1.0, self._sp_max))
        self._spin_kw.setDecimals(1)
        self._spin_kw.setSuffix(" kW")
        self._spin_kw.setSingleStep(1.0)
        form.addRow("Load setpoint", self._spin_kw)

        row_apply = QHBoxLayout()
        self._btn_apply = QPushButton("Apply Load")
        self._btn_apply.clicked.connect(self._on_apply_load)  # type: ignore
        self._btn_estop = QPushButton("Emergency Stop / Zero Load")
        self._btn_estop.setStyleSheet("font-weight: 600;")
        self._btn_estop.clicked.connect(self._on_zero_load)  # type: ignore
        row_apply.addWidget(self._btn_apply)
        row_apply.addWidget(self._btn_estop)
        gcv.addLayout(form)
        gcv.addLayout(row_apply)
        root.addWidget(gb_ctl)

        # Readback
        gb_rb = QGroupBox("Metering (readback)")
        grid = QGridLayout(gb_rb)
        self._lbl_vab = QLabel("—")
        self._lbl_vbc = QLabel("—")
        self._lbl_vca = QLabel("—")
        self._lbl_ia = QLabel("—")
        self._lbl_ib = QLabel("—")
        self._lbl_ic = QLabel("—")
        self._lbl_kw = QLabel("—")
        grid.addWidget(QLabel("Vab"), 0, 0)
        grid.addWidget(self._lbl_vab, 0, 1)
        grid.addWidget(QLabel("Vbc"), 0, 2)
        grid.addWidget(self._lbl_vbc, 0, 3)
        grid.addWidget(QLabel("Vca"), 1, 0)
        grid.addWidget(self._lbl_vca, 1, 1)
        grid.addWidget(QLabel("Ia"), 1, 2)
        grid.addWidget(self._lbl_ia, 1, 3)
        grid.addWidget(QLabel("Ib"), 2, 0)
        grid.addWidget(self._lbl_ib, 2, 1)
        grid.addWidget(QLabel("Ic"), 2, 2)
        grid.addWidget(self._lbl_ic, 2, 3)
        grid.addWidget(QLabel("Power"), 3, 0)
        grid.addWidget(self._lbl_kw, 3, 1, 1, 3)
        root.addWidget(gb_rb)
        root.addStretch(1)

    def set_bus(self, bus: Any) -> None:
        """Inject IPC control path (dict from create_ui_control_push) or None to lazy-connect."""
        self._bus = bus

    def set_link_status(self, telemetry_ok: bool, device_ready: Optional[bool] = None) -> None:
        """Core/UI link status (telemetry freshness). Optional device_ready refines label."""
        if telemetry_ok:
            self._dot.setStyleSheet("color: #2ecc71; font-size: 16px;")
            if device_ready is False:
                self._lbl_conn.setText("Connected (load bank not ready)")
                self._lbl_conn.setStyleSheet("color: #f39c12;")
            else:
                self._lbl_conn.setText("Connected")
                self._lbl_conn.setStyleSheet("color: #2ecc71;")
        else:
            self._dot.setStyleSheet("color: #e74c3c; font-size: 16px;")
            self._lbl_conn.setText("Disconnected")
            self._lbl_conn.setStyleSheet("color: #e74c3c;")

    def update_values(self, vals: Dict[str, Any]) -> None:
        """Refresh metering labels from telemetry ``values`` (same dict as console)."""
        if not isinstance(vals, dict):
            return
        exposes = self._cfg.get("expose_channels") or {}
        fan_alias = str(exposes.get("fan_alias", "Fan On"))

        v_ab = _pick_value(
            vals,
            ["Voltage A-B [Vrms]", "Voltage [Vrms]"],
            ["voltage a-b", "vab"],
        )
        v_bc = _pick_value(vals, ["Voltage B-C [Vrms]"], ["voltage b-c", "vbc"])
        v_ca = _pick_value(vals, ["Voltage C-A [Vrms]"], ["voltage c-a", "vca"])
        i_a = _pick_value(
            vals,
            ["Current L1 [A]", "Current [A]"],
            ["current l1", "ia"],
        )
        i_b = _pick_value(vals, ["Current L2 [A]"], ["current l2", "ib"])
        i_c = _pick_value(vals, ["Current L3 [A]"], ["current l3", "ic"])
        p_kw = _pick_value(
            vals,
            [
                str(exposes.get("measured_load_alias", "LB Measured Load")),
                "LB Measured Load",
            ],
            ["measured load", "power_kw"],
        )
        if p_kw is None:
            p_kw = _pick_value(vals, [str(exposes.get("power_alias", "Power"))], ["power"])

        def fmt_v(v: Optional[float]) -> str:
            if v is None:
                return "—"
            return f"{v:.1f} V"

        def fmt_a(v: Optional[float]) -> str:
            if v is None:
                return "—"
            return f"{v:.2f} A"

        def fmt_kw(v: Optional[float]) -> str:
            if v is None:
                return "—"
            return f"{v:.2f} kW"

        self._lbl_vab.setText(fmt_v(v_ab))
        self._lbl_vbc.setText(fmt_v(v_bc))
        self._lbl_vca.setText(fmt_v(v_ca))
        self._lbl_ia.setText(fmt_a(i_a))
        self._lbl_ib.setText(fmt_a(i_b))
        self._lbl_ic.setText(fmt_a(i_c))
        self._lbl_kw.setText(fmt_kw(p_kw))

        # Optional fan state sync from telemetry (0/1)
        fan_v = vals.get(fan_alias)
        if fan_v is not None:
            try:
                on = bool(int(float(fan_v)))
                self._btn_fan.blockSignals(True)
                self._btn_fan.setChecked(on)
                self._btn_fan.blockSignals(False)
            except Exception:
                pass

    def _ensure_bus(self) -> Any:
        if self._bus is not None:
            return self._bus
        try:
            from src.core.ipc.bus import create_ui_control_push

            self._bus = create_ui_control_push()
        except Exception:
            self._bus = None
        return self._bus

    def _send(self, payload: Dict[str, Any]) -> None:
        bus = self._ensure_bus()
        if bus is None or not isinstance(bus, dict):
            return
        push = bus.get("control_push")
        if push is None:
            return
        try:
            raw = json.dumps(payload).encode("utf-8")
            push.send(raw)
        except Exception:
            pass

    def _on_take_control(self) -> None:
        en = bool(self._btn_take.isChecked())
        self._send({"type": "loadbank_command", "action": "take_control", "enabled": en})

    def _on_fan_power(self) -> None:
        en = bool(self._btn_fan.isChecked())
        self._send({"type": "loadbank_command", "action": "fan_power", "enabled": en})

    def _on_apply_load(self) -> None:
        kw = float(self._spin_kw.value())
        self._send({"type": "loadbank_command", "action": "setpoint_kw", "value": kw})

    def _on_zero_load(self) -> None:
        self._spin_kw.setValue(0.0)
        self._send({"type": "loadbank_command", "action": "setpoint_kw", "value": 0.0})
