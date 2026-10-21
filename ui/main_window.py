"""
ui.main_window
==============

The whole GUI: header, device card, three working tabs and a live log panel.

Threading note
--------------
No tab ever touches :class:`~core.adb_manager.AdbManager` directly. Tabs raise
signals, :class:`MainWindow` forwards them to the worker living on the worker
thread, and results come back as signals. That keeps every adb call — including
the slow package listings — entirely off the GUI thread.

Cross-thread callbacks use :class:`UiInvoker`, a tiny QObject that lives in
the GUI thread and exposes a signal. Emitting that signal from a worker thread
produces a queued delivery, so the handler always runs on the GUI thread.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from PySide6.QtCore import QDateTime, QObject, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import config
from core import updater
from core.adb_manager import (
    AdbManager,
    AdbWorker,
    DeviceRecord,
    PackageRecord,
)

from . import styles


# ---------------------------------------------------------------------------
# Cross-thread helper
# ---------------------------------------------------------------------------


class UiInvoker(QObject):
    """Marshal a plain Python callable onto the GUI thread.

    ``QMetaObject.invokeMethod`` can only target registered slots on a QObject,
    which arbitrary lambdas are not. A signal on an object that lives in the
    GUI thread sidesteps that: emitting it from any thread delivers the call
    back to the GUI thread's event loop.
    """

    called = Signal(object, object, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.called.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)

    def _dispatch(self, fn: object, args: object, kwargs: object) -> None:
        try:
            fn(*(args or ()), **(kwargs or {}))
        except Exception as exc:  # noqa: BLE001 - never kill the event loop
            import traceback

            traceback.print_exc()
            print(f"[ADSremovedPP] GUI callback failed: {exc}")

    def post(self, fn: Callable, *args, **kwargs) -> None:
        """Queue ``fn`` for execution on the GUI thread."""
        self.called.emit(fn, args, kwargs)


# ---------------------------------------------------------------------------
# Small building blocks
# ---------------------------------------------------------------------------


class Card(QFrame):
    """Rounded surface used everywhere for grouping."""

    def __init__(self, parent: QWidget | None = None, accent: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("CardAccent" if accent else "Card")


class LogPanel(QFrame):
    """Bottom log strip with level colouring."""

    CLEARED = Signal()

    LEVEL_COLORS = {
        "INFO":  "#8b93a7",
        "OK":    styles.C["success"],
        "WARN":  styles.C["warning"],
        "ERROR": styles.C["danger"],
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LogFrame")
        self.setFixedHeight(186)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(18, 0, 12, 0)

        title = QLabel("บันทึกการทำงาน")
        title.setObjectName("FieldLabel")
        header.addWidget(title)
        header.addStretch(1)

        self.count_label = QLabel("0 รายการ")
        self.count_label.setObjectName("FieldLabel")
        header.addWidget(self.count_label)

        self.clear_button = QPushButton("ล้าง")
        self.clear_button.setObjectName("Ghost")
        self.clear_button.clicked.connect(lambda: self.clear())
        header.addWidget(self.clear_button)

        layout.addLayout(header)

        self.view = QPlainTextEdit()
        self.view.setObjectName("LogView")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(config.LOG_PANEL_MAX_BLOCKS)
        self.view.setFont(QFont("Consolas", 9))
        layout.addWidget(self.view, 1)

        self._entries = 0

    def append(self, level: str, message: str) -> None:
        self._entries += 1
        self.count_label.setText(f"{self._entries} รายการ")
        stamp = QDateTime.currentDateTime().toString("HH:mm:ss")
        color = self.LEVEL_COLORS.get(level.upper(), styles.C["text_muted"])
        safe = message.replace("&", "&amp;")
        self.view.appendHtml(
            f'<span style="color:{styles.C["text_dim"]}">{stamp}</span> '
            f'<span style="color:{color}">[{level.upper():5}]</span> '
            f'<span style="color:{styles.C["text"]}">{safe}</span>'
        )

    def clear(self) -> None:
        self.view.clear()
        self._entries = 0
        self.count_label.setText("0 รายการ")
        self.CLEARED.emit()


class StatCard(Card):
    """Large number + caption tile."""

    def __init__(self, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)

        self.value_label = QLabel("—")
        self.value_label.setObjectName("StatValue")
        layout.addWidget(self.value_label)

        self.caption_label = QLabel(caption)
        self.caption_label.setObjectName("FieldLabel")
        layout.addWidget(self.caption_label)

        self.setMinimumHeight(74)

    def set_value(self, text: str, color: str | None = None) -> None:
        self.value_label.setText(text)
        self.value_label.setStyleSheet(f"color: {color or styles.C['accent']};")


class FieldBox(Card):
    """One labelled value inside the device card."""

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(3)

        caption = QLabel(label)
        caption.setObjectName("FieldLabel")
        layout.addWidget(caption)

        self.value = QLabel("—")
        self.value.setObjectName("FieldValue")
        self.value.setWordWrap(True)
        layout.addWidget(self.value)

        self.setMinimumHeight(66)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_value(self, text: str, color: str | None = None) -> None:
        self.value.setText(text if text else "—")
        self.value.setStyleSheet(f"color: {color or styles.C['text']};")


class BatteryBar(QProgressBar):
    """Battery gauge that recolours itself by charge level."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTextVisible(False)
        self.setFixedHeight(6)
        self.setRange(0, 100)
        self.setValue(0)

    def set_level(self, level: int | None) -> None:
        base = (
            f"QProgressBar{{background:{styles.C['bg_alt']};"
            f"border:1px solid {styles.C['border']};border-radius:3px;}}"
        )
        if level is None:
            self.setValue(0)
            self.setStyleSheet(base)
            return

        self.setValue(max(0, min(100, level)))
        if level <= 15:
            color = styles.C["danger"]
        elif level <= 30:
            color = styles.C["warning"]
        else:
            color = styles.C["success"]
        self.setStyleSheet(base + f"QProgressBar::chunk{{background:{color};border-radius:3px;}}")


# ---------------------------------------------------------------------------
# Tab 1 — Quick Scan & Adware Remover
# ---------------------------------------------------------------------------


