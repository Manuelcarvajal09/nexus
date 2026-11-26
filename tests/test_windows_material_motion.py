"""Offscreen material and deterministic motion tests; no native/clipboard access."""

import time
from types import SimpleNamespace
import sys

import pytest
from PySide6.QtCore import QAbstractAnimation, QEvent, QPoint, QTimer, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget
from PySide6.QtTest import QTest

from nexus_linux import styles
from nexus_linux.windows_design import GLASS_DARK
from nexus_linux.windows_material import WindowsGlassSurface
from nexus_linux.windows_motion import WindowsMotion


@pytest.fixture(autouse=True)
def design_contract(monkeypatch):
    # The sibling design module is delivered independently. Test its public
    # contract without depending on its implementation or changing shared files.
    tokens = SimpleNamespace(panel="#14171D", border="#303640")
    design = SimpleNamespace(palette=lambda: tokens, motion_enabled=lambda: True, glass=lambda: GLASS_DARK)
    monkeypatch.setitem(sys.modules, "nexus.ui.design", design)   # where material imports it from
    monkeypatch.delenv("NEXUS_REDUCED_MOTION", raising=False)
    return design


@pytest.fixture
def window(qapp):
    widget = QWidget()
    widget.setGeometry(30, 40, 320, 200)
    yield widget
    widget.hide()
    widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def finish(animation):
    animation.setCurrentTime(animation.duration())


