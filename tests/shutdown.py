r"""Verify the real shutdown path used by main.py.

Launches the application exactly the way main.py does, runs the event loop for
a moment, then closes the window and exits. If this exits cleanly the
production shutdown is sound.

Usage:  python3.13.exe tests\shutdown.py
"""

from __future__ import annotations

import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QThread, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.adb_manager import AdbManager, AdbWorker  # noqa: E402
from ui import styles  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

print("[1/6] creating QApplication", flush=True)
QApplication.setApplicationName("ADSremovedPP")
app = QApplication.instance() or QApplication([])
styles.apply_theme(app)

print("[2/6] building manager + worker + thread", flush=True)
manager = AdbManager()
thread = QThread()
worker = AdbWorker(manager)
worker.moveToThread(thread)
thread.start()

print("[3/6] constructing MainWindow", flush=True)
window = MainWindow(manager, worker)
window.show()

print("[4/6] running event loop for 2500 ms", flush=True)
QTimer.singleShot(2500, window.close)

exit_code = app.exec()
print(f"[5/6] event loop returned {exit_code}", flush=True)

print("[6/6] shutting down worker thread", flush=True)
thread.quit()
thread.wait(5000)
print("      thread finished:", thread.isFinished(), flush=True)

# Keep references alive until the very end so destruction order is deterministic.
_window = window
_worker = worker
print("[DONE] clean exit", flush=True)