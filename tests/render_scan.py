"""Render the scan page with real device data so the visual result can be checked.

Pulls the package list from the attached phone, resolves a few launcher labels
and icons, and saves a PNG of the scan page. Run from the project root:

    python tests/render_scan.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
QFont.insertSubstitution("Segoe UI", "Leelawadee UI")
app.setFont(QFont("Leelawadee UI", 10))

from core.adb_extended import ExtendedAdbManager  # noqa: E402
from core.app_model import AppEntry, classify_installer  # noqa: E402
from core.icon_store import IconStore  # noqa: E402
from ui import styles  # noqa: E402
from ui.pages_scan import ScanPage  # noqa: E402

styles.apply_theme(app, "dark")

manager = ExtendedAdbManager()
devices = [d for d in manager.list_devices() if d.state == "device"]

page = ScanPage()
page.resize(1200, 760)
page.set_device_info(
    {
        "title": devices[0].display_name if devices else "ไม่ทราบรุ่น",
        "android_release": "10",
        "battery_level": 49,
        "battery_state": "ไม่ชาร์จ",
        "storage_free_gb": 38.9,
        "storage_total_gb": 128.0,
    }
)

entries: list[AppEntry] = []
if devices:
    serial = devices[0].serial
    entries = manager.list_packages_detailed(serial)
    store = IconStore()
    # Resolve labels + icons for a handful of user apps so the table shows
    # something real instead of package names.
    for entry in [e for e in entries if not e.is_system][:8]:
        store.prime(entry.package, entry.apk_path, manager, serial)
        label = store.label(entry.package, entry.apk_path)
        if label:
            entry.app_name = label
    entries = entries[:24]
else:
    entries = [
        AppEntry(
            package="com.example.cleaner",
            app_name="Virus Cleaner Pro",
            threat_type="Adware",
            risk="High",
            installer="null",
            source=classify_installer("null"),
        )
    ]

page.set_entries(entries, scanned_total=len(entries))
page.show()

out = Path(__file__).resolve().parent.parent / "design" / "shot_scan_fixed.png"
QTimer.singleShot(1200, lambda: (out.parent.mkdir(parents=True, exist_ok=True), page.grab().save(str(out)), print("saved", out)))
QTimer.singleShot(1400, app.quit)
app.exec()