class ScanTab(QWidget):
    """Compare installed packages against the blacklist and clean up."""

    scan_requested = Signal()
    refresh_requested = Signal()
    clean_requested = Signal(list)
    whitelist_requested = Signal(list)

    COLUMNS = ("เลือก", "ชื่อแอป", "แพ็กเกจ", "ประเภท", "ความเสี่ยง", "ที่มา")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._threats: list[PackageRecord] = []
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 14, 0, 0)
        root.setSpacing(10)

        # ---- toolbar ----
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self.btn_scan = QPushButton("เริ่มสแกน")
        self.btn_scan.setObjectName("Primary")
        self.btn_scan.setMinimumHeight(40)
        self.btn_scan.setMinimumWidth(150)
        self.btn_scan.clicked.connect(lambda: self.scan_requested.emit())
        toolbar.addWidget(self.btn_scan)

        self.btn_refresh = QPushButton("รีเฟรชอุปกรณ์")
        self.btn_refresh.setMinimumHeight(40)
        self.btn_refresh.clicked.connect(lambda: self.refresh_requested.emit())
        toolbar.addWidget(self.btn_refresh)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("ค้นหาชื่อแอปหรือแพ็กเกจ...")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setMinimumHeight(40)
        self.search_box.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.search_box, 1)

        root.addLayout(toolbar)

        # ---- statistics ----
        stats = QHBoxLayout()
        stats.setSpacing(10)
        self.stat_scanned = StatCard("แอปที่ตรวจแล้ว")
        self.stat_found = StatCard("พบรายการต้องระวัง")
        self.stat_selected = StatCard("เลือกไว้")
        for card in (self.stat_scanned, self.stat_found, self.stat_selected):
            stats.addWidget(card)
        root.addLayout(stats)

        # ---- results table ----
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.itemChanged.connect(self._on_item_changed)
        self._configure_columns()
        root.addWidget(self.table, 1)

        # ---- footer ----
        footer = QHBoxLayout()
        footer.setSpacing(8)

        self.check_all = QCheckBox("เลือกทั้งหมด")
        self.check_all.toggled.connect(self._toggle_all)
        footer.addWidget(self.check_all)

        self.btn_whitelist = QPushButton("ข้ามรายการที่เลือก")
        self.btn_whitelist.setObjectName("Ghost")
        self.btn_whitelist.setEnabled(False)
        self.btn_whitelist.clicked.connect(lambda: self._emit_whitelist())
        footer.addWidget(self.btn_whitelist)

        footer.addStretch(1)

        self.btn_clean = QPushButton("ล้างรายการที่เลือก")
        self.btn_clean.setObjectName("Danger")
        self.btn_clean.setMinimumHeight(40)
        self.btn_clean.setMinimumWidth(190)
        self.btn_clean.setEnabled(False)
        self.btn_clean.clicked.connect(lambda: self._emit_clean())
        footer.addWidget(self.btn_clean)

        root.addLayout(footer)

    def _configure_columns(self) -> None:
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 52)
        self.table.setColumnWidth(2, 230)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 100)
        self.table.setColumnWidth(5, 120)

    # ---- population ----

    def set_scanned_total(self, total: int) -> None:
        self.stat_scanned.set_value(str(total))

    def reset(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.blockSignals(False)
        self._threats = []
        self.check_all.blockSignals(True)
        self.check_all.setChecked(False)
        self.check_all.blockSignals(False)
        self.stat_scanned.set_value("—")
        self.stat_found.set_value("—")
        self.stat_selected.set_value("0")
        self.btn_clean.setEnabled(False)
        self.btn_whitelist.setEnabled(False)

    def set_threats(self, threats: Iterable[PackageRecord]) -> None:
        self._threats = list(threats)
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.check_all.blockSignals(True)
        self.check_all.setChecked(False)
        self.check_all.blockSignals(False)

        for record in self._threats:
            row = self.table.rowCount()
            self.table.insertRow(row)

            check_item = QTableWidgetItem("")
            check_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            check_item.setCheckState(Qt.CheckState.Unchecked)
            check_item.setData(Qt.ItemDataRole.UserRole, record.package)
            check_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, check_item)

            self.table.setItem(row, 1, QTableWidgetItem(record.display_name))
            self.table.setItem(row, 2, QTableWidgetItem(record.package))
            self.table.setItem(row, 3, QTableWidgetItem(record.threat_type or "—"))

            risk_item = QTableWidgetItem(record.risk or "ไม่ระบุ")
            risk_item.setForeground(QColor(styles.risk_color(record.risk)))
            risk_font = risk_item.font()
            risk_font.setBold(True)
            risk_item.setFont(risk_font)
            self.table.setItem(row, 4, risk_item)

            origin_text = "แอประบบ" if record.is_system else "ติดตั้งโดยผู้ใช้"
            origin_item = QTableWidgetItem(origin_text)
            origin_item.setForeground(
                QColor(styles.C["text_dim"] if record.is_system else styles.C["text"])
            )
            self.table.setItem(row, 5, origin_item)

        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self._update_counters()

    def remove_rows(self, packages: Iterable[str]) -> None:
        """Drop successfully removed packages from the table."""
        targets = set(packages)
        if not targets:
            return
        self.table.blockSignals(True)
        for row in range(self.table.rowCount() - 1, -1, -1):
            item = self.table.item(row, 0)
            if item is not None and str(item.data(Qt.ItemDataRole.UserRole)) in targets:
                self.table.removeRow(row)
                self._threats = [
                    t for t in self._threats if t.package not in targets
                ]
        self.table.blockSignals(False)
        self._update_counters()

    # ---- interaction ----

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.table.rowCount()):
            package_item = self.table.item(row, 2)
            name_item = self.table.item(row, 1)
            haystack = " ".join(
                [
                    package_item.text() if package_item else "",
                    name_item.text() if name_item else "",
                ]
            ).lower()
            self.table.setRowHidden(row, bool(needle) and needle not in haystack)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self._update_counters()

    def _toggle_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue
            item = self.table.item(row, 0)
            if item is not None:
                item.setCheckState(state)
        self.table.blockSignals(False)
        self._update_counters()

    def checked_packages(self) -> list[str]:
        packages: list[str] = []
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                packages.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return packages

    def _update_counters(self) -> None:
        chosen = len(self.checked_packages())
        self.stat_found.set_value(
            str(len(self._threats)),
            styles.C["danger"] if self._threats else styles.C["success"],
        )
        self.stat_selected.set_value(str(chosen), styles.C["text"])
        self.btn_clean.setEnabled(chosen > 0)
        self.btn_whitelist.setEnabled(chosen > 0)

    def _emit_clean(self) -> None:
        packages = self.checked_packages()
        if packages:
            self.clean_requested.emit(packages)

    def _emit_whitelist(self) -> None:
        packages = self.checked_packages()
        if packages:
            self.whitelist_requested.emit(packages)

    def set_busy(self, busy: bool) -> None:
        self.btn_scan.setEnabled(not busy)
        self.btn_refresh.setEnabled(not busy)


