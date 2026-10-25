"""Real Windows design tokens and integrated widgets; synthetic memory DB only."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QAbstractAnimation, QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from nexus_linux import styles, windows_design as design
from nexus_linux.windows_timeline import WindowsTimelineWindow


def luminance(value):
    color = QColor(value)
    channels = [x / 255 for x in (color.red(), color.green(), color.blue())]
    r, g, b = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in channels]
    return .2126 * r + .7152 * g + .0722 * b


@pytest.mark.parametrize("theme", list(design.PALETTES))
def test_palette_contrast_and_shared_linux_presets_are_not_mutated(monkeypatch, theme):
    before = dict(styles.PRESETS)
    monkeypatch.setattr(styles, "_active_key", theme)
    palette = design.palette()
    for ink, minimum in ((palette.text, 7), (palette.muted, 4.5), (palette.faint, 4.5)):
        ratio = (luminance(ink) + .05) / (luminance(palette.card) + .05)
        assert ratio >= minimum
    assert styles.PRESETS == before


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_explicit_reduced_motion_is_respected(monkeypatch, value):
    monkeypatch.setenv("NEXUS_REDUCED_MOTION", value)
    assert design.motion_enabled() is False


@pytest.fixture
def premium(qapp, storage, paste_service, monkeypatch):
    old_style = qapp.styleSheet()
    monkeypatch.setattr(styles, "_active_key", "liquid_glass")
    monkeypatch.setenv("NEXUS_REDUCED_MOTION", "1")
    qapp.setStyleSheet(design.qss())
    window = WindowsTimelineWindow(storage, paste_service)
    window.resolve_target_screen = lambda: SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 1920, 1080))
    yield window
    window._search_debounce.stop()
    window._dismiss_timer.stop()
    window._motion.cancel()
    window.hide()
    window.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.setStyleSheet(old_style)


def test_navigation_preserves_chip_widgets_and_search_accessibility(premium):
    board = premium._storage.create_pinboard("Synthetic board")
    premium.refresh()
    history = premium._pinboard_buttons[None]
    target = premium._pinboard_buttons[board.id]
    premium._select_pinboard(board.id)
    assert premium._pinboard_buttons[None] is history
    assert premium._pinboard_buttons[board.id] is target
    assert target.isChecked()
    assert premium._search.accessibleName()
    assert not premium._search_action.icon().isNull()


def test_plus_creates_a_draft_not_an_edit_of_selected_board(premium):
    board = premium._storage.create_pinboard("Synthetic existing")
    premium._select_pinboard(board.id)
    premium._new_pinboard()
    assert premium._editing_id is None
    assert premium._editor._name.text() == ""
    assert len(premium._storage.list_pinboards()) == 1
    assert premium._management_btn.isChecked()
    premium._pinboard_buttons[None].click()
    assert premium._pages.currentIndex() == 0
    assert premium._search.isEnabled()


def test_there_is_no_expand_control_and_tab_only_moves_focus(premium, qapp, monkeypatch):
    for i in range(8): premium._storage.capture(kind="text", text_content=f"Synthetic {i}")
    premium.show_and_focus(); qapp.processEvents()
    assert premium._expand_btn is None
    assert not [b for b in premium.findChildren(QPushButton) if b.text().strip() in ("Expandir", "Contraer", "Colapsar")]
    geometry = QRect(premium.geometry())
    calls = []
    monkeypatch.setattr(premium, "_focus_list", lambda: calls.append("list"))
    monkeypatch.setattr(premium._search, "hasFocus", lambda: True)
    premium._toggle_expanded()
    monkeypatch.setattr(premium._search, "hasFocus", lambda: False)
    monkeypatch.setattr(premium._search, "setFocus", lambda *a: calls.append("search"))
    premium._toggle_expanded(); qapp.processEvents()
    assert calls == ["list", "search"]
    assert premium.geometry() == geometry and premium._expanded is False


def test_keyboard_legend_and_footer_are_gone_but_shortcuts_still_work(premium, qapp):
    premium.show_and_focus(); qapp.processEvents()
    for admin in (False, True):
        if admin:
            premium.show_pinboard_manager(); qapp.processEvents()
        texts = " ".join(label.text() for label in premium.findChildren(QLabel))
        assert "Ctrl+F" not in texts and "Enter  Pegar" not in texts and "Esc  Cerrar" not in texts
    assert not hasattr(premium, "_keyboard_hint") and not hasattr(premium, "_footer")
    keys = {s.key().toString() for s in premium.findChildren(QShortcut)}
    assert {"Ctrl+F", "Ctrl+Left", "Ctrl+Right", "F2", *(f"Ctrl+{n}" for n in range(1, 10))} <= keys


def test_no_empty_footer_strip_remains_below_the_cards(premium, qapp):
    premium.show_and_focus(); qapp.processEvents()
    below = premium.height() - premium._pages.mapTo(premium, QPoint(0, premium._pages.height())).y()
    assert below == premium.layout().contentsMargins().bottom() + premium._surface_layout.contentsMargins().bottom()


def test_color_buttons_have_visible_focus_and_support_space(premium, qapp):
    premium.show_and_focus(); premium._new_pinboard(); qapp.processEvents()
    editor = premium._editor
    button = editor._stack.widget(1).findChildren(QPushButton)[3]
    assert button.property("colorSwatch") is True
    assert 'QPushButton[colorSwatch="true"]:focus' in qapp.styleSheet()
    assert button.accessibleName().startswith("Color #")
    button.setFocus(Qt.FocusReason.OtherFocusReason)
    assert qapp.focusWidget() is button
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert editor.selected_color == "#34C759"


def test_direct_paste_hide_cannot_be_delayed_by_visual_motion(premium, monkeypatch):
    monkeypatch.delenv("NEXUS_REDUCED_MOTION")
    premium.show_and_focus()
    premium.hide()
    assert not premium.isVisible()
    assert premium._motion._window_animation is None
    assert premium._search_highlight._animation.state() == QAbstractAnimation.State.Stopped


def test_native_backdrop_windows_never_apply_layered_opacity_motion(premium, monkeypatch):
    monkeypatch.delenv("NEXUS_REDUCED_MOTION")
    monkeypatch.setattr(design, "motion_enabled", lambda: True)
    premium._backdrop_controller = object()
    premium.show_and_focus()
    assert premium.isVisible() and premium.windowOpacity() == 1.0
    assert premium._motion._window_animation is None
    premium.fade_hide()
    assert not premium.isVisible()
    assert premium.windowOpacity() == 1.0 and premium._motion._window_animation is None


def test_preview_reference_remains_live_after_filtering(premium):
    item = premium._storage.capture(kind="text", text_content="Synthetic live preview")
    premium.refresh()
    premium._on_preview_ready(item.id, {"title": "Synthetic metadata"})
    assert premium._delegate._preview_data is premium._preview_data
    assert premium._delegate._preview_data[item.id]["title"] == "Synthetic metadata"
    premium._search.setText("no matching synthetic result")
    premium.refresh()
    assert premium._delegate._preview_data is premium._preview_data
    assert item.id not in premium._preview_data


def test_reopening_during_dismissal_cannot_hide_the_new_session(premium, monkeypatch):
    monkeypatch.delenv("NEXUS_REDUCED_MOTION")
    monkeypatch.setattr(design, "motion_enabled", lambda: True)
    premium.show_and_focus()
    premium._motion._window_animation.setCurrentTime(130)
    premium.fade_hide()
    assert premium.is_dismissing()
    premium.show_and_focus()
    assert not premium.is_dismissing()
    QTest.qWait(160)
    assert premium.isVisible()


def test_pinboard_save_has_nonblocking_feedback(premium):
    premium._new_pinboard()
    premium._editor._name.setText("Synthetic feedback")
    premium._editor._save()
    assert not premium._feedback_label.isHidden()
    assert premium._feedback_timer.isActive()
    premium._feedback_timer.timeout.emit()
    assert premium._feedback_label.isHidden()


def test_stack_hover_has_no_shared_blur_effect(premium, qapp, paste_service):
    from nexus_linux.windows_stack import WindowsClipStack, WindowsStackWindow
    sequence = [10]
    stack = WindowsClipStack(premium._storage, paste_service, sequence=lambda: sequence[0])
    window = WindowsStackWindow(stack)
    try:
        window.start_collecting()
        item = premium._storage.capture(kind="text", text_content="Synthetic hover")
        sequence[0] += 1  # a fresh copy AFTER activation, not its old baseline
        window.add_capture(item, sequence[0])
        row = window._next_widget
        assert row is not None
        QApplication.sendEvent(row, QEvent(QEvent.Type.Enter))
        assert row.property("hovered") == "true"
        assert row.graphicsEffect() is None and row._shadow is None
        QApplication.sendEvent(row, QEvent(QEvent.Type.Leave))
        assert row.property("hovered") == "false"
        assert window._order_btn.text() == "FIFO" and not window._order_btn.icon().isNull()
    finally:
        window.hide(); window.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("work_area", [QRect(0, 0, 640, 480), QRect(-480, 0, 480, 400), QRect(1920, -200, 1200, 1000)])
def test_responsive_header_keeps_close_and_search_accessible(premium, qapp, work_area):
    premium.resolve_target_screen = lambda: SimpleNamespace(availableGeometry=lambda: work_area)
    premium.show_and_focus(); qapp.processEvents()
    assert work_area.contains(premium.geometry())
    for widget in (premium._close_btn, premium._style_btn, premium._search):
        rect = QRect(widget.mapTo(premium, QPoint()), widget.size())
        assert premium.rect().contains(rect)
        assert widget.isVisible()


def test_narrow_editor_scrolls_to_keyboard_focused_save(premium, qapp):
    premium.resolve_target_screen = lambda: SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 640, 480))
    premium.show_and_focus(); premium._new_pinboard(); qapp.processEvents()
    save = next(button for button in premium._editor.findChildren(QPushButton) if button.text() == "Guardar")
    save.setFocus(Qt.FocusReason.TabFocusReason)
    qapp.processEvents()
    viewport = premium._editor_scroll.viewport()
    assert viewport.rect().contains(save.mapTo(viewport, save.rect().center()))
