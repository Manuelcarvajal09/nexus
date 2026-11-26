"""Final motion contract: short, native, interruptible, reduced-motion aware, nothing left behind."""
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEvent, QPropertyAnimation
from PySide6.QtGui import QColor, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QWidget

from nexus_linux import paths, styles, windows_design
from nexus_linux.storage import StorageService
from nexus_linux.windows_motion import fade_in
from nexus_linux.windows_timeline import WindowsTimelineWindow


@pytest.fixture
def moving(monkeypatch):
    """Motion on, as for a user who keeps Windows animations enabled."""
    monkeypatch.delenv("NEXUS_REDUCED_MOTION", raising=False)
    monkeypatch.setattr(windows_design, "motion_enabled", lambda: True)


@pytest.fixture
def still(monkeypatch):
    monkeypatch.setenv("NEXUS_REDUCED_MOTION", "1")


def shown_label(qapp):
    label = QLabel("x")
    label.show()
    qapp.processEvents()
    return label


def settle(ms=260):
    QTest.qWait(ms)


# -- the helper -----------------------------------------------------------

def test_every_motion_duration_is_brief():
    for name in ("MOTION_FAST_MS", "MOTION_OPEN_MS", "MOTION_PAGE_MS", "MOTION_HOVER_MS", "MOTION_FOCUS_MS", "MOTION_OPEN_CONTENT_MS"):
        assert 0 < getattr(windows_design, name) <= 150, name


def test_fade_in_runs_then_removes_its_effect(qapp, moving):
    label = shown_label(qapp)
    animation = fade_in(label, 60, start=.3)
    assert isinstance(animation, QPropertyAnimation) and animation.duration() == 60
    effect = label.graphicsEffect()
    assert isinstance(effect, QGraphicsOpacityEffect) and effect.opacity() == pytest.approx(.3, abs=.2)
    settle(220)
    assert label.graphicsEffect() is None
    label.close()


def test_reduced_motion_starts_nothing_at_all(qapp, still):
    label = shown_label(qapp)
    assert fade_in(label, 60) is None and label.graphicsEffect() is None
    label.close()


def test_hidden_widgets_and_existing_effects_are_left_alone(qapp, moving):
    hidden = QLabel("x")
    assert fade_in(hidden) is None
    label = shown_label(qapp)
    assert fade_in(label, 200) is not None
    first = label.graphicsEffect()
    assert fade_in(label, 200) is None and label.graphicsEffect() is first   # never stacked
    settle(450)
    assert label.graphicsEffect() is None
    label.close()


def test_a_widget_destroyed_mid_fade_causes_no_error(qapp, moving):
    host = QWidget()
    label = QLabel("x", host)
    host.show()
    qapp.processEvents()
    fade_in(label, 80)
    host.close()
    host.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    settle(200)  # animation/cleanup callbacks fire against a dead widget: must be harmless


# -- inside the panel -------------------------------------------------------

@pytest.fixture
def panel(qapp, tmp_path, monkeypatch, paste_service):
    monkeypatch.setattr(paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(styles, "_THEME_FILE", tmp_path / "theme.json")
    storage = StorageService(tmp_path / "motion.sqlite")
    for n in range(4):
        storage.capture(kind="text", text_content=f"clip {n}")
    window = WindowsTimelineWindow(storage, paste_service)
    window.setFont(QFont("Segoe UI", 10))
    window.setStyleSheet(windows_design.qss())
    window._backdrop_controller = Mock()   # native Acrylic profile: the HWND must never be faded
    yield window
    window._search_debounce.stop(); window._dismiss_timer.stop()
    window.close(); window.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    storage.close()


def running(panel):
    return [a for a in panel.findChildren(QPropertyAnimation) if a.state() == QPropertyAnimation.State.Running]


def test_opening_eases_the_content_in_but_never_fades_the_window(panel, qapp, moving):
    panel.show_and_focus(); qapp.processEvents()
    assert isinstance(panel._pages.graphicsEffect(), QGraphicsOpacityEffect)
    assert panel.windowOpacity() == 1.0           # DWM Acrylic keeps its non-layered HWND
    settle()
    assert panel._pages.graphicsEffect() is None and not running(panel)


def test_reopening_a_visible_panel_does_not_replay_the_fade(panel, qapp, moving):
    panel.show_and_focus(); settle()
    panel.show_and_focus(); qapp.processEvents()
    assert panel._pages.graphicsEffect() is None


def test_opening_with_reduced_motion_is_instant(panel, qapp, still):
    panel.show_and_focus(); qapp.processEvents()
    assert panel._pages.graphicsEffect() is None and not running(panel)
    assert panel.windowOpacity() == 1.0


def test_many_open_close_cycles_leave_no_effects_or_animations_behind(panel, qapp, moving):
    for _ in range(12):
        panel.show_and_focus(); qapp.processEvents()
        panel.hide(); qapp.processEvents()
    panel.show_and_focus(); settle(320)
    assert panel._pages.graphicsEffect() is None and not running(panel)
    assert panel.windowOpacity() == 1.0


def test_title_editor_fades_in_and_leaves_no_effect(panel, qapp, moving):
    panel.show_and_focus(); settle()
    panel._list.setCurrentIndex(panel._model.index(0, 0))
    assert panel.begin_title_edit()
    editor = panel._title_editor
    assert isinstance(editor.graphicsEffect(), QGraphicsOpacityEffect)
    settle(200)
    assert editor.graphicsEffect() is None
    editor.finish(False)


def test_title_editor_is_instant_with_reduced_motion(panel, qapp, still):
    panel.show_and_focus(); qapp.processEvents()
    panel._list.setCurrentIndex(panel._model.index(0, 0))
    panel.begin_title_edit()
    assert panel._title_editor.graphicsEffect() is None
    panel._title_editor.finish(False)


def test_quick_open_edit_close_never_traps_the_editor_in_an_effect(panel, qapp, moving):
    panel.show_and_focus(); settle()
    panel._list.setCurrentIndex(panel._model.index(0, 0))
    for _ in range(8):
        panel.begin_title_edit(); qapp.processEvents()
        panel._title_editor.setText("t")
        panel._title_editor.finish(True); qapp.processEvents()
    settle(250)
    assert panel._storage.recent()[0].title == "t" or any(i.title == "t" for i in panel._storage.recent())
    assert not running(panel)


# -- colour contrast of the status colours added in this release --------------

def luminance(value):
    color = QColor(value)
    channels = [x / 255 for x in (color.red(), color.green(), color.blue())]
    r, g, b = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in channels]
    return .2126 * r + .7152 * g + .0722 * b


@pytest.mark.parametrize("theme", list(windows_design.PALETTES) + ["liquid_glass_light"])
def test_status_colours_are_readable_in_every_theme(theme):
    light = theme == "liquid_glass_light"
    palette = windows_design.LIQUID_GLASS_LIGHT if light else windows_design.PALETTES[theme]
    error = windows_design.STATUS_ERROR_LIGHT if light else windows_design.STATUS_ERROR
    for ink in (error, palette.accent):   # error and confirmation text
        for background in (palette.panel, palette.card):
            ratio = (max(luminance(ink), luminance(background)) + .05) / (min(luminance(ink), luminance(background)) + .05)
            assert ratio >= 4.5, (theme, ink, background, round(ratio, 2))
