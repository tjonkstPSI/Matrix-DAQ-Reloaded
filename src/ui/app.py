# Author: T. Onkst | Date: 08182025

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QPixmap, QPainter, QColor, QFont
    from PySide6.QtWidgets import QApplication, QSplashScreen, QMainWindow, QDialog
except Exception as _e:  # pragma: no cover
    raise SystemExit("PySide6 is required to run the UI (pip install PySide6)")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _discover_version() -> str:
    # Try git describe; fallback to 'dev'
    try:
        import subprocess
        out = subprocess.check_output(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=str(_project_root()),
            stderr=subprocess.DEVNULL,
        )
        v = out.decode("utf-8", errors="ignore").strip()
        return v if v else "dev"
    except Exception:
        return "dev"


def _load_logo_pixmap() -> QPixmap:
    logo_path = _project_root() / "assets" / "logo.png"
    if logo_path.exists():
        pm = QPixmap(str(logo_path))
        if not pm.isNull():
            return pm
    # Fallback: simple generated pixmap with text
    w, h = 420, 220
    pm = QPixmap(w, h)
    pm.fill(QColor("#1e1e1e"))
    painter = QPainter(pm)
    painter.setPen(QColor("#ffffff"))
    font = QFont("Segoe UI", 18, QFont.Bold)
    painter.setFont(font)
    painter.drawText(20, 110, "Engine Test Data Recorder")
    painter.end()
    return pm


class _MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Engine Test Data Recorder — Console (bootstrap)")
        self.resize(1200, 800)


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv or sys.argv)
    version = _discover_version()
    splash_pixmap = _load_logo_pixmap()
    splash = QSplashScreen(splash_pixmap, Qt.WindowStaysOnTopHint)
    splash.showMessage(
        f"Version: {version}\nLoading…",
        alignment=Qt.AlignBottom | Qt.AlignLeft,
        color=Qt.white,
    )
    splash.show()

    win = _MainWindow()

    def _show_main() -> None:
        # Show launch configuration dialog before presenting the main window
        try:
            from .widgets.launch_dialog import LaunchDialog
        except Exception:
            LaunchDialog = None  # type: ignore
        if LaunchDialog is not None:
            dlg = LaunchDialog()
            # Close splash before dialog appears, per requirement
            splash.finish(dlg)
            if dlg.exec() != QDialog.Accepted:
                app.quit()
                return
        win.show()

    # Short delay to show splash; later we can tie this to real initialization
    QTimer.singleShot(1200, _show_main)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())


