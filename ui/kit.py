"""
ui.kit
======

The painted design system.

Why this exists
---------------
Qt Style Sheets are a *limited* CSS. They cover backgrounds, borders and simple
radii, but they cannot express:

* a soft, layered drop shadow,
* a linear gradient with a subtle top highlight,
* a pressed state that scales the artwork,
* a springy hover lift,
* crisp vector icons.

The Clean Pro mockup uses all five. Reproducing it with QSS alone produced the
flat, heavy result that shipped in v0.2.0, so every prominent surface is now
drawn with :class:`QPainter` instead.

What is here
------------
``soft_shadow``    layered rounded-rect painter that fakes a Gaussian blur
``gradient_fill``  surface fill with a subtle vertical highlight
``glyph``          registry of vector icons drawn from paths, never emoji
``Surface``        card with shadow, hairline border and optional hover lift
``ActionButton``   primary / secondary / danger button with press animation
``GhostButton``    borderless button used in toolbars
``Pill``           status capsule
``Segmented``      pill-shaped exclusive selector
``SearchBox``      search field with a drawn magnifier
``Divider``        hairline
``StatTile``       icon + caption + value + optional meter
``RowDelegate``    full control over how a table row is painted
``EmptyState``     centred placeholder with a drawn glyph
"""

from __future__ import annotations

import math
from typing import Callable, Sequence

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QObject,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QLineEdit,
    QSizePolicy,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from . import styles

# ---------------------------------------------------------------------------
# Geometry scale
# ---------------------------------------------------------------------------

R_CONTROL = 9
R_CHIP = 12
R_CARD = 16
R_PILL = 999
R_WINDOW = 12

#: Motion tokens. Everything lands inside this band so the app feels like one
#: piece of software rather than a dozen widgets with separate timing.
DUR_HOVER = 150
DUR_PRESS = 110
DUR_THEME = 260


# ---------------------------------------------------------------------------
# Painting primitives
# ---------------------------------------------------------------------------


def soft_shadow(
    painter: QPainter,
    rect: QRectF,
    radius: float,
    *,
    blur: float = 18.0,
    alpha: float = 46,
    dy: float = 4.0,
    colour: str = "#000000",
) -> None:
    """Paint a convincing soft shadow beneath ``rect``.

    Qt offers no cheap blur primitive in ``QPainter``, so the shadow is built
    from a stack of rounded rectangles: outermost first at the lowest alpha,
    tightening onto the shape at the highest. Fourteen layers is enough for an
    edge that reads as blurred at 1× and stays convincing at 200 % scaling, and
    it is far cheaper than a ``QGraphicsDropShadowEffect`` on every card.

    The corner radius stays **constant** across layers and is clamped to half the
    short side. Letting it grow with the spread makes ``addRoundedRect`` fall
    back to pie-slices, which renders as a pointed ellipse rather than a shadow.
    """
    if blur <= 0 or alpha <= 0:
        return

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    base = QColor(colour)
    colour_valid = base.isValid()
    base = base if colour_valid else QColor("#000000")

    layers = 14
    for step in range(layers, 0, -1):
        t = step / layers                       # 1.0 (outer) → 0.07 (inner)
        spread = blur * t
        layer = rect.adjusted(-spread, -spread + dy, spread, spread + dy)

        # Gaussian-ish falloff, flattened so the core stays readable.
        weight = (1.0 - t) ** 1.7
        shade = QColor(base)
        shade.setAlpha(max(0, min(255, round(alpha * weight))))
        if shade.alpha() == 0:
            continue

        corner = max(0.0, min(radius, min(layer.width(), layer.height()) / 2.0))
        path = QPainterPath()
        path.addRoundedRect(layer, corner, corner)
        painter.fillPath(path, shade)

    painter.restore()


def gradient_fill(rect: QRectF, top: str, bottom: str) -> QLinearGradient:
    """Vertical gradient from ``top`` to ``bottom``."""
    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    gradient.setColorAt(0.0, QColor(top))
    gradient.setColorAt(1.0, QColor(bottom))
    return gradient


def hairline(
    painter: QPainter,
    rect: QRectF,
    colour: str,
    width: float = 1.0,
    radius: float | None = None,
) -> None:
    """Draw a one-pixel border inset so it stays crisp on fractional scaling.

    ``radius`` defaults to the standard control radius. Passing half the width
    and height instead would make Qt fall back to pie-slices, which paints a
    stadium outline rather than a rounded rectangle.
    """
    corner = (
        radius
        if radius is not None
        else max(0.0, min(R_CONTROL, min(rect.width(), rect.height()) / 2.0))
    )
    painter.save()
    pen = QPen(QColor(colour))
    pen.setWidthF(width)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    inset = rect.adjusted(width / 2.0, width / 2.0, -width / 2.0, -width / 2.0)
    painter.drawRoundedRect(inset, corner, corner)
    painter.restore()


def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    """A rounded rectangle path with the radius clamped to half the short side."""
    limit = min(rect.width(), rect.height()) / 2.0
    effective = max(0.0, min(radius, limit))
    path = QPainterPath()
    path.addRoundedRect(rect, effective, effective)
    return path


def blend(colour: str, towards: str, amount: float) -> QColor:
    """Mix two hex colours; ``amount`` 0 returns ``colour`` unchanged."""
    a = QColor(colour)
    b = QColor(towards)
    return QColor(
        round(a.red() + (b.red() - a.red()) * amount),
        round(a.green() + (b.green() - a.green()) * amount),
        round(a.blue() + (b.blue() - a.blue()) * amount),
    )


def with_alpha(colour: str, alpha: float) -> QColor:
    result = QColor(colour)
    result.setAlpha(round(max(0.0, min(1.0, alpha)) * 255))
    return result


# ---------------------------------------------------------------------------
# Vector glyphs
# ---------------------------------------------------------------------------
#
# Every icon is drawn inside a normalised 24x24 box so it scales cleanly and
# stays optically consistent next to 13 px Thai text. Emoji were tried first and
# looked wrong at every size — Windows substitutes them inconsistently and they
# ignore the palette.

GlyphFn = Callable[[QPainter, QRectF, QColor], None]


def _stroke(painter: QPainter, colour: QColor, width: float = 1.8) -> QPen:
    pen = QPen(colour)
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _g_scan(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 1.9))
    c = box.center()
    r = box.width() * 0.34
    painter.drawEllipse(c, r, r)
    painter.drawLine(
        QPointF(c.x() - r * 0.42, c.y()),
        QPointF(c.x() + r * 0.42, c.y()),
    )
    painter.drawLine(
        QPointF(c.x(), c.y() - r * 0.42),
        QPointF(c.x(), c.y() + r * 0.42),
    )
    painter.restore()


def _g_live(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 1.9))
    path = QPainterPath()
    path.moveTo(box.left(), box.center().y())
    path.lineTo(box.left() + box.width() * 0.26, box.center().y())
    path.lineTo(box.left() + box.width() * 0.40, box.top() + box.height() * 0.24)
    path.lineTo(box.left() + box.width() * 0.58, box.bottom() - box.height() * 0.24)
    path.lineTo(box.left() + box.width() * 0.72, box.center().y())
    path.lineTo(box.right(), box.center().y())
    painter.drawPath(path)
    painter.restore()


def _g_tools(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 1.8))
    c = box.center()
    r = box.width() * 0.30
    painter.drawEllipse(c, r, r)
    inner = QRectF(c.x() - r * 0.36, c.y() - r * 0.36, r * 0.72, r * 0.72)
    painter.drawEllipse(inner.center(), inner.width() / 2, inner.height() / 2)
    for angle in (0, 90, 180, 270):
        rad = math.radians(angle)
        p1 = QPointF(c.x() + math.cos(rad) * r * 1.18, c.y() + math.sin(rad) * r * 1.18)
        p2 = QPointF(c.x() + math.cos(rad) * r * 1.55, c.y() + math.sin(rad) * r * 1.55)
        painter.drawLine(p1, p2)
    painter.restore()


