"""
ui.log_panel
============

The activity log dock.

Design notes
------------
* The panel is collapsible. A technician scanning a table wants the whole
  window; the log matters after something goes wrong. A single click on the
  header folds it away.
* Lines are colour-coded by level and timestamped, and the newest line is kept
  in view automatically unless the technician has scrolled up to read something.
* A filter row lets someone isolate errors when talking a customer through what
  happened.

The log is also mirrored to ``%APPDATA%\\ADSremovedPP\\adsremovedpp.log`` so a
crash can be diagnosed after the fact.
"""

from __future__ import annotations

import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import config

from . import styles
from .widgets import Card, SegmentedControl

#: How many lines to keep in memory. Enough to cover a long scan without
#: letting the widget grow without bound.
MAX_LINES = config.LOG_PANEL_MAX_BLOCKS

_LEVEL_TOKENS = {
    "OK": "success",
    "INFO": "accent",
    "WARN": "warning",
    "ERROR": "danger",
}

_LEVEL_GLYPHS = {
    "OK": "✓",
    "INFO": "·",
    "WARN": "!",
    "ERROR": "✕",
}

_FILTERS: tuple[tuple[str, str], ...] = (
    ("all", "ทั้งหมด"),
    ("warn", "เตือน"),
    ("error", "ผิดพลาด"),
)


