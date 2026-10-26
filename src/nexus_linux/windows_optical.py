"""Optical layer of Liquid Glass (prototype C, "Hybrid").

The panel keeps DWM Desktop Acrylic (live blur, native border and shadow). On top of it,
the *controls* (search, style button, close, pinboard row) and the clip *cards* get what
Acrylic cannot give: an edge lens, thickness, a lit rim and per-backdrop contrast.

Windows exposes no per-pixel access to the compositor, so the lens is an approximation:
the pixels behind the window are captured ONCE, right before it is shown (so Nexus itself
is not in them), blurred, and kept only as two small in-memory plates. Nothing is written
to disk or logged, and the plates are dropped when the window hides. Where the capture is
unavailable (disabled, no native backdrop, other themes, hidden) nothing here paints and
the previous material is used unchanged.

Paint order is the window's: material first (see windows_material), then paint_controls(),
then the child widgets, whose own background is transparent while this layer is active
(dynamic property ``opticalGlass``).
"""

from __future__ import annotations

import math
import os

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen, QRadialGradient)

from . import windows_design

LIGHT_ANGLE = 125.0          # degrees; the lamp is up and to the left
_user_enabled = True


def set_enabled(value: bool) -> None:
    """The user's choice (Configuración). The environment can only switch it off."""
    global _user_enabled
    _user_enabled = bool(value)


def enabled() -> bool:
    return _user_enabled and os.environ.get("NEXUS_GLASS_OPTICAL", "1").lower() not in ("0", "false", "no", "off")


# ---------------------------------------------------------------------------- colour maths
def _lum(color: QColor) -> float:
    def channel(value):
        value /= 255
        return value / 12.92 if value <= .03928 else ((value + .055) / 1.055) ** 2.4
    return .2126 * channel(color.red()) + .7152 * channel(color.green()) + .0722 * channel(color.blue())


def contrast(first: QColor, second: QColor) -> float:
    a, b = _lum(first), _lum(second)
    return (max(a, b) + .05) / (min(a, b) + .05)


def adaptive_alpha(backdrop: QColor, veil: QColor, text: QColor, target: float, base: int) -> int:
    """Smallest veil alpha (>= base) keeping `text` at `target`:1 over this backdrop."""
    for alpha in range(max(0, base), 256, 3):
        k = alpha / 255
        mixed = QColor(round(backdrop.red() * (1 - k) + veil.red() * k),
                       round(backdrop.green() * (1 - k) + veil.green() * k),
                       round(backdrop.blue() * (1 - k) + veil.blue() * k))
        if contrast(text, mixed) >= target:
            return alpha
    return 255


