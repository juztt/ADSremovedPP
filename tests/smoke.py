"""
tests.smoke
===========

Fast, hardware-free checks for the v0.2 logic layer.

Everything here runs without a phone and without a network: the point is to
prove the *pure* decisions (installer classification, permission grading,
database normalisation, flag persistence, GitHub push payload assembly) before
spending time on a real device.

Run::

    python tests/smoke.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [OK]   {name}")
    else:
        FAILED.append(f"{name} — {detail}")
        print(f"  [FAIL] {name}  {detail}")


# ---------------------------------------------------------------------------
# core.app_model
# ---------------------------------------------------------------------------


def test_installer_classification() -> None:
    print("\n== การจำแนกแหล่งที่ติดตั้ง ==")
    from core.app_model import classify_installer

    play = classify_installer("com.android.vending")
    check("Play Store ถูกจัดเป็น trusted", play.kind == "play" and play.trusted)

    samsung = classify_installer("com.sec.android.app.samsungapps")
    check("Galaxy Store ถูกจัดเป็น store", samsung.kind == "store" and samsung.trusted)

    sideload = classify_installer("null")
    check("installer=null เป็น Sideload", sideload.kind == "side" and not sideload.trusted)

    sideload_system = classify_installer("null", is_system=True)
    check(
        "system app ที่ไม่มี installer ไม่ถูกกล่าวหา",
        sideload_system.kind == "system",
        f"ได้ {sideload_system.kind}",
    )

    unknown = classify_installer("com.some.random.store")
    check("installer ที่ไม่รู้จักเป็น unknown", unknown.kind == "unknown")

    empty = classify_installer("")
    check("installer ว่างเป็น Sideload", empty.kind == "side")

    check("Play มี rank ต่ำกว่า Sideload", play.rank < sideload.rank)


def test_permission_grading() -> None:
    print("\n== การจัดระดับสิทธิ์ ==")
    from core.app_model import classify_permission, summarise_permissions

    overlay = classify_permission("android.permission.SYSTEM_ALERT_WINDOW")
    check("SYSTEM_ALERT_WINDOW = critical", overlay.severity == "critical")

    install = classify_permission("android.permission.REQUEST_INSTALL_PACKAGES")
    check("REQUEST_INSTALL_PACKAGES = critical", install.severity == "critical")

    boot = classify_permission("android.permission.RECEIVE_BOOT_COMPLETED")
    check("RECEIVE_BOOT_COMPLETED = warn", boot.severity == "warn")

    sms = classify_permission("android.permission.READ_SMS")
    check("READ_SMS = warn", sms.severity == "warn")

    plain = classify_permission("android.permission.INTERNET")
    check("INTERNET = normal", plain.severity == "normal")

    summary = summarise_permissions(
        [
            "android.permission.SYSTEM_ALERT_WINDOW",
            "android.permission.READ_SMS",
            "android.permission.INTERNET",
        ]
    )
    check("นับ critical ถูก", summary["critical"] == 1, str(summary))
    check("นับ warn ถูก", summary["warn"] == 1, str(summary))
    check("นับ total ถูก", summary["total"] == 3, str(summary))


def test_app_entry() -> None:
    print("\n== AppEntry ==")
    from core.app_model import AppEntry, sort_key

    clean = AppEntry(
        package="com.real.app",
        installer="com.android.vending",
        source=None or __import__("core.app_model", fromlist=["x"]).classify_installer("com.android.vending"),
    )
    check("แอปจาก Play ไม่ถือว่าอันตราย", not clean.is_flagged_danger)

    bad = AppEntry(
        package="com.bad.app",
        installer="null",
        risk="High",
        threat_type="Adware",
        source=__import__("core.app_model", fromlist=["x"]).classify_installer("null"),
    )
    check("แอปใน blacklist ถือว่าอันตราย", bad.is_flagged_danger)
    check("effective_risk คืน High", bad.effective_risk == "High")

    safe_override = AppEntry(package="x", risk="High", user_flag="safe")
    check("user_flag=safe ทับรายการ", safe_override.effective_risk == "Safe")

    danger_override = AppEntry(package="y", user_flag="danger")
    check("user_flag=danger เติมความเสี่ยง", danger_override.effective_risk == "High")

    check("ขนาดแสดงเป็น MB", bad.size_label == "—", bad.size_label)

    sized = AppEntry(package="z", apk_size_bytes=4 * 1024 * 1024)
    check("ขนาด 4 MB แสดงถูก", sized.size_label == "4.0 MB", sized.size_label)

    threat_first = sort_key(bad)
    clean_last = sort_key(clean)
    check("อันตรายมาก่อนปลอดภัย", threat_first < clean_last, f"{threat_first} vs {clean_last}")


def test_database_normalisation() -> None:
    print("\n== ฐานข้อมูล ==")
    from core.app_model import DbEntry, normalise_database

    good = DbEntry(package="com.x.y", app_name="X", threat_type="Junk", risk="high")
    check("validate ผ่าน", good.validate() == "", good.validate())

    no_package = DbEntry()
    check("ไม่มี package → error", no_package.validate() != "")

    no_dot = DbEntry(package="justtext")
    check("package ไม่มีจุด → error", no_dot.validate() != "")

    space = DbEntry(package="com x y")
    check("package มีช่องว่าง → error", space.validate() != "")

    rows = [
        {"package": "com.b.y", "appName": "B", "type": "Adware", "risk": "High"},
        {"package": "com.a.x", "appName": "A", "type": "Junk", "risk": "Critical"},
        {"package": "com.b.y", "appName": "dup", "type": "Junk", "risk": "Low"},
        {"nope": 1},
    ]
    cleaned = normalise_database(rows)
    check("ตัดรายการซ้ำ", len(cleaned) == 2, str(len(cleaned)))
    check("เรียงตามความเสี่ยง", cleaned[0]["package"] == "com.a.x", str(cleaned[0]))

    payload = good.to_dict()
    check("to_dict มี 4 คีย์หลัก", set(payload) >= {"package", "appName", "type", "risk"})

    with_note = DbEntry(package="com.n.o", note="hello")
    check("note ถูกเก็บเมื่อมี", with_note.to_dict().get("note") == "hello")

    without = DbEntry(package="com.n.o")
    check("ไม่มี note → ไม่ใส่คีย์", "note" not in without.to_dict())


def test_flag_store() -> None:
    print("\n== FlagStore ==")
    from core.app_model import FlagStore

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "flags.json"
        store = FlagStore(path)

        store.mark_danger("com.a.x")
        check("ทำเครื่องหมาย danger", store.flag_of("com.a.x") == "danger")

        store.mark_safe("com.a.x")
        check("safe ทับ danger", store.flag_of("com.a.x") == "safe")
        check("danger ถูกลบ", "com.a.x" not in store.danger_packages())

        reloaded = FlagStore(path)
        check("บันทึกลงไฟล์แล้ว", reloaded.flag_of("com.a.x") == "safe")

        reloaded.clear_flag("com.a.x")
        check("ล้างเครื่องหมายได้", reloaded.flag_of("com.a.x") == "")

        check("toggle_safe คืนค่า", reloaded.toggle_safe("com.b.y") == "safe")
        check("toggle_safe อีกครั้ง = ว่าง", reloaded.toggle_safe("com.b.y") == "")

        # Corrupt file must not explode.
        path.write_text("{ not json", encoding="utf-8")
        tolerant = FlagStore(path)
        check("ไฟล์เสียไม่ทำให้พัง", tolerant.count == 0)


def test_live_snapshot() -> None:
    print("\n== LiveSnapshot ==")
    from core.app_model import LiveSnapshot

    shot = LiveSnapshot(timestamp="13:00:00", foreground="com.android.settings")
    check("ไม่มี overlay", not shot.has_overlay)
    check("อธิบาย overlay = ไม่มี", shot.describe_overlay() == "— ไม่มี —")

    shot.overlays = [("com.bad.app", "TYPE_APPLICATION_OVERLAY")]
    check("มี overlay", shot.has_overlay)
    check("อธิบาย overlay", shot.describe_overlay() == "com.bad.app")
    check("overlay ที่อยู่ใน blacklist", shot.suspicious_overlay({"com.bad.app"}) == ["com.bad.app"])

    two = LiveSnapshot(overlays=[("a.b.c", "TYPE_APPLICATION_OVERLAY"), ("d.e.f", "TYPE_SYSTEM_ALERT")])
    check("2 overlay อธิบายแบบย่อ", "และอีก 1" in two.describe_overlay(), two.describe_overlay())


# ---------------------------------------------------------------------------
# core.device_monitor
# ---------------------------------------------------------------------------


def test_threat_evaluation() -> None:
    print("\n== ก��รประเมินความเสี่ยงเชิงเวลา ==")
    from core.app_model import LiveSnapshot
    from core.device_monitor import evaluate_snapshot, is_protected

    clean = LiveSnapshot(processes=["com.a.b", "com.c.d"])
    signals = evaluate_snapshot(clean, {})
    check("เครื่องสะอาด → OK", signals[0].level == "OK", signals[0].level)

    bad_overlay = LiveSnapshot(
        overlays=[("com.bad.app", "TYPE_APPLICATION_OVERLAY")],
        processes=["com.bad.app"],
    )
    signals = evaluate_snapshot(bad_overlay, {"com.bad.app": "High"})
    check("blacklist วาดทับหน้าจอ → ERROR", signals[0].level == "ERROR", signals[0].level)
    check("ระบุแพ็คเกจได้", "com.bad.app" in signals[0].packages)

    running_only = LiveSnapshot(processes=["com.bad.app"])
    signals = evaluate_snapshot(running_only, {"com.bad.app": "Medium"})
    check("blacklist แต่ไม่ทับหน้าจอ → WARN", signals[0].level == "WARN", signals[0].level)

    check("ระบบถูกป้องกัน", is_protected("com.android.systemui"))
    check("แอปปกติไม่ถูกป้องกัน", not is_protected("com.bad.app"))
    check("binary ไม่มีจุดถูกป้องกัน", is_protected("surfaceflinger"))


def test_parsing_helpers() -> None:
    print("\n== ตัวช่วยแปลงข้อความ ==")
    from core.adb_extended import _parse_size, _parse_device_time, _extract_permissions
    from core.device_monitor import DeviceMonitor

    check("stat -c %s", _parse_size("123456") == 123456)
    check("ls -l", _parse_size("-rw-r--r-- 1 root root 9876543 Jan 1 00:00 /data/app/x.apk") == 9876543)
    check("ว่าง → 0", _parse_size("") == 0)

    ts = _parse_device_time("firstInstallTime=2024-07-12 08:31:04")
    check("แปลงเวลาเป็น ms", ts > 1_600_000_000_000, str(ts))

    dumpsys = """
        versionName=1.2.3
        firstInstallTime=2024-07-12 08:31:04
        requested permissions:
          android.permission.SYSTEM_ALERT_WINDOW
          android.permission.READ_SMS

          install permissions:
          android.permission.INTERNET: granted=true
    """
    perms = _extract_permissions(dumpsys)
    check("ดึงสิทธิ์ได้", "android.permission.SYSTEM_ALERT_WINDOW" in perms, str(perms))
    check("ตัดสิทธิ์ที่ระบบให้อัตโนมัติ", "android.permission.INTERNET" not in perms)
    check("ไม่ลากเข้ามาจาก install permissions", perms == [
        "android.permission.SYSTEM_ALERT_WINDOW",
        "android.permission.READ_SMS",
    ], str(perms))

    ps = """USER PID PPID VSIZE RSS WCHAN ADDR S NAME
