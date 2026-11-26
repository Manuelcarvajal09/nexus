"""Finite Windows UI motion, with no geometry changes or idle timers.

Keep ``WindowsMotion(window)`` on the owner. ``show(window)`` opens in 130ms;
``hide(window)`` dismisses in 90ms. ``transition(page_or_viewport)`` fades only
that widget in 90ms, removing its temporary opacity effect on completion.
Call transition after switching pages. Existing graphics effects are untouched.

Paste must call window.hide() directly (or cancel() then hide()), never the
animated hide(). A direct hide cancels outstanding motion synchronously. Reopen
also cancels a pending dismissal, so no old callback can hide the new session.
One controller manages one window and one viewport at a time, on the GUI thread.
"""

import os

from PySide6.QtCore import QEvent, QEasingCurve, QObject, QPropertyAnimation, Property, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget
from shiboken6 import isValid
from nexus.ui.design import MOTION_FAST_MS, MOTION_FOCUS_MS, MOTION_OPEN_MS, MOTION_PAGE_MS


def _motion_enabled() -> bool:
    reduced = os.environ.get("NEXUS_REDUCED_MOTION", "").strip().lower()
    if reduced and reduced not in {"0", "false", "no", "off"}:
        return False
    from nexus.ui.design import motion_enabled

    return bool(motion_enabled())


class WindowsMotion(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._window = None
        self._window_animation = None
        self._page = None
        self._page_effect = None
        self._page_animation = None

    @staticmethod
    def _stop(animation):
        if animation is not None and isValid(animation):
            animation.stop()
            animation.finished.disconnect()
            animation.deleteLater()

    def _cancel_window(self):
        self._stop(self._window_animation)
        self._window_animation = None
        window, self._window = self._window, None
        if window is not None and isValid(window):
            window.removeEventFilter(self)
            window.setWindowOpacity(1.0)

    def _cancel_page(self):
        self._stop(self._page_animation)
        self._page_animation = None
        page, effect = self._page, self._page_effect
        self._page = self._page_effect = None
        if page is not None and isValid(page):
            page.removeEventFilter(self)
            if effect is not None and isValid(effect) and page.graphicsEffect() is effect:
                page.setGraphicsEffect(None)

    def cancel(self) -> None:
        """Stop pending callbacks; restore opacity/effects without hiding."""
        self._cancel_page()
        self._cancel_window()

    def is_dismissing(self) -> bool:
        return bool(self._window_animation is not None
                    and isValid(self._window_animation)
                    and self._window_animation.endValue() == 0.0)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Hide:
            if watched is self._window:
                self.cancel()
            elif watched is self._page:
                self._cancel_page()
        return False

    def _animate_window(self, window, start, end, duration, dismiss=False):
        self._window = window
        window.installEventFilter(self)
        animation = QPropertyAnimation(window, b"windowOpacity", self)
        self._window_animation = animation
        animation.setDuration(duration)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(QEasingCurve.Type.OutQuint)

        def finished():
            if self._window_animation is not animation:
                return
            self._cancel_window()
            if dismiss and isValid(window):
                window.hide()

        animation.finished.connect(finished)
        animation.start()


    def show(self, window: QWidget) -> None:
        """Show without moving/resizing, raising or stealing focus."""
        visible = window.isVisible()
        self.cancel()
        if getattr(window, "_backdrop_controller", None) is not None:
            # QWidget.windowOpacity installs WS_EX_LAYERED on Win32, which
            # replaces the verified non-layered DWM Acrylic composition.
            # Preserve the DWM HWND profile instead of fading the whole HWND.
            window.show()
            return
        if visible or not _motion_enabled():
            window.setWindowOpacity(1.0)
            window.show()
            return
        window.setWindowOpacity(0.0)
        window.show()
        self._animate_window(window, 0.0, 1.0, MOTION_OPEN_MS)

    def hide(self, window: QWidget) -> None:
        """Animated user dismissal only; never use this for actual paste."""
        opacity = window.windowOpacity()
        self.cancel()
        if getattr(window, "_backdrop_controller", None) is not None:
            # DWM's material is native to this HWND; whole-window opacity
            # animation would reintroduce WS_EX_LAYERED and hide Acrylic.
            window.hide()
            return
        if not window.isVisible() or not _motion_enabled():
            window.hide()
            return
        self._animate_window(window, opacity, 0.0, MOTION_FAST_MS, dismiss=True)

    def transition(self, widget: QWidget) -> None:
        """Short viewport-only fade; no snapshots, movement or persistent effect."""
        self._cancel_page()
        if not widget.isVisible() or not _motion_enabled() or widget.graphicsEffect() is not None:
            return
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        self._page, self._page_effect = widget, effect
        widget.installEventFilter(self)
        animation = QPropertyAnimation(effect, b"opacity", self)
        self._page_animation = animation
        animation.setDuration(MOTION_PAGE_MS)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutQuint)
        animation.finished.connect(self._cancel_page)
        animation.start()


def fade_in(widget: QWidget, duration: int = MOTION_PAGE_MS, start: float = 0.0):
    """Self-contained, finite opacity fade for one widget (never the HWND).

    Returns the animation, or None when nothing was started: reduced motion,
    widget not visible, or it already has a graphics effect (never stacked).
    The temporary effect removes itself on completion, so no effect lingers."""
    if not isValid(widget) or not widget.isVisible() or not _motion_enabled() or widget.graphicsEffect() is not None:
        return None
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(start)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(start)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutQuint)

    def cleanup():
        if isValid(widget) and isValid(effect) and widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)
        if isValid(animation):
            animation.deleteLater()

    # Defer: never delete the effect from inside the animation's own signal.
    animation.finished.connect(lambda: QTimer.singleShot(0, cleanup))
    animation.start()
    return animation


class SearchFocusHighlight(QWidget):
    """Finite focus sheen; mouse/keyboard input always reaches the line edit."""
    def __init__(self, line_edit):
        super().__init__(line_edit)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._strength = 0.0
        self._animation = QPropertyAnimation(self, b"strength", self)
        self._animation.setDuration(MOTION_FOCUS_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutQuint)
        line_edit.installEventFilter(self)
        self.setGeometry(line_edit.rect())
        self.show()

    def _get_strength(self): return self._strength
    def _set_strength(self, value):
        self._strength = value
        self.update()
    strength = Property(float, _get_strength, _set_strength)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect())
        elif event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self._animation.stop()
            target = 1.0 if event.type() == QEvent.Type.FocusIn else 0.0
            if not _motion_enabled():
                self._set_strength(target)
            else:
                self._animation.setStartValue(self._strength)
                self._animation.setEndValue(target)
                self._animation.start()
        elif event.type() == QEvent.Type.Hide:
            self._animation.stop()
            self._set_strength(0.0)
        return False

    def paintEvent(self, event):
        if self._strength <= 0:
            return
        from nexus.ui.design import palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(palette().accent)
        color.setAlpha(round(85 * self._strength))
        painter.setPen(QPen(color, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 10, 10)
        painter.end()
