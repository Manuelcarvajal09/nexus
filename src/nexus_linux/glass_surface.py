"""Optical finishing layer for the Liquid Glass theme.

QSS supplies the translucent body.  This widget adds the details QSS cannot:
a directional specular rim, a dim inner edge, and restrained corner caustics.
They make the surface read as shaped glass while remaining non-interactive.
"""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QBitmap, QImage, QPainter, QPainterPath, QRadialGradient, QRegion
from PySide6.QtWidgets import QWidget

from . import styles


class LiquidGlassSurface(QWidget):
    def _sync_native_shape(self) -> None:
        window = self.window()
        if not styles.active_theme().liquid_glass:
            window.clearMask()
            return
        # A raster alpha mask avoids the pointed/aliased corners produced by
        # converting a sub-pixel QPainterPath directly to a polygon region.
        image = QImage(self.size(), QImage.Format.Format_Mono)
        image.fill(0)
        mask_painter = QPainter(image)
        mask_painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        mask_painter.setBrush(Qt.GlobalColor.white)
        mask_painter.setPen(Qt.PenStyle.NoPen)
        radius = float(styles.active_theme().radius_panel)
        mask_painter.drawRoundedRect(QRectF(self.rect()).adjusted(0, 0, -1, -1), radius, radius)
        mask_painter.end()
        offset = self.mapTo(window, self.rect().topLeft())
        region = QRegion(QBitmap.fromImage(image))
        region.translate(offset)
        window.setMask(region)

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
