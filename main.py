"""
ADSremovedPP — entry point
==========================

Wires the ADB manager to a worker thread, starts Qt, and hands control to the
main window.

Run from source::

    pip install -r requirements.txt
    python main.py

The worker thread is created *before* the event loop starts and stopped during
``main()`` teardown so a running scan can never block interpreter shutdown.
"""

from __future__ import annotations

import sys
import traceback

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QMessageBox

import config
from core.adb_manager import AdbManager, AdbWorker
from ui import styles
from ui.main_window import MainWindow


def _install_excepthook(app: QApplication) -> None:
    """Log unhandled exceptions instead of dying silently."""

    def hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return

        details = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        print(details, file=sys.stderr)
        try:
            QMessageBox.critical(
                None,
                "เกิดข้อผิดพลาด",
                f"{exc_type.__name__}: {exc_value}\n\n"
                "รายละเอียดถูกบันทึกไว้ที่หน้าต่างเทอร์มินัล",
            )
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = hook


def main() -> int:
    QApplication.setApplicationName(config.APP_NAME)
    QApplication.setApplicationDisplayName(config.APP_TITLE)
    QApplication.setOrganizationName(config.APP_NAME)
    QApplication.setApplicationVersion(config.CURRENT_VERSION)

    app = QApplication(sys.argv)
    styles.apply_theme(app)
    _install_excepthook(app)

    manager = AdbManager()

    thread = QThread()
    worker = AdbWorker(manager)
    worker.moveToThread(thread)
    thread.start()

    window = MainWindow(manager, worker)
    window.show()

    try:
        return app.exec()
    finally:
        # closeEvent already asks the manager to stop the adb server, so the
        # only thing left is to wind the worker thread down cleanly.
        thread.quit()
        thread.wait(3000)


if __name__ == "__main__":
    raise SystemExit(main())