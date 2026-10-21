r"""Smoke test: import every module and exercise the pure-logic paths.

Run with:  python3.13.exe tests\smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows consoles default to cp1252 which cannot print Thai text.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(label: str, fn) -> None:
    try:
        detail = fn()
        status = "OK  "
        print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{label}: {type(exc).__name__}: {exc}")
        print(f"[FAIL] {label} -> {type(exc).__name__}: {exc}")


# --- config ---------------------------------------------------------------
def t_config():
    import config
    adb = config.find_adb()
    assert adb is not None, "find_adb() returned None"
    assert Path(adb).exists(), f"adb path does not exist: {adb}"
    return f"adb at {Path(adb).name}, v{config.CURRENT_VERSION}"


# --- updater --------------------------------------------------------------
def t_updater_local():
    from core import updater
    rows = updater.load_local_blacklist()
    assert rows, "local blacklist is empty"
    for row in rows:
        assert set(row) == {"package", "appName", "type", "risk"}, f"bad keys: {row}"
    return f"{len(rows)} entries"


def t_updater_version():
    from core import updater
    assert updater.parse_version("v1.2.3") == (1, 2, 3)
    assert updater.parse_version("2.0") == (2, 0, 0)
    assert updater.parse_version("1.10.0") > updater.parse_version("1.9.9")
    return "1.2.3 / 2.0 / 1.10.0 comparisons"


def t_updater_script():
    from core import updater
    script = updater.build_updater_script(
        old_exe=Path(r"C:\App\ADSremovedPP.exe"),
        new_exe=Path(r"C:\Data\ADSremovedPP.exe"),
        script_path=Path(ROOT / "tests" / "_updater_probe.bat"),
    )
    text = script.read_text(encoding="utf-8")
    for needle in ("del /f /q", "move /f /y", "start", "chcp 65001"):
        assert needle in text, f"missing {needle!r} in generated batch"
    script.unlink()
    return f"{len(text.splitlines())} lines"


# --- adb_manager ----------------------------------------------------------
def t_adb_manager_init():
    from core.adb_manager import AdbManager
    mgr = AdbManager()
    assert mgr.available, "AdbManager reports adb unavailable"
    return str(Path(mgr.adb_path).name)


def t_adb_devices():
    from core.adb_manager import AdbManager
    mgr = AdbManager()
    mgr.start_server()
    devices = mgr.list_devices()
    if not devices:
        return "no device attached (expected without a phone)"
    return f"{len(devices)} device(s): " + ", ".join(d.display_name for d in devices)


def t_adb_version():
    from core.adb_manager import AdbManager
    mgr = AdbManager()
    out = mgr._run(["version"], timeout=20)
    first = out.splitlines()[0] if out else "?"
    return first


# --- ui -------------------------------------------------------------------
def t_styles():
    from ui import styles
    assert styles.QSS.count("{") == styles.QSS.count("}"), "unbalanced braces in QSS"
    assert styles.risk_color("High") == "#ff8a5c"
    assert styles.risk_color("ไม่รู้จัก") == styles.C["text_muted"]
    return f"{len(styles.C)} tokens, {len(styles.QSS)} chars"


def t_widgets_build():
    """Instantiate every widget off-screen to catch layout/typo errors."""
    from PySide6.QtWidgets import QApplication
    from ui import styles
    from ui.main_window import (
        BatteryBar, FieldBox, LogPanel, PackageTab, ScanTab,
        StatCard, UtilitiesTab,
    )

    app = QApplication.instance() or QApplication([])
    styles.apply_theme(app)

    plan = [
        ("LogPanel", lambda: LogPanel()),
        ("StatCard", lambda: StatCard("ทดสอบ")),
        ("FieldBox", lambda: FieldBox("ทดสอบ")),
        ("BatteryBar", lambda: BatteryBar()),
        ("ScanTab", lambda: ScanTab()),
        ("PackageTab", lambda: PackageTab()),
        ("UtilitiesTab", lambda: UtilitiesTab()),
    ]
    built = []
    for name, factory in plan:
        obj = factory()
        obj.deleteLater()
        built.append(name)

    app.processEvents()
    return ", ".join(built)


def t_main_window():
    """Build the whole MainWindow with a live worker thread."""
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication
    from core.adb_manager import AdbManager, AdbWorker
    from ui import styles
    from ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    styles.apply_theme(app)

    manager = AdbManager()
    thread = QThread()
    worker = AdbWorker(manager)
    worker.moveToThread(thread)
    thread.start()

    try:
        window = MainWindow(manager, worker)
        window.show()
        app.processEvents()

        assert window.tabs.count() == 3, "expected 3 tabs"
        assert window.scan_tab.table.columnCount() == 6, "scan table column mismatch"
        assert window.package_tab.table.columnCount() == 4, "package table column mismatch"
        assert len(window.utilities_tab._tiles) == 13, "utility tile count mismatch"

        # The device-info card must expose all six documented fields.
        for attr in (
            "field_brand", "field_model", "field_android",
            "field_serial", "field_battery", "field_storage",
        ):
            assert getattr(window, attr) is not None, f"missing {attr}"

        tabs = [window.tabs.tabText(i) for i in range(3)]
        window.log_panel.append("INFO", "ทดสอบแล้ว")
        assert window.log_panel.count_label.text() != "0 รายการ"

        window.close()
        app.processEvents()
        return f"tabs={tabs}"
    finally:
        thread.quit()
        thread.wait(3000)
        app.processEvents()


def t_utils_actions():
    from ui.main_window import UtilitiesTab
    assert len(UtilitiesTab.SETTINGS_ACTIONS) == 6
    assert len(UtilitiesTab.CLEAR_ACTIONS) == 4
    assert len(UtilitiesTab.REBOOT_ACTIONS) == 3
    chrome = next(a for a in UtilitiesTab.CLEAR_ACTIONS if a.payload == "com.android.chrome")
    assert chrome.destructive and "Chrome" in chrome.label
    return "6 settings / 4 clear / 3 reboot"


def main() -> int:
    print("=" * 62)
    print("ADSremovedPP smoke test")
    print("=" * 62)

    check("config.find_adb", t_config)
    check("updater.load_local_blacklist", t_updater_local)
    check("updater.parse_version", t_updater_version)
    check("updater.build_updater_script", t_updater_script)
    check("adb.AdbManager init", t_adb_manager_init)
    check("adb version", t_adb_version)
    check("adb list_devices", t_adb_devices)
    check("ui.styles", t_styles)
    check("ui widget construction", t_widgets_build)
    check("ui utilities catalogue", t_utils_actions)
    check("ui.MainWindow + worker thread", t_main_window)

    print("=" * 62)
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())