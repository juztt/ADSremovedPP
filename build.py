"""
build.py
========

Build a standalone ADSremovedPP.exe with PyInstaller.

Usage::

    pip install pyinstaller
    python build.py

The ``bin/`` folder (platform-tools) and the JSON data files are bundled into
the single executable, so the result runs on a machine with no Python, no adb
and no extra files.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "ADSremovedPP"
BIN_DIR = ROOT / "bin"
ICON = ROOT / "assets" / "app.ico"


def ensure_dependencies() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("ไม่พบ PyInstaller — กำลังติดตั้ง...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def ensure_adb() -> None:
    adb = BIN_DIR / ("adb.exe" if sys.platform == "win32" else "adb")
    if not adb.exists():
        print("!" * 68)
        print("  ยังไม่พบ adb ในโฟลเดอร์ bin/")
        print("  ดาวน์โหลด platform-tools จาก:")
        print("  https://developer.android.com/tools/releases/platform-tools")
        print("  แล้วคัดลอกโฟลเดอร์ platform-tools ทั้งหมดไปไว้ที่ bin/")
        print("!" * 68)
        print("  (สร้างไฟล์ exe ได้ แต่โปรแกรมจะหา adb ไม่เจอเมื่อรัน)")
        print()


def main() -> int:
    ensure_dependencies()
    ensure_adb()

    for folder in ("build", "dist"):
        target = ROOT / folder
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)

    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", APP_NAME,
        # Windows uses ';' as the separator, POSIX uses ':'.
        f"--add-data", f"bin{';' if sys.platform == 'win32' else ':'}bin",
        f"--add-data", f"local_db.json{';' if sys.platform == 'win32' else ':'}.",
        f"--add-data", f"version.json{';' if sys.platform == 'win32' else ':'}.",
        "--collect-submodules", "core",
        "--collect-submodules", "ui",
        "--hidden-import", "PySide6.QtCore",
        "--hidden-import", "PySide6.QtGui",
        "--hidden-import", "PySide6.QtWidgets",
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "numpy",
    ]

    if ICON.exists():
        command += ["--icon", str(ICON)]
    else:
        print("หมายเหตุ: ไม่พบไฟล์ไอคอน assets/app.ico — จะใช้ไอคอนเริ่มต้นของ PyInstaller")

    command.append("main.py")

    print("\nกำลังแพ็กไฟล์...\n")
    result = subprocess.run(command, cwd=ROOT)

    if result.returncode != 0:
        print("\nแพ็กไม่สำเร็จ")
        return result.returncode

    separator = "\\" if sys.platform == "win32" else "/"
    output = ROOT / "dist" / f"{APP_NAME}{'.exe' if sys.platform == 'win32' else ''}"
    size_mb = output.stat().st_size / 1024 / 1024

    print("\n" + "=" * 50)
    print(f"แพ็กสำเร็จ: {output}")
    print(f"ขนาด: {size_mb:.1f} MB")
    print("=" * 50)

    if size_mb > 80:
        print(
            "\nหมายเหตุ: ขนาดใหญ่ผิดปกติ — สาเหตุที่เป็นไปได้คือ "
            "platform-tools ถูกรวมเข้าไป (ถูกต้อง) หรือมีโมดูลเกินจำเป็น"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())