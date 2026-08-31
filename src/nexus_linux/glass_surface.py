"""Optical finishing layer for the Liquid Glass theme.

QSS supplies the translucent body.  This widget adds the details QSS cannot:
a directional specular rim, a dim inner edge, and restrained corner caustics.
They make the surface read as shaped glass while remaining non-interactive.
"""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPolygon, QRadialGradient, QRegion
from PySide6.QtWidgets import QWidget

from . import styles


class LiquidGlassSurface(QWidget):
    def _sync_native_shape(self) -> None:
        window = self.window()
        if not styles.active_theme().liquid_glass:
            window.clearMask()
            return
        local_rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        radius = float(styles.active_theme().radius_panel)
        path.addRoundedRect(local_rect, radius, radius)
        polygon = QPolygon(path.toFillPolygon().toPolygon())
        offset = self.mapTo(window, self.rect().topLeft())
        polygon.translate(offset)
        window.setMask(QRegion(polygon))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_native_shape()

    def paintEvent(self, event) -> None:
        self._sync_native_shape()
        super().paintEvent(event)
        theme = styles.active_theme()
        if not theme.liquid_glass:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        radius = float(theme.radius_panel - 1)
        outline = QPainterPath()
        outline.addRoundedRect(rect, radius, radius)
        painter.setClipPath(outline)

        # A glancing highlight across the upper/left rim and a shadow on the
        # opposing edge imply thickness and refraction, not a flat alpha pane.
        rim = QLinearGradient(rect.topLeft(), rect.bottomRight())
        rim.setColorAt(0.00, QColor(255, 255, 255, 210))
        rim.setColorAt(0.18, QColor(255, 255, 255, 88))
        rim.setColorAt(0.55, QColor(160, 205, 255, 30))
        rim.setColorAt(1.00, QColor(0, 0, 0, 105))
        painter.setClipping(False)
        painter.setPen(QPen(rim, 1.7))
        painter.drawPath(outline)

        painter.setClipPath(outline)
        for center, color in (
            (QPointF(rect.left() + rect.width() * 0.12, rect.top()), QColor(190, 225, 255, 42)),
            (QPointF(rect.right(), rect.top() + rect.height() * 0.22), QColor(255, 255, 255, 25)),
        ):
            glow = QRadialGradient(center, min(rect.width(), rect.height()) * 0.42)
            glow.setColorAt(0, color)
            glow.setColorAt(1, QColor(255, 255, 255, 0))
            painter.fillPath(outline, glow)
