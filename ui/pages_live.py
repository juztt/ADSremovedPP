"""
ui.pages_live
=============

Live inspection page — "what is this phone doing right *now*?".

Why this page exists
--------------------
A package blacklist is a static verdict: it knows an app is bad but not whether
the app is *currently* covering the screen with an advert. That is exactly the
question a customer asks when they say "มันเด้งขึ้นมาเอง" (it pops up on its
own).

The page polls four independent signals:

* **Foreground activity** — which app owns the visible screen right now
* **Overlay windows** — which apps hold a window *above* the current one
* **Background processes** — which packages have a live process
* **Services** — which packages own a bound ``ServiceRecord``

Each poll runs on the worker thread, so a two-second refresh never freezes the
interface. A manual auto-refresh toggle is provided because a repair shop
technician may want the screen to stay still while explaining something to a
customer.
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.app_model import LiveSnapshot
from core.device_monitor import ThreatSignal, is_protected

from . import styles
from .widgets import (
    Card,
    EmptyState,
    HBox,
    SectionHeader,
    VBox,
    make_button,
)

#: Polling cadence. Two seconds is fast enough to catch a popup and slow enough
#: that five ``dumpsys`` calls per cycle stay invisible on a modern CPU.
REFRESH_MS = 2000

_LEVEL_COLORS = {"OK": "success", "WARN": "warning", "ERROR": "danger"}


class LivePage(QWidget):
    """Foreground / overlay / process monitor with quick actions."""

    # -- outbound ----------------------------------------------------------
    refresh_requested = Signal()
    force_stop_requested = Signal(list)          # packages
    open_settings_requested = Signal(str, str)   # action, label
    restrict_requested = Signal(list, str)      # packages, bucket
    detail_requested = Signal(str)              # package

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._snapshot: LiveSnapshot | None = None
        self._signals: list[ThreatSignal] = []
        self._checked: set[str] = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QWidget()
        body.setObjectName("ScrollBody")
        column = QVBoxLayout(body)
        column.setContentsMargins(24, 20, 24, 20)
        column.setSpacing(14)
        root.addWidget(body)

        self._build_header(column)
        self._build_status_grid(column)
        self._build_actions(column)
        self._build_overlay_table(column)
        self._build_signals(column)
        column.addStretch(1)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._on_tick)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_header(self, column: QVBoxLayout) -> None:
        header = SectionHeader("ตรวจจับแอปที่กำลังทำงาน", "กำลังรอข้อมูล")
        column.addWidget(header)
        self.page_header = header

        self.btn_auto = make_button(
            "◉ ตรวจอัตโนมัติ",
            kind="primary",
            tooltip="เปิดการตรวจอัตโนมัติทุก 2 วินาที",
            on_click=self._toggle_auto,
        )
        self.btn_refresh = make_button(
            "↻ ตรวจเดี๋ยวนี้",
            tooltip="ตรวจสถานะทันทีหนึ่งครั้ง",
            on_click=self.refresh_requested.emit,
        )
        self.btn_kill_all = make_button(
            "■ หยุดที่กำลังทับ",
            kind="danger",
            tooltip="บังคับหยุดทุกแอปที่กำลังวาดทับหน้าจอ",
            on_click=self._kill_overlays,
        )
        for button in (self.btn_auto, self.btn_refresh, self.btn_kill_all):
            header.actions.addWidget(button)

    def _build_status_grid(self, column: QVBoxLayout) -> None:
        card = Card()
        grid = HBox(spacing=0)

        self.box_foreground = _SignalBox("◉", "แอปหน้าจอหลัก", "—", "neutral")
        self.box_overlay = _SignalBox("⚠", "กำลังวาดทับหน้าจอ", "— ไม่มี —", "safe")
        self.box_killable = _SignalBox("■", "พร้อมสั่งหยุด", "—", "safe")
        self.box_processes = _SignalBox("◈", "โปรเซสที่ทำงาน", "—", "neutral")

        for index, box in enumerate((self.box_foreground, self.box_overlay, self.box_killable, self.box_processes)):
            if index:
                from .widgets import Hairline

                grid.addWidget(Hairline(vertical=True))
            grid.add(box, 1)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(grid)
        column.addWidget(card)

    def _build_actions(self, column: QVBoxLayout) -> None:
        row = HBox(spacing=8)
        self.btn_settings = make_button(
            "⚙ เปิดหน้าตั้งค่าบนเครื่อง",
            small=True,
            tooltip="เปิด Settings › Apps บนมือถือ",
            on_click=lambda: self.open_settings_requested.emit(
                "APPLICATION_SETTINGS", "หน้าตั้งค่าแอป"
            ),
        )
        self.btn_usage = make_button(
            "◐ แสดงสิทธิ์การทำงาน",
            small=True,
            tooltip="เปิดหน้า App battery / Battery usage",
            on_click=lambda: self.open_settings_requested.emit(
                "IGNORE_BATTERY_OPTIMIZATION_SETTINGS", "สิทธิ์พื้นหลัง"
            ),
        )
        self.btn_restrict = make_button(
            "◐ จำกัดแอปที่เลือก",
            small=True,
            tooltip="ตั้งโหมดพื้นหลังเป็น rare เพื่อลดการเด้งโฆษณา",
            on_click=self._restrict_selected,
        )
        row.addWidget(self.btn_settings)
        row.addWidget(self.btn_usage)
        row.add_stretch(1)
        row.addWidget(self.btn_restrict)

        card = Card(kind="alt")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.addWidget(row)
        column.addWidget(card)

    def _build_overlay_table(self, column: QVBoxLayout) -> None:
        header = SectionHeader("แอปที่กำลังวาดทับหน้าจอ", "ไม่มีข้อมูล")
        column.addWidget(header)
        self.overlay_header = header

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "แพ็คเกจ", "ประเภทหน้าต่าง", "สถานะ"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        column.addWidget(self.table)

        self.empty_state = EmptyState(
            "ไม่มีแอปกำลังทับหน้าจอ",
            "ตรวจอัตโนมัติเพื่อดูสถานะต่อเนื่อง",
            glyph="shield",
        )
        column.addWidget(self.empty_state)

    def _build_signals(self, column: QVBoxLayout) -> None:
        header = SectionHeader("ข้อสังเกต", "ผลวิเคราะห์จากสถานะปัจจุบัน")
        column.addWidget(header)
        self.signals_header = header

        self.signals_card = Card(kind="alt")
        layout = QVBoxLayout(self.signals_card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)
        self._signals_column = layout
        column.addWidget(self.signals_card)
        self._render_signals([])

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def apply_snapshot(self, shot: LiveSnapshot, signals: Sequence[ThreatSignal]) -> None:
        """Store a fresh sample and repaint every panel."""
        self._snapshot = shot
        self._signals = list(signals)

        self.page_header.set_caption(f"อัปเดตล่าสุด {shot.timestamp or '—'}")

        foreground = shot.foreground or "—"
        self.box_foreground.set_value(foreground, "neutral")
        self.box_foreground.set_sub(shot.foreground_label or "ไม่ทราบชื่อแอป")

        if shot.has_overlay:
            self.box_overlay.set_value(shot.describe_overlay(), "danger")
            self.box_overlay.set_sub(f"{len(shot.overlays)} หน้าต่างกำลังทับอยู่")
        else:
            self.box_overlay.set_value("— ไม่มี —", "safe")
            self.box_overlay.set_sub("ไม่มีแอปวาดทับหน้าจอ")

        killable = [pkg for pkg in shot.overlay_packages if not is_protected(pkg)]
        if killable:
            self.box_killable.set_value(f"{len(killable)} แอป", "warning")
            self.box_killable.set_sub(", ".join(killable[:2]) + ("…" if len(killable) > 2 else ""))
            self.btn_kill_all.setEnabled(True)
        else:
            self.box_killable.set_value("— ไม่มี —", "safe")
            self.box_killable.set_sub("ไม่มีแอปที่ควรสั่งหยุด")
            self.btn_kill_all.setEnabled(False)

        self.box_processes.set_value(str(shot.process_count()), "neutral")
        self.box_processes.set_sub(f"{len(shot.services)} บริการกำลังทำงาน")

        self._render_overlays(shot)
        self._render_signals(self._signals)

    def _render_overlays(self, shot: LiveSnapshot) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        if not shot.overlays:
            self.table.setVisible(False)
            self.empty_state.setVisible(True)
            self.overlay_header.set_caption("ไม่มีข้อมูล")
            self.table.blockSignals(False)
            return

        self.table.setVisible(True)
        self.empty_state.setVisible(False)
        self.table.setRowCount(len(shot.overlays))

        for row, (package, window_type) in enumerate(shot.overlays):
            check = QTableWidgetItem("")
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(
                Qt.CheckState.Checked if package in self._checked else Qt.CheckState.Unchecked
            )
            check.setToolTip(package)
            self.table.setItem(row, 0, check)

            name = QTableWidgetItem(package)
            self.table.setItem(row, 1, name)

            kind = QTableWidgetItem(window_type.replace("TYPE_", "").replace("_", " ").title())
            self.table.setItem(row, 2, kind)

            if is_protected(package):
                status = QTableWidgetItem("ส่วนประกอบระบบ")
                status.setForeground(QBrush(styles.current()["text_muted"]))
            else:
                status = QTableWidgetItem("สั่งหยุดได้")
                status.setForeground(QBrush(styles.current()["warning"]))
            self.table.setItem(row, 3, status)

        self.table.blockSignals(False)
        self.overlay_header.set_caption(f"{len(shot.overlays)} หน้าต่างกำลังทับอยู่")

    def _render_signals(self, signals: Sequence[ThreatSignal]) -> None:
        layout = self._signals_column
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not signals:
            empty = QLabel("ยังไม่มีผลวิเคราะห์ — กด «ตรวจเดี๋ยวนี้» เพื่อเริ่ม")
            empty.setObjectName("Faint")
            layout.addWidget(empty)
            return

        for signal in signals:
            card = Card(kind="sunken")
            row = HBox(spacing=11)

            glyph = QLabel("✓" if signal.level == "OK" else "⚠" if signal.level == "WARN" else "✕")
            tokens = styles.current()
            colour = tokens.get(_LEVEL_COLORS.get(signal.level, "neutral"), tokens["text_muted"])
            glyph.setFixedSize(28, 28)
            glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            glyph.setStyleSheet(
                f"color: {colour};"
                "font-size: 14px;"
                f"background-color: {tokens['surface_hover']};"
                "border-radius: 8px;"
            )
            row.addWidget(glyph)

            text_box = VBox(spacing=1)
            title = QLabel(signal.title)
            title.setObjectName("FieldValue")
            text_box.add(title)
            detail = QLabel(signal.detail)
            detail.setObjectName("Faint")
            detail.setWordWrap(True)
            text_box.add(detail)
            row.add(text_box, 1)

            if signal.has_action:
                for package in signal.packages[:3]:
                    btn = make_button(
                        "■",
                        kind="icon",
                        tooltip=f"สั่งหยุด {package}",
                        on_click=lambda pkg=package: self._kill([pkg]),
                    )
                    btn.setFixedWidth(32)
                    row.addWidget(btn)

            holder = QVBoxLayout(card)
            holder.setContentsMargins(11, 10, 11, 10)
            holder.addWidget(row)
            layout.addWidget(card)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        package = item.toolTip()
        if not package:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._checked.add(package)
        else:
            self._checked.discard(package)
        self._sync_restrict()

    def _on_row_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.table.item(rows[0].row(), 1)
        if item is not None and item.text():
            self.detail_requested.emit(item.text())

    def _sync_restrict(self) -> None:
        self.btn_restrict.setEnabled(bool(self._checked))

    def checked_packages(self) -> list[str]:
        packages: list[str] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                packages.append(item.toolTip())
        return [p for p in packages if p]

    def reset(self) -> None:
        """Clear everything — used when the device disconnects."""
        self._snapshot = None
        self._signals = []
        self._checked.clear()
        self.table.setRowCount(0)
        self.table.setVisible(False)
        self.empty_state.setVisible(True)
        self.overlay_header.set_caption("ไม่มีข้อมูล")
        self.page_header.set_caption("กำลังรอข้อมูล")
        self.box_foreground.set_value("—", "neutral")
        self.box_foreground.set_sub("ยังไม่ได้เชื่อมต่อ")
        self.box_overlay.set_value("— ไม่มี —", "safe")
        self.box_overlay.set_sub("ยังไม่ได้เชื่อมต่อ")
        self.box_killable.set_value("— ไม่มี —", "safe")
        self.box_killable.set_sub("ยังไม่ได้เชื่อมต่อ")
        self.box_processes.set_value("—", "neutral")
        self.box_processes.set_sub("ยังไม่ได้เชื่อมต่อ")
        self.btn_kill_all.setEnabled(False)
        self._render_signals([])
        self._sync_restrict()

    def set_busy(self, busy: bool) -> None:
        self.btn_refresh.setEnabled(not busy)

    # ------------------------------------------------------------------
    # Auto refresh
    # ------------------------------------------------------------------

    @property
    def auto_refresh(self) -> bool:
        return self._timer.isActive()

    def start_auto(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
            self.btn_auto.setText("◉ กำลังตรวจอัตโนมัติ")
        self.refresh_requested.emit()

    def stop_auto(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self.btn_auto.setText("◉ ตรวจอัตโนมัติ")

    def _toggle_auto(self) -> None:
        if self._timer.isActive():
            self.stop_auto()
        else:
            self.start_auto()

    def _on_tick(self) -> None:
        self.refresh_requested.emit()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _kill_overlays(self) -> None:
        if self._snapshot is None:
            return
        targets = [pkg for pkg in self._snapshot.overlay_packages if not is_protected(pkg)]
        if targets:
            self._kill(targets)

    def _kill(self, packages: Sequence[str]) -> None:
        clean = [p for p in packages if p and not is_protected(p)]
        if clean:
            self.force_stop_requested.emit(list(clean))

    def _restrict_selected(self) -> None:
        packages = self.checked_packages()
        if packages:
            self.restrict_requested.emit(packages, "rare")

    def refresh_theme(self) -> None:
        """Re-apply palette colours after a theme switch."""
        for box in (
            self.box_foreground,
            self.box_overlay,
            self.box_killable,
            self.box_processes,
        ):
            box.refresh_palette()
        if self._snapshot is not None:
            self.apply_snapshot(self._snapshot, self._signals)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _SignalBox(QWidget):
    """One of the four live-status tiles."""

    def __init__(self, glyph: str, caption: str, value: str, tone: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(18, 15, 18, 15)
        column.setSpacing(3)

        head = HBox(spacing=7)
        icon = QLabel(glyph)
        icon.setStyleSheet(
            f"color: {styles.current()['text_faint']}; font-size: 12px;"
        )
        head.addWidget(icon)
        cap = QLabel(caption)
        cap.setObjectName("FieldLabel")
        head.addWidget(cap)
        head.add_stretch(1)
        column.addWidget(head)

        self.value_label = QLabel(value)
        self.value_label.setObjectName("FieldValue")
        column.addWidget(self.value_label)

        self.sub_label = QLabel("")
        self.sub_label.setStyleSheet(
            f"color: {styles.current()['text_faint']}; font-size: 11px;"
        )
        self.sub_label.setWordWrap(True)
        column.addWidget(self.sub_label)

        self._tone = tone

    def set_value(self, text: str, tone: str = "") -> None:
        self._tone = tone or self._tone
        self.value_label.setText(text)
        tokens = styles.current()
        colour = tokens.get(
            {"safe": "success", "warning": "warning", "danger": "danger"}.get(self._tone, "text"),
            tokens["text"],
        )
        self.value_label.setStyleSheet(
            f"color: {colour}; font-size: 14px; font-weight: 600;"
        )

    def set_sub(self, text: str) -> None:
        self.sub_label.setText(text)

    def refresh_palette(self) -> None:
        """Re-read the active palette for the glyph and value colours."""
        tokens = styles.current()
        self.value_label.setText(self.value_label.text())
        self.set_value(self.value_label.text(), self._tone)
        for child in self.findChildren(QLabel):
            if child is self.value_label or child is self.sub_label:
                continue
            child.setStyleSheet(f"color: {tokens['text_faint']}; font-size: 12px;")
        self.sub_label.setStyleSheet(
            f"color: {tokens['text_faint']}; font-size: 11px;"
        )


def _grey() -> str:
    return "#8E8E93"