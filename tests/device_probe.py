"""End-to-end check against a real device.

Verifies the three things the screenshot showed as broken:

1. **Split APKs no longer produce duplicate rows** — ``pm list packages -f -i``
   lists every split file; the table used to show the same package three times.
2. **Real launcher labels** — the app name is read out of the APK (AXML →
   ARSC), not left as the package name.
3. **Real launcher icons** — the extraction actually runs (it used to die on a
   ``NameError`` before it touched a single byte).

Run from the project root with the phone plugged in:

    python tests/device_probe.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Icon rendering needs a QGuiApplication before any QPixmap is created.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv)

from core.adb_extended import ExtendedAdbManager  # noqa: E402
from core.icon_store import IconStore  # noqa: E402


def main() -> int:
    manager = ExtendedAdbManager()
    try:
        devices = manager.list_devices()
    except Exception as exc:  # noqa: BLE001
        print(f"cannot talk to adb: {exc}")
        return 0

    ready = [d for d in devices if d.state == "device"]
    if not ready:
        print("no device attached — skipped")
        return 0

    serial = ready[0].serial
    print(f"device: {serial} ({ready[0].display_name})")

    started = time.monotonic()
    entries = manager.list_packages_detailed(serial)
    elapsed = time.monotonic() - started

    packages = [entry.package for entry in entries]
    duplicates = len(packages) - len(set(packages))
    print(f"packages: {len(packages)} unique, {duplicates} duplicate(s), in {elapsed:.1f}s")

    # -- how many rows have a real label after the APK round trip ----------
    # System overlays live under /system and cannot be pulled without root,
    # so the sample is taken from user-installed apps.
    store = IconStore()
    user_apps = [e for e in entries if not e.is_system and e.apk_path.startswith("/data/")]
    if not user_apps:
        user_apps = entries
    sample = user_apps[:6]
    labelled = 0
    with_icons = 0
    for entry in sample:
        store.prime(entry.package, entry.apk_path, manager, serial)
        label = store.label(entry.package, entry.apk_path)
        if label:
            labelled += 1
        pixmap = store.pixmap(entry.package, entry.apk_path, 34)
        if pixmap and not pixmap.isNull():
            with_icons += 1
        print(f"  {entry.package}: label={label!r} icon={pixmap.width()}px")

    print()
    print(f"labels recovered: {labelled}/{len(sample)}")
    print(f"icons drawn:      {with_icons}/{len(sample)}")

    ok = duplicates == 0 and labelled == len(sample) and with_icons == len(sample)
    print("RESULT:", "OK" if ok else "needs attention")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
