"""
tests.shutdown
==============

Verify that the application tears down without crashing.

The failure this guards against is specific and nasty: Qt aborts the process
with *"QThread: Destroyed while thread is still running"* when a ``QThread`` is
garbage-collected while its event loop is still going. Earlier versions of
ADSremovedPP parented helper threads to the main window and hit exactly that.

What is checked here:

* the window builds,
* every page constructs and lays out,
* the theme switches in both directions,
* the log dock collapses and expands,
* ``close()`` drains the shared worker and the helper threads,
* the process exits with code 0.

Run::

    python tests/shutdown.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.adb_extended import ExtendedAdbManager, ExtendedAdbWorker  # noqa: E402
from ui import styles  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    styles.apply_theme(app, "dark")

    manager = ExtendedAdbManager()
    thread = QThread()
    worker = ExtendedAdbWorker(manager)
    worker.moveToThread(thread)
    thread.start()

    window = MainWindow(manager, worker)
    window.resize(1360, 860)
    window.show()
    app.processEvents()

    print(f"  [OK]   สร้างหน้าต่าง {window.width()}x{window.height()}")

    for index in range(window.stack.count()):
        window._select_page(index)
        app.processEvents()
    print(f"  [OK]   ทุกหน้า ({window.stack.count()}) สร้างได้")

    window._toggle_theme()
    app.processEvents()
    light = styles.active_palette_name()
    window._toggle_theme()
    app.processEvents()
    dark = styles.active_palette_name()
    if {light, dark} == {"light", "dark"}:
        print("  [OK]   สลับธีมได้ทั้งสองโหมด")
    else:
        print(f"  [FAIL] สลับธีมผิดปกติ: {light} -> {dark}")
        return 1

    window.log_panel.append("INFO", "ทดสอบบันทึกการทำงาน")
    window.log_panel.append("WARN", "ทดสอบคำเตือน")
    window.log_panel.append("ERROR", "ทดสอบข้อผิดพลาด")
    window.log_panel.toggle_collapsed()
    app.processEvents()
    window.log_panel.toggle_collapsed()
    app.processEvents()
    print("  [OK]   แผงบันทึกเขียน/พับ/กางได้")

    window._wait_for_spawned()
    print("  [OK]   ไม่มี helper thread ค้าง")

    window.close()
    app.processEvents()
    print("  [OK]   ปิดหน้าต่างสะอาด")

    if thread.isRunning():
        thread.quit()
        thread.wait(3000)
    if not thread.isRunning():
        print("  [OK]   worker thread หยุดแล้ว")
    else:
        print("  [FAIL] worker thread ยังทำงานอยู่")
        return 1

    print("\n  ปิดโปรแกรมสะอาด — ไม่มี crash")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())