def _g_database(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 1.8))
    w = box.width() * 0.72
    h = box.height() * 0.72
    left = box.center().x() - w / 2
    top = box.center().y() - h / 2
    ry = h * 0.17
    painter.drawEllipse(QRectF(left, top, w, ry * 2))
    painter.drawArc(QRectF(left, top, w, h), 0, 180 * 16)
    painter.drawArc(QRectF(left, top + h - ry * 2, w, ry * 2), 0, -180 * 16)
    painter.drawLine(QPointF(left, top + ry), QPointF(left, top + h - ry))
    painter.drawLine(QPointF(left + w, top + ry), QPointF(left + w, top + h - ry))
    painter.drawArc(QRectF(left, top + h / 2 - ry * 1.5, w, ry * 3), 0, 180 * 16)
    painter.restore()


def _g_search(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 2.0))
    r = box.width() * 0.30
    c = QPointF(box.center().x() - box.width() * 0.08, box.center().y() - box.height() * 0.08)
    painter.drawEllipse(c, r, r)
    painter.drawLine(
        QPointF(c.x() + r * 0.74, c.y() + r * 0.74),
        QPointF(box.right() - box.width() * 0.10, box.bottom() - box.height() * 0.10),
    )
    painter.restore()


def _g_refresh(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 2.0))
    rect = box.adjusted(box.width() * 0.20, box.height() * 0.20, -box.width() * 0.20, -box.height() * 0.20)
    painter.drawArc(rect, 40 * 16, 280 * 16)
    tip = QPointF(box.center().x() + box.width() * 0.34, box.center().y() - box.height() * 0.24)
    head = QPolygonF([
        QPointF(tip.x(), tip.y() - box.height() * 0.16),
        QPointF(tip.x() + box.width() * 0.17, tip.y() + box.height() * 0.10),
        QPointF(tip.x() - box.width() * 0.04, tip.y() + box.height() * 0.14),
    ])
    painter.setBrush(colour)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPolygon(head)
    painter.restore()


def _g_trash(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 1.8))
    left = box.left() + box.width() * 0.26
    right = box.right() - box.width() * 0.26
    top = box.top() + box.height() * 0.34
    bottom = box.bottom() - box.height() * 0.16
    painter.drawLine(QPointF(left, top), QPointF(right, top))
    painter.drawLine(QPointF(left + box.width() * 0.10, top), QPointF(left + box.width() * 0.13, bottom))
    painter.drawLine(QPointF(right - box.width() * 0.10, top), QPointF(right - box.width() * 0.13, bottom))
    painter.drawLine(QPointF(right - box.width() * 0.13, bottom), QPointF(left + box.width() * 0.13, bottom))
    painter.drawLine(
        QPointF(box.center().x() - box.width() * 0.16, top - box.height() * 0.08),
        QPointF(box.center().x() + box.width() * 0.16, top - box.height() * 0.08),
    )
    painter.restore()


def _g_broom(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 1.8))
    painter.drawLine(
        QPointF(box.left() + box.width() * 0.22, box.bottom() - box.height() * 0.16),
        QPointF(box.right() - box.width() * 0.28, box.top() + box.height() * 0.22),
    )
    head = QPolygonF([
        QPointF(box.left() + box.width() * 0.12, box.bottom() - box.height() * 0.10),
        QPointF(box.left() + box.width() * 0.34, box.bottom() - box.height() * 0.24),
        QPointF(box.left() + box.width() * 0.46, box.bottom() - box.height() * 0.06),
        QPointF(box.left() + box.width() * 0.24, box.bottom() + box.height() * 0.04),
    ])
    painter.setBrush(colour)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPolygon(head)
    painter.restore()


def _g_stop(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    r = box.width() * 0.26
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(colour)
    painter.drawRoundedRect(
        QRectF(
            box.center().x() - r,
            box.center().y() - r,
            r * 2,
            r * 2,
        ),
        r * 0.34,
        r * 0.34,
    )
    painter.restore()


def _g_warning(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 2.0))
    path = QPainterPath()
    path.moveTo(box.center().x(), box.top() + box.height() * 0.12)
    path.lineTo(box.right() - box.width() * 0.12, box.bottom() - box.height() * 0.14)
    path.lineTo(box.left() + box.width() * 0.12, box.bottom() - box.height() * 0.14)
    path.closeSubpath()
    painter.drawPath(path)
    painter.setPen(_stroke(painter, colour, 2.0))
    painter.drawLine(
        QPointF(box.center().x(), box.top() + box.height() * 0.40),
        QPointF(box.center().x(), box.top() + box.height() * 0.64),
    )
    painter.setBrush(colour)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QPointF(box.center().x(), box.top() + box.height() * 0.76), r0(box), r0(box))
    painter.restore()


def r0(box: QRectF) -> float:
    return box.width() * 0.045


def _g_check(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 2.4))
    path = QPainterPath()
    path.moveTo(box.left() + box.width() * 0.22, box.center().y())
    path.lineTo(box.center().x() - box.width() * 0.06, box.bottom() - box.height() * 0.26)
    path.lineTo(box.right() - box.width() * 0.20, box.top() + box.height() * 0.26)
    painter.drawPath(path)
    painter.restore()


def _g_battery(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 1.7))
    body = QRectF(box.left() + box.width() * 0.14, box.top() + box.height() * 0.28,
                 box.width() * 0.62, box.height() * 0.44)
    painter.drawRoundedRect(body, box.width() * 0.08, box.width() * 0.08)
    painter.setBrush(colour)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(
        QRectF(body.left() + body.width() * 0.10, body.top() + body.height() * 0.22,
               body.width() * 0.48, body.height() * 0.56),
        box.width() * 0.03, box.width() * 0.03,
    )
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 1.7))
    painter.drawRoundedRect(
        QRectF(box.right() - box.width() * 0.20, box.center().y() - box.height() * 0.10,
               box.width() * 0.10, box.height() * 0.20),
        box.width() * 0.03, box.width() * 0.03,
    )
    painter.restore()


def _g_storage(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 1.8))
    c = box.center()
    r = box.width() * 0.34
    painter.drawEllipse(c, r * 1.5, r * 0.55)
    painter.drawArc(QRectF(c.x() - r * 1.5, c.y() - r * 0.55, r * 3, r * 1.1), 0, 180 * 16)
    painter.drawArc(QRectF(c.x() - r * 1.5, c.y(), r * 3, r * 1.1), 0, 180 * 16)
    painter.restore()


def _g_phone(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 1.8))
    body = box.adjusted(box.width() * 0.28, box.height() * 0.08,
                        -box.width() * 0.28, -box.height() * 0.08)
    painter.drawRoundedRect(body, box.width() * 0.10, box.width() * 0.10)
    painter.drawLine(
        QPointF(body.center().x(), body.top() + box.height() * 0.06),
        QPointF(body.center().x(), body.top() + box.height() * 0.12),
    )
    painter.drawLine(
        QPointF(body.center().x(), body.bottom() - box.height() * 0.06),
        QPointF(body.center().x(), body.bottom() - box.height() * 0.12),
    )
    painter.restore()


