"""
ui.pages_scan
=============

The scan page — the screen a technician actually lives on.

Layout (top to bottom)
----------------------
1. **Device strip** — four stat chips: model, Android version, battery, storage.
2. **Toolbar** — search box, two segmented filters (risk, source), rescan button.
3. **App table** — checkbox, icon, name/package, type, risk pill, source badge.
4. **Selection bar** — appears once something is ticked, with the bulk actions.
5. **Detail panel** — permissions and metadata for the focused row.
6. **Log dock** — the running commentary, shared with the other pages.

The table keeps every scanned entry in memory and filters the rows in place, so
changing a filter never re-queries the device.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.app_model import AppEntry, classify_permission, sort_key
from core.device_monitor import ThreatSignal

from . import styles
from .icons import IconStore
from .kit import HeaderDelegate, RowDelegate
from .widgets import (
    Card,
    EmptyState,
    HBox,
    IconLabel,
    PermissionChip,
    RiskPill,
    SearchField,
    SectionHeader,
    SegmentedControl,
    SourceBadge,
    StatTile,
    VBox,
    elide_middle,
    icon_role,
    make_button,
    meta_role,
    pill_role,
)

#: Column layout. The names double as the spec handed to :class:`RowDelegate`,
#: so the header, the row painter and the model can never drift apart.
COL_CHECK = 0
COL_ICON = 1
COL_NAME = 2
COL_TYPE = 3
COL_RISK = 4
COL_SOURCE = 5
COL_ACTIONS = 6

COLUMN_KINDS = ["check", "icon", "name", "text", "risk", "source", "actions"]
_COLUMN_HEADERS = ["", "", "แอปพลิเคชัน", "ประเภท", "ความเสี่ยง", "แหล่งที่มา", ""]
_COLUMN_WIDTHS = [46, 52, 260, 108, 108, 118, 96]

RISK_FILTERS: Sequence[tuple[str, str]] = (
    ("all", "ทั้งหมด"),
    ("threat", "ต้องระวัง"),
    ("critical", "วิกฤต"),
    ("side", "Sideload"),
    ("clean", "ปลอดภัย"),
)

SOURCE_FILTERS: Sequence[tuple[str, str]] = (
    ("all", "ทุกแหล่งที่มา"),
    ("play", "Play Store"),
    ("side", "Sideload"),
    ("unknown", "ไม่ทราบ"),
)


class ScanPage(QWidget):
    """Device overview, blacklist table, bulk actions and app detail."""

    # -- outbound ----------------------------------------------------------
    scan_requested = Signal()
    refresh_requested = Signal()
    load_all_requested = Signal()
    prime_icons_requested = Signal(list)          # list[AppEntry]
    uninstall_requested = Signal(list, bool, str)  # packages, force, label
    clear_data_requested = Signal(list, str)       # packages, label
    force_stop_requested = Signal(list)
    restrict_requested = Signal(list, str)         # packages, bucket
    toggle_safe_requested = Signal(str)
    toggle_danger_requested = Signal(str)
    detail_requested = Signal(str)                 # package
    risk_filter_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[AppEntry] = []
        self._visible: list[AppEntry] = []
        self._icons = IconStore()
        self._device_connected = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll_body = QWidget()
        scroll_body.setObjectName("ScrollBody")
        self._body = QVBoxLayout(scroll_body)
        self._body.setContentsMargins(24, 20, 24, 20)
        self._body.setSpacing(14)

        root.addWidget(scroll_body)

        self._build_device_strip()
        self._build_toolbar()
        self._build_table()
        self._build_selection_bar()
        self._build_detail_panel()
        self._body.addStretch(1)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_device_strip(self) -> None:
        row = HBox(spacing=10)
        self.chip_device = StatTile("อุปกรณ์", "ยังไม่ได้เชื่อมต่อ", "phone")
        self.chip_android = StatTile("ระบบปฏิบัติการ", "—", "shield")
        self.chip_battery = StatTile("แบตเตอรี่", "—", "battery")
        self.chip_storage = StatTile("พื้นที่ว่าง", "—", "storage")
        for chip in (self.chip_device, self.chip_android, self.chip_battery, self.chip_storage):
            row.add(chip, 1)
        self._body.addWidget(row)

    def _build_toolbar(self) -> None:
        row = HBox(spacing=10)

        self.search = SearchField("ค้นหาชื่อแอป หรือ package name…")
        self.search.on_changed(lambda _text: self._apply_filters())
        row.add(self.search, 1)

        self.risk_filter = SegmentedControl(RISK_FILTERS, initial="all")
        self.risk_filter.valueChanged.connect(lambda _value: self._apply_filters())
        row.addWidget(self.risk_filter)

        self.source_filter = SegmentedControl(SOURCE_FILTERS, initial="all")
        self.source_filter.valueChanged.connect(lambda _value: self._apply_filters())
        row.addWidget(self.source_filter)

        self.btn_scan = make_button(
            "◎ สแกนใหม่",
            kind="primary",
            tooltip="อ่านรายชื่อแอปจากมือถือแล้วเทียบกับฐานข้อมูล",
            on_click=self._emit_scan,
        )
        row.addWidget(self.btn_scan)

        self.btn_all = make_button(
            "ทั้งหมด",
            kind="ghost",
            tooltip="แสดงแอปทุกรายการบนเครื่อง ไม่ใช่เฉพาะที่ตรงฐานข้อมูล",
            on_click=self._emit_load_all,
        )
        row.addWidget(self.btn_all)

        self._body.addWidget(row)

    def _build_table(self) -> None:
        header = SectionHeader("รายการแอป", "ยังไม่ได้สแกน")
        header.actions.addWidget(
            make_button("รีเฟรช", kind="ghost", small=True, tooltip="ตรวจการเชื่อมต่ออีกครั้ง", on_click=self.refresh_requested.emit)
        )
        self._body.addWidget(header)
        self.table_header = header

        self.table = QTableWidget(0, len(_COLUMN_HEADERS))
        self.table.setHorizontalHeaderLabels(_COLUMN_HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSortingEnabled(False)
        self.table.setWordWrap(False)
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        header_view = self.table.horizontalHeader()
        header_view.setHighlightSections(False)
        for column, width in enumerate(_COLUMN_WIDTHS):
            self.table.setColumnWidth(column, width)
        header_view.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(COL_ACTIONS, QHeaderView.ResizeMode.Fixed)

        self.table.setItemDelegate(RowDelegate(COLUMN_KINDS, self.table, row_height=52))
        self.table.itemDelegate()._attach_hover(self.table)
        self.table.horizontalHeader().setItemDelegate(
            HeaderDelegate(COLUMN_KINDS, _COLUMN_HEADERS, self.table)
        )

        self.table.itemSelectionChanged.connect(self._on_row_focus)
        self.table.itemChanged.connect(self._on_item_changed)

        self.empty_state = EmptyState(
            "ยังไม่มีข้อมูล",
            "เสียบมือถือแล้วกด «สแกนใหม่» เพื่อเริ่มตรวจหารายการต้องระวัง",
            glyph="scan",
        )
        self.empty_state.setVisible(False)

        stack_holder = QWidget()
        stack = QVBoxLayout(stack_holder)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)
        stack.addWidget(self.table)
        stack.addWidget(self.empty_state)
        self._body.addWidget(stack_holder)

    def _build_selection_bar(self) -> None:
        bar = Card(kind="alt")
        row = HBox(spacing=10)

        self.sel_label = QLabel("เลือกแล้ว 0 แอป")
        self.sel_label.setObjectName("FieldValue")
        row.addWidget(self.sel_label)

        row.add_stretch(1)

        self.btn_stop = make_button(
            "■ หยุด",
            tooltip="สั่งบังคับหยุดแอปที่เลือกทันที (ยังไม่ถอนติดตั้ง)",
            on_click=self._emit_force_stop,
        )
        self.btn_restrict = make_button(
            "◐ จำกัดเบื้องหลัง",
            tooltip="ตั้งโหมดพื้นหลังเป็น rare — ลดการเด้งแต่ยังไม่ลบแอป",
            on_click=self._emit_restrict,
        )
        self.btn_clear = make_button(
            "✦ ล้างข้อมูล",
            tooltip="ล้างข้อมูลแอปที่เลือก (ข้อมูลจะหายถาวร)",
            on_click=self._emit_clear,
        )
        self.btn_uninstall = make_button(
            "⌫ ถอนการติดตั้ง",
            kind="danger",
            tooltip="ถอนการติดตั้งแอปที่เลือก",
            on_click=self._emit_uninstall,
        )
        for button in (self.btn_stop, self.btn_restrict, self.btn_clear, self.btn_uninstall):
            row.addWidget(button)

        layout = QVBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(row)
        self._body.addWidget(bar)
        self.selection_bar = bar

        self._sync_selection_ui()

    def _build_detail_panel(self) -> None:
        self.detail_card = Card(kind="alt")
        column = QVBoxLayout(self.detail_card)
        column.setContentsMargins(14, 13, 14, 14)
        column.setSpacing(11)

        head = HBox(spacing=11)
        self.detail_icon = IconLabel(38)
        head.addWidget(self.detail_icon)

        title_box = VBox(spacing=1)
        self.detail_name = QLabel("เลือกแอปเพื่อดูรายละเอียด")
        self.detail_name.setObjectName("FieldValue")
        title_box.add(self.detail_name)
        self.detail_package = QLabel("")
        self.detail_package.setObjectName("FieldValueMono")
        title_box.add(self.detail_package)
        head.add(title_box, 1)

        self.detail_risk = RiskPill("")
        head.addWidget(self.detail_risk)
        column.addWidget(head)

        stats = HBox(spacing=18)
        self.detail_size = StatColumn("ขนาดแอป", "—")
        self.detail_installed = StatColumn("ติดตั้งเมื่อ", "—")
        self.detail_updated = StatColumn("อัปเดตล่าสุด", "—")
        self.detail_installer = StatColumn("ผู้ติดตั้ง", "—")
        for holder in (self.detail_size, self.detail_installed, self.detail_updated, self.detail_installer):
            stats.add(holder, 1)
        column.addWidget(stats)

        self.detail_perm_caption = QLabel("สิทธิ์ที่ขอ")
        self.detail_perm_caption.setObjectName("FieldLabel")
        column.addWidget(self.detail_perm_caption)

        self.detail_permissions = HBox(spacing=5)
        self.detail_permissions.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        column.addWidget(self.detail_permissions)

        self._permission_chip_cls = PermissionChip

        actions = HBox(spacing=8)
        self.btn_detail_details = make_button(
            "โหลดสิทธิ์เต็ม",
            small=True,
            tooltip="ดึงรายการสิทธิ์ทั้งหมดจากเครื่อง (ใช้เวลาเล็กน้อย)",
            on_click=self._emit_load_details,
        )
        self.btn_mark_safe = make_button(
            "✓ เก็บไว้",
            small=True,
            tooltip="ทำเครื่องหมายว่าแอปนี้ปลอดภัย ไม่ต้องแจ้งเตือนอีก",
            on_click=self._emit_mark_safe,
        )
        self.btn_mark_danger = make_button(
            "⚠ อันตราย",
            small=True,
            tooltip="ทำเครื่องหมายว่าแอปนี้อันตราย แล้วบันทึกไว้ในเครื่องนี้",
            on_click=self._emit_mark_danger,
        )
        for button in (self.btn_detail_details, self.btn_mark_safe, self.btn_mark_danger):
            actions.add(button)
        actions.add_stretch(1)
        column.addWidget(actions)

        self._body.addWidget(self.detail_card)
        self._set_detail(None)

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def set_device_state(self, connected: bool, title: str = "") -> None:
        self._device_connected = connected
        if connected and title:
            self.chip_device.set_value(title)
        elif not connected:
            self.chip_device.set_value("ยังไม่ได้เชื่อมต่อ")
            self.chip_android.set_value("—")
            self.chip_battery.set_value("—")
            self.chip_battery.set_progress(None)
            self.chip_storage.set_value("—")
            self.chip_storage.set_progress(None)

    def set_device_info(self, info: dict) -> None:
        title = str(info.get("title", "") or "ไม่ทราบรุ่น")
        self.chip_device.set_value(title)

        release = str(info.get("android_release", "") or "—")
        sdk = str(info.get("sdk_int", "") or "")
        self.chip_android.set_value(f"Android {release}" if release != "—" else "—")

        level = info.get("battery_level")
        if isinstance(level, int):
            state = str(info.get("battery_state", "") or "")
            self.chip_battery.set_value(f"{level}%  {state}".strip())
            self.chip_battery.set_progress(level)
        else:
            self.chip_battery.set_value("—")
            self.chip_battery.set_progress(None)

        free = info.get("storage_free_gb")
        total = info.get("storage_total_gb")
        if isinstance(free, (int, float)):
            label = f"{free:.1f} GB"
            if isinstance(total, (int, float)) and total > 0:
                label = f"{free:.1f} / {total:.0f} GB"
                self.chip_storage.set_progress(int(free / total * 100))
            else:
                self.chip_storage.set_progress(None)
            self.chip_storage.set_value(label)

    def set_entries(self, entries: Iterable[AppEntry], *, scanned_total: int = 0) -> None:
        """Replace the table contents."""
        self._entries = sorted(list(entries), key=sort_key)
        self._apply_filters(scanned_total=scanned_total)
        if self._entries:
            self.prime_icons_requested.emit(self._entries[:24])

    def get_entries(self) -> list[AppEntry]:
        return list(self._entries)

    def clear(self) -> None:
        self._entries = []
        self._visible = []
        self.table.setRowCount(0)
        self.empty_state.setVisible(True)
        self.table_header.set_caption("ยังไม่ได้สแกน")
        self._sync_selection_ui()
        self._set_detail(None)

    def remove_packages(self, packages: Iterable[str]) -> None:
        gone = {str(p) for p in packages if p}
        self._entries = [entry for entry in self._entries if entry.package not in gone]
        self._apply_filters()

    def refresh_icons(self) -> None:
        """Re-render icons after the worker has fetched more of them."""
        for row, entry in enumerate(self._visible):
            if row >= self.table.rowCount():
                break
            item = self.table.item(row, COL_ICON)
            if item is not None:
                item.setData(icon_role(), self._icons.pixmap(entry.package, entry.apk_path, 34))
                self.table.viewport().update()

    def repaint_pills(self) -> None:
        """Re-apply palette colours after a theme switch."""
        for chip in (self.chip_device, self.chip_android, self.chip_battery, self.chip_storage):
            chip.repaint()
        self._set_detail(getattr(self, "_current", None))

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _matches(self, entry: AppEntry, needle: str, risk: str, source: str) -> bool:
        if needle:
            haystack = f"{entry.display_name} {entry.package}".lower()
            if needle not in haystack:
                return False

        if risk == "threat":
            if not entry.is_flagged_danger and not entry.in_blacklist:
                return False
        elif risk == "critical":
            if str(entry.effective_risk).title() not in {"Critical", "High"}:
                return False
        elif risk == "side":
            if not entry.source.is_suspicious:
                return False
        elif risk == "clean":
            if entry.is_flagged_danger or entry.in_blacklist:
                return False

        if source == "play":
            if entry.source.kind != "play":
                return False
        elif source == "side":
            if entry.source.kind != "side":
                return False
        elif source == "unknown":
            if entry.source.kind not in {"unknown", "side"}:
                return False

        return True

    def _apply_filters(self, scanned_total: int = 0) -> None:
        needle = self.search.text().strip().lower()
        risk = self.risk_filter.value()
        source = self.source_filter.value()

        self._visible = [
            entry for entry in self._entries if self._matches(entry, needle, risk, source)
        ]

        checked = self.checked_packages()

        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.setRowCount(len(self._visible))

        for row, entry in enumerate(self._visible):
            check = QTableWidgetItem("")
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check.setData(pill_role(), "")
            check.setData(meta_role(), {"package": entry.package})
            check.setToolTip(entry.package)
            if entry.package in checked:
                check.setCheckState(Qt.CheckState.Checked)
            else:
                check.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(row, COL_CHECK, check)

            icon = QTableWidgetItem("")
            icon.setData(icon_role(), self._icons.pixmap(entry.package, entry.apk_path, 34))
            icon.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, COL_ICON, icon)

            name = QTableWidgetItem(entry.display_name)
            name.setFlags(Qt.ItemFlag.ItemIsEnabled)
            name.setData(meta_role(), {"subtitle": entry.package})
            name.setToolTip(f"{entry.display_name}\n{entry.package}\n{entry.describe_risk()}")
            self.table.setItem(row, COL_NAME, name)

            type_item = QTableWidgetItem(entry.threat_type or ("ระบบ" if entry.is_system else "—"))
            type_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            type_item.setData(meta_role(), {"colour": styles.current()["text_muted"]})
            self.table.setItem(row, COL_TYPE, type_item)

            risk_item = QTableWidgetItem("")
            risk_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            risk_item.setData(pill_role(), entry.effective_risk)
            risk_item.setData(meta_role(), {"package": entry.package})
            self.table.setItem(row, COL_RISK, risk_item)

            source_item = QTableWidgetItem("")
            source_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            source_item.setData(pill_role(), entry.source.kind)
            source_item.setData(meta_role(), {"label": entry.source.label})
            self.table.setItem(row, COL_SOURCE, source_item)

            actions = QTableWidgetItem("")
            actions.setFlags(Qt.ItemFlag.ItemIsEnabled)
            actions.setData(meta_role(), {
                "package": entry.package,
                "glyphs": [("trash", "danger"), ("stop", "warn")],
            })
            self.table.setItem(row, COL_ACTIONS, actions)

        self.table.blockSignals(False)

        has_rows = bool(self._visible)
        self.table.setVisible(has_rows)
        self.empty_state.setVisible(not has_rows)
        if not has_rows and self._entries:
            self.table_header.set_caption(
                f"ไม่พบแอปที่ตรงตัวกรอง — กำลังแสดง {len(self._entries)} รายการทั้งหมด"
            )
        elif has_rows:
            caption = f"{len(self._visible)} รายการ"
            if scanned_total:
                caption += f" · จากทั้งหมด {scanned_total} แอปบนเครื่อง"
            self.table_header.set_caption(caption)

        self._sync_selection_ui()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != COL_CHECK:
            return
        self._sync_selection_ui()

    def _on_row_focus(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        position = rows[0].row()
        if 0 <= position < len(self._visible):
            self._set_detail(self._visible[position])

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def checked_packages(self) -> list[str]:
        packages: list[str] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_CHECK)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                name = item.data(meta_role()) or {}
                packages.append(str(name.get("package", "")))
        return [p for p in packages if p]

    def checked_entries(self) -> list[AppEntry]:
        chosen = set(self.checked_packages())
        return [entry for entry in self._visible if entry.package in chosen]

    def _sync_selection_ui(self) -> None:
        count = len(self.checked_packages())
        self.sel_label.setText(f"เลือกแล้ว {count} แอป")
        enabled = count > 0 and self._device_connected
        for button in (self.btn_uninstall, self.btn_clear, self.btn_stop, self.btn_restrict):
            button.setEnabled(enabled)
        tokens = styles.current()
        if count:
            self.sel_label.setStyleSheet(
                f"color: {tokens['accent']}; font-size: 13.5px; font-weight: 600;"
            )
        else:
            self.sel_label.setStyleSheet(
                f"color: {tokens['text_muted']}; font-size: 13.5px; font-weight: 600;"
            )

    def set_busy(self, busy: bool) -> None:
        self.btn_scan.setEnabled(not busy and self._device_connected)
        self.btn_all.setEnabled(not busy and self._device_connected)
        for button in (self.btn_uninstall, self.btn_clear, self.btn_stop, self.btn_restrict):
            button.setEnabled(not busy and bool(self.checked_packages()))

    # ------------------------------------------------------------------
    # Detail panel
    # ------------------------------------------------------------------

    def _set_detail(self, entry: AppEntry | None) -> None:
        self._current = entry
        if entry is None:
            self.detail_name.setText("เลือกแอปเพื่อดูรายละเอียด")
            self.detail_package.setText("")
            self.detail_risk.set_risk("")
            self.detail_icon.set_package("")
            for holder in (self.detail_size, self.detail_installed, self.detail_updated, self.detail_installer):
                holder.set_value("—")
            self._clear_permissions()
            self.btn_mark_safe.setEnabled(False)
            self.btn_mark_danger.setEnabled(False)
            self.btn_detail_details.setEnabled(False)
            return

        self.detail_name.setText(entry.display_name)
        self.detail_package.setText(entry.package)
        self.detail_risk.set_risk(entry.effective_risk)
        self.detail_icon.set_package(entry.package, self._icons)

        self.detail_size.set_value(entry.size_label)
        self.detail_installed.set_value(entry.install_date_label)
        self.detail_updated.set_value(entry.update_date_label)
        installer = entry.installer or "ไม่ทราบ"
        self.detail_installer.set_value(installer if installer != "null" else "ไม่มี (Sideload)")

        self._render_permissions(entry.permissions)
        self.btn_mark_safe.setEnabled(True)
        self.btn_mark_danger.setEnabled(True)
        self.btn_detail_details.setEnabled(bool(entry.apk_path))

    def _clear_permissions(self) -> None:
        layout = self.detail_permissions._layout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render_permissions(self, permissions: Sequence[str]) -> None:
        self._clear_permissions()
        if not permissions:
            note = QLabel("ยังไม่ได้ดึงข้อมูลสิทธิ์ — กด «โหลดสิทธิ์เต็ม» เพื่ออ่านจากเครื่อง")
            note.setObjectName("Faint")
            self.detail_permissions.add(note)
            self.detail_perm_caption.setText("สิทธิ์ที่ขอ")
            return

        summary = self._summary(permissions)
        self.detail_perm_caption.setText(
            f"สิทธิ์ที่ขอ · {summary['total']} รายการ"
            + (f" · อันตราย {summary['critical']}" if summary["critical"] else "")
        )
        for raw in permissions:
            chip = self._permission_chip_cls(classify_permission(raw))
            self.detail_permissions.add(chip)
        self.detail_permissions.add_stretch(1)

    @staticmethod
    def _summary(permissions: Sequence[str]) -> dict:
        from core.app_model import summarise_permissions

        return summarise_permissions(permissions)

    def apply_details(self, entry: AppEntry) -> None:
        """Merge a freshly loaded detail entry into the table."""
        for index, existing in enumerate(self._entries):
            if existing.package == entry.package:
                existing.permissions = entry.permissions
                existing.version_name = entry.version_name
                existing.apk_size_bytes = entry.apk_size_bytes
                existing.first_install_ms = entry.first_install_ms
                existing.last_update_ms = entry.last_update_ms
                existing.installer = entry.installer or existing.installer
                existing.source = entry.source or existing.source
                if entry.app_name and not existing.app_name:
                    existing.app_name = entry.app_name
                self._apply_filters()
                self._set_detail(self._entries[index])
                return

    def set_threat_signals(self, signals: Sequence[ThreatSignal]) -> None:
        """Surface live-monitor warnings in the toolbar caption."""
        worst = min((s.level for s in signals), default="OK")
        if worst == "ERROR":
            self.table_header.set_caption("⚠ ตรวจพบแอปกำลังวาดทับหน้าจอ — ดูหน้า «ตรวจจับแอปที่ทำงาน»")
        elif worst == "WARN":
            self.table_header.set_caption("⚠ มีแอปต้องระวังกำลังทำงานอยู่")

    # ------------------------------------------------------------------
    # Emitters
    # ------------------------------------------------------------------

    def _emit_scan(self) -> None:
        self.risk_filter.set_value("all")
        self.source_filter.set_value("all")
        self.search.setText("")
        self.scan_requested.emit()

    def _emit_load_all(self) -> None:
        self.load_all_requested.emit()

    def _emit_uninstall(self) -> None:
        packages = self.checked_packages()
        if not packages:
            return
        self.uninstall_requested.emit(packages, False, "ถอนการติดตั้ง")

    def _emit_clear(self) -> None:
        packages = self.checked_packages()
        if not packages:
            return
        self.clear_data_requested.emit(packages, "ล้างข้อมูล")

    def _emit_force_stop(self) -> None:
        packages = self.checked_packages()
        if packages:
            self.force_stop_requested.emit(packages)

    def _emit_restrict(self) -> None:
        packages = self.checked_packages()
        if packages:
            self.restrict_requested.emit(packages, "rare")

    def _emit_mark_safe(self) -> None:
        entry = getattr(self, "_current", None)
        if entry is not None:
            self.toggle_safe_requested.emit(entry.package)

    def _emit_mark_danger(self) -> None:
        entry = getattr(self, "_current", None)
        if entry is not None:
            self.toggle_danger_requested.emit(entry.package)

    def _emit_load_details(self) -> None:
        entry = getattr(self, "_current", None)
        if entry is not None:
            self.detail_requested.emit(entry.package)


class StatColumn(VBox):
    """A tiny two-line statistic used across the detail panel."""

    def __init__(self, caption: str, value: str = "—", parent: QWidget | None = None) -> None:
        super().__init__(parent, spacing=1)
        self.caption_label = QLabel(caption)
        self.caption_label.setObjectName("FieldLabel")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("FieldValue")
        self.add(self.caption_label)
        self.add(self.value_label)

    def set_value(self, text: str, colour: str | None = None) -> None:
        self.value_label.setText(text)
        if colour:
            tokens = styles.current()
            self.value_label.setStyleSheet(
                f"color: {colour}; font-size: 13.5px; font-weight: 600;"
            )
        else:
            self.value_label.setStyleSheet("")