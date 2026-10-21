"""End-to-end check of the uninstall → GAS report path.

Runs the **real** :class:`MainWindow` code path — ``_on_uninstall_results`` →
``_ship_uninstall_report`` → ``GASReporterWorker`` → HTTP POST — against a
local mock server, and prints the JSON that would reach Google Apps Script.

This is the test that would have caught the ``Q_ARG(list, …)`` bug, which
made every report disappear before it left the machine.

Run from the project root:

    python tests/gas_report_probe.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from core.adb_manager import UninstallOutcome  # noqa: E402
from core.app_model import AppEntry, classify_installer  # noqa: E402
from core.gas_reporter import load_gas_config, save_gas_config  # noqa: E402

received: list[tuple[str, bytes]] = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        length = int(self.headers.get("Content-Length", 0))
        received.append((self.path, self.rfile.read(length)))
        body = json.dumps(
            {
                "ok": True,
                "received": 1,
                "logged": 0,
                "queued": 1,
                "skipped": 0,
                "details": [{"package": "com.fake.cleaner", "outcome": "queued"}],
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def main() -> int:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    original = load_gas_config()
    cfg = load_gas_config()
    cfg.url = f"http://127.0.0.1:{port}/exec"
    cfg.reporter = "probe-branch"
    save_gas_config(cfg)

    from core.adb_extended import ExtendedAdbManager, ExtendedAdbWorker
    from ui import styles
    from ui.main_window import MainWindow

    styles.apply_theme(app, "dark")
    manager = ExtendedAdbManager()
    worker = ExtendedAdbWorker(manager)
    window = MainWindow(manager, worker)
    window.state.device_title = "Samsung SM-A546E"
    window.state.android_release = "14"

    # A realistic entry: blacklisted, installed from a sideload, labelled.
    entry = AppEntry(
        package="com.fake.cleaner",
        app_name="Virus Cleaner Pro",
        threat_type="Adware",
        risk="High",
        installer="null",
        source=classify_installer("null"),
    )
    window.scan_page.set_entries([entry])

    outcome = UninstallOutcome(package="com.fake.cleaner", success=True, message="Success")
    window._on_uninstall_results([outcome])

    deadline = time.monotonic() + 10
    while not received and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.05)

    print(f"POST received: {len(received)}")
    if not received:
        print("FAIL — ไม่มีข้อมูลไปถึง backend")
        save_gas_config(original)
        server.shutdown()
        return 1

    path, raw = received[0]
    payload = json.loads(raw.decode("utf-8"))
    print(f"path:   {path}")
    print(f"body:   {json.dumps(payload, ensure_ascii=False, indent=2)}")

    checks = [
        ("มี packages", bool(payload.get("packages"))),
        ("package ถูกต้วย", payload["packages"][0]["package"] == "com.fake.cleaner"),
        ("ชื่อแอปถูกต้วย", payload["packages"][0]["appName"] == "Virus Cleaner Pro"),
        ("ประเภทถูกต้วย", payload["packages"][0]["category"] == "Adware"),
        ("ความเสี่ยงถูกต้วย", payload["packages"][0]["riskLevel"] == "สูง"),
        ("แหล่งที่มาถูกต้วย", payload["packages"][0]["source"] == "Sideload"),
        ("ชื่อเครื่องถูกต้วย", payload.get("deviceModel") == "Samsung SM-A546E"),
        ("เวอร์ชัน Android ถูกต้วย", payload.get("osVersion") == "Android 14"),
        ("ชื่อสาขาถูกต้วย", payload.get("reporter") == "probe-branch"),
    ]

    ok = True
    for label, passed in checks:
        print(f"  {'[OK]  ' if passed else '[FAIL]'} {label}")
        ok = ok and passed

    window.close()
    app.processEvents()
    save_gas_config(original)
    server.shutdown()
    server.server_close()

    print()
    print("RESULT:", "OK" if ok else "needs attention")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
