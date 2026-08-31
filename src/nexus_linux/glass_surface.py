"""Optical finishing layer for the Liquid Glass theme.

QSS supplies the translucent body.  This widget adds the details QSS cannot:
a directional specular rim, a dim inner edge, and restrained corner caustics.
They make the surface read as shaped glass while remaining non-interactive.
"""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QRadialGradient
from PySide6.QtWidgets import QWidget

from . import styles


class LiquidGlassSurface(QWidget):
    def _sync_native_shape(self) -> None:
        """Clear legacy per-pixel masks.

        Windows acrylic windows use a layered backing surface; applying a
        QBitmap mask to it can leave the backing surface visible while Qt's
        child widgets are clipped away.  Rounded geometry is therefore owned
        by the native DWM surface and the QSS panel, never by a bitmap mask.
        """
        window = self.window()
        window.clearMask()

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

        painter.setClipPath(outline)
        for center, color in (
            (QPointF(rect.left() + rect.width() * 0.12, rect.top()), QColor(190, 225, 255, 42)),
            (QPointF(rect.right(), rect.top() + rect.height() * 0.22), QColor(255, 255, 255, 25)),
        ):
            glow = QRadialGradient(center, min(rect.width(), rect.height()) * 0.42)
            glow.setColorAt(0, color)
            glow.setColorAt(1, QColor(255, 255, 255, 0))
            painter.fillPath(outline, glow)
