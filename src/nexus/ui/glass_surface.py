"""Optical finishing layer for the Liquid Glass theme.

QSS supplies the translucent body.  This widget adds the details QSS cannot:
a directional specular rim, a dim inner edge, and restrained corner caustics.
They make the surface read as shaped glass while remaining non-interactive.
"""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QRadialGradient
from PySide6.QtWidgets import QWidget

from nexus.ui import styles


class LiquidGlassSurface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # This is a QWidget subclass, so opt in explicitly to stylesheet
        # background painting.  Without it, non-Liquid themes lose their
        # panel fill/radius while Liquid Glass happens to work via paintEvent.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def _sync_native_shape(self) -> None:
        # Never apply a window mask/region to a translucent Qt surface.  Both
        # mechanisms can desynchronize Windows' backing layer from Qt child
        # painting. The rounded panel itself is rendered by QSS.
        self.window().clearMask()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_native_shape()

    def showEvent(self, event) -> None:
        super().showEvent(event)
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
        # The main material is intentionally painted here rather than via
        # QSS: on a translucent Windows HWND, QSS's rgba gradient can become
        # far more transparent than specified and destroy text contrast.
        body = QLinearGradient(rect.topLeft(), rect.bottomRight())
        body.setColorAt(0.00, QColor(40, 51, 70, 248))
        body.setColorAt(0.18, QColor(29, 37, 53, 250))
        body.setColorAt(0.58, QColor(19, 25, 39, 252))
        body.setColorAt(1.00, QColor(7, 12, 23, 254))
        painter.fillPath(outline, body)
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