def _g_shield(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 1.8))
    path = QPainterPath()
    path.moveTo(box.center().x(), box.top() + box.height() * 0.10)
    path.lineTo(box.right() - box.width() * 0.18, box.top() + box.height() * 0.28)
    path.lineTo(box.right() - box.width() * 0.18, box.center().y() + box.height() * 0.06)
    path.quadTo(
        box.right() - box.width() * 0.18,
        box.bottom() - box.height() * 0.14,
        box.center().x(),
        box.bottom() - box.height() * 0.08,
    )
    path.quadTo(
        box.left() + box.width() * 0.18,
        box.bottom() - box.height() * 0.14,
        box.left() + box.width() * 0.18,
        box.center().y() + box.height() * 0.06,
    )
    path.lineTo(box.left() + box.width() * 0.18, box.top() + box.height() * 0.28)
    path.closeSubpath()
    painter.drawPath(path)
    painter.restore()


def _g_upload(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 1.9))
    painter.drawLine(
        QPointF(box.center().x(), box.bottom() - box.height() * 0.16),
        QPointF(box.center().x(), box.top() + box.height() * 0.24),
    )
    painter.drawLine(
        QPointF(box.center().x() - box.width() * 0.17, box.top() + box.height() * 0.40),
        QPointF(box.center().x(), box.top() + box.height() * 0.20),
    )
    painter.drawLine(
        QPointF(box.center().x() + box.width() * 0.17, box.top() + box.height() * 0.40),
        QPointF(box.center().x(), box.top() + box.height() * 0.20),
    )
    painter.drawLine(
        QPointF(box.left() + box.width() * 0.20, box.bottom() - box.height() * 0.14),
        QPointF(box.right() - box.width() * 0.20, box.bottom() - box.height() * 0.14),
    )
    painter.restore()


def _g_download(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 1.9))
    painter.drawLine(
        QPointF(box.center().x(), box.top() + box.height() * 0.16),
        QPointF(box.center().x(), box.bottom() - box.height() * 0.30),
    )
    painter.drawLine(
        QPointF(box.center().x() - box.width() * 0.17, box.bottom() - box.height() * 0.46),
        QPointF(box.center().x(), box.bottom() - box.height() * 0.28),
    )
    painter.drawLine(
        QPointF(box.center().x() + box.width() * 0.17, box.bottom() - box.height() * 0.46),
        QPointF(box.center().x(), box.bottom() - box.height() * 0.28),
    )
    painter.drawLine(
        QPointF(box.left() + box.width() * 0.20, box.bottom() - box.height() * 0.14),
        QPointF(box.right() - box.width() * 0.20, box.bottom() - box.height() * 0.14),
    )
    painter.restore()


def _g_sun(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setPen(_stroke(painter, colour, 1.8))
    c = box.center()
    r = box.width() * 0.22
    painter.setBrush(colour)
    painter.drawEllipse(c, r, r)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for index in range(8):
        rad = math.radians(index * 45)
        p1 = QPointF(c.x() + math.cos(rad) * r * 1.5, c.y() + math.sin(rad) * r * 1.5)
        p2 = QPointF(c.x() + math.cos(rad) * r * 2.0, c.y() + math.sin(rad) * r * 2.0)
        painter.drawLine(p1, p2)
    painter.restore()


def _g_moon(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(colour)
    path = QPainterPath()
    path.addEllipse(box.adjusted(box.width() * 0.22, box.height() * 0.16,
                                 -box.width() * 0.22, -box.height() * 0.16))
    path.addEllipse(box.adjusted(box.width() * 0.46, box.height() * 0.02,
                                 -box.width() * 0.02, -box.height() * 0.30))
    path.setFillRule(Qt.FillRule.OddEvenFill)
    painter.drawPath(path)
    painter.restore()


def _g_chevron(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 2.2))
    path = QPainterPath()
    path.moveTo(box.left() + box.width() * 0.34, box.top() + box.height() * 0.22)
    path.lineTo(box.center().x(), box.center().y())
    path.lineTo(box.left() + box.width() * 0.34, box.bottom() - box.height() * 0.22)
    painter.drawPath(path)
    painter.restore()


def _g_plus(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 2.4))
    c = box.center()
    r = box.width() * 0.26
    painter.drawLine(QPointF(c.x() - r, c.y()), QPointF(c.x() + r, c.y()))
    painter.drawLine(QPointF(c.x(), c.y() - r), QPointF(c.x(), c.y() + r))
    painter.restore()


def _g_pencil(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 1.8))
    path = QPainterPath()
    path.moveTo(box.left() + box.width() * 0.22, box.bottom() - box.height() * 0.18)
    path.lineTo(box.left() + box.width() * 0.28, box.bottom() - box.height() * 0.32)
    path.lineTo(box.right() - box.width() * 0.22, box.top() + box.height() * 0.18)
    path.lineTo(box.right() - box.width() * 0.12, box.top() + box.height() * 0.30)
    path.closeSubpath()
    painter.drawPath(path)
    painter.drawLine(
        QPointF(box.left() + box.width() * 0.14, box.bottom() - box.height() * 0.14),
        QPointF(box.left() + box.width() * 0.26, box.bottom() - box.height() * 0.26),
    )
    painter.restore()


def _g_filter(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 1.9))
    path = QPainterPath()
    path.moveTo(box.left() + box.width() * 0.16, box.top() + box.height() * 0.24)
    path.lineTo(box.right() - box.width() * 0.16, box.top() + box.height() * 0.24)
    path.lineTo(box.center().x() + box.width() * 0.08, box.center().y())
    path.lineTo(box.center().x() + box.width() * 0.08, box.bottom() - box.height() * 0.18)
    path.lineTo(box.center().x() - box.width() * 0.08, box.bottom() - box.height() * 0.26)
    path.lineTo(box.center().x() - box.width() * 0.08, box.center().y())
    path.closeSubpath()
    painter.drawPath(path)
    painter.restore()


def _g_lock(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 1.8))
    body = QRectF(box.left() + box.width() * 0.26, box.center().y() - box.height() * 0.04,
                  box.width() * 0.48, box.height() * 0.34)
    painter.drawRoundedRect(body, box.width() * 0.08, box.width() * 0.08)
    arc = QRectF(box.left() + box.width() * 0.33, box.top() + box.height() * 0.16,
                 box.width() * 0.34, box.height() * 0.34)
    painter.drawArc(arc, 0, 180 * 16)
    painter.restore()


def _g_empty(painter: QPainter, box: QRectF, colour: QColor) -> None:
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_stroke(painter, colour, 1.6))
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    r = min(box.width(), box.height()) * 0.34
    painter.drawEllipse(box.center(), r, r)
    painter.restore()


GLYPHS: dict[str, GlyphFn] = {
    "scan": _g_scan,
    "live": _g_live,
    "tools": _g_tools,
    "database": _g_database,
    "search": _g_search,
    "refresh": _g_refresh,
    "trash": _g_trash,
    "broom": _g_broom,
    "stop": _g_stop,
    "warning": _g_warning,
    "check": _g_check,
    "battery": _g_battery,
    "storage": _g_storage,
    "phone": _g_phone,
    "shield": _g_shield,
    "upload": _g_upload,
    "download": _g_download,
    "sun": _g_sun,
    "moon": _g_moon,
    "chevron": _g_chevron,
    "plus": _g_plus,
    "pencil": _g_pencil,
    "filter": _g_filter,
    "lock": _g_lock,
    "empty": _g_empty,
}