# ---------------------------------------------------------------------------
# Tab 2 — Package Manager
# ---------------------------------------------------------------------------


class PackageTab(QWidget):
    """Full app browser with search, normal removal and forced removal."""

    refresh_requested = Signal()
    force_uninstall_requested = Signal(list)
    normal_uninstall_requested = Signal(list)
    clear_data_requested = Signal(list)

    COLUMNS = ("เลือก", "แพ็กเกจ", "ประเภท", "สถานะ")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._records: list[PackageRecord] = []
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 14, 0, 0)
        root.setSpacing(10)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self.btn_reload = QPushButton("โหลดรายชื่อแอป")
        self.btn_reload.setObjectName("Primary")
        self.btn_reload.setMinimumHeight(40)
        self.btn_reload.setMinimumWidth(160)
        self.btn_reload.clicked.connect(lambda: self.refresh_requested.emit())
        toolbar.addWidget(self.btn_reload)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("ค้นหาแพ็กเกจ เช่น com.facebook...")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setMinimumHeight(40)
        self.search_box.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.search_box, 1)

        root.addLayout(toolbar)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.itemChanged.connect(self._on_item_changed)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 52)
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(3, 140)

        root.addWidget(self.table, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)

        self.check_all = QCheckBox("เลือกทั้งหมดที่เห็น")
        self.check_all.toggled.connect(self._toggle_all)
        footer.addWidget(self.check_all)

        self.selection_label = QLabel("เลือกไว้ 0 รายการ")
        self.selection_label.setObjectName("SectionHint")
        footer.addWidget(self.selection_label)

        footer.addStretch(1)

        self.btn_clear = QPushButton("ล้างข้อมูลแอป")
        self.btn_clear.setObjectName("Ghost")
        self.btn_clear.setEnabled(False)
        self.btn_clear.clicked.connect(lambda: self._emit(self.clear_data_requested))
        footer.addWidget(self.btn_clear)

        self.btn_normal = QPushButton("ถอนการติดตั้ง")
        self.btn_normal.setEnabled(False)
        self.btn_normal.clicked.connect(lambda: self._emit(self.normal_uninstall_requested))
        footer.addWidget(self.btn_normal)

        self.btn_force = QPushButton("บังคับถอนการติดตั้ง")
        self.btn_force.setObjectName("Danger")
        self.btn_force.setMinimumHeight(40)
        self.btn_force.setMinimumWidth(200)
        self.btn_force.setEnabled(False)
        self.btn_force.clicked.connect(lambda: self._emit(self.force_uninstall_requested))
        footer.addWidget(self.btn_force)

        root.addLayout(footer)

    def set_packages(self, records: Iterable[PackageRecord]) -> None:
        self._records = list(records)
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.check_all.blockSignals(True)
        self.check_all.setChecked(False)
        self.check_all.blockSignals(False)

        for record in self._records:
            row = self.table.rowCount()
            self.table.insertRow(row)

            check_item = QTableWidgetItem("")
            check_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            check_item.setCheckState(Qt.CheckState.Unchecked)
            check_item.setData(Qt.ItemDataRole.UserRole, record.package)
            check_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, check_item)

            self.table.setItem(row, 1, QTableWidgetItem(record.package))

            kind_text = "แอประบบ" if record.is_system else "แอปผู้ใช้"
            kind_item = QTableWidgetItem(kind_text)
            kind_item.setForeground(
                QColor(styles.C["text_dim"] if record.is_system else styles.C["text"])
            )
            self.table.setItem(row, 2, kind_item)

            threat = self._lookup_threat(record.package)
            if threat is not None:
                status_item = QTableWidgetItem(f"ต้องระวัง ({threat})")
                status_item.setForeground(QColor(styles.C["danger"]))
            else:
                status_item = QTableWidgetItem("ไม่พบรายการต้องระวัง")
                status_item.setForeground(QColor(styles.C["text_dim"]))
            self.table.setItem(row, 3, status_item)

        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self._update_selection_label()

    def set_threat_map(self, mapping: dict[str, str]) -> None:
        """Refresh the status column once a scan has run."""
        for row in range(self.table.rowCount()):
            package_item = self.table.item(row, 1)
            if package_item is None:
                continue
            package = package_item.text()
            status_item = self.table.item(row, 3)
            if status_item is None:
                continue
            if package in mapping:
                status_item.setText(f"ต้องระวัง ({mapping[package]})")
                status_item.setForeground(QColor(styles.C["danger"]))
            else:
                status_item.setText("ไม่พบรายการต้องระวัง")
                status_item.setForeground(QColor(styles.C["text_dim"]))

    def _lookup_threat(self, package: str) -> str | None:
        mapping = getattr(self, "_threat_lookup", None)
        if not mapping:
            return None
        return mapping.get(package)

    def remove_rows(self, packages: Iterable[str]) -> None:
        targets = set(packages)
        if not targets:
            return
        self.table.blockSignals(True)
        for row in range(self.table.rowCount() - 1, -1, -1):
            item = self.table.item(row, 1)
            if item is not None and item.text() in targets:
                self.table.removeRow(row)
        self.table.blockSignals(False)
        self._update_selection_label()

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            haystack = item.text().lower() if item else ""
            self.table.setRowHidden(row, bool(needle) and needle not in haystack)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self._update_selection_label()

    def _toggle_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue
            item = self.table.item(row, 0)
            if item is not None:
                item.setCheckState(state)
        self.table.blockSignals(False)
        self._update_selection_label()

    def checked_packages(self) -> list[str]:
        packages: list[str] = []
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                packages.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return packages

    def _update_selection_label(self) -> None:
        count = len(self.checked_packages())
        self.selection_label.setText(f"เลือกไว้ {count} รายการ")
        enabled = count > 0
        self.btn_force.setEnabled(enabled)
        self.btn_normal.setEnabled(enabled)
        self.btn_clear.setEnabled(enabled)

    def _emit(self, signal: Signal) -> None:
        packages = self.checked_packages()
        if packages:
            signal.emit(packages)

    def set_busy(self, busy: bool) -> None:
        self.btn_reload.setEnabled(not busy)


