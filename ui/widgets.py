"""
ui.widgets
==========

Reusable presentation pieces for the Clean Pro interface.

Everything here is deliberately dumb: widgets know how to *draw* a value, never
what the value means. Decisions about risk, provenance or priority live in
:mod:`core.app_model`; this module only maps them onto pixels.

Contents
--------
* Layout shells — :class:`Card`, :class:`Hairline`, :class:`SectionHeader`
* Status chips — :class:`StatChip`, :class:`RiskPill`, :class:`SourceBadge`
* Inputs — :class:`SegmentedControl`, :class:`SearchField`
* Table plumbing — :class:`PillDelegate`, :class:`IconDelegate`
"""

from __future__ import annotations

from typing import Callable, Iterable, Sequence

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from . import styles

# ---------------------------------------------------------------------------
# Layout shells
# ---------------------------------------------------------------------------


class Card(QFrame):
    """A rounded surface with a hairline border.

    ``variant`` picks the token set: ``"default"``, ``"alt"`` or ``"sunken"``.
    """

    def __init__(self, parent: QWidget | None = None, variant: str = "default") -> None:
        super().__init__(parent)
        names = {"default": "Card", "alt": "CardAlt", "sunken": "Sunken"}
        self.setObjectName(names.get(variant, "Card"))
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)