def paint_glyph(
    painter: QPainter,
    name: str,
    rect: QRectF,
    colour: str,
    *,
    weight: float = 1.8,
) -> None:
    """Draw the named glyph inside ``rect``.

    Unknown names fall back to a hollow circle rather than drawing nothing, so
    a typo is visible immediately instead of leaving a silent gap.
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    fn = GLYPHS.get(name, _g_empty)
    fn(painter, rect, QColor(colour))
    painter.restore()


class Glyph(QWidget):
    """A fixed-size widget that paints one icon."""

    def __init__(
        self,
        name: str,
        size: int = 18,
        colour: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._name = name
        self._colour = colour
        self.setFixedSize(size, size)

    def set_name(self, name: str) -> None:
        self._name = name
        self.update()

    def set_colour(self, colour: str) -> None:
        self._colour = colour
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        colour = self._colour or styles.current()["text_muted"]
        inset = self.width() * 0.10
        paint_glyph(painter, self._name, QRectF(inset, inset, self.width() - inset * 2, self.height() - inset * 2), colour)
        painter.end()


# ---------------------------------------------------------------------------
# Animated hover state
# ---------------------------------------------------------------------------


def shadow_profile() -> tuple[str, float]:
    """Return ``(colour, alpha_scale)`` suited to the active palette.

    A black shadow reads as depth on a dark window but as a smudge on a light
    one, so the light palette uses a desaturated slate at roughly half strength.
    """
    tokens = styles.current()
    if tokens["name"] == "light":
        return "#4A4A55", 0.55
    return "#000000", 1.0


class _HoverMixin:
    """Shared hover/press animation plumbing.

    The animated value lives in ``self._t`` (0 = idle, 1 = hovered). Each
    concrete widget must additionally declare ``hoverLevel`` as a
    ``Property`` — PySide6 only exposes animatable properties it can see on
    the concrete ``QObject`` subclass, so a property declared here on a plain
    mixin is invisible and ``QPropertyAnimation`` silently refuses to run.

    Subclasses only need::

        hoverLevel = Property(float, _get_hover, _set_hover)
    """

    def _init_hover(self, *, lift: float = 2.0, press: float = 0.97) -> None:
        self._t = 0.0
        self._lift = lift
        self._press = press
        self._pressed = False
        self._hover_anim = QPropertyAnimation(self, b"hoverLevel", self)
        self._hover_anim.setDuration(DUR_HOVER)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _animate_to(self, value: float) -> None:
        self._hover_anim.stop()
        self._hover_anim.setStartValue(float(self._t))
        self._hover_anim.setEndValue(value)
        self._hover_anim.start()

    def _get_hover(self) -> float:
        return self._t

    def _set_hover(self, value: float) -> None:
        self._t = value
        self.update()


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------


class Surface(_HoverMixin, QWidget):
    """A card: soft shadow, gradient fill, hairline border, hover lift.

    ``kind`` selects the token set — ``"card"``, ``"alt"``, ``"sunken"`` or
    ``"accent"`` for the currently selected sidebar row.
    """

    clicked = Signal()

    #: Declared on the concrete subclass so PySide6 can animate it.
    hoverLevel = Property(float, _HoverMixin._get_hover, _HoverMixin._set_hover)

    def __init__(
        self,
        parent: QWidget | None = None,
        kind: str = "card",
        *,
        radius: int = R_CARD,
        hoverable: bool = False,
        padding: int = 16,
    ) -> None:
        super().__init__(parent)
        self.kind = kind
        self._radius = radius
        self._padding = padding
        self._hoverable = hoverable
        self._init_hover(lift=3.0)
        self.setMouseTracking(True)

    def set_padding(self, padding: int) -> None:
        self._padding = padding

    # -- events ------------------------------------------------------------

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._hoverable:
            self._animate_to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._hoverable:
            self._animate_to(0.0)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._hoverable and event.button() == Qt.MouseButton.LeftButton:
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
        super().mouseReleaseEvent(event)

    # -- painting ----------------------------------------------------------

    def _palette(self) -> tuple[str, str, str, str]:
        t = styles.current()
        if self.kind == "alt":
            return t["surface_alt"], t["surface_alt"], t["hairline"], t["border"]
        if self.kind == "sunken":
            return t["surface_sunken"], t["surface_sunken"], t["hairline"], t["hairline"]
        if self.kind == "transparent":
            return "transparent", "transparent", "transparent", "transparent"
        if self.kind == "accent":
            top = blend(t["accent"], "#FFFFFF", 0.10)
            bottom = blend(t["accent"], "#000000", 0.06)
            return top.name(), bottom.name(), with_alpha("#FFFFFF", 0.22).name(), with_alpha("#FFFFFF", 0.14).name()
        return t["surface"], blend(t["surface"], t["surface_alt"], 0.7), t["border"], t["border"]

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        top, bottom, edge, inner = self._palette()
        rect = QRectF(self.rect()).adjusted(1.5, 1.5 - self._lift * self._t, -1.5, -1.5)

        if self.kind not in {"transparent", "accent"}:
            shadow_colour, shadow_scale = shadow_profile()
            soft_shadow(
                painter,
                rect,
                self._radius,
                blur=20.0 if self.kind != "sunken" else 10.0,
                alpha=int((34 if self.kind != "sunken" else 16) * shadow_scale),
                dy=5.0,
                colour=shadow_colour,
            )

        painter.fillPath(rounded_path(rect, self._radius), QColor(top))

        if self.kind == "accent":
            # Inner highlight along the top edge, the detail that makes a flat
            # fill read as a lit surface.
            shine = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + rect.height() * 0.55))
            shine.setColorAt(0.0, with_alpha("#FFFFFF", 0.18))
            shine.setColorAt(1.0, with_alpha("#FFFFFF", 0.0))
            painter.save()
            painter.setClipPath(rounded_path(rect, self._radius))
            painter.fillRect(rect, shine)
            painter.restore()

        if self.kind not in {"sunken", "transparent"} and self._hoverable and self._t > 0.01:
            t = styles.current()
            tint = QColor(t["accent"])
            tint.setAlpha(int(30 * self._t))
            painter.fillPath(rounded_path(rect, self._radius), tint)

        if self.kind != "sunken":
            hairline(painter, rect, inner, radius=self._radius)
        painter.end()


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------


class ActionButton(_HoverMixin, QAbstractButton):
    """A painted button with lift-on-hover and scale-on-press.

    ``kind`` is ``primary``, ``secondary``, ``danger``, ``ghost`` or ``quiet``.
    """

    #: Declared on the concrete subclass so PySide6 can animate it.
    hoverLevel = Property(float, _HoverMixin._get_hover, _HoverMixin._set_hover)

    def __init__(
        self,
        text: str,
        parent: QWidget | None = None,
        *,
        kind: str = "secondary",
        glyph: str = "",
        radius: int = R_CONTROL,
        compact: bool = False,
        padding_h: int | None = None,
    ) -> None:
        super().__init__(parent)
        self._text = text
        self._kind = kind
        self._glyph = glyph
        self._radius = radius
        self._compact = compact
        self._padding_h = padding_h if padding_h is not None else (13 if compact else 17)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._init_hover(lift=1.5, press=0.96)
        self._recompute()

    # -- metrics -----------------------------------------------------------

    def _font(self) -> QFont:
        font = QFont("Segoe UI")
        font.setPointSizeF(9.0 if self._compact else 9.5)
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def _text_width(self) -> float:
        metrics = QFontMetrics(self._font())
        return metrics.horizontalAdvance(self._text)

    def _recompute(self) -> None:
        height = 28 if self._compact else 34
        width = self._padding_h * 2 + self._text_width()
        if self._glyph:
            width += height * 0.72
        self.setFixedSize(int(math.ceil(width)), height)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(self.width(), self.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(self.width(), self.height())

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        self._text = text
        self._recompute()
        self.update()

    def setGlyph(self, glyph: str) -> None:  # noqa: N802 - Qt naming
        self._glyph = glyph
        self._recompute()
        self.update()

    def text(self) -> str:
        return self._text

    def set_kind(self, kind: str) -> None:
        self._kind = kind
        self.update()

    # -- events ------------------------------------------------------------

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._animate_to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._animate_to(0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = False
            self.update()
        super().mouseReleaseEvent(event)

    # -- painting ----------------------------------------------------------

    def _colours(self) -> dict[str, tuple[str, str, str, str]]:
        t = styles.current()
        if not self.isEnabled():
            return {
                "fill_top": t["border"],
                "fill_bottom": t["border"],
                "ink": t["text_faint"],
                "edge": t["hairline"],
            }
        if self._kind == "primary":
            return {
                "fill_top": blend(t["accent"], "#FFFFFF", 0.08).name(),
                "fill_bottom": t["accent"],
                "ink": "#FFFFFF",
                "edge": with_alpha("#FFFFFF", 0.18).name(),
            }
        if self._kind == "danger":
            return {
                "fill_top": blend(t["danger"], "#FFFFFF", 0.08).name(),
                "fill_bottom": t["danger"],
                "ink": "#FFFFFF",
                "edge": with_alpha("#FFFFFF", 0.18).name(),
            }
        if self._kind == "ghost":
            return {
                "fill_top": "#00000000",
                "fill_bottom": "#00000000",
                "ink": t["text_muted"],
                "edge": "transparent",
            }
        if self._kind == "onaccent":
            return {
                "fill_top": "#FFFFFF",
                "fill_bottom": blend("#FFFFFF", t["accent"], 0.10).name(),
                "ink": t["accent_press"],
                "edge": with_alpha("#FFFFFF", 0.30).name(),
            }
        if self._kind == "quiet":
            return {
                "fill_top": t["surface_alt"],
                "fill_bottom": t["surface_alt"],
                "ink": t["text_muted"],
                "edge": t["hairline"],
            }
        return {
            "fill_top": blend(t["surface"], "#FFFFFF", 0.35).name(),
            "fill_bottom": t["surface_alt"],
            "ink": t["text"],
            "edge": t["border"],
        }

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = styles.current()

        colours = self._colours()
        lift = self._lift * self._t if self._kind not in {"ghost", "quiet"} else 0.0
        rect = QRectF(self.rect()).adjusted(1, 1 - lift, -1, -1 - lift)

        filled = self._kind in {"primary", "danger", "onaccent"}
        if filled and self.isEnabled():
            shadow_alpha = int(58 + 40 * self._t)
            shadow_colour = (
                t["accent"] if self._kind == "primary"
                else t["danger"] if self._kind == "danger"
                else "#000000"
            )
            soft_shadow(
                painter, rect, self._radius,
                blur=13.0 + 7.0 * self._t,
                alpha=shadow_alpha if self._kind != "onaccent" else int(34 + 20 * self._t),
                dy=3.0 + 2.0 * self._t,
                colour=shadow_colour,
            )

        if colours["fill_top"] != "#00000000":
            painter.fillPath(
                rounded_path(rect, self._radius),
                gradient_fill(rect, colours["fill_top"], colours["fill_bottom"]),
            )

        if self._kind == "ghost" and self._t > 0.01:
            painter.fillPath(
                rounded_path(rect, self._radius),
                with_alpha(t["text"], 0.06 * self._t),
            )

        if colours["edge"] != "transparent":
            hairline(painter, rect, colours["edge"], radius=self._radius)

        # Press feedback: shrink the artwork slightly around the centre.
        scale = self._press if self._pressed else 1.0
        if scale != 1.0:
            painter.translate(self.width() / 2.0, self.height() / 2.0)
            painter.scale(scale, scale)
            painter.translate(-self.width() / 2.0, -self.height() / 2.0)
            rect = QRectF(self.rect()).adjusted(1, 1 - lift, -1, -1 - lift)

        ink = colours["ink"]
        if self._hoverable_tint_needed():
            ink = styles.current()["accent"]

        font = self._font()
        painter.setFont(font)
        metrics = QFontMetrics(font)
        text_w = self._text_width()
        glyph_w = self.height() * 0.72 if self._glyph else 0.0
        total = text_w + glyph_w
        x = self.width() / 2.0 - total / 2.0

        if self._glyph:
            gsize = self.height() * 0.52
            gy = self.height() / 2.0 - gsize / 2.0
            paint_glyph(painter, self._glyph, QRectF(x, gy, gsize, gsize), ink, weight=2.0)
            x += glyph_w

        if self._text:
            painter.setPen(QColor(ink))
            painter.drawText(
                QRectF(x, 0, text_w + 4, self.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self._text,
            )
        painter.end()

    def _hoverable_tint_needed(self) -> bool:
        return self._kind == "ghost" and self._t > 0.55


class IconButton(_HoverMixin, QAbstractButton):
    """A square painted button used for row actions."""

    #: Declared on the concrete subclass so PySide6 can animate it.
    hoverLevel = Property(float, _HoverMixin._get_hover, _HoverMixin._set_hover)

    def __init__(
        self,
        glyph: str,
        parent: QWidget | None = None,
        *,
        tone: str = "neutral",
        size: int = 30,
    ) -> None:
        super().__init__(parent)
        self._glyph = glyph
        self._tone = tone
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("")
        self._init_hover(lift=0.0, press=0.90)
        self.setEnabled(True)

    def setEnabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._animate_to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._animate_to(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = styles.current()

        if not self.isEnabled():
            ink = with_alpha(t["text_faint"], 0.4).name()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(with_alpha(t["text"], 0.05))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), R_CONTROL, R_CONTROL)
            painter.end()
            return

        active = self._t > 0.02
        if self._tone == "danger":
            fill = with_alpha(t["danger"], 0.10 + 0.14 * self._t)
            ink = t["danger"]
        elif self._tone == "warn":
            fill = with_alpha(t["warning"], 0.12 + 0.14 * self._t)
            ink = t["warning_ink"]
        else:
            fill = with_alpha(t["text"], 0.04 + 0.10 * self._t)
            ink = t["text_muted"] if self._t < 0.5 else t["text"]

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), R_CONTROL, R_CONTROL)
        if active and self._tone == "neutral":
            hairline(painter, QRectF(self.rect()).adjusted(1, 1, -1, -1), t["border"], radius=R_CONTROL)

        inset = self.width() * 0.26
        paint_glyph(
            painter, self._glyph,
            QRectF(inset, inset, self.width() - inset * 2, self.height() - inset * 2),
            ink, weight=2.0,
        )
        painter.end()


# ---------------------------------------------------------------------------
# Pills, dividers, meters
# ---------------------------------------------------------------------------


def paint_pill(
    painter: QPainter,
    rect: QRectF,
    label: str,
    *,
    colour: str,
    background: str,
    font: QFont | None = None,
    dot: bool = False,
) -> None:
    """Draw a capsule with optional leading dot, centred in ``rect``."""
    font = font or QFont("Segoe UI")
    font.setPointSizeF(8.0)
    font.setWeight(QFont.Weight.DemiBold)
    metrics = QFontMetrics(font)

    dot_w = metrics.height() * 0.42 if dot else 0.0
    text_w = metrics.horizontalAdvance(label)
    pad = 11.0
    width = min(text_w + pad * 2 + dot_w, rect.width())
    height = 22.0
    pill = QRectF(
        rect.center().x() - width / 2.0,
        rect.center().y() - height / 2.0,
        width,
        height,
    )

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(background))
    painter.drawRoundedRect(pill, height / 2.0, height / 2.0)

    painter.setFont(font)
    painter.setPen(QColor(colour))
    text_x = pill.left() + pad
    if dot:
        dot_x = pill.left() + pad - 1
        painter.setBrush(QColor(colour))
        painter.drawEllipse(QPointF(dot_x, pill.center().y()), 3.0, 3.0)
        text_x += dot_w + 3
    painter.drawText(
        QRectF(text_x, pill.top(), pill.width(), pill.height()),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        label,
    )
    painter.restore()


def draw_divider(painter: QPainter, rect: QRectF, vertical: bool = False) -> None:
    t = styles.current()
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(t["hairline"]))
    if vertical:
        painter.drawRect(QRectF(rect.center().x(), rect.top(), 1.0, rect.height()))
    else:
        painter.drawRect(QRectF(rect.left(), rect.center().y(), rect.width(), 1.0))
    painter.restore()


def paint_meter(
    painter: QPainter,
    rect: QRectF,
    percent: float,
    colour: str,
    track: str,
) -> None:
    """Draw the thin battery/storage meter used inside stat tiles."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(track))
    painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)

    filled = max(0.0, min(1.0, percent / 100.0)) * rect.width()
    if filled > 0.5:
        painter.setBrush(QColor(colour))
        painter.drawRoundedRect(
            QRectF(rect.left(), rect.top(), max(filled, rect.height()), rect.height()),
            rect.height() / 2.0,
            rect.height() / 2.0,
        )
    painter.restore()


