"""Windows panel material, painted locally without desktop capture or blur.

Use WindowsGlassSurface(parent) in place of LiquidGlassSurface. Only call
set_native_backdrop(True) after the native integration confirms a real backdrop;
False (the default) retains a nearly opaque, high-contrast satin fallback.
The surface owns its fill: do not give it a second QSS background/border.
"""

import os

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

from nexus.ui import styles


def upgrade_surface(window, old):
    """Move an existing layout intact; connections and child identity survive."""
    surface = WindowsGlassSurface(window)
    surface.setObjectName("panelSurface")
    surface.setLayout(old.layout())
    window.layout().replaceWidget(old, surface)
    old.hide()
    old.deleteLater()
    return surface


class WindowsGlassSurface(QWidget):
    """A quiet 22px rounded satin panel; no masks, effects or idle work."""

    RADIUS = 22.0
    SHADOW_INSET = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self._native_backdrop = False
        self._shape_key = None
        self._outline = QPainterPath()
        self._inner_edge = QPainterPath()
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

    def set_native_backdrop(self, enabled: bool) -> None:
        """Adjust tint opacity, not the HWND: native setup belongs to the host."""
        enabled = bool(enabled)
        if enabled != self._native_backdrop:
            self._native_backdrop = enabled
            self.update()

    def _shape(self) -> QRectF:
        key = (self.width(), self.height())
        if key != self._shape_key:
            self._shape_key = key
            self._outline = QPainterPath()
            self._inner_edge = QPainterPath()
            inset = min(self.SHADOW_INSET, max(0, min(self.width(), self.height()) / 4))
            rect = QRectF(self.rect()).adjusted(inset + .5, inset + .5, -inset - .5, -inset - .5)
            if rect.width() > 0 and rect.height() > 0:
                radius = min(self.RADIUS, rect.width() / 2, rect.height() / 2)
                self._outline.addRoundedRect(rect, radius, radius)
                inner = rect.adjusted(1, 1, -1, -1)
                if inner.width() > 0 and inner.height() > 0:
                    self._inner_edge.addRoundedRect(inner, max(0, radius - 1), max(0, radius - 1))
        return self._outline.boundingRect()

    def paint_native_backdrop(self, painter: QPainter) -> None:
        """Paint into the top-level Qt backing store, matching the DWM probe.

        Child-widget alpha was flattened by the Windows Qt backing store even
        though DWM returned S_OK. The isolated non-layered HWND test works when
        its own top-level paint event clears the client and draws the material.
        This method lets the owner use that exact paint/composition order.
        """
        from nexus.ui.design import palette
        tokens = palette()
        # Restore switch: NEXUS_SINGLE_CONTOUR=0 keeps the old inset/shadow
        # rendering without touching code or data. Default is the single native
        # silhouette proven in the DWM probe.
        if os.environ.get("NEXUS_SINGLE_CONTOUR", "1") == "0":
            self._paint_native_backdrop_legacy(painter, tokens)
            return
        # DWM owns the sole border, rounding and shadow. Qt contributes only a
        # full-client material; there is no second rounded body, inset, rim or
        # painted shadow. The native non-client border is set by NativeBackend.
        theme = styles.active_theme()
        theme_key = getattr(theme, "key", "minimalist")
        rect = QRectF(self.pos(), self.size())
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not theme.liquid_glass:
            # Solid themes must remain opaque even over DWM Acrylic.
            if theme_key == "midnight_teal":
                base = QColor("#06151B")
                painter.fillRect(rect, base)
                glow = QRadialGradient(rect.left() + rect.width() * .25,
                                       rect.top() + rect.height() * .12,
                                       max(rect.width(), rect.height()) * .95)
                glow.setColorAt(0, QColor(16, 56, 61, 220))
                glow.setColorAt(.45, QColor(11, 36, 42, 150))
                glow.setColorAt(1, QColor(6, 21, 27, 0))
                painter.fillRect(rect, glow)
                lower = QRadialGradient(rect.right() - rect.width() * .10,
                                        rect.bottom() - rect.height() * .05,
                                        max(rect.width(), rect.height()) * .8)
                lower.setColorAt(0, QColor(32, 191, 181, 42))
                lower.setColorAt(1, QColor(6, 21, 27, 0))
                painter.fillRect(rect, lower)
            elif theme_key == "tech_neon":
                body = QLinearGradient(rect.topLeft(), rect.bottomRight())
                body.setColorAt(0, QColor("#080B12"))
                body.setColorAt(.55, QColor("#0B111B"))
                body.setColorAt(1, QColor("#101722"))
                painter.fillRect(rect, body)
            elif theme_key == "storem":
                body = QLinearGradient(rect.topLeft(), rect.bottomRight())
                body.setColorAt(0, QColor("#080807"))
                body.setColorAt(1, QColor("#12110D"))
                painter.fillRect(rect, body)
            else:
                body = QLinearGradient(rect.topLeft(), rect.bottomRight())
                body.setColorAt(0, QColor("#191919"))
                body.setColorAt(1, QColor("#202020"))
                painter.fillRect(rect, body)
            painter.restore()
            return
        # Liquid Glass over real DWM Acrylic (which does the blur and the base
        # tint). Qt adds only what Acrylic cannot: a flat tint for legibility, a
        # soft reflection that fades within 72 px of the top edge, and one inner
        # highlight line along that edge. No diagonal wash, no second outline.
        from nexus.ui.design import glass
        g = glass()
        painter.fillRect(rect, QColor(g.tint))
        height = min(72.0, rect.height())
        reflection = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.top() + height)
        reflection.setColorAt(0, QColor(g.specular))
        reflection.setColorAt(1, QColor(255, 255, 255, 0))
        painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), height), reflection)
        clear = QColor(g.rim_top); clear.setAlpha(0)
        line = QLinearGradient(rect.left(), 0, rect.right(), 0)
        line.setColorAt(0, clear); line.setColorAt(.10, QColor(g.rim_top))
        line.setColorAt(.90, QColor(g.rim_top)); line.setColorAt(1, clear)
        painter.setPen(QPen(QBrush(line), 1))
        painter.drawLine(QPointF(rect.left(), rect.top() + .5), QPointF(rect.right(), rect.top() + .5))
        painter.restore()

    def _paint_native_backdrop_legacy(self, painter: QPainter, tokens) -> None:
        """Previous inset/shadow renderer, retained only as a restore switch."""
        self._shape()
        if self._outline.isEmpty():
            return
        painter.save()
        painter.translate(self.pos())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for width, alpha in ((14, 7), (10, 12), (5, 24)):
            painter.setPen(QPen(QColor(0, 0, 0, alpha), width))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self._outline)
        base = QColor(tokens.panel)
        top, middle, bottom = QColor(base), QColor(base), QColor(base)
        top = base.lighter(132); top.setAlpha(60)
        middle.setAlpha(42)
        bottom = base.darker(112); bottom.setAlpha(50)
        rect = self._outline.boundingRect()
        body = QLinearGradient(rect.topLeft(), rect.bottomRight())
        body.setColorAt(0, top); body.setColorAt(.46, middle); body.setColorAt(1, bottom)
        painter.fillPath(self._outline, body)
        rim = QLinearGradient(rect.topLeft(), rect.bottomRight())
        rim.setColorAt(0, QColor(239, 246, 255, 55))
        rim.setColorAt(.35, QColor(218, 231, 250, 24))
        rim.setColorAt(1, QColor(210, 224, 244, 14))
        painter.setPen(QPen(rim, 1.0)); painter.drawPath(self._outline)
        painter.setPen(QPen(QColor(0, 0, 0, 55), 1.0)); painter.drawPath(self._inner_edge)
        painter.restore()

    def paintEvent(self, event) -> None:
        # Resolve tokens at paint time so theme changes do not leave stale fills.
        from nexus.ui.design import palette

        tokens = palette()
        rect = self._shape()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        glass = styles.active_theme().liquid_glass
        if self._native_backdrop:
            # DWM owns the single outline in native mode for EVERY theme.
            # The top-level owner paints the full-client material; the child
            # surface must never draw a second inset contour.
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._native_backdrop:
            # This is an alpha clear within a normal, non-layered HWND. DWM's
            # Acrylic remains behind the client area; the screenshot probe is
            # only a verifier, never part of this paint path.
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        # Three bounded pen strokes approximate ambient elevation. No widget
        # graphics effect, blur buffer, bitmap allocation or animation loop.
        for width, alpha in ((14, 7), (10, 12), (5, 24)):
            painter.setPen(QPen(QColor(0, 0, 0, alpha), width))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self._outline)
        base = QColor(tokens.panel)
        if glass:
            # Native mode reveals DWM's Acrylic behind the layout while
            # retaining a quiet brand tint. Fallback is an intentional opaque
            # satin material, never a whole-window opacity trick.
            alpha = 42 if self._native_backdrop else 253
            top = base.lighter(132)
            top.setAlpha(min(255, alpha + (18 if self._native_backdrop else 0)))
            middle = QColor(base)
            middle.setAlpha(alpha)
            bottom = base.darker(112)
            bottom.setAlpha(min(255, alpha + (8 if self._native_backdrop else 2)))
            body = QLinearGradient(rect.topLeft(), rect.bottomRight())
            body.setColorAt(0, top)
            body.setColorAt(0.46, middle)
            body.setColorAt(1, bottom)
            painter.fillPath(self._outline, body)
            rim = QLinearGradient(rect.topLeft(), rect.bottomRight())
            rim.setColorAt(0, QColor(239, 246, 255, 55))
            rim.setColorAt(0.35, QColor(218, 231, 250, 24))
            rim.setColorAt(1, QColor(210, 224, 244, 14))
            painter.setPen(QPen(rim, 1.0))
            painter.drawPath(self._outline)
            painter.setPen(QPen(QColor(0, 0, 0, 55), 1.0))
            painter.drawPath(self._inner_edge)
        else:
            base.setAlpha(255)
            painter.fillPath(self._outline, base)
            painter.setPen(QPen(QColor(tokens.border), 1.0))
            painter.drawPath(self._outline)
        painter.end()