u0_a1  123  1  1000  500 0 0 S com.example.one
u0_a1  124  1  1000  500 0 0 S com.example.two:remote
root   1    0  1000  500 0 0 S /init
"""
    packages = DeviceMonitor._packages_from_ps(ps)
    check("อ่าน ps -A ได้", "com.example.one" in packages, str(packages))
    check("ข้าม /init", "/init" not in packages)


def test_shell_quoting() -> None:
    print("\n== การอ้างอิง path บนอุปกรณ์ ==")
    from core.adb_extended import _shell_quote

    check("path ธรรมดา", _shell_quote("/data/app/x.apk") == "'/data/app/x.apk'")
    check("path มี quote", "'\\''" in _shell_quote("/data/a'b.apk"), _shell_quote("/data/a'b.apk"))


# ---------------------------------------------------------------------------
# core.updater
# ---------------------------------------------------------------------------


def test_updater_helpers() -> None:
    print("\n== ตัวช่วยอัปเดต ==")
    from core import updater

    check("เทียบเวอร์ชัน v1.2.3", updater.parse_version("v1.2.3") == (1, 2, 3))
    check("เทียบเวอร์ชัน 0.2.0", updater.parse_version("0.2.0") == (0, 2, 0))
    check("เวอร์ชันสั้น", updater.parse_version("1.2") == (1, 2, 0))

    outcome = updater.push_blacklist_to_github([{"package": "com.x.y"}], token="")
    check("ไม่มี token → error ชัดเจน", not outcome.ok and "Token" in outcome.message)

    rows = updater._parse_blacklist(
        {"entries": [{"package": "com.a.b", "risk": "High"}]}
    )
    check("แปลง wrapper ได้", len(rows) == 1, str(rows))


def test_styles() -> None:
    print("\n== ธีม ==")
    from ui import styles

    qss_light = styles.build_qss("light")
    qss_dark = styles.build_qss("dark")
    check("QSS light ไม่ว่าง", len(qss_light) > 500)
    check("light ≠ dark", qss_light != qss_dark)
    check("มี accent ใน QSS", "0071E3" in qss_light)
    check("มี accent ใน dark", "0A84FF" in qss_dark)

    check("risk High มีสี", styles.risk_color("High").startswith("#"))
    check("risk label ไทย", styles.risk_label("Critical") == "วิกฤต")
    check("risk ที่ไม่รู้จัก", styles.risk_color("ไม่มี") == styles.current()["text_muted"])


def test_icon_fallback() -> None:
    print("\n== ไอคอนสำรอง ==")
    from PySide6.QtWidgets import QApplication

    from ui.icons import colour_for_package, render_fallback_icon

    app = QApplication.instance() or QApplication([])

    c1 = colour_for_package("com.a.b")
    c2 = colour_for_package("com.a.b")
    check("สีคงที่ตาม package", c1 == c2, f"{c1} vs {c2}")

    c3 = colour_for_package("com.x.y")
    check("คนละแพ็คเกจได้คนละสี", isinstance(c3, str) and len(c3) == 7)

    pixmap = render_fallback_icon("com.fake.cleaner", 34)
    check("วาด pixmap ได้", not pixmap.isNull())
    check("ขนาดถูกต้อง", pixmap.width() == 34 and pixmap.height() == 34)

    tiny = render_fallback_icon("", 16)
    check("package ว่างก็วาดได้", not tiny.isNull())


# ---------------------------------------------------------------------------
# UI smoke (no device needed)
# ---------------------------------------------------------------------------


def test_ui_construction() -> None:
    print("\n== หน้าต่าง ==")
    from PySide6.QtWidgets import QApplication

    from ui import styles
    from ui.pages_database import DatabasePage
    from ui.pages_live import LivePage
    from ui.pages_scan import ScanPage
    from ui.pages_tools import ToolsPage

    app = QApplication.instance() or QApplication([])
    styles.apply_theme(app, "dark")

    scan = ScanPage()
    check("ScanPage สร้างได้", scan is not None)
    check("เริ่มว่าง", len(scan.get_entries()) == 0)

    from core.app_model import AppEntry, classify_installer

    entries = [
        AppEntry(
            package="com.fake.cleaner",
            installer="null",
            risk="Critical",
            threat_type="Adware",
            app_name="Virus Cleaner",
            source=classify_installer("null"),
        ),
        AppEntry(
            package="com.speedbooster.lite",
            installer="com.android.vending",
            risk="Medium",
            threat_type="Junk",
            app_name="Speed Booster",
            source=classify_installer("com.android.vending"),
        ),
    ]
    scan.set_entries(entries)
    app.processEvents()
    check("ใส่ข้อมูลแล้วมีแถว", scan.table.rowCount() == 2, str(scan.table.rowCount()))

    scan.search.setText("speed")
    app.processEvents()
    check("ค้นหากรอง", scan.table.rowCount() == 1, str(scan.table.rowCount()))
    scan.search.setText("")
    app.processEvents()

    scan.risk_filter.set_value("side")
    app.processEvents()
    check("กรอง sideload ได้", scan.table.rowCount() == 1, str(scan.table.rowCount()))
    scan.risk_filter.set_value("all")
    app.processEvents()

    live = LivePage()
    check("LivePage สร้างได้", live is not None)
    check("เริ่มไม่ auto", not live.auto_refresh)
    live.reset()
    check("reset ได้", live is not None)

    tools = ToolsPage()
    check("ToolsPage สร้างได้", tools is not None)

    db = DatabasePage()
    check("DatabasePage สร้างได้", db is not None)
    db.load(
        [
            {"package": "com.a.b", "appName": "A", "type": "Adware", "risk": "High"},
            {"package": "com.c.d", "appName": "C", "type": "Junk", "risk": "Low"},
        ],
        origin="remote",
    )
    app.processEvents()
    check("โหลด DB แล้วมีแถว", db.table.rowCount() == 2, str(db.table.rowCount()))
    check("เรียงตามความเสี่ยง", db._visible[0].package == "com.a.b")


def test_bulk_selection() -> None:
    print("\n== เลือกหลายแอป ==")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from core.app_model import AppEntry, classify_installer
    from ui import styles
    from ui.pages_scan import COL_CHECK, ScanPage

    app = QApplication.instance() or QApplication([])
    styles.apply_theme(app, "dark")

    scan = ScanPage()
    scan.set_entries(
        [
            AppEntry(package="com.a.one", source=classify_installer("null")),
            AppEntry(package="com.b.two", source=classify_installer("null")),
            AppEntry(package="com.c.three", source=classify_installer("null")),
        ]
    )
    app.processEvents()

    check("เริ่มไม่เลือกอะไร", len(scan.checked_packages()) == 0)

    for row in (0, 2):
        scan.table.item(row, COL_CHECK).setCheckState(Qt.CheckState.Checked)
    app.processEvents()

    chosen = scan.checked_packages()
    check("เลือกได้ 2 รายการ", len(chosen) == 2, str(chosen))
    check("เลือกถูกตัว", set(chosen) == {"com.a.one", "com.c.three"}, str(chosen))

    scan.table.item(0, COL_CHECK).setCheckState(Qt.CheckState.Unchecked)
    app.processEvents()
    check("เอาออกได้", len(scan.checked_packages()) == 1)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 62)
    print(f"ADSremovedPP smoke tests — v0.2.0")
    print("=" * 62)

    tests = [
        test_installer_classification,
        test_permission_grading,
        test_app_entry,
        test_database_normalisation,
        test_flag_store,
        test_live_snapshot,
        test_threat_evaluation,
        test_parsing_helpers,
        test_shell_quoting,
        test_updater_helpers,
        test_styles,
        test_icon_fallback,
        test_ui_construction,
        test_bulk_selection,
    ]

    for test in tests:
        try:
            test()
        except Exception as exc:  # noqa: BLE001 - a crashing test is a failing test
            import traceback

            traceback.print_exc()
            FAILED.append(f"{test.__name__} raised {exc.__class__.__name__}: {exc}")

    print("\n" + "=" * 62)
    print(f"ผ่าน {len(PASSED)} เคส · ไม่ผ่าน {len(FAILED)} เคส")
    if FAILED:
        print("\nรายการที่ไม่ผ่าน:")
        for item in FAILED:
            print(f"  - {item}")
    print("=" * 62)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())