class LogPanel(QWidget):
    """A collapsible, filterable, file-backed activity log."""

    collapsed_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._collapsed = False
        self._autoscroll = True
        self._filter = "all"
        self._buffer: deque[tuple[str, str]] = deque(maxlen=MAX_LINES)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.card = Card(variant="alt")
        inner = QVBoxLayout(self.card)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)

        self.header = QWidget()
        head_row = QHBoxLayout(self.header)
        head_row.setContentsMargins(14, 9, 12, 9)
        head_row.setSpacing(9)

        self.toggle = QPushButton("▾  บันทึกการทำงาน")
        self.toggle.setObjectName("Ghost")
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setStyleSheet(
            "QPushButton#Ghost{background:transparent;border:none;text-align:left;"
            f'font-family:{styles.FONT_UI};font-size:12.5px;font-weight:600;'
            f"color:{styles.current()['text']};padding:2px 4px;}}"
            "QPushButton#Ghost:hover{background:transparent;"
            f"color:{styles.current()['accent']};}}"
        )
        self.toggle.clicked.connect(self.toggle_collapsed)
        head_row.addWidget(self.toggle)

        self.count_label = QLabel("0 บรรทัด")
        self.count_label.setObjectName("Faint")
        head_row.addWidget(self.count_label)
        head_row.addStretch(1)

        self.filter_control = SegmentedControl(_FILTERS, initial="all")
        self.filter_control.valueChanged.connect(self._on_filter_change)
        head_row.addWidget(self.filter_control)

        self.autoscroll_box = QCheckBox("เลื่อนอัตโนมัติ")
        self.autoscroll_box.setChecked(True)
        self.autoscroll_box.setToolTip("เลื่อนไปที่บรรทัดล่าสุดอัตโนมัติ")
        self.autoscroll_box.toggled.connect(self._on_autoscroll_toggle)
        head_row.addWidget(self.autoscroll_box)

        self.btn_copy = QPushButton("คัดลอก")
        self.btn_copy.setObjectName("Small")
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.clicked.connect(self.copy_all)
        head_row.addWidget(self.btn_copy)

        self.btn_save = QPushButton("บันทึกไฟล์")
        self.btn_save.setObjectName("Small")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.setToolTip(f"บันทึกเป็นไฟล์ข้อความ — {config.log_file()}")
        self.btn_save.clicked.connect(self.save_to_file)
        head_row.addWidget(self.btn_save)

        self.btn_clear = QPushButton("ล้าง")
        self.btn_clear.setObjectName("Small")
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.clicked.connect(self.clear)
        head_row.addWidget(self.btn_clear)

        inner.addWidget(self.header)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(MAX_LINES)
        self.text.setStyleSheet(
            f"QPlainTextEdit{{background-color:{styles.current()['surface_sunken']};"
            f"border:none;border-top:1px solid {styles.current()['hairline']};"
            f'font-family:{styles.FONT_MONO};font-size:11.5px;'
            f"color:{styles.current()['code_text']};padding:10px 14px;}}"
        )
        self.text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        inner.addWidget(self.text)

        root.addWidget(self.card)

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def append(self, level: str, message: str) -> None:
        """Add one line. ``level`` is OK / INFO / WARN / ERROR."""
        normalised = str(level or "INFO").upper()
        if normalised not in _LEVEL_TOKENS:
            normalised = "INFO"
        stamp = time.strftime("%H:%M:%S")
        self._buffer.append((normalised, f"{stamp}  {_LEVEL_GLYPHS[normalised]} {message}"))

        if self._passes_filter(normalised):
            self._write_line(stamp, normalised, message)
        self._refresh_count()

    def append_lines(self, lines: list[tuple[str, str]]) -> None:
        for level, message in lines:
            self.append(level, message)

    def _write_line(self, stamp: str, level: str, message: str) -> None:
        if self._collapsed:
            return
        tokens = styles.current()
        colour = tokens.get(_LEVEL_TOKENS.get(level, "text_muted"), tokens["text_muted"])
        safe = (
            str(message)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        self.text.appendHtml(
            f'<span style="color:{tokens["text_faint"]}">{stamp}</span>&nbsp;&nbsp;'
            f'<span style="color:{colour}">{_LEVEL_GLYPHS.get(level, "·")}</span>&nbsp;'
            f'<span style="color:{tokens["code_text"]}">{safe}</span>'
        )
        if self._autoscroll:
            self.text.moveCursor(QTextCursor.MoveOperation.End)

    def _passes_filter(self, level: str) -> bool:
        if self._filter == "warn":
            return level in {"WARN", "ERROR"}
        if self._filter == "error":
            return level == "ERROR"
        return True

    def _on_filter_change(self, value: str) -> None:
        self._filter = value
        self.text.clear()
        for _level, line in self._buffer:
            self.text.appendPlainText(line)
        if self._autoscroll:
            self.text.moveCursor(QTextCursor.MoveOperation.End)
        self._refresh_count()

    def _on_autoscroll_toggle(self, checked: bool) -> None:
        self._autoscroll = checked
        if checked:
            self.text.moveCursor(QTextCursor.MoveOperation.End)

    def _refresh_count(self) -> None:
        shown = self._filter == "all" or True
        total = len(self._buffer)
        self.count_label.setText(f"{total} บรรทัด" if shown else "")

    def clear(self) -> None:
        self._buffer.clear()
        self.text.clear()
        self._refresh_count()

    # ------------------------------------------------------------------
    # Collapsing
    # ------------------------------------------------------------------

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self.text.setVisible(not collapsed)
        self.toggle.setText("▸  บันทึกการทำงาน" if collapsed else "▾  บันทึกการทำงาน")
        self.collapsed_changed.emit(collapsed)

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def plain_lines(self) -> list[str]:
        return [line for _level, line in self._buffer]

    def copy_all(self) -> None:
        from PySide6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText("\n".join(self.plain_lines()))

    def save_to_file(self, path: Path | None = None) -> Path:
        """Write the buffer to ``path`` (default: the app data log)."""
        target = Path(path) if path else config.log_file()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(self.plain_lines()) + "\n", encoding="utf-8")
        except OSError:
            pass
        return target

    def to_html(self) -> str:
        """A minimal standalone HTML export, handy for sending to a customer."""
        tokens = styles.current()
        lines = []
        for _level, line in self._buffer:
            safe = (
                line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
            lines.append(f"<div>{safe}</div>")
        body = "\n".join(lines)
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>{config.APP_NAME} log</title><style>"
            "body{font-family:'Cascadia Mono',Consolas,monospace;font-size:12px;"
            f"background:{tokens['surface_sunken']};color:{tokens['code_text']};"
            "padding:18px;line-height:1.7;}"
            "</style></head><body>"
            f"<h2>{config.APP_NAME} — activity log</h2>{body}</body></html>"
        )