"""
ui.pages_tools
==============

Utility tiles plus the application self-update panel.

This page collects the one-shot device actions that do not belong to a scan —
wipe data, wipe-user-data, open settings screens, reboot modes — and the
version/database maintenance the technician needs when the tool misbehaves.

Tiles are grouped so the destructive ones sit at the bottom, behind a hairline,
away from the taps of a hurried hand.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

import config

from . import styles
from .widgets import (
    Card,
    ClickableFrame,
    HBox,
    SectionHeader,
    VBox,
    make_button,
)


@dataclass(frozen=True)
class UtilityAction:
    """One tappable tile on the tools page."""

    key: str
    glyph: str
    title: str
    caption: str
    danger: bool = False


REPAIR_ACTIONS: tuple[UtilityAction, ...] = (
    UtilityAction("clear_one", "✦", "ล้างข้อมูลแอป", "เลือกแอปในหน้าสแกนแล้วกดล้าง"),
    UtilityAction("wipe_cache", "◍", "ล้างแคชระบบ", "pm trim-caches — ช่วยเรื่องเครื่องช้า"),
    UtilityAction("restart_app", "↻", "รีสตาร์ทแอปที่ค้าง", "force-stop ทุกแอปที่ค้างอยู่"),
    UtilityAction("dev_options", "⚙", "เปิดตัวเลือกนักพัฒนา", "เปิด USB debugging เพิ่ม"),
    UtilityAction("app_settings", "▤", "หน้าตั้งค่าแอป", "เปิดรายชื่อแอปทั้งหมด"),
    UtilityAction("usage_access", "◐", "สิทธิ์พื้นหลัง", "ดูแอปที่กินแบตเตอรี่เบื้องหลัง"),
    UtilityAction("package_installer", "↓", "ตัวติดตั้งแพ็คเกจ", "เปิดการติดตั้ง APK ด้วยมือ"),
)

REBOOT_ACTIONS: tuple[UtilityAction, ...] = (
    UtilityAction("reboot", "◑", "ปิด-เปิดเครื่องปกติ", "แก้ปัญหาค้างด้วยการรีสตาร์ท"),
    UtilityAction("reboot_recovery", "⚑", "เข้าโหมด Recovery", "ลบข้อมูล / ติดตั้งอัปเดตระบบ"),
    UtilityAction("reboot_bootloader", "⚐", "เข้าโหมด Bootloader", "ต้องกดปุ่มเปิดเครื่องค้างไว้"),
)

MAINTENANCE_ACTIONS: tuple[UtilityAction, ...] = (
    UtilityAction("kill_server", "■", "ปิดเซิร์ฟเวอร์ ADB", "แก้ปัญหา adb ค้างหรือพอร์ตชน"),
    UtilityAction("restart_server", "◉", "เปิดเซิร์ฟเวอร์ ADB ใหม่", "เริ่ม adb ใหม่จากศูนย์"),
)


class _Tile(ClickableFrame):
    """A single utility button drawn as a card."""

    def __init__(self, action: UtilityAction, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.action = action
        self.setObjectName("Tile")

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 13, 14, 13)
        row.setSpacing(12)

        glyph = QLabel(action.glyph)
        glyph.setFixedSize(34, 34)
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tokens = styles.current()
        tone = tokens["danger_soft"] if action.danger else tokens["accent_soft"]
        ink = tokens["danger"] if action.danger else tokens["accent"]
        glyph.setStyleSheet(
            f"background-color: {tone}; color: {ink};"
            "border-radius: 9px; font-size: 15px;"
        )
        row.addWidget(glyph)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(1)
        title = QLabel(action.title)
        title.setObjectName("FieldValue")
        text_box.addWidget(title)
        caption = QLabel(action.caption)
        caption.setObjectName("Faint")
        caption.setWordWrap(True)
        text_box.addWidget(caption)
        row.addLayout(text_box, 1)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        tokens = styles.current()
        self.setStyleSheet(
            f"QFrame#Tile{{background-color: {tokens['surface']};"
            f"border: 1px solid {tokens['accent']}; border-radius: 14px;}}"
        )
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.setStyleSheet("")
        super().leaveEvent(event)

    def refresh_palette(self) -> None:
        """Repaint the glyph badge using the active palette."""
        tokens = styles.current()
        tone = tokens["danger_soft"] if self.action.danger else tokens["accent_soft"]
        ink = tokens["danger"] if self.action.danger else tokens["accent"]
        for child in self.findChildren(QLabel):
            if child.text() == self.action.glyph:
                child.setStyleSheet(
                    f"background-color: {tone}; color: {ink};"
                    "border-radius: 9px; font-size: 15px;"
                )
                break


class ToolsPage(QWidget):
    """Utility tiles and application maintenance."""

    # -- outbound ----------------------------------------------------------
    utility_requested = Signal(str, str)        # action key, label
    reboot_requested = Signal(str, str)         # target, label
    check_update_requested = Signal()
    reload_database_requested = Signal()
    clear_icon_cache_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QWidget()
        body.setObjectName("ScrollBody")
        column = QVBoxLayout(body)
        column.setContentsMargins(24, 20, 24, 20)
        column.setSpacing(14)
        root.addWidget(body)

        self._build_title(column)
        self._build_tile_group(column, "ซ่อมแซมและตรวจสอบ", REPAIR_ACTIONS)
        self._build_tile_group(column, "ควบคุมการเปิด-ปิดเครื่อง", REBOOT_ACTIONS)
        self._build_tile_group(column, "บำรุงรักษา ADB", MAINTENANCE_ACTIONS)
        self._build_update_panel(column)
        column.addStretch(1)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_title(self, column: QVBoxLayout) -> None:
        header = SectionHeader("เครื่องมือ", "คำสั่งที่ใช้บ่อยและการดูแลระบบ")
        column.addWidget(header)
        self.page_header = header

    def _build_tile_group(self, column: QVBoxLayout, title: str, actions: tuple[UtilityAction, ...]) -> None:
        header = SectionHeader(title, f"{len(actions)} คำสั่ง")
        column.addWidget(header)

        card = Card()
        grid = QGridLayout(card)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        for index, action in enumerate(actions):
            tile = _Tile(action)
            tile.clicked.connect(lambda a=action: self._dispatch(a))
            grid.addWidget(tile, index // 3, index % 3)
        for column_index in range(3):
            grid.setColumnStretch(column_index, 1)

        column.addWidget(card)

    def _build_update_panel(self, column: QVBoxLayout) -> None:
        header = SectionHeader("โปรแกรมและฐานข้อมูล", "เวอร์ชันและการอัปเดต")
        column.addWidget(header)

        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 15, 16, 16)
        layout.setSpacing(12)

        # -- version row
        version_row = HBox(spacing=14)
        stamp = VBox(spacing=1)
        self.version_label = QLabel(config.CURRENT_VERSION)
        self.version_label.setObjectName("PageTitle")
        stamp.add(self.version_label)
        build = QLabel(config.APP_TITLE)
        build.setObjectName("Faint")
        stamp.add(build)
        version_row.add(stamp, 1)

        self.btn_check = make_button(
            "ตรวจอัปเดต",
            kind="primary",
            small=True,
            tooltip="เปรียบเทียบเวอร์ชันกับเซิร์ฟเวอร์",
            on_click=self.check_update_requested.emit,
        )
        version_row.addWidget(self.btn_check)
        layout.addWidget(version_row)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # -- database row
        db_row = HBox(spacing=10)
        self.db_label = QLabel("ยังไม่ได้ตรวจสอบฐานข้อมูล")
        self.db_label.setObjectName("Muted")
        self.db_label.setWordWrap(True)
        db_row.add(self.db_label, 1)

        self.btn_reload_db = make_button(
            "↻ โหลดใหม่",
            small=True,
            tooltip="ดึงรายการต้องระวังจาก GitHub อีกครั้ง",
            on_click=self.reload_database_requested.emit,
        )
        self.btn_edit_db = make_button(
            "⛨ จัดการฐานข้อมูล",
            small=True,
            tooltip="เพิ่ม แก้ไข หรือลบรายการต้องระวัง",
            on_click=lambda: self.utility_requested.emit("open_db", "จัดการฐานข้อมูล"),
        )
        self.btn_clear_icons = make_button(
            "◍ ล้างแคชไอคอน",
            small=True,
            tooltip="ลบไฟล์แคชที่ดาวน์โหลดไว้ในเครื่อง",
            on_click=self.clear_icon_cache_requested.emit,
        )
        for button in (self.btn_reload_db, self.btn_edit_db, self.btn_clear_icons):
            db_row.addWidget(button)
        layout.addWidget(db_row)

        self.cache_label = QLabel("")
        self.cache_label.setObjectName("Faint")
        layout.addWidget(self.cache_label)

        column.addWidget(card)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _dispatch(self, action: UtilityAction) -> None:
        if action.key.startswith("reboot"):
            target = action.key.replace("reboot_", "").replace("reboot", "")
            self.reboot_requested.emit(target, action.title)
            return
        self.utility_requested.emit(action.key, action.title)

    def set_database_status(self, count: int, origin: str) -> None:
        origin_label = {
            "remote": "จาก GitHub",
            "local": "จากไฟล์ในเครื่อง (ออฟไลน์)",
            "empty": "ไม่มีข้อมูล",
        }.get(origin, origin)
        tone = "success" if origin == "remote" else "warning" if origin == "local" else "danger"
        self.db_label.setText(
            f"ฐานข้อมูล: {count} รายการ · {origin_label}"
        )
        self.db_label.setStyleSheet(
            f"color: {styles.current()[tone]}; font-size: 12.5px; font-weight: 600;"
        )

    def set_cache_size(self, megabytes: float) -> None:
        self.cache_label.setText(f"แคชไอคอนในเครื่อง: {megabytes:.2f} MB")

    def set_busy(self, busy: bool) -> None:
        self.btn_check.setEnabled(not busy)
        self.btn_reload_db.setEnabled(not busy)

    def show_progress(self, visible: bool) -> None:
        self.progress.setVisible(visible)

    def refresh_theme(self) -> None:
        """Re-apply palette colours after a theme switch."""
        for child in self.findChildren(_Tile):
            child.refresh_palette()