# ---------------------------------------------------------------------------- backdrop plates
def _soften(image: QImage, divisor: int, saturation: float) -> QImage:
    """Blur by repeated halving (kept small: Qt's smooth scaling blurs it back up on draw)."""
    target_w, target_h = max(2, image.width() // divisor), max(2, image.height() // divisor)
    current = image
    while current.width() > target_w * 1.5 or current.height() > target_h * 1.5:
        current = current.scaled(max(target_w, current.width() // 2), max(target_h, current.height() // 2),
                                 Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    if saturation != 1.0:
        out = QImage(current)
        for y in range(out.height()):
            for x in range(out.width()):
                h, s, v, a = out.pixelColor(x, y).getHsvF()
                if h >= 0:
                    out.setPixelColor(x, y, QColor.fromHsvF(h, min(1.0, s * saturation), v, a))
        current = out
    # one more soft pass (half size and back): kills the residual aliasing of fine periodic backdrops
    half = current.scaled(max(2, current.width() // 2), max(2, current.height() // 2),
                          Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return half.scaled(current.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


class Snapshot:
    """Two blurred plates of what is behind the window (logical size = the window's)."""

    def __init__(self, image: QImage, width: float, height: float):
        self.rect = QRectF(0, 0, width, height)
        raw = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        # Both plates are blurred about as much as the Acrylic beneath them (a sharper control than its
        # panel reads as a different material and makes fine backdrop detail shimmer through the lens).
        self.control = _soften(raw, 28, 1.15)
        self.card = _soften(raw, 28, 1.1)         # frosted: the content layer stays quiet

    def sample(self, rect: QRectF, which: str, worst: str) -> QColor:
        """Worst-case colour of the plate under `rect` (window coordinates), 5x5 grid.

        worst="light": brightest sample (light text on dark glass must survive it);
        worst="dark": darkest one (dark text on light glass)."""
        image = getattr(self, which)
        kx, ky = image.width() / self.rect.width(), image.height() / self.rect.height()
        found = None
        for i in range(5):
            for j in range(5):
                x = (rect.left() + rect.width() * (i + .5) / 5) * kx
                y = (rect.top() + rect.height() * (j + .5) / 5) * ky
                color = image.pixelColor(max(0, min(image.width() - 1, int(x))), max(0, min(image.height() - 1, int(y))))
                if found is None or (_lum(color) > _lum(found)) == (worst == "light"):
                    found = color
        return found


# ---------------------------------------------------------------------------- painting
def rounded(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    radius = max(0.0, min(radius, rect.width() / 2, rect.height() / 2))
    path.addRoundedRect(rect, radius, radius)
    return path


def lens(painter: QPainter, rect: QRectF, radius: float, image: QImage, snap: Snapshot,
         depth: float, width: float, bands: int = 10) -> None:
    """Edge refraction approximation.

    Concentric layers, painted outside-in; each is the plate scaled about the shape's centre so
    that, at that layer's edge, the sample comes `d` pixels from beyond the border (d decays
    quadratically inward). The surrounding content bunches up at the rim like a thick lens."""
    centre = rect.center()
    half_w, half_h = rect.width() / 2, rect.height() / 2
    width = max(1.0, min(width, half_w - 1, half_h - 1))
    for i in range(bands + 1):
        inset = width * i / bands
        shape = rounded(rect.adjusted(inset, inset, -inset, -inset), radius - inset)
        d = depth * max(0.0, 1 - i / bands) ** 2
        painter.save()
        painter.setClipPath(shape)
        painter.translate(centre)
        painter.scale(half_w / (half_w + d), half_h / (half_h + d))
        painter.translate(-centre)
        painter.drawImage(snap.rect, image)
        painter.restore()


def rim(painter: QPainter, rect: QRectF, radius: float, dark: bool, accent: QColor | None = None,
        strength: float = 1.0, dispersion: bool = False, light: float = LIGHT_ANGLE) -> None:
    """Thickness and light: a bright edge facing the lamp, a weaker one opposite, shade inside."""
    ux, uy = math.cos(math.radians(light)), -math.sin(math.radians(light))
    centre = rect.center()
    reach = abs(ux) * rect.width() / 2 + abs(uy) * rect.height() / 2
    a = QPointF(centre.x() + ux * reach, centre.y() + uy * reach)
    b = QPointF(centre.x() - ux * reach, centre.y() - uy * reach)
    hi, lo = (.62 if dark else .98) * strength, (.26 if dark else .55) * strength
    gradient = QLinearGradient(a, b)
    gradient.setColorAt(0, QColor(255, 255, 255, int(255 * hi)))
    gradient.setColorAt(.34, QColor(255, 255, 255, int(255 * .10 * strength)))
    gradient.setColorAt(.66, QColor(255, 255, 255, int(255 * .08 * strength)))
    gradient.setColorAt(1, QColor(255, 255, 255, int(255 * lo)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if dispersion:
        fringe = QLinearGradient(a, b)
        fringe.setColorAt(0, QColor(120, 200, 255, int(70 * strength))); fringe.setColorAt(.4, QColor(0, 0, 0, 0))
        fringe.setColorAt(.6, QColor(0, 0, 0, 0)); fringe.setColorAt(1, QColor(255, 130, 190, int(60 * strength)))
        painter.setPen(QPen(QBrush(fringe), 1.0))
        painter.drawPath(rounded(rect.adjusted(1.6, 1.6, -1.6, -1.6), radius - 1.1))
    painter.setPen(QPen(QBrush(gradient), 1.1))
    painter.drawPath(rounded(rect.adjusted(.5, .5, -.5, -.5), radius))
    painter.save()
    painter.setClipPath(rounded(rect, radius))
    inner = QLinearGradient(a, b)
    inner.setColorAt(0, QColor(255, 255, 255, int(255 * (.20 if dark else .55) * strength)))
    inner.setColorAt(.22, QColor(255, 255, 255, 0))
    inner.setColorAt(.78, QColor(0, 0, 0, 0))
    inner.setColorAt(1, QColor(0, 0, 0, int(255 * (.20 if dark else .07) * strength)))
    painter.setPen(QPen(QBrush(inner), 5))
    painter.drawPath(rounded(rect.adjusted(2.5, 2.5, -2.5, -2.5), radius - 2.5))
    painter.restore()
    if accent is not None:
        accent = QColor(accent); accent.setAlpha(150)
        painter.setPen(QPen(accent, 1.4))
        painter.drawPath(rounded(rect.adjusted(.5, .5, -.5, -.5), radius))


def specular(painter: QPainter, rect: QRectF, radius: float, dark: bool, strength: float = 1.0,
             light: float = LIGHT_ANGLE) -> None:
    ux, uy = math.cos(math.radians(light)), -math.sin(math.radians(light))
    centre = rect.center()
    focus = QPointF(centre.x() + ux * rect.width() * .30, centre.y() + uy * rect.height() * .62)
    gradient = QRadialGradient(focus, max(rect.width() * .55, rect.height() * .9))
    gradient.setColorAt(0, QColor(255, 255, 255, int(255 * (.16 if dark else .46) * strength)))
    gradient.setColorAt(1, QColor(255, 255, 255, 0))
    painter.save()
    painter.setClipPath(rounded(rect, radius))
    painter.fillRect(rect, gradient)
    painter.restore()


def shadow(painter: QPainter, rect: QRectF, radius: float, dark: bool, size: float = 1.0) -> None:
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for width, alpha in ((16, 5), (11, 8), (6, 14), (3, 18)):
        painter.setPen(QPen(QColor(0, 0, 0, int(alpha * (1.5 if dark else .8) * size)), width))
        painter.drawPath(rounded(rect.adjusted(0, 3, 0, 3), radius))


# ---------------------------------------------------------------------------- the per-window layer
class OpticalGlass(QObject):
    """Owned by one window. `controls()` returns [(widget, corner radius)] to give lens glass."""

    PROPERTY = "opticalGlass"
    TARGET_CONTROL, TARGET_CARD, TARGET_CARD_SELECTED = 5.0, 5.8, 6.4

    def __init__(self, window, controls):
        super().__init__(window)
        self.window = window
        self._controls = controls
        self.snapshot: Snapshot | None = None
        self._raw: QImage | None = None
        self._flagged: list = []
        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.timeout.connect(self._build)

    # -- lifecycle ---------------------------------------------------------
    def available(self) -> bool:
        """Only Liquid Glass over a live native backdrop."""
        surface = getattr(self.window, "_surface", None)
        return bool(enabled() and windows_design.glass() is not None
                    and getattr(surface, "_native_backdrop", False))

    def active(self) -> bool:
        return self.snapshot is not None and self.available()

    def capture(self) -> bool:
        """Grab the pixels behind the (still hidden) window. Call before show()."""
        self.release()
        if self.window.isVisible() or not enabled() or windows_design.glass() is None:
            return False
        if QGuiApplication.platformName() != "windows":
            return False
        geometry = self.window.geometry()
        screen = self.window.screen() or QGuiApplication.screenAt(geometry.center())
        if screen is None or geometry.width() < 8 or geometry.height() < 8:
            return False
        origin = screen.geometry().topLeft()
        grabbed = screen.grabWindow(0, geometry.x() - origin.x(), geometry.y() - origin.y(),
                                    geometry.width(), geometry.height())
        if grabbed.isNull():
            return False
        self._raw = grabbed.toImage()
        self._size = (geometry.width(), geometry.height())
        # Build after the window is on screen: capture is the only work before show().
        self._pending.start(0)
        return True

    def _build(self):
        if self._raw is None:
            return
        raw, self._raw = self._raw, None
        self.snapshot = Snapshot(raw, *self._size)
        self._flag(True)
        self.window.update()

    def release(self):
        """Drop every captured pixel and give the controls their normal backgrounds back."""
        self._pending.stop()
        self._raw = None
        self.snapshot = None
        self._flag(False)

    def _flag(self, on: bool):
        widgets = [w for w, _ in self._controls()] if on else self._flagged
        for widget in widgets:
            if widget.property(self.PROPERTY) != on:
                widget.setProperty(self.PROPERTY, on)
                widget.style().unpolish(widget); widget.style().polish(widget)
        self._flagged = widgets if on else []

    # -- painting ----------------------------------------------------------
    def paint_controls(self, painter: QPainter) -> None:
        if not self.active():
            return
        glass = windows_design.glass()
        tokens = windows_design.palette()
        dark = glass.dark
        veil_color = QColor(8, 12, 20) if dark else QColor(255, 255, 255)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        dirty = painter.clipBoundingRect()          # partial repaints (hover) skip the controls they do not touch
        for widget, radius in self._controls():
            if not widget.isVisibleTo(self.window) or widget.width() < 8 or widget.height() < 8:
                continue
            rect = QRectF(widget.mapTo(self.window, QPoint(0, 0)), widget.size())
            if not self.snapshot.rect.contains(rect.center()):
                continue
            if not dirty.isEmpty() and not dirty.intersects(rect.adjusted(-20, -20, 20, 20)):
                continue
            radius = min(radius, rect.height() / 2)
            checked = bool(getattr(widget, "isChecked", lambda: False)())
            shadow(painter, rect, radius, dark)
            painter.save()
            painter.setClipPath(rounded(rect, radius))
            lens(painter, rect, radius, self.snapshot.control, self.snapshot, depth=12, width=min(16, rect.height() / 2 - 1))
            base = 40 if dark else 98
            alpha = adaptive_alpha(self.snapshot.sample(rect, "control", "light" if dark else "dark"), veil_color,
                                   QColor(tokens.muted), self.TARGET_CONTROL, base)
            painter.fillRect(rect, QColor(veil_color.red(), veil_color.green(), veil_color.blue(), alpha))
            if dark:
                painter.fillRect(rect, QColor(255, 255, 255, 18))
            painter.restore()
            specular(painter, rect, radius, dark)
            rim(painter, rect, radius, dark, accent=None, dispersion=True)
        painter.restore()

    def card_veil(self, base: QColor, rect_in_window: QRectF, selected: bool) -> QColor:
        """The card's contrast layer, never weaker than its token, stronger where the backdrop needs it."""
        if not self.active():
            return base
        glass = windows_design.glass()
        dark = glass.dark
        sample = self.snapshot.sample(rect_in_window, "card", "light" if dark else "dark")
        target = self.TARGET_CARD_SELECTED if selected else self.TARGET_CARD
        alpha = adaptive_alpha(sample, QColor(base.red(), base.green(), base.blue()),
                               QColor(windows_design.palette().faint), target, base.alpha())
        out = QColor(base)
        out.setAlpha(max(base.alpha(), alpha))
        return out
