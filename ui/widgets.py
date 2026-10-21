"""
ui.widgets
==========

Composites built on top of :mod:`ui.kit`.

``kit`` provides the primitives — surfaces, buttons, pills, glyphs, delegates.
This module assembles the handful of larger pieces the pages actually use, and
keeps the names the pages already import so the page code stays readable:

``Card`` / ``Surface``      rounded containers
``StatTile``                icon + caption + value + meter
``SectionHeader``           caption with an action slot
``RiskPill`` / ``SourceBadge``  status capsules
``NavItem``                 sidebar entry with a count badge
``RiskLabel`` / ``MonoLabel``   small text helpers
``PermissionChip``          one permission, severity-coloured

Everything paints through ``QPainter`` so shadows, gradients and motion survive;
no widget here relies on a stylesheet rule for its appearance.
"""

from __future__ import annotations

import math
from typing import Callable, Sequence

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import styles
from .kit import (
    R_CARD,
    R_CHIP,
    R_CONTROL,
    ActionButton,
    Divider,
    EmptyState,
    Glyph,
    HeaderDelegate,
    IconButton,
    RowDelegate,
    SearchBox,
    Segmented,
    Surface,
    blend,
    gradient_fill,
    hairline,
    paint_glyph,
    paint_meter,
    paint_pill,
    rounded_path,
    soft_shadow,
    with_alpha,
)

__all__ = [
    "ActionButton", "Card", "ClickableFrame", "Divider", "EmptyState", "Glyph",
    "HBox", "Hairline", "HeaderDelegate", "IconButton", "IconLabel", "MonoLabel",
    "NavItem", "NavButton", "PermissionChip", "RiskLabel", "RiskPill",
    "RowDelegate", "SearchField", "SearchBox", "SectionHeader", "SegmentedControl",
    "SourceBadge", "SourceLabel", "StatTile", "Surface", "VBox", "make_button",
    "make_icon_button",
]


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------


class HBox(QWidget):
    """Horizontal container with consistent spacing."""

    def __init__(
        self,
        parent: QWidget | None = None,
        spacing: int = 10,
        margins: tuple[int, int, int, int] = (0, 0, 0, 0),
    ) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(*margins)
        self._layout.setSpacing(spacing)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._layout.addWidget(widget, stretch)
        return widget

    def addWidget(self, widget: QWidget, stretch: int = 0) -> QWidget:  # noqa: N802
        return self.add(widget, stretch)

    def add_layout(self, layout) -> None:
        self.addLayout(layout)

    def addLayout(self, layout, stretch: int = 0) -> None:  # noqa: N802
        owner = layout.parent()
        if owner is not None:
            owner.removeItem(layout)
        self._layout.addLayout(layout, stretch)

    def add_stretch(self, stretch: int = 1) -> None:
        self._layout.addStretch(stretch)

    def addStretch(self, stretch: int = 1) -> None:  # noqa: N802
        self._layout.addStretch(stretch)


class VBox(QWidget):
    """Vertical container with consistent spacing."""

    def __init__(
        self,
        parent: QWidget | None = None,
        spacing: int = 12,
        margins: tuple[int, int, int, int] = (0, 0, 0, 0),
    ) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(*margins)
        self._layout.setSpacing(spacing)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._layout.addWidget(widget, stretch)
        return widget

    def addWidget(self, widget: QWidget, stretch: int = 0) -> QWidget:  # noqa: N802
        return self.add(widget, stretch)

    def add_layout(self, layout) -> None:
        self.addLayout(layout)

    def addLayout(self, layout, stretch: int = 0) -> None:  # noqa: N802
        owner = layout.parent()
        if owner is not None:
            owner.removeItem(layout)
        self._layout.addLayout(layout, stretch)

    def add_stretch(self, stretch: int = 1) -> None:
        self._layout.addStretch(stretch)

    def addStretch(self, stretch: int = 1) -> None:  # noqa: N802
        self._layout.addStretch(stretch)


Hairline = Divider
Card = Surface
SegmentedControl = Segmented
NavButton = None  # filled below after NavItem class