# ---------------------------------------------------------------------------
# Segmented control
# ---------------------------------------------------------------------------


class Segmented(QWidget):
    """Pill-shaped exclusive selector drawn entirely with QPainter."""

    valueChanged = Signal(str)

    def __init__(
        self,
        options: Sequence[tuple[str, str]],
        parent: QWidget | None = None,
        initial: str = "",
    ) -> None:
        super().__init__(parent)
        self._options = list(options)
        self._value = initial or (self._options[0][0] if self._options else "")
        self._hover_index = -1
        self._press_index = -1
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sync()

    # -- metrics -----------------------------------------------------------

    def _font(self) -> QFont:
        font = QFont("Segoe UI")
        font.setPointSizeF(9.0)
        font.setWeight(QFont.Weight.Medium)
        return font

    def _widths(self) -> list[float]:
        metrics = QFontMetrics(self._font())
        return [metrics.horizontalAdvance(label) + 26.0 for _value, label in self._options]

    def _sync(self) -> None:
        widths = self._widths()
        self.setFixedHeight(32)
        self.setFixedSize(int(sum(widths)) + 6, 32)
        self.update()

    def _index_at(self, x: float) -> int:
        widths = self._widths()
        cursor = 3.0
        for index, width in enumerate(widths):
            if cursor <= x <= cursor + width:
                return index
            cursor += width
        return -1

    def _rect_for(self, index: int) -> QRectF:
        widths = self._widths()
        x = 3.0 + sum(widths[:index])
        return QRectF(x, 3.0, widths[index], self.height() - 6.0)

    # -- state -------------------------------------------------------------

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        if value == self._value:
            return
        if all(value != option for option, _label in self._options):
            return
        self._value = value
        self.update()
        self.valueChanged.emit(value)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - Qt naming
        super().setEnabled(enabled)
        self.update()

    # -- events ------------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        index = self._index_at(event.position().x())
        if index != self._hover_index:
            self._hover_index = index
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._hover_index = -1
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_index = self._index_at(event.position().x())
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._index_at(event.position().x())
            if index >= 0 and index == self._press_index:
                value = self._options[index][0]
                if value != self._value:
                    self._value = value
                    self.valueChanged.emit(value)
            self._press_index = -1
            self.update()
        super().mouseReleaseEvent(event)

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = styles.current()

        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.fillPath(rounded_path(outer, 11.0), QColor(t["surface_alt"]))
        hairline(painter, outer, t["border"], radius=11.0)

        for index, (_value, label) in enumerate(self._options):
            cell = self._rect_for(index)
            selected = self._options[index][0] == self._value
            pressed = index == self._press_index

            if selected:
                soft_shadow(
                    painter, cell, 8.0,
                    blur=6.0, alpha=int(28 + 22 * self._hover_index == index), dy=2.0,
                )
                painter.fillPath(
                    rounded_path(cell, 8.0),
                    gradient_fill(cell, t["surface"], blend(t["surface"], t["surface_alt"], 0.5)),
                )
                hairline(painter, cell, t["border"], radius=8.0)
            elif index == self._hover_index:
                painter.fillPath(rounded_path(cell, 8.0), with_alpha(t["text"], 0.05))

            font = self._font()
            if selected:
                font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(t["text"] if selected else t["text_muted"]))
            if not self.isEnabled():
                painter.setPen(QColor(t["text_faint"]))
            if pressed:
                painter.setOpacity(0.6)
            painter.drawText(
                cell, Qt.AlignmentFlag.AlignCenter, label,
            )
            painter.setOpacity(1.0)
        painter.end()