def render(surface):
    image = QImage(surface.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        surface.render(painter, QPoint(), renderFlags=QWidget.RenderFlag.DrawChildren)
    finally:
        painter.end()
    return image


def test_satin_has_dark_fallback_rounded_corners_and_cached_shape(qapp, monkeypatch):
    monkeypatch.setattr(styles, "active_theme", lambda: SimpleNamespace(liquid_glass=True))
    surface = WindowsGlassSurface()
    surface.resize(320, 180)
    image = render(surface)
    assert image.pixelColor(0, 0).alpha() == 0
    center = image.pixelColor(160, 90)
    assert center.alpha() >= 250
    assert max(center.red(), center.green(), center.blue()) < 45
    assert image.pixelColor(40, 40).lightness() > image.pixelColor(280, 140).lightness()
    path = surface._outline
    render(surface)
    assert surface._outline is path
    assert surface.mask().isEmpty()
    assert surface.graphicsEffect() is None
    surface.set_native_backdrop(True)
    native = render(surface).pixelColor(160, 90)
    assert native.alpha() == 0  # native material is painted by the top-level owner
    from PySide6.QtGui import QPainter
    material = QImage(surface.size(), QImage.Format.Format_ARGB32_Premultiplied)
    material.fill(Qt.GlobalColor.transparent)
    painter = QPainter(material)
    surface.paint_native_backdrop(painter)
    painter.end()
    assert 0 < material.pixelColor(160, 90).alpha() < 80
    surface.set_native_backdrop(False)
    assert render(surface).pixelColor(160, 90) == center
    surface.close()


def test_solid_uses_active_tokens_and_ignores_native_opacity(qapp, monkeypatch, design_contract):
    monkeypatch.setattr(styles, "active_theme", lambda: SimpleNamespace(liquid_glass=False))
    surface = WindowsGlassSurface()
    surface.resize(160, 100)
    surface.set_native_backdrop(True)
    # Native mode delegates the whole client to the top-level material painter;
    # the child surface must stay transparent for every theme.
    assert render(surface).pixelColor(80, 50).alpha() == 0
    design_contract.palette = lambda: SimpleNamespace(panel="#20232A", border="#424750")
    assert render(surface).pixelColor(80, 50).alpha() == 0
    surface.close()


def test_open_and_dismiss_are_finite_and_preserve_geometry(window):
    motion = WindowsMotion(window)
    geometry = window.geometry()
    motion.show(window)
    opening = motion._window_animation
    assert opening.duration() == 130
    finish(opening)
    assert window.isVisible() and window.windowOpacity() == 1
    assert motion._window_animation is None
    motion.hide(window)
    dismissal = motion._window_animation
    assert dismissal.duration() == 90
    assert window.isVisible()
    finish(dismissal)
    assert not window.isVisible()
    assert window.windowOpacity() == 1
    assert window.geometry() == geometry
    assert motion.findChildren(QTimer) == []


def test_reopen_cancels_old_dismiss_callback(window):
    motion = WindowsMotion(window)
    window.show()
    motion.hide(window)
    old = motion._window_animation
    old.setCurrentTime(40)
    motion.show(window)
    assert old.state() == QAbstractAnimation.State.Stopped
    old.finished.emit()  # queued/stale finish must not hide a new opening
    assert window.isVisible() and window.windowOpacity() == 1


@pytest.mark.parametrize("operation", ["show", "hide"])
def test_direct_hide_remains_immediate_and_stops_animation(window, operation):
    motion = WindowsMotion(window)
    if operation == "hide":
        window.show()
    getattr(motion, operation)(window)
    old = motion._window_animation
    window.hide()  # central paste path, not the motion API
    assert not window.isVisible()
    assert old.state() == QAbstractAnimation.State.Stopped
    assert motion._window_animation is None
    assert window.windowOpacity() == 1
    old.finished.emit()
    assert not window.isVisible()


def test_transition_effect_is_transient_and_cancel_safe(window):
    motion = WindowsMotion(window)
    window.show()
    motion.transition(window)
    assert isinstance(window.graphicsEffect(), QGraphicsOpacityEffect)
    animation = motion._page_animation
    assert animation.duration() <= 100
    finish(animation)
    assert window.graphicsEffect() is None
    motion.transition(window)
    motion.cancel()
    assert window.graphicsEffect() is None
    motion.transition(window)
    window.hide()
    assert window.graphicsEffect() is None
    assert motion._page_animation is None


def test_transition_preserves_existing_effect(window):
    motion = WindowsMotion(window)
    effect = QGraphicsOpacityEffect(window)
    window.setGraphicsEffect(effect)
    window.show()
    motion.transition(window)
    motion.cancel()
    assert window.graphicsEffect() is effect
    assert motion._page_animation is None


@pytest.mark.parametrize("env,system", [("1", True), ("true", True), ("0", False), ("", False)])
def test_reduced_motion_is_instant(window, monkeypatch, design_contract, env, system):
    monkeypatch.setenv("NEXUS_REDUCED_MOTION", env)
    design_contract.motion_enabled = lambda: system
    motion = WindowsMotion(window)
    motion.show(window)
    assert window.isVisible() and window.windowOpacity() == 1
    motion.transition(window)
    assert window.graphicsEffect() is None
    assert motion._window_animation is None
    motion.hide(window)
    assert not window.isVisible()
    assert motion._window_animation is None


def test_deleted_target_can_be_cancelled_safely(window, qapp):
    motion = WindowsMotion()
    page = QWidget(window)
    window.show()
    page.show()
    motion.transition(page)
    page.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    motion.cancel()
    assert motion._page_animation is None


def test_natural_completion_leaves_no_animation_or_effect(window, qapp):
    motion = WindowsMotion(window)
    motion.show(window)
    motion.transition(window)
    deadline = time.monotonic() + 2  # animation length is timer-driven: poll, do not guess
    while window.windowOpacity() != 1 and time.monotonic() < deadline:
        QTest.qWait(20)
    assert window.isVisible() and window.windowOpacity() == 1
    assert window.graphicsEffect() is None
    assert motion._window_animation is None
    assert motion._page_animation is None
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert motion.findChildren(QAbstractAnimation) == []
    motion.hide(window)
    QTest.qWait(160)
    assert not window.isVisible()
    assert motion._window_animation is None


def test_window_destroyed_during_animation_can_be_cancelled(qapp):
    widget = QWidget()
    motion = WindowsMotion()
    motion.show(widget)
    widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    motion.cancel()
    assert motion._window_animation is None


def test_replaced_page_effect_is_not_removed_by_cancel(window):
    motion = WindowsMotion(window)
    window.show()
    motion.transition(window)
    replacement = QGraphicsOpacityEffect(window)
    window.setGraphicsEffect(replacement)
    motion.cancel()
    assert window.graphicsEffect() is replacement
