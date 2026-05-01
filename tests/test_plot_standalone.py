# Standalone matplotlib validation -- run with: .venv\Scripts\python tests\test_plot_standalone.py
# Author: T. Onkst
# Date: 04292026
# Delete after confirming it works.

import sys
import math
import time

from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QLabel
from PySide6.QtCore import QTimer

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np

print(f"[test] matplotlib {matplotlib.__version__}, numpy {np.__version__}")
print(f"[test] Python {sys.version}")

app = QApplication(sys.argv)
win = QMainWindow()
win.setWindowTitle("matplotlib standalone test")
win.resize(900, 600)

container = QWidget()
lay = QVBoxLayout(container)

# --- Test 1: static sine wave ---
fig_static = Figure(figsize=(8, 2.5), dpi=100, facecolor="#1e1e1e")
ax_s = fig_static.add_subplot(111)
ax_s.set_facecolor("#1e1e1e")
ax_s.set_title("Static test (should show a red sine wave)", color="white")
ax_s.tick_params(colors="white")
x_static = np.linspace(0, 10, 200)
y_static = np.sin(x_static)
ax_s.plot(x_static, y_static, color="red", linewidth=2)
ax_s.set_ylim(-1.5, 1.5)
fig_static.tight_layout()

canvas_static = FigureCanvas(fig_static)
lay.addWidget(canvas_static)

# --- Test 2: live updating plot with two Y axes ---
fig_live = Figure(figsize=(8, 3), dpi=100, facecolor="#1e1e1e")
ax1 = fig_live.add_subplot(111)
ax1.set_facecolor("#1e1e1e")
ax1.set_title("Live test (should animate, 2 Y-axes)", color="white")
ax1.tick_params(colors="white")
ax1.set_ylabel("Sine (blue)", color="#1f77b4")

ax2 = ax1.twinx()
ax2.set_ylabel("Cosine (orange)", color="#ff7f0e")
ax2.tick_params(axis="y", colors="#ff7f0e")

(line1,) = ax1.plot([], [], color="#1f77b4", linewidth=2, label="sin(t)")
(line2,) = ax2.plot([], [], color="#ff7f0e", linewidth=2, label="cos(t)")
ax1.legend(handles=[line1, line2], loc="upper right", facecolor="#2e2e2e",
           labelcolor="white", edgecolor="gray")
fig_live.tight_layout()

canvas_live = FigureCanvas(fig_live)
lay.addWidget(canvas_live)

lbl = QLabel("Tick: 0")
lbl.setStyleSheet("color: lime; font-size: 14px;")
lay.addWidget(lbl)

win.setCentralWidget(container)

t0 = time.time()
tick = [0]
t_data: list[float] = []
y1_data: list[float] = []
y2_data: list[float] = []

WINDOW_S = 10.0

def update():
    now = time.time()
    elapsed = now - t0
    t_data.append(elapsed)
    y1_data.append(math.sin(elapsed * 1.5) * 100.0)
    y2_data.append(math.cos(elapsed * 0.8) * 50.0)

    if len(t_data) > 500:
        del t_data[:1]
        del y1_data[:1]
        del y2_data[:1]

    t_arr = np.array(t_data)
    line1.set_data(t_arr, np.array(y1_data))
    line2.set_data(t_arr, np.array(y2_data))

    xmin = max(0, elapsed - WINDOW_S)
    ax1.set_xlim(xmin, elapsed)
    ax1.set_ylim(min(y1_data) - 10, max(y1_data) + 10)
    ax2.set_ylim(min(y2_data) - 10, max(y2_data) + 10)

    canvas_live.draw_idle()

    tick[0] += 1
    lbl.setText(f"Tick: {tick[0]}  pts: {len(t_data)}  "
                f"sin: {y1_data[-1]:.1f}  cos: {y2_data[-1]:.1f}")

timer = QTimer()
timer.timeout.connect(update)
timer.start(100)  # 10Hz

win.show()
print("[test] Window shown. Close to exit.")
print("[test] You should see:")
print("  - Top: static red sine wave")
print("  - Bottom: live blue sine + orange cosine with dual Y axes")
sys.exit(app.exec())
