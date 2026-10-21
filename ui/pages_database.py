"""
ui.pages_database
=================

The Database Manager — edit the shared blacklist without leaving the app.

Two editing paths, as agreed for v0.2
-------------------------------------
**Local (always available, works offline).** Rows live in a JSON file beside
the executable. Edits apply immediately to this machine and survive a restart.
No token, no network, no account.

**Remote (optional).** The same rows can be committed straight to the GitHub
repository through the Contents API using a Personal Access Token. Everyone
running ADSremovedPP then picks the change up on their next database sync.

The remote path is deliberately opt-in and clearly labelled: a token is a
credential, and pasting one into a tool that pushes JSON into a public repo
should feel like an explicit decision rather than a hidden default.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import config
from core.app_model import DbEntry, normalise_database, risk_rank

from . import styles
from .widgets import (
    Card,
    EmptyState,
    HBox,
    SearchField,
    SectionHeader,
    SegmentedControl,
    make_button,
)

RISK_FILTERS: Sequence[tuple[str, str]] = (
    ("all", "ทุกระดับ"),
    ("Critical", "วิกฤต"),
    ("High", "สูงมาก"),
    ("Medium", "ปานกลาง"),
    ("Low", "ต่ำ"),
)


class EntryDialog(QDialog):
    """Modal form for creating or editing one blacklist row."""

    def __init__(self, entry: DbEntry | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("แก้ไขรายการต้องระวัง" if entry else "เพิ่มรายการต้องระวัง")
        self.setModal(True)
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        form = QFormLayout()
        form.setSpacing(9)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.package_input = QLineEdit(entry.package if entry else "")
        self.package_input.setPlaceholderText("com.example.app")
        form.addRow("แพ็คเกจ *", self.package_input)

        self.name_input = QLineEdit(entry.app_name if entry else "")
        self.name_input.setPlaceholderText("ชื่อที่แสดงให้ช่างเห็น")
        form.addRow("ชื่อแอป", self.name_input)

        self.type_input = QLineEdit(entry.threat_type if entry else "")
        self.type_input.setPlaceholderText("Adware / Junk / PUA / Scam / …")
        form.addRow("ประเภท", self.type_input)

        self.risk_filter = SegmentedControl(
            [(level, level) for level in DbEntry.RISK_LEVELS],
            initial=entry.risk if entry else "Medium",
        )
        form.addRow("ความเสี่ยง *", self.risk_filter)

        self.note_input = QPlainTextEdit(entry.note if entry else "")
        self.note_input.setPlaceholderText("หมายเหตุสำหรับช่าง (ไม่บังคับ)")
        self.note_input.setFixedHeight(64)
        form.addRow("หมายเหตุ", self.note_input)

        layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setObjectName("Danger")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox()
        self.ok_button = buttons.addButton("บันทึก", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("ยกเลิก", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        problem = self.result_entry().validate()
        if problem:
            self.error_label.setText(problem)
            self.error_label.setVisible(True)
            return
        self.error_label.setVisible(False)
        self.accept()

    def result_entry(self) -> DbEntry:
        """Build a :class:`DbEntry` from the form fields."""
        return DbEntry(
            package=self.package_input.text().strip(),
            app_name=self.name_input.text().strip(),
            threat_type=self.type_input.text().strip(),
            risk=self.risk_filter.value(),
            note=self.note_input.toPlainText().strip(),
        )


class DatabasePage(QWidget):
    """Table of blacklist rows with local editing and optional GAS cloud sync.

    The page drives two writes:

    * ``save_local_requested`` — persist the table to ``local_db.json``
      on the technician's machine. Always available, no credentials needed.
    * ``gas_config_changed`` — install a new GAS WebApp URL, reporter name
      and (optionally) admin token. Triggers a worker restart in the main
      window so subsequent uninstall batches report to the new backend.
    """

    # -- outbound ----------------------------------------------------------
    save_local_requested = Signal(list)          # list[dict]
    push_remote_requested = Signal(list)         # list[dict]
    gas_config_changed = Signal(str, str, str)  # url, reporter, admin_token
    status_message = Signal(str, str)            # level, message

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[DbEntry] = []
        self._visible: list[DbEntry] = []
        self._db_path: Path = config.LOCAL_DB_PORTABLE
        self._baseline: list[dict] = []
        self._dirty = False

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
        self._build_toolbar(column)
        self._build_gas_panel(column)
        self._build_table(column)
        self._build_footer(column)
        column.addStretch(1)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_header(self, column: QVBoxLayout) -> None:
        header = SectionHeader("ฐานข้อมูลรายการต้องระวัง", "ยังไม่ได้โหลด")
        column.addWidget(header)
        self.page_header = header

        self.btn_add = make_button(
            "+ เพิ่มรายการ",
            kind="primary",
            small=True,
            tooltip="เพิ่มแพ็คเกจใหม่เข้าฐานข้อมูล",
            on_click=self._add_entry,
        )
        self.btn_edit = make_button(
            "✎ แก้ไข",
            small=True,
            tooltip="แก้ไขรายการที่เลือก",
            on_click=self._edit_entry,
        )
        self.btn_delete = make_button(
            "⌫ ลบ",
            small=True,
            tooltip="ลบรายการที่เลือกออกจากฐานข้อมูล",
            on_click=self._delete_entry,
        )
        self.btn_revert = make_button(
            "↺ คืนค่าเดิม",
            small=True,
            tooltip="ยกเลิกการแก้ไขที่ยังไม่บันทึก",
            on_click=self._revert,
        )
        for button in (self.btn_add, self.btn_edit, self.btn_delete, self.btn_revert):
            header.actions.addWidget(button)

    def _build_toolbar(self, column: QVBoxLayout) -> None:
        row = HBox(spacing=10)

        self.search = SearchField("ค้นหาแพ็คเกจ หรือชื่อแอป…")
        self.search.on_changed(lambda _text: self._apply_filter())
        row.add(self.search, 1)

        self.risk_filter = SegmentedControl(RISK_FILTERS, initial="all")
        self.risk_filter.valueChanged.connect(lambda _value: self._apply_filter())
        row.addWidget(self.risk_filter)

        column.addWidget(row)

    def _build_gas_panel(self, column: QVBoxLayout) -> None:
        """The card holding the GAS URL / reporter / admin token inputs.

        The page never talks to the GAS backend directly — it only collects
        the values, persists them, and emits ``gas_config_changed``. The main
        window rebuilds the worker in response.
        """
        from core import gas_reporter
        from PySide6.QtWidgets import QFormLayout, QLineEdit

        header = SectionHeader(
            "Cloud Sync · Google Apps Script",
            "ตั้งค่า GAS WebApp URL เพื่อส่งรายงานการถอนแอปเข้า backend",
        )
        column.addWidget(header)
        self.gas_header = header

        card = Card()
        outer = QVBoxLayout(card)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        # status pill at the top of the card
        status_row = HBox(spacing=6)
        self.gas_status_label = QLabel("ยังไม่ได้ตั้งค่า")
        self.gas_status_label.setObjectName("Faint")
        status_row.add(self.gas_status_label, 1)

        self.btn_gas_test = make_button(
            "ทดสอบการเชื่อมต่อ",
            kind="ghost",
            small=True,
            tooltip="ยิง ping ไป GAS WebApp URL ที่ตั้งไว้",
            on_click=self._on_gas_test,
        )
        self.btn_gas_save = make_button(
            "บันทึกการตั้งค่า",
            kind="primary",
            small=True,
            tooltip="บันทึก URL + reporter ลงเครื่อง และเปิดใช้งาน background uploader",
            on_click=self._on_gas_save,
        )
        status_row.addWidget(self.btn_gas_test)
        status_row.addWidget(self.btn_gas_save)
        outer.addWidget(status_row)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.gas_url_input = QLineEdit()
        self.gas_url_input.setPlaceholderText(
            "https://script.google.com/macros/s/AKfy.../exec"
        )
        self.gas_url_input.setClearButtonEnabled(True)
        form.addRow("GAS WebApp URL", self.gas_url_input)

        self.gas_reporter_input = QLineEdit()
        self.gas_reporter_input.setPlaceholderText("เช่น สาขาเซ็นทรัลเวิลด์")
        self.gas_reporter_input.setClearButtonEnabled(True)
        form.addRow("ชื่อสาขา / Reporter", self.gas_reporter_input)

        self.gas_admin_input = QLineEdit()
        self.gas_admin_input.setPlaceholderText("(ไม่บังคับ · ใช้เฉพาะถ้า GAS ตั้งค่าไว้)")
        self.gas_admin_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.gas_admin_input.setClearButtonEnabled(True)
        form.addRow("Admin token", self.gas_admin_input)

        outer.addLayout(form)

        hint = QLabel(
            "แอปจะ POST รายการถอนการติดตั้งผ่าน background thread ทันทีที่ uninstall สำเร็จ — "
            "ไม่บล็อก UI และทำงานต่อเมื่อออฟไลน์อยู่ก็จะข้ามไปอย่างเงียบ ๆ"
        )
        hint.setObjectName("Faint")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        column.addWidget(card)

        # hydrate inputs from disk
        cfg = gas_reporter.load_gas_config()
        self.gas_url_input.setText(cfg.url)
        self.gas_reporter_input.setText(cfg.reporter)
        self.gas_admin_input.setText(cfg.admin_token)

    def _on_gas_test(self) -> None:
        """Round-trip a ping to the URL currently typed into the form."""
        from core.gas_reporter import GASClient

        url = self.gas_url_input.text().strip()
        if not url:
            self.gas_status_label.setText("⚠ กรุณากรอก URL ก่อน")
            return
        self.gas_status_label.setText("กำลังทดสอบ...")
        self.gas_status_label.repaint() if hasattr(self.gas_status_label, "repaint") else None
        response = GASClient(url=url, timeout=8).ping()
        if response.ok:
            self.gas_status_label.setText(f"✓ เชื่อมต่อได้ — {response.message}")
        else:
            self.gas_status_label.setText(f"✗ {response.message[:80]}")

    def _on_gas_save(self) -> None:
        """Persist all three fields, then ask the main window to rebuild the worker."""
        from core import gas_reporter

        url = self.gas_url_input.text().strip()
        reporter = self.gas_reporter_input.text().strip() or gas_reporter._default_gas_config().reporter
        admin_token = self.gas_admin_input.text().strip()

        gas_reporter.set_gas_url(url)
        gas_reporter.set_reporter(reporter)
        gas_reporter.set_admin_token(admin_token)

        if url:
            self.gas_status_label.setText(f"✓ บันทึกเรียบร้อย — {url[:48]}...")
            self.status_message.emit(
                "OK",
                f"GAS: ตั้งค่า backend ใหม่ → {url}",
            )
        else:
            self.gas_status_label.setText("ปิดการส่งรายงาน (ไม่มี URL)")

        self.gas_config_changed.emit(url, reporter, admin_token)

    def refresh_gas_status(self) -> None:
        """Pull the persisted config and refresh the status pill on focus."""
        from core import gas_reporter

        cfg = gas_reporter.load_gas_config()
        if cfg.is_configured():
            masked = cfg.url[:48] + "..." if len(cfg.url) > 50 else cfg.url
            self.gas_status_label.setText(f"✓ พร้อมส่ง → {masked}")
        else:
            self.gas_status_label.setText("ยังไม่ได้ตั้งค่า — รายงานจะไม่ถูกส่ง")

    def _build_table(self, column: QVBoxLayout) -> None:
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["แพ็คเกจ", "ชื่อแอป", "ประเภท", "ความเสี่ยง", "หมายเหตุ"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)

        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        self.table.itemSelectionChanged.connect(self._sync_buttons)
        self.table.itemDoubleClicked.connect(lambda _item: self._edit_entry())
        column.addWidget(self.table)

        self.empty_state = EmptyState(
            "ฐานข้อมูลยังว่าง",
            "กด «เพิ่มรายการ» เพื่อเริ่มสร้างรายการต้องระวังของคุณเอง",
            glyph="database",
        )
        column.addWidget(self.empty_state)

    def _build_footer(self, column: QVBoxLayout) -> None:
        card = Card(kind="alt")
        column_widget = QVBoxLayout(card)
        column_widget.setContentsMargins(14, 13, 14, 14)
        column_widget.setSpacing(10)

        self.path_label = QLabel("")
        self.path_label.setObjectName("Mono")
        self.path_label.setWordWrap(True)
        self.path_label.setToolTip(str(self._db_path))
        column_widget.addWidget(self.path_label)

        self.dirty_label = QLabel("")
        self.dirty_label.setObjectName("Muted")
        column_widget.addWidget(self.dirty_label)

        row = HBox(spacing=9)
        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        row.add(self.status_label, 1)

        self.btn_save = make_button(
            "✓ บันทึกในเครื่อง",
            kind="primary",
            tooltip="เขียนรายการลงไฟล์ JSON บนเครื่องนี้ (ทำงานได้แม้ออฟไลน์)",
            on_click=self._save_local,
        )
        self.btn_push = make_button(
            "↑ ส่งขึ้น GitHub",
            tooltip="commit รายการเข้า repo เพื่อให้ทุกเครื่องได้รับข้อมูลใหม่",
            on_click=self._push_remote,
        )
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_push)
        column_widget.addWidget(row)

        column.addWidget(card)
        self._sync_buttons()

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def load(self, rows: Sequence[dict], origin: str = "local") -> None:
        """Replace the table with ``rows`` and treat them as the clean baseline."""
        self._rows = []
        seen: set[str] = set()
        for raw in rows or []:
            entry = DbEntry.from_dict(raw)
            if entry is None or entry.package in seen:
                continue
            seen.add(entry.package)
            self._rows.append(entry)
        self._baseline = [entry.to_dict() for entry in self._rows]
        self._apply_filter()
        self._dirty = False
        self._sync_buttons()
        origin_label = {"remote": "GitHub", "local": "เครื่อง"}.get(origin, origin)
        self.page_header.set_caption(
            f"{len(self._rows)} รายการ · ต้นทาง: {origin_label} · แก้ไขในเครื่องจะไม่กระทบของผู้อื่น"
        )

    def set_path(self, path: Path) -> None:
        self._db_path = Path(path)
        self.path_label.setText(f"ไฟล์: {self._db_path}")

    def _apply_filter(self) -> None:
        needle = self.search.text().strip().lower()
        risk = self.risk_filter.value()

        self._visible = [
            entry
            for entry in self._rows
            if (
                not needle
                or needle in entry.package.lower()
                or needle in entry.app_name.lower()
                or needle in entry.threat_type.lower()
            )
            and (risk == "all" or entry.risk == risk)
        ]

        self._visible.sort(key=lambda item: (risk_rank(item.risk), item.package.lower()))

        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._visible))
        for row, entry in enumerate(self._visible):
            self.table.setItem(row, 0, _cell(entry.package, mono=True))
            self.table.setItem(row, 1, _cell(entry.app_name or "—"))
            self.table.setItem(row, 2, _cell(entry.threat_type or "—"))
            self.table.setItem(row, 3, _cell(styles.risk_label(entry.risk), colour=styles.risk_ink(entry.risk)))
            self.table.setItem(row, 4, _cell(entry.note or "—"))
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)

        self.table.setVisible(bool(self._visible))
        self.empty_state.setVisible(not self._visible)
        self._sync_buttons()

    def is_dirty(self) -> bool:
        return self._dirty

    def refresh_theme(self) -> None:
        """Re-apply palette colours after a theme switch."""
        self._apply_filter()

    def _sync_buttons(self) -> None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        has_selection = bool(rows)
        self.btn_edit.setEnabled(has_selection)
        self.btn_delete.setEnabled(has_selection)
        self.btn_save.setEnabled(self._dirty)
        self.btn_push.setEnabled(self._dirty)
        self.btn_revert.setEnabled(self._dirty)

        if self._dirty:
            count = len({e.to_dict()["package"] for e in self._rows} ^ {d["package"] for d in self._baseline})
            self.dirty_label.setText(
                f"มีการแก้ไขที่ยังไม่บันทึก — {count} รายการเปลี่ยนแปลง"
            )
            self.dirty_label.setStyleSheet(
                f"color: {styles.current()['warning_ink']}; font-size: 12.5px; font-weight: 600;"
            )
        else:
            self.dirty_label.setText("ไม่มีการแก้ไขที่ยังไม่บันทึก")
            self.dirty_label.setStyleSheet("")

    def _set_dirty(self, value: bool) -> None:
        self._dirty = value
        self._sync_buttons()

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def _selected(self) -> DbEntry | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        position = rows[0].row()
        if 0 <= position < len(self._visible):
            return self._visible[position]
        return None

    def _add_entry(self) -> None:
        dialog = EntryDialog(None, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        entry = dialog.result_entry()
        if any(existing.package == entry.package for existing in self._rows):
            QMessageBox.warning(
                self,
                "มีอยู่แล้ว",
                f"แพ็คเกจ {entry.package} อยู่ในฐานข้อมูลอยู่แล้ว",
            )
            return
        self._rows.append(entry)
        self._set_dirty(True)
        self._apply_filter()

    def _edit_entry(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        draft = entry.clone()
        dialog = EntryDialog(draft, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.result_entry()

        clash = any(
            other.package == updated.package and other.package != entry.package
            for other in self._rows
        )
        if clash:
            QMessageBox.warning(
                self,
                "มีอยู่แล้ว",
                f"แพ็คเกจ {updated.package} อยู่ในฐานข้อมูลอยู่แล้ว",
            )
            return

        entry.package = updated.package
        entry.app_name = updated.app_name
        entry.threat_type = updated.threat_type
        entry.risk = updated.risk
        entry.note = updated.note
        self._set_dirty(True)
        self._apply_filter()

    def _delete_entry(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        answer = QMessageBox.question(
            self,
            "ยืนยันการลบ",
            f"ลบ «{entry.app_name or entry.package}» ออกจากฐานข้อมูล?\n\n"
            "การลบจะมีผลเมื่อบันทึกเท่านั้น",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._rows = [item for item in self._rows if item.package != entry.package]
        self._set_dirty(True)
        self._apply_filter()

    def _revert(self) -> None:
        self._rows = [DbEntry.from_dict(raw) or DbEntry() for raw in self._baseline]
        self._rows = [entry for entry in self._rows if entry.package]
        self._set_dirty(False)
        self._apply_filter()
        self.status_message.emit("INFO", "ยกเลิกการแก้ไขแล้ว — กลับไปใช้ข้อมูลเดิม")

    # ------------------------------------------------------------------
    # Saving
    # ------------------------------------------------------------------

    def _serialise(self) -> list[dict]:
        return normalise_database(entry.to_dict() for entry in self._rows)

    def _save_local(self) -> None:
        payload = self._serialise()
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            self.status_message.emit("ERROR", f"บันทึกไฟล์ไม่สำเร็จ: {exc}")
            return

        self._baseline = payload
        self._set_dirty(False)
        self.status_message.emit(
            "OK",
            f"บันทึกในเครื่องแล้ว — {len(payload)} รายการ (มีผลกับเครื่องนี้เท่านั้น)",
        )
        self.save_local_requested.emit(payload)

    def _push_remote(self) -> None:
        payload = self._serialise()
        answer = QMessageBox.question(
            self,
            "ส่งขึ้น GitHub",
            f"จะส่งรายการ {len(payload)} รายการขึ้น repository\n"
            f"{config.GITHUB_OWNER}/{config.GITHUB_REPO}\n\n"
            "ทุกเครื่องที่ใช้ ADSremovedPP จะได้รับข้อมูลชุดนี้ในการซิงก์ครั้งถัดไป\n\n"
            "ต้องตั้งค่า Personal Access Token (สิทธิ์ repo) ก่อน\n\n"
            "ดำเนินการต่อหรือไม่?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.push_remote_requested.emit(payload)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _cell(text: str, *, mono: bool = False, colour: str | None = None) -> QTableWidgetItem:
    """Build a read-only table cell with optional monospace and colour."""
    from PySide6.QtGui import QBrush, QFont

    item = QTableWidgetItem(str(text))
    if mono:
        font = QFont(styles.FONT_MONO.split(",")[0].strip('"'))
        font.setPointSizeF(8.5)
        item.setFont(font)
    if colour:
        item.setForeground(QBrush(colour))
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    return item