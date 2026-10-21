"""Isolate which construction step crashes the interpreter."""

from __future__ import annotations

import sys
from pathlib import Path

# Windows consoles default to cp1252 which cannot print Thai text.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui import styles  # noqa: E402

STEP = sys.argv[1] if len(sys.argv) > 1 else "all"

app = QApplication.instance() or QApplication([])
styles.apply_theme(app)
print(f"[setup] QApplication ready ({QApplication.instance() is app})", flush=True)


def report(name: str) -> None:
    print(f"[PASS] {name}", flush=True)


if STEP in ("all", "basic"):
    from ui.main_window import BatteryBar, FieldBox, LogPanel, PackageTab, ScanTab, StatCard, UtilitiesTab
    plan = [
        ("LogPanel", lambda: LogPanel()),
        ("StatCard", lambda: StatCard("ทดสอบ")),
        ("FieldBox", lambda: FieldBox("ทดสอบ")),
        ("BatteryBar", lambda: BatteryBar()),
        ("ScanTab", lambda: ScanTab()),
        ("PackageTab", lambda: PackageTab()),
        ("UtilitiesTab", lambda: UtilitiesTab()),
    ]
    for name, factory in plan:
        obj = factory()
        obj.deleteLater()
        report(name)
    app.processEvents()
    report("processEvents after widgets")


if STEP in ("all", "window"):
    from PySide6.QtCore import QThread
    from core.adb_manager import AdbManager, AdbWorker
    from ui.main_window import MainWindow

    manager = AdbManager()
    thread = QThread()
    worker = AdbWorker(manager)
    worker.moveToThread(thread)
    thread.start()
    report("thread started")

    window = MainWindow(manager, worker)
    report("MainWindow constructed")
    window.show()
    app.processEvents()
    report("MainWindow shown")

    print(f"       tabs={window.tabs.count()}", flush=True)
    print(f"       scan cols={window.scan_tab.table.columnCount()}", flush=True)
    print(f"       pkg cols={window.package_tab.table.columnCount()}", flush=True)
    print(f"       utility tiles={len(window.utilities_tab._tiles)}", flush=True)
    print(f"       db pill={window.db_pill.text()}", flush=True)
    print(f"       log entries={window.log_panel.count_label.text()}", flush=True)

    window.close()
    report("MainWindow closed")

    try:
        thread.quit()
        thread.wait(3000)
        report("thread stopped")
    finally:
        app.processEvents()


print("[DONE]", flush=True)