class ClickableFrame(QFrame):
    """A flat frame that emits ``clicked`` whenever it is tapped.

    Kept as a thin back-compat shim for screens that have not yet been
    migrated onto the kit-painted :class:`kit.Surface`.  The frame respects the
    active palette via ``setStyleSheet`` from the caller — the kit renders
    fully self-contained surfaces and is the preferred replacement.
    """

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QFrame.Shape.NoFrame)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class SearchField(SearchBox):
    """Alias kept for the pages that predate :class:`kit.SearchBox`."""

    def __init__(self, placeholder: str = "ค้นหา…", parent: QWidget | None = None, **kwargs) -> None:
        super().__init__(placeholder, parent, **kwargs)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _label(text: str, size: float, weight: QFont.Weight, colour: str) -> QLabel:
    label = QLabel(text)
    font = QFont("Segoe UI")
    font.setPointSizeF(size)
    font.setWeight(weight)
    label.setFont(font)
    label.setStyleSheet(f"color:{colour}; background:transparent;")
    return label


class RiskLabel(QLabel):
    """A plain coloured text label, typically a risk name in a caption."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = styles.current()["text"]
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self.setStyleSheet(f"color:{self._tokens}; background:transparent;")

    def setTextWithColor(self, text: str, colour: str) -> None:  # noqa: N802
        self._tokens = colour
        super().setText(text)


class MonoLabel(QLabel):
    """Monospaced label used for package identifiers."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setFont(QFont("Cascadia Mono", 9))
        self.setStyleSheet(
            f"color:{styles.current()['text_faint']}; background:transparent;"
        )


class RiskPill(QLabel):
    """A risk capsule; repaints itself through the kit painter."""

    def __init__(self, risk: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(22)
        self._risk = risk
        self.setMinimumWidth(58)
        self.setMaximumWidth(120)
        self.set_risk(risk)

    def set_risk(self, risk: str) -> None:
        self._risk = risk
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = styles.current()
        if not self._risk:
            painter.setPen(QColor(t["text_faint"]))
            font = QFont("Segoe UI")
            font.setPointSizeF(8.5)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "—")
            painter.end()
            return

        colour = styles.risk_color(self._risk)
        soft = styles.risk_soft(self._risk)
        ink = styles.risk_ink(self._risk)
        paint_pill(painter, QRectF(self.rect()), styles.risk_label(self._risk), colour=ink, background=soft)
        painter.end()


class SourceBadge(QLabel):
    """Install-source capsule with a coloured leading dot."""

    def __init__(self, verdict=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(22)
        self._verdict = verdict
        self.setMinimumWidth(72)
        self.setMaximumWidth(150)

    def set_verdict(self, verdict) -> None:
        self._verdict = verdict
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = styles.current()
        kind = getattr(self._verdict, "kind", "unknown")
        label = getattr(self._verdict, "label", "—") or "—"
        colour = {
            "play": t["success"],
            "store": t["info_ink"],
            "system": t["text_muted"],
            "side": t["danger"],
            "unknown": t["text_faint"],
        }.get(kind, t["text_faint"])

        if getattr(self._verdict, "is_suspicious", False):
            self.setToolTip("แอปนี้ไม่ได้มาจากร้านค้าที่เชื่อถือได้")
        paint_pill(
            painter, QRectF(self.rect()), label,
            colour=colour, background=with_alpha(colour, 0.12), dot=True,
        )
        painter.end()


class SourceLabel(SourceBadge):
    """Alias used by the database page."""


class PermissionChip(QWidget):
    """One requested permission, coloured by severity."""

    def __init__(self, verdict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._verdict = verdict
        self._text = getattr(verdict, "short", "?")
        self._name = getattr(verdict, "name", self._text)

        font = QFont("Cascadia Mono", 8)
        font.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetrics(font)
        self.setFixedHeight(22)
        self.setFixedWidth(metrics.horizontalAdvance(self._text) + 16)
        self.setToolTip(self._name)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = styles.current()
        severity = getattr(self._verdict, "severity", "normal")
        colour = t["risk_critical"] if severity == "critical" else t["warning_ink"]
        soft = t["danger_soft"] if severity == "critical" else t["warning_soft"]

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.fillPath(rounded_path(rect, 6.0), QColor(soft))

        font = QFont("Cascadia Mono", 8)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(colour))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)
        painter.end()


