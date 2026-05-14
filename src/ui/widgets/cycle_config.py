# Author: T. Onkst | Date: 04212026

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from PySide6.QtCore import QMargins, QPointF, Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QPainter, QPen
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
        QMessageBox,
        QPushButton,
        QSpinBox,
        QVBoxLayout,
    )
except Exception:
    raise

try:
    from PySide6.QtCharts import QChart, QChartView, QLineSeries, QAreaSeries, QValueAxis
    _HAS_CHARTS = True
except Exception:
    _HAS_CHARTS = False

from .cycle_profile_math import build_expanded_cycle_profile


class CycleConfigDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure Cycle")
        self.resize(720, 520)
        self._project_root = Path(__file__).resolve().parents[3]
        self._cfg_path = self._project_root / "configs" / "cycle.yaml"
        self._cfg: Dict[str, Any] = {}
        self._series_refs: list = []
        self._init_ui()
        self._load(defer_preview=True)

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)

        src_box = QGroupBox("CSV Source")
        sf = QVBoxLayout(src_box)
        row = QHBoxLayout()
        self.txt_csv = QLineEdit(self)
        self.btn_browse = QPushButton("Browse…")
        self.btn_browse.clicked.connect(self._browse_csv)  # type: ignore
        self.btn_preview = QPushButton("Refresh preview")
        self.btn_preview.clicked.connect(self._refresh_preview)  # type: ignore
        row.addWidget(self.txt_csv)
        row.addWidget(self.btn_browse)
        row.addWidget(self.btn_preview)
        sf.addLayout(row)
        cols = QFormLayout()
        self.txt_col_time = QLineEdit(self)
        self.txt_col_load = QLineEdit(self)
        cols.addRow("Column name: time", self.txt_col_time)
        cols.addRow("Column name: load", self.txt_col_load)
        sf.addLayout(cols)
        sf.addWidget(QLabel("Cycle profile preview"))
        if _HAS_CHARTS:
            self._chart = QChart()
            self._chart.setBackgroundBrush(QColor("#1e1e1e"))
            self._chart.legend().hide()
            self._chart.setMargins(QMargins(4, 4, 4, 4))
            self._chart_view = QChartView(self._chart)
            self._chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._chart_view.setMinimumHeight(220)
            sf.addWidget(self._chart_view)
        else:
            self._chart = None
            self._chart_view = None
            lbl = QLabel("(PySide6-Charts not available for plot preview)")
            lbl.setMinimumHeight(60)
            sf.addWidget(lbl)
        self._preview_status = QLabel("")
        sf.addWidget(self._preview_status)
        root.addWidget(src_box)

        ex = QGroupBox("Execution")
        ef = QFormLayout(ex)
        self.spin_loops = QSpinBox(self)
        self.spin_loops.setRange(1, 100000)
        self.spin_loops.setValue(1)
        self.chk_start_with_test = QCheckBox("Start with test")
        self.spin_dwell = QSpinBox(self)
        self.spin_dwell.setRange(0, 86400)
        self.spin_dwell.setValue(0)
        ef.addRow("Loops total", self.spin_loops)
        ef.addRow(self.chk_start_with_test)
        ef.addRow("Inter-loop dwell (s)", self.spin_dwell)
        root.addWidget(ex)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        btns.accepted.connect(self._on_accept)  # type: ignore
        btns.rejected.connect(self.reject)  # type: ignore
        root.addWidget(btns)

    def _resolve_csv_path(self, rel_or_abs: str) -> Optional[Path]:
        p = Path(rel_or_abs.strip())
        if p.is_absolute() and p.exists():
            return p
        cand = self._project_root / p
        if cand.exists():
            return cand
        cand2 = (self._project_root / "configs" / p).resolve()
        if cand2.exists():
            return cand2
        return None

    def _browse_csv(self) -> None:
        start = str(self._project_root / "configs")
        path, _ = QFileDialog.getOpenFileName(self, "Select cycle CSV", start, "CSV (*.csv);;All (*.*)")
        if path:
            try:
                rel = Path(path).resolve().relative_to(self._project_root)
                self.txt_csv.setText(str(rel).replace("\\", "/"))
            except Exception:
                self.txt_csv.setText(path)
            self._refresh_preview()

    def _refresh_preview(self) -> None:
        self._preview_status.setText("")
        raw = self.txt_csv.text().strip()
        if not raw:
            self._preview_status.setText("(no CSV path)")
            self._clear_plot()
            return
        resolved = self._resolve_csv_path(raw)
        if resolved is None or not resolved.exists():
            self._preview_status.setText(f"File not found: {raw}")
            self._clear_plot()
            return
        col_time = self.txt_col_time.text().strip() or "Time"
        col_load = self.txt_col_load.text().strip() or "Load"
        times: List[float] = []
        loads: List[float] = []
        try:
            text = resolved.read_text(encoding="utf-8-sig", errors="replace")
            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                try:
                    t = float(row.get(col_time, ""))
                    v = float(row.get(col_load, ""))
                    times.append(t)
                    loads.append(v)
                except (ValueError, TypeError):
                    continue
        except Exception as e:
            self._preview_status.setText(f"Read error: {e}")
            self._clear_plot()
            return
        if not times:
            self._preview_status.setText(
                f"No numeric data found (columns: '{col_time}', '{col_load}')"
            )
            self._clear_plot()
            return
        loops = max(1, self.spin_loops.value())
        dwell = max(0, self.spin_dwell.value())
        try:
            self._draw_plot(times, loads, loops, dwell, col_time, col_load)
        except Exception as e:
            self._preview_status.setText(f"Plot error: {e}")
            return
        self._preview_status.setText(
            f"{len(times)} points loaded  ·  {loops} loop(s)  ·  "
            f"cycle duration {times[-1] - times[0]:.1f} s"
        )

    def _clear_plot(self) -> None:
        if self._chart is None:
            return
        self._chart.removeAllSeries()
        for ax in self._chart.axes():
            self._chart.removeAxis(ax)
        self._series_refs.clear()
        self._chart.setTitle("")

    def _style_axis(self, axis: QValueAxis, label: str) -> None:
        axis.setTitleText(label)
        axis.setTitleBrush(QColor("#cccccc"))
        axis.setLabelsBrush(QColor("#aaaaaa"))
        axis.setLabelsFont(QFont("Segoe UI", 7))
        axis.setGridLineColor(QColor("#333333"))
        axis.setLinePenColor(QColor("#444444"))

    def _draw_plot(
        self,
        times: List[float],
        loads: List[float],
        loops: int,
        dwell_s: int,
        label_time: str,
        label_load: str,
    ) -> None:
        if self._chart is None:
            return
        self._chart.removeAllSeries()
        for ax in self._chart.axes():
            self._chart.removeAxis(ax)

        profile = build_expanded_cycle_profile(times, loads, loops, dwell_s)
        all_t = profile.times
        all_v = profile.loads

        self._series_refs.clear()

        line_series = QLineSeries()
        line_series.setPen(QPen(QColor("#4fc3f7"), 1.6))
        baseline = QLineSeries()
        for t, v in zip(all_t, all_v):
            line_series.append(QPointF(float(t), float(v)))
            baseline.append(QPointF(float(t), 0.0))

        self._series_refs.extend([line_series, baseline])

        area = QAreaSeries(line_series, baseline)
        area.setPen(QPen(QColor("#4fc3f7"), 1.6))
        fill = QColor("#4fc3f7")
        fill.setAlpha(35)
        area.setBrush(fill)
        area.setBorderColor(QColor("#4fc3f7"))
        self._series_refs.append(area)
        self._chart.addSeries(area)

        if loops > 1:
            for x in profile.loop_boundaries_s:
                vline = QLineSeries()
                vline.setPen(QPen(QColor("#555555"), 0.8, Qt.PenStyle.DashLine))
                v_min = min(all_v) if all_v else 0.0
                v_max = max(all_v) if all_v else 1.0
                vline.append(QPointF(x, v_min))
                vline.append(QPointF(x, v_max))
                self._series_refs.append(vline)
                self._chart.addSeries(vline)

        x_axis = QValueAxis()
        self._style_axis(x_axis, f"{label_time} (s)")
        y_axis = QValueAxis()
        self._style_axis(y_axis, label_load)

        t_min, t_max = min(all_t), max(all_t)
        v_min, v_max = min(all_v), max(all_v)
        margin = max(v_max * 0.05, 0.5)
        x_axis.setRange(t_min, t_max)
        y_axis.setRange(0.0, v_max + margin)

        self._chart.addAxis(x_axis, Qt.AlignmentFlag.AlignBottom)
        self._chart.addAxis(y_axis, Qt.AlignmentFlag.AlignLeft)
        for s in self._chart.series():
            s.attachAxis(x_axis)
            s.attachAxis(y_axis)

    def _read_yaml(self, path: Path) -> Dict[str, Any]:
        try:
            import yaml  # type: ignore

            if not path.exists():
                return {}
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

    def _load(self, defer_preview: bool = False) -> None:
        self._cfg = self._read_yaml(self._cfg_path)
        c = self._cfg

        src = (c.get("source") or {}) if isinstance(c.get("source"), dict) else {}
        self.txt_csv.setText(str(src.get("csv_path", "") or ""))
        cols = src.get("columns") or {}
        if isinstance(cols, dict):
            self.txt_col_time.setText(str(cols.get("time", "Time")))
            self.txt_col_load.setText(str(cols.get("load", "Load")))
        else:
            self.txt_col_time.setText("Time")
            self.txt_col_load.setText("Load")

        ex = (c.get("execution") or {}) if isinstance(c.get("execution"), dict) else {}
        try:
            self.spin_loops.setValue(int(ex.get("loops_total", 1)))
        except Exception:
            self.spin_loops.setValue(1)
        self.chk_start_with_test.setChecked(bool(ex.get("start_with_test", False)))
        try:
            self.spin_dwell.setValue(int(ex.get("inter_loop_dwell_s", 0)))
        except Exception:
            self.spin_dwell.setValue(0)

        if defer_preview:
            QTimer.singleShot(100, self._refresh_preview)
        else:
            self._refresh_preview()

    def _build_doc(self) -> Dict[str, Any]:
        doc: Dict[str, Any] = {}
        doc["source"] = {
            "csv_path": self.txt_csv.text().strip() or "configs/cycles/demo.csv",
            "columns": {
                "time": self.txt_col_time.text().strip() or "Time",
                "load": self.txt_col_load.text().strip() or "Load",
            },
        }
        doc["execution"] = {
            "loops_total": int(self.spin_loops.value()),
            "start_with_test": self.chk_start_with_test.isChecked(),
            "inter_loop_dwell_s": int(self.spin_dwell.value()),
        }
        return doc

    def _save_and_reload(self) -> bool:
        doc = self._build_doc()
        csv_path = (doc.get("source") or {}).get("csv_path") if isinstance(doc.get("source"), dict) else None
        if csv_path:
            resolved = self._resolve_csv_path(str(csv_path))
            if resolved is None or not resolved.exists():
                QMessageBox.warning(
                    self,
                    "CSV not found",
                    "The CSV path does not resolve to an existing file. The YAML will still be saved.",
                )
        try:
            import yaml  # type: ignore

            self._cfg_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
            self._cfg = dict(doc)
        except Exception as e:
            QMessageBox.critical(self, "Write Error", f"Failed to save cycle.yaml: {e}")
            return False

        try:
            from src.core.ipc.bus import create_ui_control_push  # type: ignore

            ctrl = create_ui_control_push()
            if ctrl is not None:
                msg = json.dumps({"type": "reload_plugin", "plugin": "Cycle"}).encode("utf-8")
                ctrl["control_push"].send(msg)
        except Exception:
            pass
        return True

    def _on_accept(self) -> None:
        if not self._save_and_reload():
            return
        self.accept()
