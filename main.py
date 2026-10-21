"""
ADSremovedPP — entry point
==========================

Wires the ADB manager to a worker thread, starts Qt, and hands control to the
main window.

Run from source::

    pip install -r requirements.txt
    python main.py

Threading
---------
Two kinds of thread exist and they must not be confused:

* **The shared worker.** One ``QThread`` plus one ``ExtendedAdbWorker`` created
  here *before* the event loop starts. Every long-running action (scan,
  uninstall, live probe) is queued here so a technician can keep clicking
  while the phone is busy.
* **One-shot helpers.** Short jobs that do not need to interleave with the
  shared queue — loading the database, checking for an update, pushing to
  GitHub — get their own parentless thread created by
  :meth:`MainWindow._spawn`.

Both are drained during teardown so a running ``QThread`` is never destroyed by
Qt, which would abort the interpreter.
"""

from __future__ import annotations

import sys
import traceback

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QMessageBox

import config
from core.adb_extended import ExtendedAdbManager, ExtendedAdbWorker
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
            with open(config.log_file(), "a", encoding="utf-8") as handle:
                handle.write("\n" + details + "\n")
        except OSError:
            pass

        try:
            QMessageBox.critical(
                None,
                "เกิดข้อผิดพลาด",
                f"{exc_type.__name__}: {exc_value}\n\n"
                "รายละเอียดถูกบันทึกไว้ที่หน้าต่างเทอร์มินัล",
            )
        except Exception:  # noqa: BLE001 - last-resort reporting
            pass

    sys.excepthook = hook


def _configure_high_dpi() -> None:
    """Enable fractional scaling so the layout stays crisp at 125 % / 150 %.

    Must run *before* the ``QApplication`` is constructed — Qt reads the
    rounding policy once, at startup. A build without the enum simply keeps its
    default behaviour.
    """
    from PySide6.QtCore import Qt

    policy = getattr(
        Qt.HighDpiScaleFactorRoundingPolicy, "PassThrough", None
    )
    if policy is not None:
        QApplication.setHighDpiScaleFactorRoundingPolicy(policy)


def main() -> int:
    QApplication.setApplicationName(config.APP_NAME)
    QApplication.setApplicationDisplayName(config.APP_TITLE)
    QApplication.setOrganizationName(config.APP_NAME)
    QApplication.setApplicationVersion(config.CURRENT_VERSION)

    _configure_high_dpi()
    app = QApplication(sys.argv)
    styles.apply_theme(app, "dark")
    _install_excepthook(app)

    manager = ExtendedAdbManager()

    thread = QThread()
    worker = ExtendedAdbWorker(manager)
    worker.moveToThread(thread)
    thread.start()

    window = MainWindow(manager, worker)
    window.show()

    try:
        return app.exec()
    finally:
        # closeEvent already asked the manager to stop the adb server and drained
        # the helper threads; this winds down the shared worker.
        thread.quit()
        thread.wait(3000)


if __name__ == "__main__":
    raise SystemExit(main())