class Hairline(QFrame):
    """A one-pixel divider."""

    def __init__(self, parent: QWidget | None = None, vertical: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("VLine" if vertical else "Hairline")
        if vertical:
            self.setFixedWidth(1)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        else:
            self.setFixedHeight(1)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class VBox(QWidget):
    """Vertical container with consistent spacing."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 12, margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(*margins)
        self._layout.setSpacing(spacing)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._layout.addWidget(widget, stretch)
        return widget

    #: Qt-flavoured alias so callers can treat this like a real layout.
    def addWidget(self, widget: QWidget, stretch: int = 0) -> QWidget:  # noqa: N802
        return self.add(widget, stretch)

    def add_layout(self, layout) -> None:
        self.addLayout(layout)

    def addLayout(self, layout, stretch: int = 0) -> None:  # noqa: N802
        """Move ``layout`` into this container.

        Qt refuses to add a layout that still belongs to another one, which
        happens easily when a helper row is built once and handed to two
        parents. ``removeItem`` detaches it properly — note that a ``QLayout``
        is *not* a ``QWidget`` and must never be re-parented with
        ``setParent``.
        """
        owner = layout.parent()
        if owner is not None:
            owner.removeItem(layout)
        self._layout.addLayout(layout, stretch)

    def add_stretch(self, stretch: int = 1) -> None:
        self._layout.addStretch(stretch)

    def addStretch(self, stretch: int = 1) -> None:  # noqa: N802
        self._layout.addStretch(stretch)


class HBox(QWidget):
    """Horizontal container with consistent spacing."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 10, margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(*margins)
        self._layout.setSpacing(spacing)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._layout.addWidget(widget, stretch)
        return widget

    #: Qt-flavoured alias so callers can treat this like a real layout.
    def addWidget(self, widget: QWidget, stretch: int = 0) -> QWidget:  # noqa: N802
        return self.add(widget, stretch)

    def add_layout(self, layout) -> None:
        self.addLayout(layout)

    def addLayout(self, layout, stretch: int = 0) -> None:  # noqa: N802
        """Move ``layout`` into this container.

        Qt refuses to add a layout that still belongs to another one, which
        happens easily when a helper row is built once and handed to two
        parents. ``removeItem`` detaches it properly — note that a ``QLayout``
        is *not* a ``QWidget`` and must never be re-parented with
        ``setParent``.
        """
        owner = layout.parent()
        if owner is not None:
            owner.removeItem(layout)
        self._layout.addLayout(layout, stretch)

    def add_stretch(self, stretch: int = 1) -> None:
        self._layout.addStretch(stretch)

    def addStretch(self, stretch: int = 1) -> None:  # noqa: N802
        self._layout.addStretch(stretch)


class SectionHeader(QWidget):
    """A caption above a block of content, with an optional right-hand slot."""

    def __init__(
        self,
        title: str,
        caption: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("SectionTitle")
        column.addWidget(self.title_label)

        self.caption_label = QLabel(caption)
        self.caption_label.setObjectName("SectionCaption")
        self.caption_label.setVisible(bool(caption))
        column.addWidget(self.caption_label)

        row.addLayout(column)
        row.addStretch(1)

        self.actions = HBox(spacing=6)
        row.addWidget(self.actions)

    def set_caption(self, text: str) -> None:
        self.caption_label.setText(text)
        self.caption_label.setVisible(bool(text))


# ---------------------------------------------------------------------------
# Status chips
# ---------------------------------------------------------------------------


class StatChip(QFrame):
    """Icon + caption + value, optionally with a thin progress bar.

    Used for the device strip: model, Android version, battery, storage.
    """

    def __init__(
        self,
        caption: str,
        value: str = "—",
        glyph: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CardAlt")

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 11, 12, 11)
        row.setSpacing(11)

        self.icon_badge = QLabel(glyph)
        self.icon_badge.setFixedSize(34, 34)
        self.icon_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_badge.setStyleSheet(
            "background-color: rgba(0, 113, 227, 0.13);"
            "border-radius: 9px;"
            "font-size: 15px;"
        )
        row.addWidget(self.icon_badge)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)

        self.caption_label = QLabel(caption)
        self.caption_label.setObjectName("FieldLabel")
        column.addWidget(self.caption_label)

        self.value_label = QLabel(value)
        self.value_label.setObjectName("FieldValue")
        column.addWidget(self.value_label)

        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setVisible(False)
        column.addWidget(self.bar)

        row.addLayout(column)
        row.addStretch(1)
        self._column = column

    def set_value(self, text: str, colour: str | None = None) -> None:
        self.value_label.setText(text)
        if colour:
            self.value_label.setStyleSheet(f"color: {colour}; font-size: 13.5px; font-weight: 600;")

    def set_glyph(self, glyph: str) -> None:
        self.icon_badge.setText(glyph)

    def set_progress(self, percent: int | None) -> None:
        """Show or hide the thin bar and fill it to ``percent``."""
        if percent is None:
            self.bar.setVisible(False)
            return
        self.bar.setVisible(True)
        self.bar.setValue(max(0, min(100, int(percent))))

        tokens = styles.current()
        if percent >= 50:
            colour = tokens["success"]
        elif percent >= 20:
            colour = tokens["warning"]
        else:
            colour = tokens["danger"]
        self.bar.setStyleSheet(
            "QProgressBar{background-color:" + tokens["surface_sunken"] + ";"
            "border:none;border-radius:3px;height:5px;max-height:5px;}"
            "QProgressBar::chunk{background-color:" + colour + ";border-radius:3px;}"
        )

    def set_caption_text(self, text: str) -> None:
        self.caption_label.setText(text)


class RiskPill(QLabel):
    """A coloured pill showing a risk level."""

    def __init__(self, risk: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_risk(risk)

    def set_risk(self, risk: str) -> None:
        if not risk:
            self.setText("—")
            self.setStyleSheet(
                "background-color: transparent;"
                "color: " + styles.current()["text_faint"] + ";"
                "font-size: 11.5px;"
                "font-weight: 600;"
            )
            return
        tokens = styles.current()
        colour = styles.risk_color(risk)
        soft = styles.risk_soft(risk)
        ink = styles.risk_ink(risk)
        self.setText(styles.risk_label(risk))
        self.setStyleSheet(
            f"background-color: {soft};"
            f"color: {ink};"
            "border-radius: 100px;"
            "padding: 3px 10px;"
            "font-size: 11.5px;"
            "font-weight: 600;"
        )
        self.setToolTip(f"ระดับความเสี่ยง: {tokens.get('name', '')} {risk}".strip())


class SourceBadge(QLabel):
    """A coloured dot plus the install-source name."""

    def __init__(self, verdict=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_verdict(verdict)

    def set_verdict(self, verdict) -> None:
        tokens = styles.current()
        colour = _source_colour(tokens, verdict)
        label = getattr(verdict, "label", "—") or "—"
        self.setText(f"●  {label}")
        self.setStyleSheet(
            f"color: {colour};"
            "font-size: 12px;"
            "font-weight: 500;"
        )
        if getattr(verdict, "is_suspicious", False):
            self.setToolTip("แอปนี้ไม่ได้มาจากร้านค้าที่เชื่อถือได้")


def _source_colour(tokens: dict, verdict) -> str:
    kind = getattr(verdict, "kind", "unknown")
    return {
        "play": tokens["success"],
        "store": tokens["info_ink"],
        "system": tokens["text_muted"],
        "side": tokens["danger"],
        "unknown": tokens["text_faint"],
    }.get(kind, tokens["text_faint"])


class IconLabel(QLabel):
    """A fixed-size square that shows an app icon or a coloured fallback."""

    def __init__(self, size: int = 34, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._package = ""

    def set_package(self, package: str, icon_store=None) -> None:
        """Render ``package``'s icon, falling back to the generated tile."""
        from .icons import render_fallback_icon

        self._package = package or ""
        if icon_store is not None and self._package:
            pixmap = icon_store.pixmap(self._package, size=self._size)
        else:
            pixmap = render_fallback_icon(self._package, self._size)
        self.setPixmap(pixmap)


class PermissionChip(QLabel):
    """A small monospace chip for one Android permission."""

    def __init__(self, verdict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        tokens = styles.current()
        severity = getattr(verdict, "severity", "normal")
        short = getattr(verdict, "short", "") or "?"
        colour = tokens["risk_critical"] if severity == "critical" else tokens["warning_ink"]
        soft = tokens["danger_soft"] if severity == "critical" else tokens["warning_soft"]
        self.setText(short)
        self.setStyleSheet(
            f"background-color: {soft};"
            f"color: {colour};"
            "border-radius: 6px;"
            "padding: 2px 7px;"
            f'font-family: {styles.FONT_MONO};'
            "font-size: 10.5px;"
            "font-weight: 600;"
        )
        self.setToolTip(getattr(verdict, "name", short))


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


class SearchField(QWidget):
    """A search box with a leading magnifier glyph."""

    def __init__(self, placeholder: str = "ค้นหา…", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.glyph = QLabel("⌕")
        self.glyph.setFixedWidth(30)
        self.glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.glyph.setStyleSheet(
            f"color: {styles.current()['text_faint']};"
            "font-size: 15px;"
            "border: none;"
            "background: transparent;"
        )
        row.addWidget(self.glyph)

        self.input = QLineEdit()
        self.input.setPlaceholderText(placeholder)
        self.input.setClearButtonEnabled(True)
        row.addWidget(self.input, 1)

    def text(self) -> str:
        return self.input.text()

    def setText(self, value: str) -> None:
        self.input.setText(value)

    def on_changed(self, handler: Callable[[str], None]) -> None:
        """Connect ``handler`` to every keystroke in the field."""
        self.input.textChanged.connect(handler)

    def on_return(self, handler: Callable[[], None]) -> None:
        """Connect ``handler`` to Enter being pressed."""
        self.input.returnPressed.connect(handler)


class SegmentedControl(QWidget):
    """A row of mutually exclusive toggles rendered like a pill selector.

    Each option is a checkable :class:`QPushButton` inside an auto-exclusive
    button group, so exactly one is always active.
    """

    valueChanged = Signal(str)

    def __init__(
        self,
        options: Sequence[tuple[str, str]],
        parent: QWidget | None = None,
        initial: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Segment")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(2)

        self._buttons: dict[str, QPushButton] = {}
        for value, label in options:
            button = QPushButton(label)
            button.setObjectName("SegmentButton")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, v=value: self._on_click(v))
            row.addWidget(button)
            self._buttons[value] = button

        if initial and initial in self._buttons:
            self._buttons[initial].setChecked(True)
        elif self._buttons:
            first = next(iter(self._buttons.values()))
            first.setChecked(True)

    def _on_click(self, value: str) -> None:
        self.valueChanged.emit(value)

    def value(self) -> str:
        for key, button in self._buttons.items():
            if button.isChecked():
                return key
        return ""

    def set_value(self, value: str) -> None:
        """Select ``value`` programmatically *and* notify listeners.

        ``setChecked`` alone emits nothing, so a caller changing the filter from
        code — "reset to all before a rescan", say — would leave the table
        showing stale rows. Emitting here keeps the two paths consistent.
        """
        button = self._buttons.get(value)
        if button is None:
            return
        already = button.isChecked()
        button.setChecked(True)
        if not already:
            self.valueChanged.emit(value)

    def set_enabled(self, enabled: bool) -> None:
        for button in self._buttons.values():
            button.setEnabled(enabled)


class MiniBar(QProgressBar):
    """A five-pixel progress bar used inside stat chips."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTextVisible(False)
        self.setRange(0, 100)
        self.setFixedHeight(5)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------


def make_button(
    text: str,
    kind: str = "default",
    small: bool = False,
    tooltip: str = "",
    on_click: Callable[[], None] | None = None,
) -> QPushButton:
    """Build a themed button.

    ``kind`` is one of ``default``, ``primary``, ``danger``, ``ghost`` or
    ``icon``.
    """
    button = QPushButton(text)
    names = {
        "default": "",
        "primary": "Primary",
        "danger": "Danger",
        "ghost": "Ghost",
        "icon": "IconButton",
    }
    name = names.get(kind, "")
    if name:
        button.setObjectName(name)
    if small:
        button.setObjectName((name + " Small").strip())
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setToolTip(tooltip)
    if on_click is not None:
        button.clicked.connect(lambda _checked=False: on_click())
    return button


class NavButton(QPushButton):
    """A sidebar entry with an optional trailing count badge."""

    def __init__(self, glyph: str, label: str, key: str, parent: QWidget | None = None) -> None:
        super().__init__(f"  {glyph}    {label}", parent)
        self.key = key
        self.setObjectName("NavItem")
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(36)

    def set_badge(self, count: int) -> None:
        """Re-label the button with a trailing count, like ``สแกน  3``."""
        base = self.property("baseText") or self.text()
        self.setProperty("baseText", base)
        self.setText(base if count <= 0 else f"{base}   {count}")


# ---------------------------------------------------------------------------
# Table delegates
# ---------------------------------------------------------------------------

_ROLE = Qt.ItemDataRole.UserRole + 1


class PillDelegate(QStyledItemDelegate):
    """Paints rounded pills for risk and install-source columns.

    Storing the payload on the item (via ``Qt.UserRole+1``) keeps sorting and
    filtering working, which is why the app does not embed one widget per cell.
    """

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind

    def paint(self, painter: QPainter, option, index) -> None:
        payload = index.data(_ROLE)
        if not payload:
            super().paint(painter, option, index)
            return

        tokens = styles.current()
        if self.kind == "risk":
            ink = styles.risk_ink(payload)
            soft = styles.risk_soft(payload)
            label = styles.risk_label(payload)
        else:
            ink = _source_colour(tokens, _VerdictShim(payload))
            soft = tokens["surface_sunken"]
            label = str(payload)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = option.rect
        metrics = painter.fontMetrics()
        width = min(metrics.horizontalAdvance(label) + 18, rect.width() - 8)
        height = 22
        pill = QRect(
            rect.left() + 4,
            rect.top() + (rect.height() - height) // 2,
            max(width, 34),
            height,
        )

        path = QPainterPath()
        path.addRoundedRect(pill, 11, 11)
        painter.fillPath(path, QColor(soft))

        if self.kind != "risk":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(ink))
            dot = pill.left() + 11
            centre_y = pill.center().y()
            painter.drawEllipse(QRect(dot - 4, centre_y - 4, 8, 8))

        font = painter.font()
        font.setPointSizeF(max(font.pointSizeF() - 0.5, 7.5))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(ink))
        text_left = pill.left() + (18 if self.kind != "risk" else 9)
        painter.drawText(
            QRect(text_left, pill.top(), pill.width(), pill.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            label,
        )
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        return QSize(120, 32)


class _VerdictShim:
    """Adapts a plain string into something :func:`_source_colour` accepts."""

    __slots__ = ("kind",)

    def __init__(self, kind: str) -> None:
        self.kind = kind


class IconDelegate(QStyledItemDelegate):
    """Renders a rounded app icon stored on the item's ``DecorationRole``."""

    def paint(self, painter: QPainter, option, index) -> None:
        pixmap = index.data(Qt.ItemDataRole.DecorationRole)
        if not isinstance(pixmap, QPixmap) or pixmap.isNull():
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = option.rect
        target = QSize(min(rect.width() - 8, 34), min(rect.height() - 6, 34))
        scaled = pixmap.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawPixmap(
            rect.left() + (rect.width() - scaled.width()) // 2,
            rect.top() + (rect.height() - scaled.height()) // 2,
            scaled,
        )
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        return QSize(46, 46)


class EmptyState(QWidget):
    """A friendly placeholder shown when a list has nothing in it."""

    def __init__(self, glyph: str = "○", title: str = "", hint: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(24, 40, 24, 40)
        column.setSpacing(6)
        column.addStretch(1)

        icon = QLabel(glyph)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"color: {styles.current()['text_faint']}; font-size: 34px;")
        column.addWidget(icon)

        if title:
            label = QLabel(title)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setObjectName("Muted")
            column.addWidget(label)

        if hint:
            hint_label = QLabel(hint)
            hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint_label.setStyleSheet(
                f"color: {styles.current()['text_faint']}; font-size: 11.5px;"
            )
            column.addWidget(hint_label)

        column.addStretch(1)


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


class ClickableFrame(QFrame):
    """A card that reports clicks; used for the utility tiles."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def mouseReleaseEvent(self, event: QEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def elide_middle(text: str, limit: int = 42) -> str:
    """Shorten long package identifiers so columns stay readable."""
    value = str(text or "")
    if len(value) <= limit:
        return value
    head = value[: limit // 2 - 1]
    tail = value[-(limit // 2 - 1):]
    return f"{head}…{tail}"


def pill_role() -> int:
    """The item-data role used to hand payload to :class:`PillDelegate`."""
    return _ROLE