# ---------------------------------------------------------------------------
# Search field
# ---------------------------------------------------------------------------


class SearchBox(QWidget):
    """A search field whose magnifier and focus ring are painted by hand."""

    def __init__(
        self,
        placeholder: str = "ค้นหา…",
        parent: QWidget | None = None,
        *,
        width: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedHeight(34)
        if width:
            self.setMinimumWidth(width)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._placeholder = placeholder
        self._focused = False
        self._hover = False

        self._input = QLineEdit(self)
        self._input.setPlaceholderText(placeholder)
        self._input.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._input.setStyleSheet("background: transparent; border: none; padding: 0;")
        self._input.setClearButtonEnabled(False)
        self._input.installEventFilter(self)

    # -- Qt plumbing -------------------------------------------------------

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        from PySide6.QtCore import QEvent

        if watched is self._input:
            if event.type() == QEvent.Type.FocusIn:
                self._focused = True
                self.update()
            elif event.type() == QEvent.Type.FocusOut:
                self._focused = False
                self.update()
            elif event.type() == QEvent.Type.Enter:
                self._hover = True
                self.update()
            elif event.type() == QEvent.Type.Leave:
                self._hover = False
                self.update()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._input.setGeometry(34, 0, max(self.width() - 46, 10), self.height())
        super().resizeEvent(event)

    # -- api ---------------------------------------------------------------

    def text(self) -> str:
        return self._input.text()

    def setText(self, value: str) -> None:  # noqa: N802 - Qt naming
        self._input.setText(value)

    def on_changed(self, handler) -> None:
        self._input.textChanged.connect(handler)

    def clear(self) -> None:
        self._input.clear()

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = styles.current()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        painter.fillPath(rounded_path(rect, R_CONTROL), QColor(t["surface_alt"]))

        if self._focused:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(with_alpha(t["accent"], 0.16))
            focus = rect.adjusted(-3, -3, 3, 3)
            painter.drawRoundedRect(focus, R_CONTROL + 3, R_CONTROL + 3)
            painter.fillPath(rounded_path(rect, R_CONTROL), QColor(t["surface"]))
            hairline(painter, rect, t["accent"], radius=R_CONTROL)
        else:
            hairline(painter, rect, t["border"] if not self._hover else t["border_strong"], radius=R_CONTROL)

        ink = t["accent"] if self._focused else t["text_faint"]
        paint_glyph(painter, "search", QRectF(11, self.height() / 2 - 7, 14, 14), ink, weight=2.0)

        if self._input.text():
            painter.setPen(QColor(t["text_faint"]))
            font = QFont("Segoe UI")
            font.setPointSizeF(9.5)
            painter.setFont(font)
            metrics = QFontMetrics(font)
            cross = QRectF(self.width() - 26, self.height() / 2 - 7, 14, 14)
            painter.drawLine(cross.topLeft() + QPointF(4, 4), cross.bottomRight() - QPointF(4, 4))
            painter.drawLine(cross.topRight() + QPointF(-4, 4), cross.bottomLeft() + QPointF(4, -4))
            del metrics

        painter.end()


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


class EmptyState(QWidget):
    """A centred placeholder drawn with a glyph rather than a text symbol."""

    def __init__(
        self,
        title: str,
        hint: str = "",
        parent: QWidget | None = None,
        *,
        glyph: str = "empty",
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 40, 24, 40)
        layout.setSpacing(4)
        layout.addStretch(1)

        from PySide6.QtWidgets import QLabel

        holder = QWidget()
        holder.setFixedSize(64, 64)
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 0, 0, 0)
        self._glyph_widget = Glyph(glyph, 52, None, holder)
        self._glyph_widget.setStyleSheet("background: transparent;")
        box.addWidget(self._glyph_widget, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(holder, 0, Qt.AlignmentFlag.AlignCenter)

        if title:
            label = QLabel(title)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet(
                f"color:{styles.current()['text_muted']};font-size:14px;font-weight:600;"
            )
            layout.addWidget(label)

        if hint:
            hint_label = QLabel(hint)
            hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint_label.setWordWrap(True)
            hint_label.setStyleSheet(f"color:{styles.current()['text_faint']};font-size:12px;")
            layout.addWidget(hint_label)

        layout.addStretch(1)

    def refresh(self) -> None:
        t = styles.current()
        self._glyph_widget.set_colour(t["text_faint"])


__all__ = [
    "R_CONTROL", "R_CHIP", "R_CARD", "R_PILL",
    "ActionButton", "Divider", "EmptyState", "Glyph", "IconButton", "SearchBox",
    "Segmented", "Surface", "blend", "draw_divider", "gradient_fill", "hairline",
    "paint_glyph", "paint_meter", "paint_pill", "soft_shadow", "with_alpha",
]


# ---------------------------------------------------------------------------
# Divider
# ---------------------------------------------------------------------------


class Divider(QWidget):
    """A hairline that repaints itself when the theme changes."""

    def __init__(self, parent: QWidget | None = None, vertical: bool = False) -> None:
        super().__init__(parent)
        self.vertical = vertical
        if vertical:
            self.setFixedWidth(1)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        else:
            self.setFixedHeight(1)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        draw_divider(painter, QRectF(self.rect()), self.vertical)
        painter.end()


# ---------------------------------------------------------------------------
# Table row painting
# ---------------------------------------------------------------------------

_ROLE = Qt.ItemDataRole.UserRole + 1
_ROLE_META = Qt.ItemDataRole.UserRole + 2
_ROLE_ICON = Qt.ItemDataRole.UserRole + 3


class RowDelegate(QStyledItemDelegate):
    """Paints an entire table row by hand.

    Qt's own table styling produces the flat, boxed look that v0.2.0 shipped.
    Drawing the row ourselves fixes all of it at once: full-bleed background,
    hairline separators, generous vertical rhythm, a coloured risk bar down the
    leading edge, and a checkbox that animates its tick.

    Each column is described by one of:

    ``"check"``   animated checkbox; payload is the boolean state
    ``"icon"``    launcher icon or generated fallback
    ``"name"``    bold primary line plus a monospace secondary line
    ``"text"``    plain single line
    ``"risk"``    risk capsule
    ``"source"``   install-source capsule with a coloured dot
    ``"actions"`` per-row buttons drawn from the item's payload
    ``"blank"``   spacing only
    """

    def __init__(
        self,
        columns: Sequence[str],
        parent: QWidget | None = None,
        *,
        row_height: int = 50,
        icon_size: int = 34,
        show_header: bool = True,
    ) -> None:
        super().__init__(parent)
        self.columns = list(columns)
        self.row_height = row_height
        self.icon_size = icon_size
        self.show_header = show_header
        self.hovered_row = -1

    def _attach_hover(self, table) -> None:
        """Feed mouse movement in ``table`` into :attr:`hovered_row`."""
        from PySide6.QtCore import QEvent, QPoint

        class _Watcher(QObject):
            def __init__(self, delegate, widget) -> None:
                super().__init__()
                self.delegate = delegate
                self.widget = widget
                widget.installEventFilter(self)

            def eventFilter(self, watched, event) -> bool:  # noqa: N802
                if event.type() == QEvent.Type.MouseMove:
                    index = watched.indexAt(event.position().toPoint()) if hasattr(watched, "indexAt") else None
                    row = index.row() if index is not None and index.isValid() else -1
                    if row != self.delegate.hovered_row:
                        self.delegate.hovered_row = row
                        watched.viewport().update()
                elif event.type() == QEvent.Type.Leave:
                    if self.delegate.hovered_row != -1:
                        self.delegate.hovered_row = -1
                        watched.viewport().update()
                return super().eventFilter(watched, event)

        self._watcher = _Watcher(self, table)

    # -- geometry ----------------------------------------------------------

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt naming
        return QSize(120, self.row_height)

    def headerSizeHint(self, option, section, model):  # noqa: N802 - Qt naming
        return QSize(120, 38)

    # -- painting ----------------------------------------------------------

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        t = styles.current()
        rect = QRectF(option.rect)
        selected = bool(option.state & option.state.__class__.State_Selected)
        hovered = index.row() == self.hovered_row

        # Background — full bleed so the row reads as one surface.
        if selected:
            painter.fillRect(rect, QColor(t["accent_soft"]))
        elif hovered:
            painter.fillRect(rect, QColor(t["surface_alt"]))
        else:
            painter.fillRect(rect, QColor(t["surface"]))

        risk = index.data(_ROLE) or ""
        risk_key = str(risk).strip().title()
        if risk_key in {"Critical", "High", "Medium", "Low"}:
            bar = QRectF(rect.left(), rect.top() + 6, 3.0, rect.height() - 12)
            painter.fillRect(bar, QColor(_risk_colour(t, risk_key)))

        meta = index.data(_ROLE_META) or {}
        kind = self.columns[index.column()] if index.column() < len(self.columns) else "text"
        handler = getattr(self, f"_paint_{kind}", self._paint_text)
        handler(painter, rect, index, meta)

        # Separator, except under the last row.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillRect(
            QRectF(rect.left() + 14, rect.bottom() - 1, rect.width() - 28, 1.0),
            QColor(t["hairline"]),
        )
        painter.restore()

    # -- per-column painters ------------------------------------------------

    def _paint_check(self, painter: QPainter, rect: QRectF, index, meta) -> None:
        t = styles.current()
        size = 18.0
        box = QRectF(
            rect.center().x() - size / 2,
            rect.center().y() - size / 2,
            size,
            size,
        )
        checked = bool(index.data(Qt.ItemDataRole.CheckStateRole))

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        if checked:
            soft_shadow(painter, box, 5.0, blur=6.0, alpha=40, dy=2.0, colour=t["accent"])
            painter.fillPath(
                rounded_path(box, 5.0),
                gradient_fill(box, blend(t["accent"], "#FFFFFF", 0.14).name(), t["accent"]),
            )
            pen = QPen(QColor("#FFFFFF"))
            pen.setWidthF(2.1)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            path = QPainterPath()
            path.moveTo(box.left() + 4.2, box.center().y() + 0.2)
            path.lineTo(box.left() + 7.6, box.bottom() - 5.0)
            path.lineTo(box.right() - 3.8, box.top() + 4.6)
            painter.drawPath(path)
        else:
            painter.setBrush(QColor(t["surface"]))
            painter.drawRoundedRect(box, 5.0, 5.0)
            hairline(painter, box, t["border_strong"], radius=5.0)

    def _paint_icon(self, painter: QPainter, rect: QRectF, index, meta) -> None:
        pixmap = index.data(_ROLE_ICON)
        if not isinstance(pixmap, QPixmap) or pixmap.isNull():
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        target = min(self.icon_size, rect.height() - 12)
        scaled = pixmap.scaled(
            int(target),
            int(target),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawPixmap(
            int(rect.center().x() - scaled.width() / 2),
            int(rect.center().y() - scaled.height() / 2),
            scaled,
        )

    def _paint_name(self, painter: QPainter, rect: QRectF, index, meta) -> None:
        t = styles.current()
        title = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        subtitle = str(meta.get("subtitle", "") or "")

        area = rect.adjusted(0, 8, -8, -8)
        title_font = QFont("Segoe UI")
        title_font.setPointSizeF(9.8)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(t["text"]))

        if subtitle:
            painter.drawText(
                QRectF(area.left(), area.top(), area.width(), area.height() / 2),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, int(area.width())),
            )
            sub_font = QFont("Cascadia Mono")
            sub_font.setPointSizeF(8.0)
            painter.setFont(sub_font)
            painter.setPen(QColor(t["text_faint"]))
            painter.drawText(
                QRectF(area.left(), area.center().y(), area.width(), area.height() / 2),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                painter.fontMetrics().elidedText(subtitle, Qt.TextElideMode.ElideMiddle, int(area.width())),
            )
        else:
            painter.drawText(
                area,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, int(area.width())),
            )

    def _paint_text(self, painter: QPainter, rect: QRectF, index, meta) -> None:
        t = styles.current()
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        colour = meta.get("colour") or t["text_muted"]
        font = QFont("Segoe UI")
        font.setPointSizeF(9.2)
        if meta.get("bold"):
            font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(colour))
        area = rect.adjusted(6, 4, -8, -4)
        painter.drawText(
            area,
            meta.get("align", Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, int(area.width())),
        )

    def _paint_risk(self, painter: QPainter, rect: QRectF, index, meta) -> None:
        t = styles.current()
        risk = str(index.data(_ROLE) or "").strip().title()
        if not risk:
            painter.setPen(QColor(t["text_faint"]))
            font = QFont("Segoe UI")
            font.setPointSizeF(9.0)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "—")
            return
        colour = _risk_colour(t, risk)
        soft = _risk_soft(t, risk)
        ink = _risk_ink(t, risk)
        paint_pill(painter, rect, styles.risk_label(risk), colour=ink, background=soft)

    def _paint_source(self, painter: QPainter, rect: QRectF, index, meta) -> None:
        t = styles.current()
        kind = str(index.data(_ROLE) or "unknown")
        label = str(meta.get("label", "") or "—")
        colour = {
            "play": t["success"],
            "store": t["info_ink"],
            "system": t["text_muted"],
            "side": t["danger"],
            "unknown": t["text_faint"],
        }.get(kind, t["text_faint"])
        paint_pill(
            painter, rect, label,
            colour=colour,
            background=with_alpha(colour, 0.12).name(),
            dot=True,
        )

    def _paint_actions(self, painter: QPainter, rect: QRectF, index, meta) -> None:
        glyphs = meta.get("glyphs") or []
        if not glyphs:
            return
        t = styles.current()
        size = 26.0
        gap = 5.0
        total = len(glyphs) * size + (len(glyphs) - 1) * gap
        x = rect.right() - 12 - total
        y = rect.center().y() - size / 2

        for index_of, spec in enumerate(glyphs):
            box = QRectF(x, y, size, size)
            name = spec[0] if isinstance(spec, (tuple, list)) else spec
            tone = spec[1] if isinstance(spec, (tuple, list)) and len(spec) > 1 else "neutral"
            fill = with_alpha(t["danger"] if tone == "danger" else t["warning"], 0.10)
            ink = t["danger"] if tone == "danger" else t["warning_ink"]

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(box, 7.0, 7.0)
            inset = size * 0.27
            paint_glyph(
                painter, name,
                QRectF(box.left() + inset, box.top() + inset, size - inset * 2, size - inset * 2),
                ink, weight=2.0,
            )
            x += size + gap

    def _paint_blank(self, painter: QPainter, rect: QRectF, index, meta) -> None:
        return


