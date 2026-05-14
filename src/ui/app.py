# Author: T. Onkst | Date: 05052026

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QPixmap, QPainter, QColor, QFont
    from PySide6.QtWidgets import QApplication, QSplashScreen, QMainWindow, QDialog, QMessageBox
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


def _load_splash_pixmap() -> QPixmap:
    root = _project_root()
    for logo_path in (
        root / "assets" / "splash.png",
        root / "assets" / "logo.png",
        root / "Loading Icon.png",
    ):
        if logo_path.exists():
            pm = QPixmap(str(logo_path))
            if not pm.isNull():
                return pm.scaled(640, 420, Qt.KeepAspectRatio, Qt.SmoothTransformation)
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


class CoreSupervisor:
    def __init__(self, app: QApplication, on_ready, on_failed) -> None:
        self.app = app
        self.on_ready = on_ready
        self.on_failed = on_failed
        self.project_root = _project_root()
        self.proc: subprocess.Popen | None = None
        self._sub = None
        self._ctrl = None
        self._ready = False
        self._shutdown_started = False
        self._last_ready_request_mono = 0.0
        self._ready_request_count = 0
        self._seen_status = False
        self._seen_telemetry = False
        self._poll_logged = False
        self._ready_deadline = time.monotonic() + 60.0
        self._ready_timer = QTimer()
        self._ready_timer.setInterval(50)
        self._ready_timer.timeout.connect(self._poll_ready)  # type: ignore

    def start(self) -> None:
        print(f"[LAUNCHER] CoreSupervisor.start() from {__file__}", flush=True)
        try:
            from src.core.ipc.bus import create_ui_control_push, create_ui_subscriber
            sockets = create_ui_subscriber()
            if sockets is not None:
                self._sub = sockets.telemetry_sub
                print("[LAUNCHER] Status/telemetry subscriber created.", flush=True)
            else:
                print("[LAUNCHER] Subscriber creation returned None.", flush=True)
            self._ctrl = create_ui_control_push()
            print("[LAUNCHER] Control PUSH socket created.", flush=True)
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "src.core.app"],
                cwd=str(self.project_root),
            )
            print(f"[LAUNCHER] Core subprocess started pid={self.proc.pid}", flush=True)
            self._ready_timer.start()
            print("[LAUNCHER] Ready poll timer started.", flush=True)
        except Exception as exc:
            print(f"[LAUNCHER] Failed to start core supervisor: {exc}", flush=True)
            self.on_failed(f"Failed to start core process: {exc}")

    def _send_control(self, payload: dict) -> None:
        try:
            if self._ctrl is None:
                from src.core.ipc.bus import create_ui_control_push
                self._ctrl = create_ui_control_push()
            if self._ctrl is not None:
                self._ctrl["control_push"].send(json.dumps(payload).encode("utf-8"))
        except Exception:
            pass

    def _send_ready_request_if_due(self) -> None:
        now_mono = time.monotonic()
        if (now_mono - self._last_ready_request_mono) < 0.5:
            return
        self._last_ready_request_mono = now_mono
        self._ready_request_count += 1
        if self._ready_request_count == 1:
            print("[LAUNCHER] Requesting core_ready...", flush=True)
        self._send_control({"type": "core_ready_request"})

    def _poll_ready(self) -> None:
        if not self._poll_logged:
            print("[LAUNCHER] Ready poll timer ticked.", flush=True)
            self._poll_logged = True
        if self.proc is not None and self.proc.poll() is not None and not self._ready:
            self._ready_timer.stop()
            self.on_failed(f"Core process exited before becoming ready (exit code {self.proc.returncode}).")
            return
        self._send_ready_request_if_due()
        if time.monotonic() > self._ready_deadline and not self._ready:
            self._ready_timer.stop()
            seen = f"status_seen={self._seen_status}, telemetry_seen={self._seen_telemetry}, ready_requests={self._ready_request_count}"
            self.shutdown(block=True)
            self.on_failed(f"Timed out waiting for core_ready from the core process ({seen}).")
            return
        if self._sub is None:
            return
        try:
            import zmq
            while True:
                try:
                    topic, raw = self._sub.recv_multipart(flags=zmq.NOBLOCK)
                except Exception:
                    break
                if topic == b"telemetry":
                    self._seen_telemetry = True
                    continue
                if topic != b"status":
                    continue
                self._seen_status = True
                try:
                    msg = json.loads(raw.decode("utf-8"))
                except Exception:
                    continue
                if msg.get("type") == "core_ready":
                    print("[LAUNCHER] Received core_ready.", flush=True)
                    self._ready = True
                    self._ready_timer.stop()
                    self._send_control({"type": "ui_ready_ack"})
                    self.on_ready()
                    return
        except Exception:
            pass

    def shutdown(self, block: bool = False) -> None:
        if self._shutdown_started:
            if block:
                self._wait_for_exit(timeout_s=10.0)
            return
        self._shutdown_started = True
        try:
            self._ready_timer.stop()
        except Exception:
            pass
        self._send_control({"type": "shutdown"})
        if block:
            self._wait_for_exit(timeout_s=10.0)

    def _wait_for_exit(self, timeout_s: float) -> None:
        if self.proc is None:
            return
        try:
            self.proc.wait(timeout=timeout_s)
            return
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3.0)
            return
        except Exception:
            pass
        try:
            self.proc.kill()
        except Exception:
            pass

    def close_sockets(self) -> None:
        try:
            if self._sub is not None:
                self._sub.close(0)
        except Exception:
            pass
        try:
            if self._ctrl is not None:
                self._ctrl["control_push"].close(0)
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    print(f"[LAUNCHER] UI app main() starting from {__file__}", flush=True)
    app = QApplication(argv or sys.argv)
    app.setQuitOnLastWindowClosed(False)

    def _handle_sigint(_signum, _frame) -> None:
        print("[LAUNCHER] Ctrl+C received; shutting down...", flush=True)
        app.quit()

    try:
        signal.signal(signal.SIGINT, _handle_sigint)
    except Exception:
        pass
    sigint_timer = QTimer()
    sigint_timer.setInterval(100)
    sigint_timer.timeout.connect(lambda: None)  # type: ignore
    sigint_timer.start()
    app._sigint_timer = sigint_timer  # type: ignore[attr-defined]

    version = _discover_version()
    splash_pixmap = _load_splash_pixmap()
    splash = QSplashScreen(splash_pixmap, Qt.WindowStaysOnTopHint)
    splash.showMessage(
        f"Version: {version}\nLoading…",
        alignment=Qt.AlignBottom | Qt.AlignHCenter,
        color=Qt.white,
    )
    splash.show()

    win = _MainWindow()
    supervisor_holder: dict[str, CoreSupervisor] = {}

    def _show_error_and_quit(message: str) -> None:
        print(f"[LAUNCHER] Startup error: {message}", flush=True)
        try:
            QMessageBox.critical(None, "Core Startup Error", message)
        except Exception:
            pass
        app.quit()

    def _show_console() -> None:
        print("[LAUNCHER] Creating ConsoleWindow...", flush=True)
        try:
            from .widgets.console import ConsoleWindow
            app._console = ConsoleWindow()  # type: ignore[attr-defined]
            app._console.show()
            app.setQuitOnLastWindowClosed(True)
            splash.finish(app._console)  # type: ignore[attr-defined]
            print("[LAUNCHER] ConsoleWindow shown.", flush=True)
        except Exception as exc:
            print(f"[LAUNCHER] ConsoleWindow creation failed, showing bootstrap window: {exc}", flush=True)
            win.show()
            app.setQuitOnLastWindowClosed(True)
            try:
                splash.finish(win)
            except Exception:
                splash.close()

    def _shutdown_core() -> None:
        print("[LAUNCHER] aboutToQuit: shutting down core supervisor.", flush=True)
        supervisor = supervisor_holder.get("core")
        if supervisor is not None:
            supervisor.shutdown(block=True)
            supervisor.close_sockets()

    app.aboutToQuit.connect(_shutdown_core)  # type: ignore

    def _show_main() -> None:
        print("[LAUNCHER] _show_main() entered.", flush=True)
        # Show launch configuration dialog before presenting the main window
        try:
            from .widgets.launch_dialog import LaunchDialog
        except Exception:
            LaunchDialog = None  # type: ignore
        if LaunchDialog is not None:
            dlg = LaunchDialog()
            # Close splash before dialog appears, per requirement
            splash.finish(dlg)
            print("[LAUNCHER] LaunchDialog opened.", flush=True)
            if dlg.exec() != QDialog.Accepted:
                print("[LAUNCHER] LaunchDialog cancelled.", flush=True)
                app.quit()
                return
            print("[LAUNCHER] LaunchDialog accepted.", flush=True)
        splash.show()
        splash.showMessage(
            f"Version: {version}\nStarting core…",
            alignment=Qt.AlignBottom | Qt.AlignHCenter,
            color=Qt.white,
        )
        supervisor = CoreSupervisor(app, _show_console, _show_error_and_quit)
        supervisor_holder["core"] = supervisor
        app._core_supervisor = supervisor  # type: ignore[attr-defined]
        print("[LAUNCHER] Starting CoreSupervisor.", flush=True)
        supervisor.start()

    # Short delay to show splash; later we can tie this to real initialization
    QTimer.singleShot(1200, _show_main)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())


