"""
ui.main_window
==============

The application shell: sidebar navigation, a stacked page area and a collapsible
log dock.

Threading contract
------------------
The window never calls :class:`~core.adb_extended.ExtendedAdbManager` directly.
It emits requests through :meth:`ExtendedAdbWorker.request` and receives results
through the worker's signals. Every slot below therefore runs on the GUI thread
and can touch widgets directly.

Two subtleties are worth remembering when editing this file:

* **Worker threads must be parentless.** An earlier version parented each
  helper ``QThread`` to the window; Qt destroyed them during shutdown and the
  process crashed. :meth:`_spawn` now creates unparented threads and keeps them
  in ``_spawned_threads`` so :meth:`closeEvent` can drain them politely.
* **Signals are wired by name.** :meth:`_spawn` receives a mapping of
  *signal names* to slots rather than signal objects, because the worker does
  not exist until the thread is built.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QThread, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import config
from core import updater
from core.adb_extended import ExtendedAdbManager, ExtendedAdbWorker
from core.app_model import AppEntry, FlagStore, LiveSnapshot

from . import styles
from .icons import IconStore
from .log_panel import LogPanel
from .pages_database import DatabasePage
from .pages_live import LivePage
from .pages_scan import ScanPage
from .pages_tools import ToolsPage
from .widgets import HBox, Hairline, NavButton, VBox, make_button

#: Navigation order. The index into the stacked widget matches this order.
PAGE_SCAN = 0
PAGE_LIVE = 1
PAGE_TOOLS = 2
PAGE_DATABASE = 3


class AppState:
    """Mutable window state shared between pages."""

    def __init__(self) -> None:
        self.serial: str = ""
        self.connected: bool = False
        self.blacklist: list[dict] = []
        self.db_origin: str = "empty"
        self.entries: list[AppEntry] = []
        self.live_snapshot: LiveSnapshot | None = None
        self.flags = FlagStore()


class MainWindow(QMainWindow):
    """Sidebar + pages + log, wired to the ADB worker."""

    def __init__(
        self,
        manager: ExtendedAdbManager,
        worker: ExtendedAdbWorker,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.worker = worker
        self.state = AppState()
        self.icons = IconStore()

        self._spawned_threads: list[QThread] = []
        self._background_workers: list[ExtendedAdbWorker] = []
        self._busy = False
        self._closing = False

        self.setWindowTitle(f"{config.APP_NAME} — {config.APP_TITLE}")
        self.setMinimumSize(1180, 740)
        self.resize(1360, 860)

        self._build_ui()
        self._wire_pages()
        self._connect_worker()
        self._install_shortcuts()

        QTimer.singleShot(250, self._startup)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        row.addWidget(self._build_sidebar())

        right = VBox(spacing=0)
        self.stack = QStackedWidget()
        self.scan_page = ScanPage()
        self.live_page = LivePage()
        self.tools_page = ToolsPage()
        self.database_page = DatabasePage()

        self.stack.addWidget(self.scan_page)
        self.stack.addWidget(self.live_page)
        self.stack.addWidget(self.tools_page)
        self.stack.addWidget(self.database_page)
        right.add(self.stack, 1)

        self.log_panel = LogPanel()
        right.addWidget(self.log_panel)

        row.addWidget(right, 1)
        root.addWidget(body)
        self.setCentralWidget(central)

        self._apply_theme_styles()

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(206)

        column = QVBoxLayout(sidebar)
        column.setContentsMargins(10, 16, 10, 10)
        column.setSpacing(3)

        head = VBox(spacing=1, margins=(6, 0, 6, 0))
        title = QLabel(config.APP_NAME)
        title.setObjectName("SidebarTitle")
        head.add(title)
        version = QLabel(f"เวอร์ชัน {config.CURRENT_VERSION}")
        version.setObjectName("SidebarVersion")
        head.add(version)
        column.addWidget(head)
        column.addSpacing(12)

        self.nav_scan = NavButton("◎", "สแกน", "scan")
        self.nav_live = NavButton("◉", "ตรวจจับแอปที่ทำงาน", "live")
        self.nav_tools = NavButton("⚒", "เครื่องมือ", "tools")
        self.nav_database = NavButton("⛨", "ฐานข้อมูล", "database")

        self.nav_scan.clicked.connect(lambda: self._select_page(PAGE_SCAN))
        self.nav_live.clicked.connect(lambda: self._select_page(PAGE_LIVE))
        self.nav_tools.clicked.connect(lambda: self._select_page(PAGE_TOOLS))
        self.nav_database.clicked.connect(lambda: self._select_page(PAGE_DATABASE))

        for button in (self.nav_scan, self.nav_live, self.nav_tools, self.nav_database):
            button.setProperty("baseText", button.text())
            column.addWidget(button)

        column.addStretch(1)

        column.addWidget(Hairline())
        column.addSpacing(6)

        self.connection_label = QLabel("●  ยังไม่ได้เชื่อมต่อ")
        self.connection_label.setObjectName("Faint")
        self.connection_label.setContentsMargins(6, 0, 6, 0)
        column.addWidget(self.connection_label)

        footer = HBox(spacing=4, margins=(4, 2, 2, 2))
        self.btn_theme = make_button(
            "◐",
            kind="ghost",
            tooltip="สลับธีมสว่าง / มืด",
            on_click=self._toggle_theme,
        )
        footer.addWidget(self.btn_theme)

        self.btn_reconnect = make_button(
            "↻",
            kind="ghost",
            tooltip="ตรวจการเชื่อมต่ออีกครั้ง",
            on_click=self.on_probe,
        )
        footer.addWidget(self.btn_reconnect)
        footer.addStretch(1)
        column.addWidget(footer)

        self.nav_scan.setChecked(True)
        return sidebar

    def _wire_pages(self) -> None:
        # -- scan page
        scan = self.scan_page
        scan.refresh_requested.connect(self.on_probe)
        scan.scan_requested.connect(self.on_scan)
        scan.load_all_requested.connect(self.on_load_all)
        scan.prime_icons_requested.connect(self._prime_icons)
        scan.uninstall_requested.connect(self._on_uninstall)
        scan.clear_data_requested.connect(self._on_clear_data)
        scan.force_stop_requested.connect(self._on_force_stop)
        scan.restrict_requested.connect(self._on_restrict)
        scan.toggle_safe_requested.connect(self._on_toggle_safe)
        scan.toggle_danger_requested.connect(self._on_toggle_danger)
        scan.detail_requested.connect(self._on_detail_requested)

        # -- live page
        live = self.live_page
        live.refresh_requested.connect(self._on_live_refresh)
        live.force_stop_requested.connect(self._on_force_stop)
        live.restrict_requested.connect(self._on_restrict)
        live.open_settings_requested.connect(self._on_open_settings)
        live.detail_requested.connect(self._on_detail_requested)

        # -- tools page
        tools = self.tools_page
        tools.utility_requested.connect(self._on_utility)
        tools.reboot_requested.connect(self._on_reboot)
        tools.check_update_requested.connect(self._on_check_update)
        tools.reload_database_requested.connect(self._on_reload_database)
        tools.clear_icon_cache_requested.connect(self._on_clear_icon_cache)

        # -- database page
        database = self.database_page
        database.status_message.connect(self.log_panel.append)
        database.save_local_requested.connect(self._on_database_saved)
        database.push_remote_requested.connect(self._on_database_push)
        database.set_path(config.LOCAL_DB_PORTABLE)

    def _connect_worker(self) -> None:
        self.worker.task_requested.connect(self.worker._dispatch)
        self.worker.log_line.connect(self.log_panel.append)
        self.worker.devices_ready.connect(self._on_devices_ready)
        self.worker.device_info_ready.connect(self._on_device_info)
        self.worker.scan_finished.connect(self._on_scan_finished)
        self.worker.uninstall_results.connect(self._on_uninstall_results)
        self.worker.action_result.connect(self._on_action_result)
        self.worker.task_failed.connect(self._on_task_failed)
        self.worker.task_done.connect(self._on_task_done)

        self.worker.packages_detailed_ready.connect(self._on_packages_detailed)
        self.worker.app_details_ready.connect(self._on_app_details)
        self.worker.live_snapshot_ready.connect(self._on_live_snapshot)
        self.worker.live_signals_ready.connect(self._on_live_signals)
        self.worker.force_stop_results.connect(self._on_uninstall_results)
        self.worker.icons_primed.connect(self._on_icons_primed)

    def _install_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.on_probe)
        QShortcut(QKeySequence("F5"), self, activated=self.on_scan)
        QShortcut(QKeySequence("Ctrl+1"), self, activated=lambda: self._select_page(PAGE_SCAN))
        QShortcut(QKeySequence("Ctrl+2"), self, activated=lambda: self._select_page(PAGE_LIVE))
        QShortcut(QKeySequence("Ctrl+3"), self, activated=lambda: self._select_page(PAGE_TOOLS))
        QShortcut(QKeySequence("Ctrl+4"), self, activated=lambda: self._select_page(PAGE_DATABASE))
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.log_panel.toggle_collapsed)
        QShortcut(QKeySequence("Ctrl+D"), self, activated=self._toggle_theme)

    def _apply_theme_styles(self) -> None:
        """Re-apply per-widget stylesheets that need palette values."""
        tokens = styles.current()
        self.log_panel.text.setStyleSheet(
            f"QPlainTextEdit{{background-color:{tokens['surface_sunken']};"
            f"border:none;border-top:1px solid {tokens['hairline']};"
            f'font-family:{styles.FONT_MONO};font-size:11.5px;'
            f"color:{tokens['code_text']};padding:10px 14px;}}"
        )
        self.log_panel.toggle.setStyleSheet(
            "QPushButton#Ghost{background:transparent;border:none;text-align:left;"
            f'font-family:{styles.FONT_UI};font-size:12.5px;font-weight:600;'
            f"color:{tokens['text']};padding:2px 4px;}}"
            "QPushButton#Ghost:hover{background:transparent;"
            f"color:{tokens['accent']};}}"
        )

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _startup(self) -> None:
        self.log_panel.append("INFO", f"{config.APP_NAME} เวอร์ชัน {config.CURRENT_VERSION}")
        self.tools_page.set_cache_size(IconStore.cache_size_mb())

        if self.manager.available:
            self.log_panel.append("OK", f"พบ adb ที่ {self.manager.adb_path}")
        else:
            self.log_panel.append("ERROR", "ไม่พบ adb.exe — วางไฟล์ไว้ที่โฟลเดอร์ bin/")

        self._on_reload_database()
        QTimer.singleShot(500, self.on_probe)

    def _toggle_theme(self) -> None:
        name = styles.toggle_theme(QApplication.instance())
        self._apply_theme_styles()
        self.log_panel.append("INFO", f"เปลี่ยนธีมเป็นโหมด{'สว่าง' if name == 'light' else 'มืด'}")
        # Widgets that painted palette values directly need a repaint.
        self.scan_page.refresh_icons()
        self.scan_page.repaint_pills()
        self.database_page.refresh_theme()
        self.live_page.refresh_theme()
        self.tools_page.refresh_theme()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _select_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for offset, button in enumerate(
            (self.nav_scan, self.nav_live, self.nav_tools, self.nav_database)
        ):
            button.setChecked(offset == index)

        if index == PAGE_LIVE and not self.state.connected:
            self.live_page.reset()

    # ------------------------------------------------------------------
    # Background helpers
    # ------------------------------------------------------------------

    def _spawn(self, task: str, payload: dict | None = None, hooks: dict | None = None) -> None:
        """Run one short background job on its own parentless thread.

        The thread must not be parented to the window: Qt destroys child
        ``QThread`` objects during shutdown, which killed the interpreter
        outright in earlier versions. Keeping them in a list and draining it in
        :meth:`closeEvent` is the reliable pattern.

        ``hooks`` maps a worker signal *name* to a slot, because the worker does
        not exist until this method runs.
        """
        if self._closing:
            return

        thread = QThread()
        worker = ExtendedAdbWorker(self.manager)
        worker.moveToThread(thread)

        thread.started.connect(
            lambda w=worker, t=task, p=payload or {}: w._dispatch(t, p)
        )
        for signal_name, slot in (hooks or {}).items():
            signal = getattr(worker, signal_name, None)
            if signal is not None:
                signal.connect(slot)

        worker.log_line.connect(self.log_panel.append)
        worker.task_failed.connect(self._forward_error)

        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._spawned_threads.append(thread)
        thread.start()

        def _cleanup() -> None:
            if thread in self._spawned_threads:
                self._spawned_threads.remove(thread)

        thread.finished.connect(_cleanup)

    def _forward_error(self, message: str) -> None:
        self.log_panel.append("ERROR", message)

    def _post(self, task: str, payload: dict | None = None) -> None:
        """Queue a task on the shared worker thread."""
        self.worker.request(task, payload or {})

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.scan_page.set_busy(busy)
        self.live_page.set_busy(busy)
        self.tools_page.set_busy(busy)
        self.btn_reconnect.setEnabled(not busy)

    # ------------------------------------------------------------------
    # Device
    # ------------------------------------------------------------------

    def on_probe(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self.log_panel.append("INFO", "กำลังตรวจหามือถือ…")
        self._post("probe", {"serial": self.state.serial})

    def _on_devices_ready(self, records: list) -> None:
        self._set_busy(False)
        if not records:
            self.state.connected = False
            self.state.serial = ""
            self._set_connected_ui(False, "")
            self.scan_page.set_device_state(False)
            self.live_page.reset()
            self.connection_label.setText("●  ยังไม่ได้เชื่อมต่อ")
            self.connection_label.setStyleSheet(
                f"color:{styles.current()['text_faint']};font-size:11.5px;"
            )
            return

        record = records[0]
        self.state.connected = True
        self.state.serial = record.serial
        self._set_connected_ui(True, record.display_name)
        self.scan_page.set_device_state(True, record.display_name)

    def _set_connected_ui(self, connected: bool, name: str) -> None:
        tokens = styles.current()
        if connected:
            self.connection_label.setText(f"●  เชื่อมต่อแล้ว · {name}")
            self.connection_label.setStyleSheet(
                f"color:{tokens['success']};font-size:11.5px;font-weight:600;"
            )
        else:
            self.connection_label.setText("●  ยังไม่ได้เชื่อมต่อ")
            self.connection_label.setStyleSheet(f"color:{tokens['text_faint']};font-size:11.5px;")

    def _on_device_info(self, info: dict) -> None:
        self.scan_page.set_device_info(info)

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def on_scan(self) -> None:
        if not self.state.connected:
            self.log_panel.append("WARN", "ยังไม่ได้เชื่อมต่อมือถือ — เสียบแล้วกดรีเฟรช")
            return
        self._set_busy(True)
        self._post(
            "scan_detailed",
            {
                "serial": self.state.serial,
                "blacklist": self.state.blacklist,
                "flags": {p: self.state.flags.flag_of(p) for p in [e.package for e in self.scan_page.get_entries()]} or {},
                "include_clean": False,
            },
        )

    def on_load_all(self) -> None:
        if not self.state.connected:
            self.log_panel.append("WARN", "ยังไม่ได้เชื่อมต่อมือถือ")
            return
        self._set_busy(True)
        self._post(
            "scan_detailed",
            {
                "serial": self.state.serial,
                "blacklist": self.state.blacklist,
                "flags": self._flags_map(),
                "include_clean": True,
            },
        )

    def _flags_map(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for entry in self.scan_page.get_entries():
            state = self.state.flags.flag_of(entry.package)
            if state:
                mapping[entry.package] = state
        for package in self.state.flags.safe_packages():
            mapping.setdefault(package, "safe")
        for package in self.state.flags.danger_packages():
            mapping[package] = "danger"
        return mapping

    def _on_packages_detailed(self, entries: list) -> None:
        self._set_busy(False)
        self.state.entries = list(entries)
        self.scan_page.set_entries(entries)
        threats = [entry for entry in entries if entry.is_flagged_danger or entry.in_blacklist]
        self.nav_scan.set_badge(len(threats))
        self.tools_page.set_database_status(len(self.state.blacklist), self.state.db_origin)

    def _on_scan_finished(self, threats: list, total: int) -> None:
        self._set_busy(False)
        self.nav_scan.set_badge(len(threats))

    def _prime_icons(self, entries: list) -> None:
        if not entries or not self.state.connected:
            return
        self._post(
            "prime_icons",
            {"serial": self.state.serial, "entries": entries, "limit": 24},
        )

    def _on_icons_primed(self, found: int, attempted: int) -> None:
        if found:
            self.log_panel.append("OK", f"ดึงไอคอนจริงได้ {found}/{attempted} รายการ")
            self.scan_page.refresh_icons()

    # ------------------------------------------------------------------
    # App detail
    # ------------------------------------------------------------------

    def _on_detail_requested(self, package: str) -> None:
        if not self.state.connected or not package:
            return
        self._select_page(PAGE_SCAN)
        self._post(
            "app_details",
            {
                "serial": self.state.serial,
                "package": package,
                "blacklist": self.state.blacklist,
                "flags": self._flags_map(),
            },
        )

    def _on_app_details(self, entry: AppEntry) -> None:
        self.scan_page.apply_details(entry)

    # ------------------------------------------------------------------
    # Live monitor
    # ------------------------------------------------------------------

    def _on_live_refresh(self) -> None:
        if not self.state.connected:
            self.live_page.reset()
            return
        self._post(
            "live_snapshot",
            {"serial": self.state.serial, "blacklist": self.state.blacklist},
        )

    def _on_live_snapshot(self, shot: LiveSnapshot) -> None:
        self.state.live_snapshot = shot  # type: ignore[attr-defined]

    def _on_live_signals(self, signals: list) -> None:
        self.live_page.apply_snapshot(self.state.live_snapshot, signals)  # type: ignore[attr-defined]
        self.scan_page.set_threat_signals(signals)
        errors = [s for s in signals if s.level == "ERROR"]
        if errors:
            self.nav_live.set_badge(len(errors))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _require_serial(self, action: str) -> str:
        if not self.state.connected or not self.state.serial:
            self.log_panel.append("WARN", f"ยังไม่ได้เชื่อมต่อมือถือ — ไม่สามารถ{action}")
            return ""
        return self.state.serial

    def _on_uninstall(self, packages: list, force: bool, label: str) -> None:
        serial = self._require_serial("ถอนการติดตั้ง")
        if not serial:
            return

        if not self._confirm_destructive(
            f"ยืนยันการ{label}",
            f"จะ{'ถอนการติดตั้ง' if force else 'ลบ'} {len(packages)} แอป:\n\n"
            + "\n".join(f"  · {p}" for p in packages[:12])
            + ("\n  …" if len(packages) > 12 else "")
            + "\n\nข้อมูลภายในแอปจะถูกลบถาวร กู้คืนไม่ได้",
        ):
            return

        self._set_busy(True)
        self._post(
            "uninstall",
            {"serial": serial, "packages": packages, "force": force, "label": label},
        )

    def _on_clear_data(self, packages: list, label: str) -> None:
        serial = self._require_serial("ล้างข้อมูล")
        if not serial:
            return

        if not self._confirm_destructive(
            "ยืนยันการล้างข้อมูล",
            f"จะล้างข้อมูล {len(packages)} แอป:\n\n"
            + "\n".join(f"  · {p}" for p in packages[:12])
            + "\n\nรูปภาพ ข้อความ และบัญชีที่ล็อกอินไว้ในแอปจะหายถาวร",
        ):
            return

        self._set_busy(True)
        self._post("clear_data", {"serial": serial, "packages": packages, "label": label})

    def _on_force_stop(self, packages: list) -> None:
        serial = self._require_serial("สั่งหยุดแอป")
        if not serial:
            return
        self._post("force_stop", {"serial": serial, "packages": packages})

    def _on_restrict(self, packages: list, bucket: str) -> None:
        serial = self._require_serial("จำกัดการทำงานเบื้องหลัง")
        if not serial:
            return
        self._set_busy(True)
        self._post("restrict", {"serial": serial, "packages": packages, "bucket": bucket})

    def _on_open_settings(self, action: str, label: str) -> None:
        serial = self._require_serial(f"เปิด{label}")
        if not serial:
            return
        self._post("open_settings", {"serial": serial, "action": action, "label": label})

    def _on_reboot(self, target: str, label: str) -> None:
        serial = self._require_serial("สั่งรีสตาร์ท")
        if not serial:
            return
        self._post("reboot", {"serial": serial, "target": target, "label": label})

    def _on_utility(self, key: str, label: str) -> None:
        if key == "open_db":
            self._select_page(PAGE_DATABASE)
            return

        serial = self._require_serial(f"ใช้{label}")
        if not serial:
            return

        mapping = {
            "clear_one": "open_settings",
            "wipe_cache": "wipe_cache",
            "restart_app": "restart_apps",
            "dev_options": "open_settings",
            "app_settings": "open_settings",
            "usage_access": "open_settings",
            "package_installer": "open_settings",
            "kill_server": "kill_server",
            "restart_server": "start_server",
        }
        task = mapping.get(key)
        if task == "open_settings":
            action = {
                "dev_options": "APPLICATION_DEVELOPMENT_SETTINGS",
                "app_settings": "APPLICATION_SETTINGS",
                "usage_access": "IGNORE_BATTERY_OPTIMIZATION_SETTINGS",
                "package_installer": "MANAGE_UNKNOWN_APP_SOURCES",
            }.get(key, "APPLICATION_SETTINGS")
            self._post("open_settings", {"serial": serial, "action": action, "label": label})
            return

        if task == "wipe_cache":
            if not self._confirm_destructive("ล้างแคชระบบ", "ล้างแคชของแอปทั้งระบบ — ค่าต่าง ๆ อาจถูกรีเซ็ต"):
                return
            self._post("wipe_cache", {"serial": serial})
            return

        if task == "restart_apps":
            if not self._confirm_destructive("รีสตาร์ทแอป", "บังคับหยุดแอปทั้งหมดที่กำลังทำงาน"):
                return
            self._post("restart_apps", {"serial": serial})
            return

        if task in {"kill_server", "start_server"}:
            self._post(task, {})
            return

        self.log_panel.append("INFO", f"คำสั่ง {label} ยังไม่ได้เชื่อมต่อ")

    def _on_toggle_safe(self, package: str) -> None:
        state = self.state.flags.toggle_safe(package)
        if state:
            self.log_panel.append("OK", f"เก็บ {package} ไว้ — จะไม่แจ้งเตือนอีก")
        else:
            self.log_panel.append("INFO", f"ยกเลิกการเก็บ {package}")
        self._reapply_flags()

    def _on_toggle_danger(self, package: str) -> None:
        state = self.state.flags.toggle_danger(package)
        if state:
            self.log_panel.append("WARN", f"ทำเครื่องหมาย {package} ว่าอันตราย")
        else:
            self.log_panel.append("INFO", f"ยกเลิกการทำเครื่องหมายอันตรายของ {package}")
        self._reapply_flags()

    def _reapply_flags(self) -> None:
        mapping = self._flags_map()
        for entry in self.scan_page.get_entries():
            entry.user_flag = mapping.get(entry.package, "")
        self.scan_page.set_entries(self.scan_page.get_entries())

    def _on_uninstall_results(self, results: list) -> None:
        self._set_busy(False)
        succeeded = [r for r in results if getattr(r, "success", False)]
        if succeeded:
            self.scan_page.remove_packages([r.package for r in succeeded])
            if self._closing:
                return
        failed = [r for r in results if not getattr(r, "success", False)]
        if failed and not self._closing:
            names = ", ".join(r.package for r in failed[:5])
            self.log_panel.append("ERROR", f"บางรายการทำไม่สำเร็จ: {names}")

    def _on_action_result(self, label: str, ok: bool, message: str) -> None:
        level = "OK" if ok else "ERROR"
        self.log_panel.append(level, message)
        if ok and not self._closing:
            QMessageBox.information(self, label, message)

    def _on_task_failed(self, message: str) -> None:
        self._set_busy(False)
        self.log_panel.append("ERROR", message)

    def _on_task_done(self, task: str) -> None:
        if not self._closing:
            self._set_busy(False)

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------

    def _on_reload_database(self) -> None:
        self.tools_page.show_progress(True)
        self._spawn("db_load", {}, {"db_loaded": self._on_db_loaded})

    def _on_database_saved(self, rows: list) -> None:
        self.state.blacklist = list(rows)
        self.tools_page.set_database_status(len(rows), "local")
        self.log_panel.append("OK", "อัปเดตรายการในเครื่องเรียบร้อย")

    def _on_database_push(self, rows: list) -> None:
        self.log_panel.append("INFO", "กำลังส่งรายการขึ้น GitHub…")
        self._spawn("db_push", {"rows": rows}, {"db_pushed": self._on_db_pushed})

    def _on_db_loaded(self, rows: list, origin: str) -> None:
        self.state.blacklist = list(rows)
        self.state.db_origin = origin
        self.database_page.load(rows, origin)
        self.tools_page.set_database_status(len(rows), origin)
        self.tools_page.show_progress(False)
        self.log_panel.append("OK", f"โหลดฐานข้อมูลแล้ว — {len(rows)} รายการ ({origin})")

    def _on_db_pushed(self, ok: bool, message: str, url: str) -> None:
        level = "OK" if ok else "ERROR"
        text = (
            message
            if ok
            else updater.describe_push_failure(updater.PushOutcome(ok, message, url))
        )
        self.log_panel.append(level, text)
        self.database_page.status_message.emit(level, text)
        if ok:
            self.database_page.load(self.state.blacklist, "local")
            self.database_page._set_dirty(False)

    def _on_clear_icon_cache(self) -> None:
        removed = IconStore.clear_cache()
        self.tools_page.set_cache_size(IconStore.cache_size_mb())
        self.log_panel.append("OK", f"ล้างแคชไอคอนแล้ว — ลบ {removed} ไฟล์")
        self.scan_page.refresh_icons()

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def _on_check_update(self) -> None:
        self.tools_page.show_progress(True)
        self.log_panel.append("INFO", "กำลังตรวจเวอร์ชันใหม่…")
        self._spawn(
            "check_update",
            {},
            {"update_found": self._on_update_found},
        )

    def _on_update_found(self, info: dict) -> None:
        self.tools_page.show_progress(False)
        if not info:
            QMessageBox.information(
                self,
                "ไม่มีเวอร์ชันใหม่",
                f"เวอร์ชันปัจจุบัน {config.CURRENT_VERSION} เป็นเวอร์ชันล่าสุดแล้ว",
            )
            return

        version = str(info.get("version", "?"))
        notes = str(info.get("notes", ""))
        answer = QMessageBox.question(
            self,
            "พบเวอร์ชันใหม่",
            f"พบเวอร์ชัน {version} (ปัจจุบัน {config.CURRENT_VERSION})\n\n"
            f"{notes}\n\n"
            "ดาวน์โหลดและติดตั้งเลยหรือไม่?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.tools_page.show_progress(True)
        self._spawn(
            "download_update",
            {"info": info},
            {"update_ready": self._on_update_ready},
        )

    def _on_update_ready(self, path: str, message: str) -> None:
        self.tools_page.show_progress(False)
        if not path:
            self.log_panel.append("ERROR", message)
            return

        self.log_panel.append("OK", message)
        answer = QMessageBox.question(
            self,
            "ดาวน์โหลดเสร็จแล้ว",
            f"{message}\n\n"
            "โปรแกรมจะปิดและเปิดใหม่อัตโนมัติ ปิดตอนนี้หรือไม่?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._launch_updater(Path(path))

    def _launch_updater(self, downloaded: Path) -> None:
        """Write and run the batch file that swaps the executable."""
        try:
            current, _staging = updater.manual_update_paths()
            script = updater.temp_script_path()
            updater.build_updater_script(current, downloaded, script)
            updater.launch_updater(script)
        except Exception as exc:  # noqa: BLE001 - shutdown path
            self.log_panel.append("ERROR", f"ติดตั้งอัปเดตไม่สำเร็จ: {exc}")
            QMessageBox.critical(
                self,
                "อัปเดตไม่สำเร็จ",
                f"เขียนสคริปต์อัปเดตไม่สำเร็จ:\n{exc}\n\n"
                f"ดาวน์โหลดไว้ที่:\n{downloaded}\n\n"
                "กรุณาปิดโปรแกรมแล้วเปิดไฟล์ดังกล่าวเอง",
            )
            return

        self._closing = True
        QApplication.quit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _confirm_destructive(self, title: str, message: str) -> bool:
        """Ask before anything irreversible. Returns ``True`` to proceed."""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(message)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._closing = True
        try:
            self.manager.kill_server()
        except Exception:  # noqa: BLE001 - shutting down regardless
            pass

        self._wait_for_spawned()

        # Wind the shared worker down too. Leaving its event loop running makes
        # Qt print "QThread: Destroyed while thread is still running" and abort.
        shared = self.worker.thread()
        if shared is not None and shared.isRunning():
            shared.quit()
            shared.wait(3000)

        super().closeEvent(event)

    def _wait_for_spawned(self, timeout_ms: int = 4000) -> None:
        """Drain helper threads so Qt never destroys a running ``QThread``."""
        deadline = time.monotonic() + timeout_ms / 1000.0
        for thread in list(self._spawned_threads):
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0:
                break
            thread.quit()
            thread.wait(remaining)
        self._spawned_threads.clear()