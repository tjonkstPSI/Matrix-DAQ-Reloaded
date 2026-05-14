# Author: T. Onkst | Date: 04212026

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from PySide6.QtWidgets import (
        QCheckBox,
        QDoubleSpinBox,
        QFormLayout,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QProgressBar,
        QPushButton,
        QSpinBox,
        QVBoxLayout,
        QWidget,
    )
except Exception:
    raise

try:
    from .cycle_chart import CycleChartWidget
except Exception:
    CycleChartWidget = None  # type: ignore


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

    _STATE_NAMES = {0: "Idle", 1: "Running", 2: "Paused", 3: "Complete"}

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._bus: Any = None
        self._cfg: Dict[str, Any] = {}
        self._primary_model = "—"
        self._secondary_model = "—"
        self._sp_max = 2000.0
        self._heartbeat_capable = False
        self._heartbeat_aliases: List[str] = []
        self._heartbeat_last_value: Optional[bool] = None
        self._heartbeat_last_change_ts = 0.0
        self._cycle_schedule: List[Tuple[float, float]] = []
        self._cycle_duration_s: float = 0.0
        self._cycle_loops_total: int = 1
        self._cycle_dwell_s: float = 0.0
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
            else:
                self._primary_model = "—"
            if isinstance(sec, dict) and sec.get("model"):
                self._secondary_model = str(sec.get("model"))
            else:
                self._secondary_model = "—"
        safety = self._cfg.get("safety") or {}
        lim = (safety.get("setpoint_limits_percent") or {}) if isinstance(safety, dict) else {}
        try:
            self._sp_max = float(lim.get("max", self._sp_max))
        except Exception:
            pass
        self._load_heartbeat_meta()
        self._load_cycle_schedule()

    def _load_heartbeat_meta(self) -> None:
        self._heartbeat_capable = False
        self._heartbeat_aliases = []
        self._heartbeat_last_value = None
        self._heartbeat_last_change_ts = 0.0

        lb_block = self._cfg.get("load_banks") or {}
        primary = (lb_block.get("primary") or {}) if isinstance(lb_block, dict) else {}
        map_file = ""
        if isinstance(primary, dict):
            map_file = str(primary.get("map_file") or "")
        if not map_file:
            map_file = str(((self._cfg.get("model") or {}).get("map_file")) or "")
        if not map_file:
            return

        root_dir = Path(__file__).resolve().parents[3]
        configs_dir = root_dir / "configs"
        mf = Path(map_file)
        candidates = [mf, (configs_dir / mf).resolve(), (root_dir / mf).resolve()]
        data: Dict[str, Any] = {}
        try:
            import yaml  # type: ignore
            for candidate in candidates:
                if candidate.exists():
                    data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
                    break
        except Exception:
            data = {}

        commands = data.get("commands") or {}
        if isinstance(commands, dict) and isinstance(commands.get("heartbeat"), dict):
            self._heartbeat_capable = True

        status = data.get("status") or {}
        if isinstance(status, dict):
            for key, cfg in status.items():
                if not isinstance(cfg, dict):
                    continue
                alias = str(cfg.get("alias") or key)
                if "heartbeat" in str(key).lower() or "heartbeat" in alias.lower():
                    self._heartbeat_aliases.append(alias)

        if self._heartbeat_capable and not self._heartbeat_aliases:
            self._heartbeat_aliases.append("LB700/UnitHeartbeat")

    def _refresh_control_labels(self) -> None:
        if hasattr(self, "_btn_take"):
            label = "Enable Remote Control" if self._heartbeat_capable else "Take Control"
            self._btn_take.setText(label)
        if hasattr(self, "_lbl_heartbeat") and not self._heartbeat_capable:
            self._lbl_heartbeat.setText("N/A")
            self._lbl_heartbeat.setStyleSheet("color: #888;")

    def _load_cycle_schedule(self) -> None:
        """Read cycle.yaml to get the schedule CSV for the chart widget."""
        import csv as csv_mod
        self._cycle_schedule = []
        self._cycle_duration_s = 0.0
        cyc_path = Path(__file__).resolve().parents[3] / "configs" / "cycle.yaml"
        try:
            import yaml  # type: ignore
            if cyc_path.exists():
                cyc_cfg = yaml.safe_load(cyc_path.read_text(encoding="utf-8")) or {}
            else:
                cyc_cfg = {}
        except Exception:
            cyc_cfg = {}
        csv_rel = (cyc_cfg.get("source") or {}).get("csv_path", "")
        exec_cfg = (cyc_cfg.get("execution") or {}) if isinstance(cyc_cfg.get("execution"), dict) else {}
        try:
            self._cycle_loops_total = max(1, int(exec_cfg.get("loops_total", 1)))
        except Exception:
            self._cycle_loops_total = 1
        try:
            self._cycle_dwell_s = max(0.0, float(exec_cfg.get("inter_loop_dwell_s", 0)))
        except Exception:
            self._cycle_dwell_s = 0.0
        if not csv_rel:
            return
        source_cfg = (cyc_cfg.get("source") or {}) if isinstance(cyc_cfg.get("source"), dict) else {}
        cols = (source_cfg.get("columns") or {}) if isinstance(source_cfg.get("columns"), dict) else {}
        col_time = str(cols.get("time", "Time"))
        col_load = str(cols.get("load", "Load"))
        configs_dir = Path(__file__).resolve().parents[3] / "configs"
        candidates = [Path(csv_rel), (configs_dir / csv_rel).resolve(), (configs_dir.parent / csv_rel).resolve()]
        rows: List[Tuple[float, float]] = []
        for cp in candidates:
            if cp.exists():
                try:
                    text = cp.read_text(encoding="utf-8-sig", errors="replace")
                    reader = csv_mod.DictReader(text.splitlines())
                    if reader.fieldnames and col_time in reader.fieldnames and col_load in reader.fieldnames:
                        for row in reader:
                            try:
                                rows.append((float(row.get(col_time, "")), float(row.get(col_load, ""))))
                            except (ValueError, TypeError):
                                continue
                    if not rows:
                        for row in csv_mod.reader(text.splitlines()):
                            if not row or row[0].startswith("#"):
                                continue
                            rows.append((float(row[0]), float(row[1])))
                except Exception:
                    pass
                break
        rows.sort(key=lambda x: x[0])
        self._cycle_schedule = rows
        self._cycle_duration_s = (rows[-1][0] - rows[0][0]) if len(rows) > 1 else max((t for t, _ in rows), default=0.0)

    def reload_config(self) -> None:
        """Re-read loadbank.yaml and refresh model labels and setpoint range."""
        self._load_config_meta()
        self._lbl_primary.setText(f"Primary: {self._primary_model}")
        self._lbl_secondary.setText(f"Secondary: {self._secondary_model}")
        self._spin_kw.setRange(0.0, max(1.0, self._sp_max))
        self._refresh_control_labels()
        if self._cycle_chart is not None and self._cycle_schedule and self._cycle_duration_s > 0:
            self._cycle_chart.set_schedule(
                self._cycle_schedule,
                self._cycle_duration_s,
                loops=self._cycle_loops_total,
                dwell_s=self._cycle_dwell_s,
            )

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
        self._refresh_control_labels()
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
        self._lbl_freq = QLabel("—")
        self._lbl_fan = QLabel("—")
        self._lbl_heartbeat = QLabel("N/A")
        self._lbl_heartbeat.setStyleSheet("color: #888;")
        grid.addWidget(QLabel("Vab"), 0, 0)
        grid.addWidget(self._lbl_vab, 0, 1)
        grid.addWidget(QLabel("Ia"), 0, 2)
        grid.addWidget(self._lbl_ia, 0, 3)
        grid.addWidget(QLabel("Vbc"), 1, 0)
        grid.addWidget(self._lbl_vbc, 1, 1)
        grid.addWidget(QLabel("Ib"), 1, 2)
        grid.addWidget(self._lbl_ib, 1, 3)
        grid.addWidget(QLabel("Vca"), 2, 0)
        grid.addWidget(self._lbl_vca, 2, 1)
        grid.addWidget(QLabel("Ic"), 2, 2)
        grid.addWidget(self._lbl_ic, 2, 3)
        grid.addWidget(QLabel("Power"), 3, 0)
        grid.addWidget(self._lbl_kw, 3, 1)
        grid.addWidget(QLabel("Freq"), 3, 2)
        grid.addWidget(self._lbl_freq, 3, 3)
        grid.addWidget(QLabel("Fan"), 4, 0)
        grid.addWidget(self._lbl_fan, 4, 1)
        grid.addWidget(QLabel("Heartbeat"), 4, 2)
        grid.addWidget(self._lbl_heartbeat, 4, 3)
        self._refresh_control_labels()
        root.addWidget(gb_rb)

        # Cycle Control
        gb_cyc = QGroupBox("Cycle Control")
        cyc_v = QVBoxLayout(gb_cyc)

        self._chk_start_with_test = QCheckBox("Start with Test")
        self._chk_start_with_test.toggled.connect(self._on_start_with_test)  # type: ignore
        cyc_v.addWidget(self._chk_start_with_test)

        row_state = QHBoxLayout()
        self._lbl_cyc_state = QLabel("State: Idle")
        self._lbl_cyc_state.setStyleSheet("font-weight: 600;")
        self._lbl_cyc_loop = QLabel("Loop: — / —")
        row_state.addWidget(self._lbl_cyc_state)
        row_state.addStretch(1)
        row_state.addWidget(self._lbl_cyc_loop)
        cyc_v.addLayout(row_state)

        row_info = QHBoxLayout()
        self._lbl_cyc_pos = QLabel("Position: 0.0s")
        self._lbl_cyc_sp = QLabel("Setpoint: 0 kW")
        row_info.addWidget(self._lbl_cyc_pos)
        row_info.addStretch(1)
        row_info.addWidget(self._lbl_cyc_sp)
        cyc_v.addLayout(row_info)

        self._cyc_progress = QProgressBar()
        self._cyc_progress.setRange(0, 1000)
        self._cyc_progress.setValue(0)
        self._cyc_progress.setTextVisible(True)
        self._cyc_progress.setFormat("%p%")
        self._cyc_progress.setFixedHeight(18)
        cyc_v.addWidget(self._cyc_progress)

        if CycleChartWidget is not None:
            self._cycle_chart = CycleChartWidget()
            self._cycle_chart.setFixedHeight(120)
            if self._cycle_schedule and self._cycle_duration_s > 0:
                self._cycle_chart.set_schedule(
                    self._cycle_schedule,
                    self._cycle_duration_s,
                    loops=self._cycle_loops_total,
                    dwell_s=self._cycle_dwell_s,
                )
            cyc_v.addWidget(self._cycle_chart)
        else:
            self._cycle_chart = None

        row_btns = QHBoxLayout()
        self._btn_cyc_play = QPushButton("Play")
        self._btn_cyc_play.clicked.connect(self._on_cycle_play)  # type: ignore
        self._btn_cyc_pause = QPushButton("Pause")
        self._btn_cyc_pause.clicked.connect(self._on_cycle_pause)  # type: ignore
        row_btns.addWidget(self._btn_cyc_play)
        row_btns.addWidget(self._btn_cyc_pause)

        row_btns.addWidget(QLabel("Seek:"))
        self._spin_seek = QDoubleSpinBox()
        self._spin_seek.setRange(0.0, max(1.0, self._cycle_duration_s))
        self._spin_seek.setDecimals(1)
        self._spin_seek.setSuffix(" s")
        self._spin_seek.setSingleStep(1.0)
        row_btns.addWidget(self._spin_seek)
        self._btn_seek = QPushButton("Go")
        self._btn_seek.clicked.connect(self._on_cycle_seek)  # type: ignore
        row_btns.addWidget(self._btn_seek)
        cyc_v.addLayout(row_btns)

        row_loops = QHBoxLayout()
        row_loops.addWidget(QLabel("Loops:"))
        self._spin_loops = QSpinBox()
        self._spin_loops.setRange(1, 999)
        self._spin_loops.setValue(1)
        self._spin_loops.valueChanged.connect(self._on_loops_changed)  # type: ignore
        row_loops.addWidget(self._spin_loops)
        row_loops.addStretch(1)
        cyc_v.addLayout(row_loops)

        root.addWidget(gb_cyc)
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

        vab_alias = str(exposes.get("voltage_ab_alias", "lVO_Ldb1"))
        vbc_alias = str(exposes.get("voltage_bc_alias", "lVO_Ldb2"))
        vca_alias = str(exposes.get("voltage_ca_alias", "lVO_Ldb3"))
        ia_alias = str(exposes.get("current_l1_alias", "lCT_Ldb1"))
        ib_alias = str(exposes.get("current_l2_alias", "lCT_Ldb2"))
        ic_alias = str(exposes.get("current_l3_alias", "lCT_Ldb3"))
        kw_alias = str(exposes.get("measured_load_alias", "lPO_LdbAct"))
        freq_alias = str(exposes.get("frequency_alias", "LB Frequency"))
        fan_alias = str(exposes.get("fan_alias", "lDG_Fan"))

        v_ab = _pick_value(vals, [vab_alias, "Voltage A-B [Vrms]"], ["voltage a-b", "vab", "lvo_ldb1"])
        v_bc = _pick_value(vals, [vbc_alias, "Voltage B-C [Vrms]"], ["voltage b-c", "vbc", "lvo_ldb2"])
        v_ca = _pick_value(vals, [vca_alias, "Voltage C-A [Vrms]"], ["voltage c-a", "vca", "lvo_ldb3"])
        i_a = _pick_value(vals, [ia_alias, "Current L1 [A]"], ["current l1", "ia", "lct_ldb1"])
        i_b = _pick_value(vals, [ib_alias, "Current L2 [A]"], ["current l2", "ib", "lct_ldb2"])
        i_c = _pick_value(vals, [ic_alias, "Current L3 [A]"], ["current l3", "ic", "lct_ldb3"])
        p_kw = _pick_value(vals, [kw_alias, "LB Measured Load"], ["measured load", "power_kw", "lpo_ldbact"])
        if p_kw is None:
            p_kw = _pick_value(vals, [str(exposes.get("power_alias", "Power"))], ["power"])
        freq = _pick_value(vals, [freq_alias], ["frequency", "freq", "hz"])

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

        def fmt_hz(v: Optional[float]) -> str:
            if v is None:
                return "—"
            return f"{v:.2f} Hz"

        self._lbl_vab.setText(fmt_v(v_ab))
        self._lbl_vbc.setText(fmt_v(v_bc))
        self._lbl_vca.setText(fmt_v(v_ca))
        self._lbl_ia.setText(fmt_a(i_a))
        self._lbl_ib.setText(fmt_a(i_b))
        self._lbl_ic.setText(fmt_a(i_c))
        self._lbl_kw.setText(fmt_kw(p_kw))
        self._lbl_freq.setText(fmt_hz(freq))

        # Fan readback (shared indicator -- may reflect A-side or B-side)
        fan_v = vals.get(fan_alias)
        if fan_v is not None:
            try:
                on = bool(int(float(fan_v)))
                self._lbl_fan.setText("ON (shared)" if on else "OFF")
                self._lbl_fan.setStyleSheet("color: #2ecc71; font-weight: 600;" if on else "color: #888;")
            except Exception:
                self._lbl_fan.setText("—")
        else:
            self._lbl_fan.setText("—")
            self._lbl_fan.setStyleSheet("")

        if not self._heartbeat_capable:
            self._lbl_heartbeat.setText("N/A")
            self._lbl_heartbeat.setStyleSheet("color: #888;")
        else:
            hb_v = _pick_value(
                vals,
                self._heartbeat_aliases,
                ["lb700/unitheartbeat", "unitheartbeat", "unit heartbeat"],
            )
            if hb_v is None:
                self._lbl_heartbeat.setText("—")
                self._lbl_heartbeat.setStyleSheet("")
            else:
                try:
                    on = bool(int(float(hb_v)))
                    now = time.monotonic()
                    if self._heartbeat_last_value is None:
                        self._heartbeat_last_change_ts = now
                    elif on != self._heartbeat_last_value:
                        self._heartbeat_last_change_ts = now
                    self._heartbeat_last_value = on

                    state = "HIGH" if on else "LOW"
                    if self._heartbeat_last_change_ts and (now - self._heartbeat_last_change_ts) <= 3.0:
                        self._lbl_heartbeat.setText(f"Pulsing ({state})")
                        self._lbl_heartbeat.setStyleSheet("color: #2ecc71; font-weight: 600;")
                    else:
                        self._lbl_heartbeat.setText(f"No pulse ({state})")
                        self._lbl_heartbeat.setStyleSheet("color: #f39c12; font-weight: 600;")
                except Exception:
                    self._lbl_heartbeat.setText("—")
                    self._lbl_heartbeat.setStyleSheet("")

        # Cycle telemetry
        cyc_state_val = vals.get("Cycle/state")
        if cyc_state_val is not None:
            state_int = int(float(cyc_state_val))
            state_name = self._STATE_NAMES.get(state_int, "Unknown")
            self._lbl_cyc_state.setText(f"State: {state_name}")
            colors = {0: "#888", 1: "#2ecc71", 2: "#f39c12", 3: "#3498db"}
            self._lbl_cyc_state.setStyleSheet(f"font-weight: 600; color: {colors.get(state_int, '#888')};")

        cyc_pos = vals.get("Cycle/position_s")
        if cyc_pos is not None:
            self._lbl_cyc_pos.setText(f"Position: {float(cyc_pos):.1f}s")
        if self._cycle_chart is not None:
            cyc_elapsed = vals.get("Cycle/elapsed_s")
            if cyc_elapsed is not None:
                self._cycle_chart.set_position(float(cyc_elapsed))
            elif cyc_pos is not None:
                cyc_loop_for_chart = vals.get("Cycle/loop_current")
                cyc_len_for_chart = vals.get("Cycle/schedule_len_s")
                try:
                    loop_idx = max(0, int(float(cyc_loop_for_chart or 1)) - 1)
                    elapsed_est = loop_idx * float(cyc_len_for_chart or self._cycle_duration_s) + float(cyc_pos)
                except Exception:
                    elapsed_est = float(cyc_pos)
                self._cycle_chart.set_position(elapsed_est)

        cyc_sp = vals.get("Cycle/setpoint_kw")
        if cyc_sp is not None:
            self._lbl_cyc_sp.setText(f"Setpoint: {float(cyc_sp):.1f} kW")

        cyc_loop = vals.get("Cycle/loop_current")
        cyc_total = vals.get("Cycle/loop_total")
        if cyc_loop is not None and cyc_total is not None:
            self._lbl_cyc_loop.setText(f"Loop: {int(float(cyc_loop))} / {int(float(cyc_total))}")

        cyc_pct = vals.get("Cycle/progress_pct")
        if cyc_pct is not None:
            self._cyc_progress.setValue(int(float(cyc_pct) * 10))

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
        self._send({"type": "loadbank_command", "action": "master_load", "enabled": True})
        kw = float(self._spin_kw.value())
        self._send({"type": "loadbank_command", "action": "setpoint_kw", "value": kw})

    def _on_zero_load(self) -> None:
        self._spin_kw.setValue(0.0)
        self._send({"type": "loadbank_command", "action": "setpoint_kw", "value": 0.0})
        self._send({"type": "loadbank_command", "action": "master_load", "enabled": False})

    def _on_start_with_test(self, checked: bool) -> None:
        self._send({"type": "cycle_set_start_with_test", "enabled": checked})

    def _on_cycle_play(self) -> None:
        self._send({"type": "cycle_play"})

    def _on_cycle_pause(self) -> None:
        self._send({"type": "cycle_pause"})

    def _on_cycle_seek(self) -> None:
        self._send({"type": "cycle_seek", "time_s": float(self._spin_seek.value())})

    def _on_loops_changed(self, value: int) -> None:
        self._send({"type": "cycle_set_loops", "loops": value})