def _risk_colour(tokens: dict, risk: str) -> str:
    return {
        "Critical": tokens["risk_critical"],
        "High": tokens["risk_high"],
        "Medium": tokens["risk_medium"],
        "Low": tokens["risk_low"],
        "Safe": tokens["risk_safe"],
    }.get(risk, tokens["text_muted"])


def _risk_soft(tokens: dict, risk: str) -> str:
    return {
        "Critical": tokens["danger_soft"],
        "High": tokens["warning_soft"],
        "Medium": tokens["warning_soft"],
        "Low": tokens["accent_soft"],
        "Safe": tokens["success_soft"],
    }.get(risk, tokens["neutral_soft"])


def _risk_ink(tokens: dict, risk: str) -> str:
    return {
        "Critical": tokens["danger"],
        "High": tokens["warning_ink"],
        "Medium": tokens["warning_ink"],
        "Low": tokens["info_ink"],
        "Safe": tokens["success"],
    }.get(risk, tokens["text_muted"])


def paint_header(
    painter: QPainter,
    rect: QRectF,
    columns: Sequence[str],
    labels: Sequence[str],
    alignments: Sequence | None = None,
) -> None:
    """Draw a table header row using the same visual language as the body."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    t = styles.current()
    painter.fillRect(rect, QColor(t["surface_alt"]))

    font = QFont("Segoe UI")
    font.setPointSizeF(7.6)
    font.setWeight(QFont.Weight.DemiBold)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
    painter.setFont(font)
    painter.setPen(QColor(t["text_faint"]))

    x = rect.left()
    widths = _column_widths(rect.width(), columns)
    for index, column in enumerate(columns):
        if label := labels[index] if index < len(labels) else "":
            cell = QRectF(x, rect.left() and rect.top() or rect.top(), widths[index], rect.height())
            align = alignments[index] if alignments and index < len(alignments) else Qt.AlignmentFlag.AlignLeft
            area = cell.adjusted(8, 0, -8, 0)
            painter.drawText(
                area, align | Qt.AlignmentFlag.AlignVCenter, label.upper(),
            )
        x += widths[index]

    painter.setPen(Qt.PenStyle.NoPen)
    painter.fillRect(QRectF(rect.left(), rect.bottom() - 1, rect.width(), 1.0), QColor(t["hairline"]))
    painter.restore()


def _column_widths(total: float, columns: Sequence[str]) -> list[float]:
    """Distribute the header width; the name column absorbs the remainder."""
    fixed = {"check": 46.0, "icon": 52.0, "actions": 96.0}
    widths = [fixed.get(column, 120.0) for column in columns]
    remaining = total - sum(widths)
    widths[columns.index("name")] += max(remaining, 160.0) if "name" in columns else 0.0
    if "name" not in columns:
        widths[-1] += max(remaining, 0.0)
    return widths


class HeaderDelegate(QStyledItemDelegate):
    """Feeds :func:`paint_header` from a ``QTableWidget`` header."""

    def __init__(
        self,
        columns: Sequence[str],
        labels: Sequence[str],
        parent: QWidget | None = None,
        alignments: Sequence | None = None,
    ) -> None:
        super().__init__(parent)
        self.columns = list(columns)
        self.labels = list(labels)
        self.alignments = alignments

    def paint(self, painter: QPainter, option, index) -> None:
        rect = QRectF(option.rect)
        section = index.column()
        x = rect.left()
        widths = _column_widths(option.widget.width(), self.columns)
        for position in range(section):
            x += widths[position]
        cell = QRectF(x, rect.top(), widths[section], rect.height())
        paint_header(painter, cell, self.columns, self.labels, self.alignments)

    def sizeHint(self, option, section, model) -> QSize:  # noqa: N802 - Qt naming
        return QSize(120, 38)