class IconLabel(QWidget):
    """A fixed square that shows an app icon or the generated fallback."""

    def __init__(self, size: int = 34, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self._pixmap = None

    def set_package(self, package: str, icon_store=None) -> None:
        from .icons import render_fallback_icon

        if icon_store is not None and package:
            self._pixmap = icon_store.pixmap(package, size=self._size)
        else:
            self._pixmap = render_fallback_icon(package, self._size)
        self.update()

    def set_pixmap(self, pixmap) -> None:
        self._pixmap = pixmap
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self._pixmap is not None and not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                self._size, self._size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(
                (self.width() - scaled.width()) // 2,
                (self.height() - scaled.height()) // 2,
                scaled,
            )
        painter.end()


# ---------------------------------------------------------------------------
# Composites
# ---------------------------------------------------------------------------


class StatTile(QWidget):
    """Icon + caption + value with an optional meter underneath."""

    def __init__(
        self,
        caption: str,
        value: str = "—",
        glyph: str = "phone",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(11)

        self._badge = _GlyphBadge(glyph)
        layout.addWidget(self._badge)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        self._caption = _label(caption, 8.4, QFont.Weight.Medium, styles.current()["text_faint"])
        self._value = _label(value, 11.0, QFont.Weight.DemiBold, styles.current()["text"])
        self._meter = _Meter()
        column.addWidget(self._caption)
        column.addWidget(self._value)
        column.addWidget(self._meter)
        layout.addLayout(column)
        layout.addStretch(1)
        self._column = column

    def set_value(self, text: str, colour: str | None = None) -> None:
        font = QFont("Segoe UI")
        font.setPointSizeF(11.0)
        font.setWeight(QFont.Weight.DemiBold)
        self._value.setFont(font)
        self._value.setStyleSheet(
            f"color:{colour or styles.current()['text']}; background:transparent;"
        )
        self._value.setText(text)

    def set_glyph(self, glyph: str) -> None:
        self._badge.set_glyph(glyph)

    def set_caption_text(self, text: str) -> None:
        self._caption.setText(text)

    def set_progress(self, percent: int | None) -> None:
        self._meter.set_percent(percent)

    def meter(self) -> "_Meter":
        return self._meter


class _GlyphBadge(QWidget):
    """A rounded tinted square holding one vector glyph."""

    def __init__(self, glyph: str, size: int = 36, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._glyph = glyph
        self.setFixedSize(size, size)

    def set_glyph(self, glyph: str) -> None:
        self._glyph = glyph
        self.update()

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = styles.current()
        tone = getattr(self, "_tone", "accent")
        colour = {
            "accent": t["accent"],
            "success": t["success"],
            "warning": t["warning"],
            "danger": t["danger"],
            "neutral": t["text_muted"],
        }.get(tone, t["accent"])

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.fillPath(rounded_path(rect, 10.0), with_alpha(colour, 0.13))
        inset = self.width() * 0.28
        paint_glyph(
            painter, self._glyph,
            QRectF(inset, inset, self.width() - inset * 2, self.height() - inset * 2),
            colour, weight=2.0,
        )
        painter.end()


class _Meter(QWidget):
    """A thin progress bar used inside stat tiles."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._percent: int | None = None
        self.setFixedHeight(5)
        self.setVisible(False)

    def set_percent(self, percent: int | None) -> None:
        self._percent = percent
        self.setVisible(percent is not None)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._percent is None:
            painter.end()
            return
        t = styles.current()
        colour = (
            t["success"] if self._percent >= 50
            else t["warning"] if self._percent >= 20
            else t["danger"]
        )
        paint_meter(painter, QRectF(self.rect()), self._percent, colour, t["surface_sunken"])
        painter.end()


class SectionHeader(QWidget):
    """A title with an optional caption and a right-hand action slot."""

    def __init__(self, title: str, caption: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)
        self.title_label = _label(title, 12.0, QFont.Weight.Bold, styles.current()["text"])
        self.caption_label = _label(caption, 8.4, QFont.Weight.DemiBold, styles.current()["text_faint"])
        self.caption_label.setVisible(bool(caption))
        column.addWidget(self.title_label)
        column.addWidget(self.caption_label)
        row.addLayout(column)
        row.addStretch(1)

        self.actions = HBox(spacing=6)
        row.addWidget(self.actions)

    def set_caption(self, text: str) -> None:
        self.caption_label.setText(text)
        self.caption_label.setVisible(bool(text))

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)


class NavItem(QWidget):
    """A sidebar row: glyph, label, optional count, animated active state."""

    clicked = Signal()

    def __init__(
        self,
        glyph: str,
        label: str,
        key: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.key = key
        self._glyph = glyph
        self._label = label
        self._badge = 0
        self._active = False
        self._hover = False
        self.setFixedHeight(38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def set_badge(self, count: int) -> None:
        self._badge = max(0, int(count))
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = styles.current()
        rect = QRectF(self.rect()).adjusted(5, 1, -5, -1)

        if self._active:
            painter.fillPath(
                rounded_path(rect, 9.0),
                gradient_fill(
                    rect,
                    blend(t["accent"], "#FFFFFF", 0.06).name(),
                    t["accent"],
                ),
            )
        elif self._hover:
            painter.fillPath(rounded_path(rect, 9.0), with_alpha(t["text"], 0.06))

        ink = "#FFFFFF" if self._active else (t["text"] if self._hover else t["text_muted"])
        inset = 12.0
        paint_glyph(
            painter, self._glyph,
            QRectF(rect.left() + inset, rect.center().y() - 8, 16, 16),
            ink, weight=2.0,
        )

        font = QFont("Segoe UI")
        font.setPointSizeF(10.0)
        font.setWeight(QFont.Weight.DemiBold if self._active else QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(ink))
        text_x = rect.left() + inset + 22
        metrics = QFontMetrics(font)
        available = rect.right() - text_x - (26 if self._badge else 10)
        painter.drawText(
            QRectF(text_x, rect.top(), available, rect.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(self._label, Qt.TextElideMode.ElideRight, int(available)),
        )

        if self._badge:
            badge_rect = QRectF(rect.right() - 24, rect.center().y() - 9, 18, 18)
            painter.fillPath(rounded_path(badge_rect, 9.0), QColor("#FFFFFF") if self._active else QColor(t["danger"]))
            small = QFont("Segoe UI")
            small.setPointSizeF(7.5)
            small.setWeight(QFont.Weight.Bold)
            painter.setFont(small)
            painter.setPen(QColor(t["accent_press"] if self._active else "#FFFFFF"))
            painter.drawText(
                badge_rect, Qt.AlignmentFlag.AlignCenter,
                str(self._badge) if self._badge < 100 else "99+",
            )
        painter.end()


NavButton = NavItem


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


_KIND_ALIASES = {
    "default": "secondary",
    "": "secondary",
    "primary": "primary",
    "danger": "danger",
    "ghost": "ghost",
    "quiet": "quiet",
    "icon": "icon",
    "onaccent": "onaccent",
}


def make_button(
    text: str,
    kind: str = "default",
    small: bool = False,
    tooltip: str = "",
    on_click: Callable[[], None] | None = None,
    *,
    glyph: str = "",
) -> ActionButton | IconButton:
    """Build a themed button.

    ``kind`` is one of ``default``/``secondary``, ``primary``, ``danger``,
    ``ghost``, ``quiet``, ``onaccent`` or ``icon``.
    """
    resolved = _KIND_ALIASES.get(kind, "secondary")

    if resolved == "icon":
        widget: QWidget = IconButton(glyph or "close", tone="danger")
    else:
        button = ActionButton(
            text,
            kind=resolved,
            glyph=glyph,
            compact=small,
        )
        if on_click is not None:
            button.clicked.connect(on_click)
        widget = button

    if tooltip:
        widget.setToolTip(tooltip)
    return widget


def make_icon_button(glyph: str, tone: str = "neutral", tooltip: str = "") -> IconButton:
    button = IconButton(glyph, tone=tone)
    if tooltip:
        button.setToolTip(tooltip)
    return button


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def elide_middle(text: str, limit: int = 42) -> str:
    """Shorten long package identifiers so columns stay readable."""
    value = str(text or "")
    if len(value) <= limit:
        return value
    head = value[: limit // 2 - 1]
    tail = value[-(limit // 2 - 1):]
    return f"{head}…{tail}"


def pill_role() -> int:
    """The item-data role the kit delegates read their payload from."""
    return Qt.ItemDataRole.UserRole + 1


def meta_role() -> int:
    """The item-data role carrying per-cell metadata dictionaries."""
    return Qt.ItemDataRole.UserRole + 2


def icon_role() -> int:
    """The item-data role carrying a ``QPixmap`` launcher icon."""
    return Qt.ItemDataRole.UserRole + 3