# ---------------------------------------------------------------------------
# Tab 3 — Quick Utilities
# ---------------------------------------------------------------------------


@dataclass
class UtilityAction:
    """One button on the utilities tab."""

    label: str
    description: str
    kind: str            # "settings" | "reboot" | "clear"
    payload: str = ""   # settings action / reboot target / package id
    destructive: bool = False


class UtilitiesTab(QWidget):
    """Tactile repair actions: settings shortcuts, app-data wipes, reboots."""

    settings_requested = Signal(str, str)
    reboot_requested = Signal(str, str)
    clear_requested = Signal(str, str)

    SETTINGS_ACTIONS: tuple[UtilityAction, ...] = (
        UtilityAction(
            "เปิดตัวเลือกสำหรับนักพัฒนา",
            "USB debugging, OEM unlocking",
            "settings",
            "APPLICATION_DEVELOPMENT_SETTINGS",
        ),
        UtilityAction(
            "เปิดการเข้าถึง (Accessibility)",
            "ดูแอปที่เปิดสิทธิ์อันตราย",
            "settings",
            "ACCESSIBILITY_SETTINGS",
        ),
        UtilityAction(
            "เปิดการติดตั้งแอปที่ไม่รู้จัก",
            "เปิด/ปิดการลอกติดตั้งไฟล์ APK",
            "settings",
            "MANAGE_UNKNOWN_APP_SOURCES",
        ),
        UtilityAction(
            "เปิดการใช้งานข้อมูล",
            "จำกัดสิทธิ์แอปที่ทำงานเบื้องหลัง",
            "settings",
            "USAGE_ACCESS_SETTINGS",
        ),
        UtilityAction(
            "เปิดแอปทั้งหมด",
            "ดูรายชื่อและกำหนดค่าทุกแอป",
            "settings",
            "MANAGE_APPLICATIONS_SETTINGS",
        ),
        UtilityAction(
            "เปิดการประหยัดแบตเตอรี่",
            "ตั้งค่าเพื่อลดพลังงาน",
            "settings",
            "BATTERY_SAVER_SETTINGS",
        ),
    )

    CLEAR_ACTIONS: tuple[UtilityAction, ...] = (
        UtilityAction(
            "ล้างข้อมูล Chrome",
            "ลบแคช/คุกกี้ — ต้องล็อกอินใหม่",
            "clear",
            "com.android.chrome",
            destructive=True,
        ),
        UtilityAction(
            "ล้างข้อมูล WebView",
            "รีเซ็ตเครื่องมือแสดงผลเว็บของ Android",
            "clear",
            "com.google.android.webview",
            destructive=True,
        ),
        UtilityAction(
            "ล้างข้อมูล Samsung Internet",
            "เฉพาะเครื่อง Samsung",
            "clear",
            "com.sec.android.app.sbrowser",
            destructive=True,
        ),
        UtilityAction(
            "ล้างข้อมูล Google Play Services",
            "แก้แอปไม่อัปเดต — เปิด Play Store ใหม่ภายหลัง",
            "clear",
            "com.google.android.gms",
            destructive=True,
        ),
    )

    REBOOT_ACTIONS: tuple[UtilityAction, ...] = (
        UtilityAction("ปิด-เปิดเครื่องปกติ", "แก้อาการค้าง", "reboot", ""),
        UtilityAction("เข้าโหมด Recovery", "ลบข้อมูล/รีเซ็ตเครื่อง", "reboot", "recovery"),
        UtilityAction(
            "เข้าโหมด Bootloader",
            "ติดไฟคอมได้ — ค้างให้กดปุ่มเปิดเครื่องค้าง",
            "reboot",
            "bootloader",
        ),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tiles: list[QPushButton] = []
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 14, 0, 0)
        outer.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        layout.addWidget(self._group(
            "ทางลัดการตั้งค่าบนมือถือ",
            "เปิดหน้าจอตั้งค่าที่ช่างใช้บ่อย โดยไม่ต้องเลื่อนเมนูเอง",
            self.SETTINGS_ACTIONS,
            columns=2,
        ))

        layout.addWidget(self._group(
            "ล้างข้อมูลแอปที่ค้างบ่อย",
            "ลบแคชและการตั้งค่าของแอป — ข้อมูลภายในแอปจะหาย",
            self.CLEAR_ACTIONS,
            columns=2,
        ))

        layout.addWidget(self._group(
            "ควบคุมการเปิด-ปิดเครื่อง",
            "ใช้เมื่อเครื่องค้าง หรือต้องการเข้าโหมดกู้คืน",
            self.REBOOT_ACTIONS,
            columns=3,
        ))

        layout.addStretch(1)

    def _group(
        self,
        title: str,
        hint: str,
        actions: tuple[UtilityAction, ...],
        columns: int,
    ) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        heading = QLabel(title)
        heading.setObjectName("CardTitle")
        layout.addWidget(heading)

        subtitle = QLabel(hint)
        subtitle.setObjectName("SectionHint")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        grid = QGridLayout()
        grid.setSpacing(8)
        for index, action in enumerate(actions):
            tile = QPushButton(f"{action.label}\n{action.description}")
            tile.setObjectName("UtilityTile")
            tile.setMinimumHeight(58)
            tile.setCursor(Qt.CursorShape.PointingHandCursor)
            tile.clicked.connect(lambda _checked=False, a=action: self._dispatch(a))
            grid.addWidget(tile, index // columns, index % columns)
            self._tiles.append(tile)
        for col in range(columns):
            grid.setColumnStretch(col, 1)

        layout.addLayout(grid)
        return card

    def _dispatch(self, action: UtilityAction) -> None:
        if action.kind == "settings":
            self.settings_requested.emit(action.payload, action.label)
        elif action.kind == "reboot":
            self.reboot_requested.emit(action.payload, action.label)
        elif action.kind == "clear":
            self.clear_requested.emit(action.payload, action.label)

    def set_busy(self, busy: bool) -> None:
        for tile in self._tiles:
            tile.setEnabled(not busy)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


@dataclass
class AppState:
    serial: str = ""
    connected: bool = False
    blacklist: list = field(default_factory=list)
    blacklist_origin: str = "empty"
    busy: bool = False
    threat_map: dict = field(default_factory=dict)


class MainWindow(QMainWindow):
    """Owns the worker thread, the device card and the three tabs."""

    def __init__(self, manager: AdbManager, worker: AdbWorker) -> None:
        super().__init__()
        self.manager = manager
        self.worker = worker
        self.state = AppState()

        self.setWindowTitle(f"{config.APP_TITLE}  v{config.CURRENT_VERSION}")
        self.resize(1240, 860)
        self.setMinimumSize(1040, 720)

        # Lives in the GUI thread; every worker-thread callback routes here.
        self.invoker = UiInvoker(self)

        # Short-lived helper threads are tracked so closeEvent can wait for
        # them. Parenting them to this window would make Qt destroy them —
        # while still running — at shutdown.
        self._spawned_threads: list[QThread] = []

        self._build_ui()
        self._connect_worker()
        self._build_menu()
        self._bootstrap()

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(18, 14, 18, 12)
        body_layout.setSpacing(12)

        body_layout.addWidget(self._build_device_card())

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        self.scan_tab = ScanTab()
        self.package_tab = PackageTab()
        self.utilities_tab = UtilitiesTab()

        self.tabs.addTab(self.scan_tab, "สแกนด่วน & ล้างแอดแวร์")
        self.tabs.addTab(self.package_tab, "จัดการแอป")
        self.tabs.addTab(self.utilities_tab, "เครื่องมือช่าง")

        body_layout.addWidget(self.tabs, 1)
        root.addWidget(body, 1)

        self.log_panel = LogPanel()
        root.addWidget(self.log_panel)

        self._wire_tabs()

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("HeaderBar")
        header.setFixedHeight(64)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(18, 0, 18, 0)
        layout.setSpacing(12)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel("ADSremovedPP")
        title.setObjectName("AppTitle")
        title_box.addWidget(title)
        subtitle = QLabel("Android ADB Cleaner & Repair Utility")
        subtitle.setObjectName("AppVersion")
        title_box.addWidget(subtitle)
        layout.addLayout(title_box)

        layout.addStretch(1)

        self.db_pill = QLabel("ฐานข้อมูล: กำลังโหลด")
        self.db_pill.setObjectName("StatusPill")
        layout.addWidget(self.db_pill)

        self.status_pill = QLabel("ไม่ได้เชื่อมต่อ")
        self.status_pill.setObjectName("StatusPill")
        layout.addWidget(self.status_pill)

        self.btn_connect = QPushButton("ตรวจหามือถือ")
        self.btn_connect.setObjectName("Primary")
        self.btn_connect.setMinimumHeight(36)
        self.btn_connect.setMinimumWidth(140)
        self.btn_connect.clicked.connect(lambda: self.on_probe())
        layout.addWidget(self.btn_connect)

        return header

    def _build_device_card(self) -> Card:
        card = Card()
        grid = QGridLayout(card)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setSpacing(10)

        self.field_brand = FieldBox("ยี่ห้อ")
        self.field_model = FieldBox("รุ่นเครื่อง")
        self.field_android = FieldBox("เวอร์ชัน Android")
        self.field_serial = FieldBox("Serial Number")
        self.field_battery = FieldBox("สถานะแบตเตอรี่")
        self.field_storage = FieldBox("พื้นที่ว่าง")

        for col, box in enumerate(
            (
                self.field_brand,
                self.field_model,
                self.field_android,
                self.field_serial,
                self.field_battery,
                self.field_storage,
            )
        ):
            grid.addWidget(box, 0, col)
            grid.setColumnStretch(col, 1)

        self.battery_bar = BatteryBar()
        self.field_battery.layout().addWidget(self.battery_bar)

        self.battery_detail = QLabel("—")
        self.battery_detail.setObjectName("FieldLabel")
        self.battery_detail.setWordWrap(True)
        self.field_battery.layout().addWidget(self.battery_detail)

        return card

    def _wire_tabs(self) -> None:
        scan = self.scan_tab
        scan.scan_requested.connect(self.on_scan)
        scan.refresh_requested.connect(self.on_probe)
        scan.clean_requested.connect(self.on_clean_threats)
        scan.whitelist_requested.connect(self.on_whitelist)

        packages = self.package_tab
        packages.refresh_requested.connect(self.on_load_packages)
        packages.force_uninstall_requested.connect(
            lambda pkgs: self.on_uninstall(pkgs, force=True, label="บังคับถอนการติดตั้ง")
        )
        packages.normal_uninstall_requested.connect(
            lambda pkgs: self.on_uninstall(pkgs, force=False, label="ถอนการติดตั้ง")
        )
        packages.clear_data_requested.connect(self.on_clear_data)

        utilities = self.utilities_tab
        utilities.settings_requested.connect(self.on_open_settings)
        utilities.reboot_requested.connect(self.on_reboot)
        utilities.clear_requested.connect(self.on_clear_single)

    def _connect_worker(self) -> None:
        self.worker.task_requested.connect(
            self.worker._dispatch, Qt.ConnectionType.QueuedConnection
        )
        self.worker.log_line.connect(self.log_panel.append)
        self.worker.devices_ready.connect(self.on_devices_ready)
        self.worker.device_info_ready.connect(self.on_device_info)
        self.worker.packages_ready.connect(self.on_packages_ready)
        self.worker.scan_finished.connect(self.on_scan_finished)
        self.worker.uninstall_results.connect(self.on_uninstall_results)
        self.worker.action_result.connect(self.on_action_result)
        self.worker.task_failed.connect(self.on_task_failed)
        self.worker.task_done.connect(self.on_task_done)

    def _build_menu(self) -> None:
        menu = self.menuBar()

        file_menu = QMenu("ไฟล์", self)
        reload_action = QAction("โหลดฐานข้อมูลใหม่", self)
        reload_action.setShortcut(QKeySequence.StandardKey.Refresh)
        reload_action.triggered.connect(lambda: self._load_blacklist(force_remote=True))
        file_menu.addAction(reload_action)
        file_menu.addSeparator()
        exit_action = QAction("ออกจากโปรแกรม", self)
        exit_action.triggered.connect(lambda: self.close())
        file_menu.addAction(exit_action)
        menu.addMenu(file_menu)

        tools_menu = QMenu("เครื่องมือ", self)
        update_action = QAction("ตรวจหาการอัปเดต...", self)
        update_action.triggered.connect(lambda: self.on_check_update())
        tools_menu.addAction(update_action)

        kill_action = QAction("รีสตาร์ตเซิร์ฟเวอร์ ADB", self)
        kill_action.triggered.connect(lambda: self.worker.request("kill_server"))
        tools_menu.addAction(kill_action)
        menu.addMenu(tools_menu)

        help_menu = QMenu("ช่วยเหลือ", self)
        about_action = QAction("เกี่ยวกับโปรแกรม", self)
        about_action.triggered.connect(lambda: self.on_about())
        help_menu.addAction(about_action)
        menu.addMenu(help_menu)

    def _bootstrap(self) -> None:
        if self.manager.available:
            self.log_panel.append("INFO", f"พบ adb ที่ {self.manager.adb_path}")
        else:
            self.log_panel.append(
                "ERROR",
                "ไม่พบ adb — วางไฟล์ไว้ที่โฟลเดอร์ bin/ หรือติดตั้ง Android platform-tools",
            )
        self.log_panel.append("INFO", f"{config.APP_NAME} v{config.CURRENT_VERSION} พร้อมใช้งาน")
        self.set_busy(False)
        self._load_blacklist(force_remote=True)

    # ------------------------------------------------------------------
    # database
    # ------------------------------------------------------------------

    def _load_blacklist(self, force_remote: bool = False) -> None:
        url = config.BLACKLIST_URL if force_remote else ""
        if not force_remote:
            self.log_panel.append("INFO", "โหลดฐานข้อมูลจากเครื่อง")

        def work() -> None:
            entries, origin = updater.load_blacklist(url, on_status=self._db_status)
            self.invoker.post(self._apply_blacklist, entries, origin)

        self._spawn(work)

    def _db_status(self, message: str) -> None:
        # Called from the loader thread; forward onto the GUI thread.
        self.invoker.post(self.log_panel.append, "INFO", message)

    def _apply_blacklist(self, entries: list, origin: str) -> None:
        self.state.blacklist = entries
        self.state.blacklist_origin = origin
        labels = {"remote": "อินเทอร์เน็ต", "local": "ในเครื่อง", "empty": "ยังไม่มีข้อมูล"}
        text = f"ฐานข้อมูล: {labels.get(origin, origin)} ({len(entries)})"
        self.db_pill.setText(text)
        if entries and self.package_tab.table.rowCount():
            self.package_tab._threat_lookup = {
                str(e.get("package")): str(e.get("risk", ""))
                for e in entries
                if e.get("package")
            }
            self.package_tab.set_threat_map(self.package_tab._threat_lookup)

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------

    def on_probe(self) -> None:
        if self.state.busy:
            return
        self.set_busy(True)
        self.worker.request("probe", {"serial": self.state.serial})

    def on_scan(self) -> None:
        if not self._require_device_ready():
            return
        if not self.state.blacklist:
            QMessageBox.warning(
                self,
                "ยังไม่มีรายการต้องระวัง",
                "ฐานข้อมูลรายการต้องระวังยังว่างอยู่\n\n"
                "โปรแกรมจะสแกนได้ผลเป็นศูนย์จนกว่าจะมีข้อมูล\n"
                "เชื่อมต่ออินเทอร์เน็ตแล้วเลือก ไฟล์ > โหลดฐานข้อมูลใหม่",
            )
        self.set_busy(True)
        self.scan_tab.reset()
        self.worker.request(
            "scan",
            {"serial": self.state.serial, "blacklist": self.state.blacklist},
        )

    def on_load_packages(self) -> None:
        if not self._require_device_ready():
            return
        self.set_busy(True)
        self.worker.request(
            "list_packages",
            {"serial": self.state.serial, "third_party_only": False, "origin": "packages"},
        )

    def on_clean_threats(self, packages: list) -> None:
        if not self._require_device_ready():
            return
        if not packages:
            return
        preview = "\n".join(f"  •  {p}" for p in packages[:12])
        more = f"\n  ... และอีก {len(packages) - 12} รายการ" if len(packages) > 12 else ""
        answer = QMessageBox.question(
            self,
            "ยืนยันการลบ",
            f"ต้องการถอนการติดตั้ง {len(packages)} แอปต่อไปนี้:\n\n{preview}{more}\n\n"
            "ข้อมูลภายในแอปเหล่านี้จะถูกลบถาวร ดำเนินการต่อหรือไม่?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.set_busy(True)
        self.worker.request(
            "uninstall",
            {
                "serial": self.state.serial,
                "packages": packages,
                "force": False,
                "label": "รายการต้องระวัง",
            },
        )

    def on_uninstall(self, packages: list, force: bool, label: str) -> None:
        if not self._require_device_ready() or not packages:
            return

        if force:
            body = (
                f"บังคับถอนการติดตั้ง {len(packages)} รายการ\n\n"
                "วิธีนี้ใช้สำหรับแอประบบที่ถอนแบบปกติไม่ได้\n"
                "แอปจะถูกซ่อนเฉพาะผู้ใช้รายนี้ (รีเซ็ตเครื่องแล้วจะกลับมา)\n\n"
                "ดำเนินการต่อหรือไม่?"
            )
        else:
            preview = "\n".join(f"  •  {p}" for p in packages[:12])
            more = f"\n  ... และอีก {len(packages) - 12} รายการ" if len(packages) > 12 else ""
            body = (
                f"ถอนการติดตั้ง {len(packages)} รายการ:\n\n{preview}{more}\n\n"
                "ข้อมูลภายในแอปจะถูกลบถาวร ดำเนินการต่อหรือไม่?"
            )

        answer = QMessageBox.question(
            self,
            "ยืนยันการถอนการติดตั้ง",
            body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.set_busy(True)
        self.worker.request(
            "uninstall",
            {
                "serial": self.state.serial,
                "packages": packages,
                "force": force,
                "label": label,
            },
        )

    def on_clear_data(self, packages: list) -> None:
        if not self._require_device_ready() or not packages:
            return
        answer = QMessageBox.question(
            self,
            "ยืนยันการล้างข้อมูล",
            f"ล้างข้อมูล {len(packages)} แอป\n\n"
            "เบราว์เซอร์จะถูกออกจากระบบ และการตั้งค่าจะกลับเป็นค่าเริ่มต้น\n"
            "ข้อมูลที่บันทึกไว้ในแอปอาจหาย ดำเนินการต่อหรือไม่?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.set_busy(True)
        self.worker.request(
            "clear_data",
            {"serial": self.state.serial, "packages": packages, "label": "แอปที่เลือก"},
        )

    def on_clear_single(self, package: str, label: str) -> None:
        if not self._require_device_ready() or not package:
            return
        answer = QMessageBox.question(
            self,
            "ยืนยันการล้างข้อมูล",
            f"{label}\n\nแพ็กเกจ: {package}\n\n"
            "เบราว์เซอร์จะถูกออกจากระบบ และแอปจะกลับเป็นค่าเริ่มต้น\n"
            "ดำเนินการต่อหรือไม่?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.set_busy(True)
        self.worker.request(
            "clear_data",
            {"serial": self.state.serial, "packages": [package], "label": label},
        )

    def on_open_settings(self, action: str, label: str) -> None:
        if not self._require_device_ready():
            return
        self.worker.request(
            "open_settings",
            {"serial": self.state.serial, "action": action, "label": label},
        )

    def on_reboot(self, target: str, label: str) -> None:
        if not self._require_device_ready():
            return
        answer = QMessageBox.question(
            self,
            "ยืนยันการสั่งเครื่อง",
            f"{label}\n\n"
            "มือถือจะหยุดการทำงานชั่วคราว กรุณาบันทึกงานที่ยังไม่เสร็จก่อน\n"
            "ดำเนินการต่อหรือไม่?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.worker.request(
            "reboot",
            {"serial": self.state.serial, "target": target, "label": label},
        )

    def on_whitelist(self, packages: list) -> None:
        count = len(packages)
        self.log_panel.append(
            "WARN",
            f"ข้าม {count} รายการในรอบการทำงานนี้ (ยังไม่บันทึกถาวร)",
        )
        QMessageBox.information(
            self,
            "รายการข้าม",
            f"ทำเครื่องหมาย {count} รายการว่าไม่ต้องแจ้งซ้ำในรอบนี้\n\n"
            "ถ้าต้องการให้จำถาวร ให้นำแพ็กเกจเหล่านี้ไปลบออกจากรายการต้องระวัง "
            "ในไฟล์ local_db.json",
        )

    # ------------------------------------------------------------------
    # worker results
    # ------------------------------------------------------------------

    def on_devices_ready(self, devices: list) -> None:
        if not devices:
            self.state.connected = False
            self.state.serial = ""
            self._set_status("ไม่ได้เชื่อมต่อ", styles.C["text_muted"])
            self.field_serial.set_value("")
            return

        record: DeviceRecord = devices[0]
        self.state.connected = True
        self.state.serial = record.serial
        self._set_status(record.state_label, styles.C["success"])
        self.btn_connect.setText("ตรวจหาอีกครั้ง")

    def on_device_info(self, info: dict) -> None:
        self.field_brand.set_value(info.get("brand") or info.get("manufacturer", ""))
        self.field_model.set_value(info.get("model", ""))

        android = info.get("android_release", "")
        sdk = info.get("sdk_int", "")
        self.field_android.set_value(f"{android} (API {sdk})" if sdk else android)
        self.field_serial.set_value(info.get("serial", ""))

        level = info.get("battery_level")
        state = info.get("battery_state", "")
        if level is None:
            self.field_battery.set_value("ไม่ทราบ")
            self.battery_bar.set_level(None)
        else:
            suffix = f"  ({state})" if state else ""
            color = styles.C["danger"] if level <= 15 else styles.C["text"]
            self.field_battery.set_value(f"{level}%{suffix}", color)
            self.battery_bar.set_level(int(level))

        details = []
        if info.get("battery_temperature"):
            details.append(f"อุณหภูมิ {info['battery_temperature']}")
        if info.get("abi"):
            details.append(f"ชิป {info['abi']}")
        self.battery_detail.setText(" · ".join(details) if details else "—")

        free = info.get("storage_free_gb")
        total = info.get("storage_total_gb")
        if free is not None and total:
            ratio = free / total
            color = styles.C["danger"] if ratio < 0.10 else styles.C["text"]
            self.field_storage.set_value(f"{free} / {total} GB", color)
        elif free is not None:
            self.field_storage.set_value(f"{free} GB ว่าง")
        else:
            self.field_storage.set_value("—")

    def on_packages_ready(self, records: list, origin: str) -> None:
        self.package_tab.set_packages(records)
        self.log_panel.append("INFO", f"แสดง {len(records)} แอปในรายการ")

    def on_scan_finished(self, threats: list, total: int) -> None:
        self.scan_tab.set_scanned_total(total)
        self.scan_tab.set_threats(threats)
        self.state.threat_map = {r.package: r.risk for r in threats}
        self.package_tab._threat_lookup = dict(self.state.threat_map)
        if self.package_tab.table.rowCount():
            self.package_tab.set_threat_map(self.package_tab._threat_lookup)

    def on_uninstall_results(self, results: list) -> None:
        removed = [r.package for r in results if r.success]
        failed = [r for r in results if not r.success]

        if removed:
            self.scan_tab.remove_rows(removed)
            self.package_tab.remove_rows(removed)

        if failed:
            QMessageBox.warning(
                self,
                "บางรายการลบไม่สำเร็จ",
                f"ลบสำเร็จ {len(removed)} รายการ แต่มี {len(failed)} รายการที่ลบไม่ได้\n\n"
                "แอประบบบางตัวถูกผู้ผลิตล็อกไว้\n"
                "ให้ลองใช้ปุ่ม 'บังคับถอนการติดตั้ง' ในแท็บจัดการแอป",
            )

    def on_action_result(self, label: str, ok: bool, message: str) -> None:
        self.log_panel.append("OK" if ok else "ERROR", f"{label}: {message}")
        if ok:
            QMessageBox.information(self, label, message)

    def on_task_failed(self, message: str) -> None:
        self.log_panel.append("ERROR", message)
        QMessageBox.critical(self, "ทำงานไม่สำเร็จ", message)

    def on_task_done(self, task: str) -> None:
        self.set_busy(False)

    # ------------------------------------------------------------------
    # update flow
    # ------------------------------------------------------------------

    def on_check_update(self) -> None:
        self.log_panel.append("INFO", "กำลังตรวจหาเวอร์ชันใหม่...")

        def work() -> None:
            try:
                info = updater.check_for_update()
            except Exception as exc:  # noqa: BLE001
                self.invoker.post(self.log_panel.append, "ERROR", f"ตรวจอัปเดตผิดพลาด: {exc}")
                self.invoker.post(self._update_none)
                return
            if info is None:
                self.invoker.post(self._update_none)
            else:
                self.invoker.post(self._update_found, info)

        self._spawn(work)

    def _update_none(self) -> None:
        self.log_panel.append("INFO", "ไม่พบเวอร์ชันใหม่ (หรือไม่มีอินเทอร์เน็ต)")
        QMessageBox.information(
            self,
            "ตรวจสอบอัปเดต",
            f"โปรแกรมเป็นเวอร์ชันล่าสุดแล้ว (v{config.CURRENT_VERSION})",
        )

    def _update_found(self, info) -> None:
        answer = QMessageBox.question(
            self,
            "พบเวอร์ชันใหม่",
            f"{updater.describe_update(info)}\n\nต้องการดาวน์โหลดและติดตั้งเลยหรือไม่?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._download_update(info)

    def _download_update(self, info) -> None:
        self.set_busy(True)
        self.log_panel.append("INFO", f"กำลังดาวน์โหลด {info.version} ...")

        def work() -> None:
            try:
                updater.clear_staging()
                staged = updater.stage_download(info)
            except Exception as exc:  # noqa: BLE001
                self.invoker.post(self._update_failed, str(exc))
                return
            self.invoker.post(self._update_ready, staged, info)

        self._spawn(work)

    def _update_failed(self, message: str) -> None:
        self.set_busy(False)
        self.log_panel.append("ERROR", f"ดาวน์โหลดไม่สำเร็จ: {message}")
        QMessageBox.critical(self, "อัปเดตไม่สำเร็จ", message)

    def _update_ready(self, staged, info) -> None:
        self.log_panel.append("OK", "ดาวน์โหลดสำเร็จ กำลังเตรียมติดตั้ง...")

        if not getattr(sys, "frozen", False):
            self.set_busy(False)
            QMessageBox.information(
                self,
                "ดาวน์โหลดสำเร็จ",
                f"ไฟล์ใหม่อยู่ที่:\n{staged}\n\n"
                "กำลังรันแบบโค้ด โปรแกรมจึงไม่สามารถแทนที่ตัวเองได้\n"
                "ให้แทนที่ไฟล์ .exe ด้วยมือ",
            )
            return

        try:
            script = updater.build_updater_script(
                old_exe=Path(sys.executable).resolve(),
                new_exe=staged,
                script_path=updater.temp_script_path(),
            )
            updater.launch_updater(script)
        except Exception as exc:  # noqa: BLE001
            self.set_busy(False)
            self.log_panel.append("ERROR", f"ติดตั้งไม่สำเร็จ: {exc}")
            QMessageBox.critical(self, "อัปเดตไม่สำเร็จ", str(exc))
            return

        QMessageBox.information(
            self,
            "กำลังอัปเดต",
            "โปรแกรมจะปิดและเปิดใหม่โดยอัตโนมัติภายในไม่กี่วินาที",
        )
        QApplication.instance().quit()

    # ------------------------------------------------------------------
    # misc
    # ------------------------------------------------------------------

    def on_about(self) -> None:
        QMessageBox.about(
            self,
            "เกี่ยวกับโปรแกรม",
            f"<b>{config.APP_NAME}</b> v{config.CURRENT_VERSION}<br><br>"
            "เครื่องมือสำหรับช่างซ่อมมือถือ — สแกนแอปเทียบกับรายการต้องระวัง "
            "ถอนการติดตั้ง และเรียกคำสั่งซ่อมแซมพื้นฐานผ่าน ADB<br><br>"
            "<b>คำเตือน</b><br>"
            "โปรแกรมนี้ตรวจจับได้ไม่ 100% กรุณาสำรองข้อมูลลูกค้าก่อนลบแอป<br>"
            "การถอนแบบบังคับ (--user 0) ไม่ได้ลบไฟล์ระบบ "
            "เพียงซ่อนแอปไว้ฝั่งผู้ใช้รายนั้น",
        )

    def _spawn(self, fn: Callable) -> None:
        """Run ``fn`` on a throwaway thread and return immediately.

        The thread is intentionally parentless and tracked in
        ``_spawned_threads``: a child QThread would be destroyed along with the
        window during shutdown, and Qt aborts when a running thread is
        destroyed.
        """
        thread = QThread()
        self._spawned_threads.append(thread)
        thread.started.connect(fn)
        thread.finished.connect(lambda: self._retire_thread(thread))
        thread.start()

    def _retire_thread(self, thread: QThread) -> None:
        if thread in self._spawned_threads:
            self._spawned_threads.remove(thread)
        thread.deleteLater()

    def _wait_for_spawned(self, timeout_ms: int = 5000) -> None:
        """Block until every helper thread finishes (or the timeout lapses)."""
        for thread in list(self._spawned_threads):
            if not thread.isFinished():
                thread.quit()
        for thread in list(self._spawned_threads):
            if not thread.wait(timeout_ms):
                self.log_panel.append(
                    "WARN", "มีงานเบื้องหลังที่ยังไม่เสร็จ — กำลังบังคับปิด"
                )
                break
        self._spawned_threads.clear()

    def _set_status(self, text: str, color: str) -> None:
        self.status_pill.setText(text)
        self.status_pill.setStyleSheet(
            f"background-color: {styles.C['surface_hi']};"
            f"color: {color};"
            f"border: 1px solid {styles.C['border_hi']};"
            "border-radius: 11px; padding: 4px 14px;"
            "font-size: 12px; font-weight: 600;"
        )

    def set_busy(self, busy: bool) -> None:
        self.state.busy = busy
        self.btn_connect.setEnabled(not busy)
        self.scan_tab.set_busy(busy)
        self.package_tab.set_busy(busy)
        self.utilities_tab.set_busy(busy)
        if busy:
            self._set_status("กำลังทำงาน...", styles.C["warning"])

    def _require_device_ready(self, allow_disconnected: bool = False) -> bool:
        if self.state.connected and self.state.serial:
            return True
        if allow_disconnected:
            return True
        QMessageBox.information(
            self,
            "ยังไม่ได้เชื่อมต่อมือถือ",
            "กรุณาเชื่อมต่อมือถือก่อน\n\n"
            "1. เสียบสาย USB\n"
            "2. เปิด ตั้งค่า > ตัวเลือกสำหรับนักพัฒนา > การแก้ไขข้อบกพร่อง USB\n"
            "3. กดปุ่ม 'ตรวจหามือถือ'",
        )
        return False

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.log_panel.append("INFO", "กำลังปิดโปรแกรม...")
        self._wait_for_spawned()
        try:
            self.manager.kill_server()
        except Exception:  # noqa: BLE001
            pass
        event.accept()