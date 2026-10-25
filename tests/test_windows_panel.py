"""Windows-only panel contract, exercised offscreen without real user data."""

from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QPushButton

from nexus_linux import paths, styles, timeline_window, windows_design
from nexus_linux.storage import AVAILABLE_COLORS, StorageService
from nexus_linux.windows_timeline import WindowsTimelineWindow


@pytest.fixture
def panel(qapp, tmp_path, monkeypatch, paste_service):
    # Even StorageService's directory setup is sandboxed: no user DB, capture,
    # clipboard, global hotkey, network preview or native backdrop is used.
    monkeypatch.setattr(paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(styles, "_THEME_FILE", tmp_path / "theme.json")
    storage = StorageService(tmp_path / "panel.sqlite")
    window = WindowsTimelineWindow(storage, paste_service)
    window.setFont(QFont("Segoe UI", 10))
    window.setStyleSheet(styles.qss())
    yield window
    window._search_debounce.stop()
    window._dismiss_timer.stop()
    window.close()
    window.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    storage.close()


def click_named(window, text):
    buttons = [b for b in window.findChildren(QPushButton) if b.text() == text]
    assert buttons, text
    buttons[-1].click()


def test_open_refreshes_once_without_late_selection_reset(panel, qapp, monkeypatch):
    panel._storage.capture(kind="text", text_content="synthetic open")
    calls = []
    original = panel.refresh
    def refresh():
        calls.append(True)
        original()
    monkeypatch.setattr(panel, "refresh", refresh)
    panel.show_and_focus()
    qapp.processEvents()
    assert len(calls) == 1


def test_preview_memory_is_bounded_to_current_model(panel):
    item = panel._storage.capture(kind="text", text_content="synthetic preview")
    panel.refresh()
    panel._on_preview_ready(item.id, {"title": "synthetic title"})
    panel._on_preview_ready(999999, {"og_image": b"synthetic discarded image"})
    assert set(panel._preview_data) == {item.id}
    panel._search.setText("no synthetic matching value")
    panel.refresh()
    assert panel._preview_data == {}


def test_plus_and_menu_signal_open_inline_without_top_level(panel, qapp):
    panel.show_and_focus()
    qapp.processEvents()
    before = set(QApplication.topLevelWidgets())
    options = panel._management_btn
    assert options is not None and options.menu() is not None
    next(action for action in options.menu().actions()
         if action.text() == "Administrar Pinboards").trigger()
    panel._sidebar._add_btn.click()
    assert panel._pages.currentWidget() is panel._admin_page
    assert panel._editor.window() is panel
    assert not panel._editor.isWindow()
    assert set(QApplication.topLevelWidgets()) == before
    panel._back_btn.click()
    assert panel._pages.currentIndex() == 0
    panel.manage_pinboards_requested.emit()  # context menu's existing route
    assert panel._pages.currentIndex() == 1
    assert panel._storage.list_pinboards() == []  # drafts do not write
    assert panel._editor is None  # manager without a selection is not a draft
    panel._sidebar._add_btn.click()
    panel._editor.reject()
    assert panel._pages.currentIndex() == 0


def test_create_rename_color_emoji_and_filter_results(panel, qapp):
    item = panel._storage.capture("text", "Mercado Libre synthetic")
    panel._storage.capture("text", "unrelated synthetic")
    panel.manage_pinboards_requested.emit()
    panel._sidebar._add_btn.click()
    panel._editor._name.setText("Mercado Libre")
    panel._editor._switch_mode("emoji")
    panel._editor._pick_emoji("🛒")
    panel._editor._save()
    board_id = panel._pinboard_id
    assert board_id is not None
    assert panel._current_pinboard_id == board_id
    assert panel._storage.get_pinboard(board_id).emoji == "🛒"
    assert panel._model.rowCount() == 0
    panel._storage.add_to_pinboard(item.id, board_id)
    panel.refresh()
    assert panel._model.rowCount() == 1
    assert panel._model.item_at(0).id == item.id
    assert panel._count_badge.text() == "1"
    panel._editor._name.setText("Compras synthetic")
    panel._editor._switch_mode("color")
    panel._editor._pick_color(AVAILABLE_COLORS[-1])
    panel._editor._save()
    board = panel._storage.get_pinboard(board_id)
    assert (board.name, board.emoji, board.color_hex) == (
        "Compras synthetic", None, AVAILABLE_COLORS[-1],
    )
    assert panel._pinboard_buttons[board_id].text() == "Compras synthetic"
    selected = panel._sidebar._list.currentItem().data(Qt.ItemDataRole.UserRole)
    assert selected == board_id
    panel._back_btn.click()
    panel._search.setText("no match")
    panel.refresh()
    assert panel._model.rowCount() == 0
    assert "Sin resultados" in panel._empty_label.text()
    panel._search.clear()
    panel._select_pinboard(None)
    assert panel._model.rowCount() == 2


def test_delete_confirmation_cancel_then_confirm_keeps_clips(panel, monkeypatch):
    board = panel._storage.create_pinboard("temporary synthetic")
    item = panel._storage.capture("text", "preserved synthetic")
    panel._storage.add_to_pinboard(item.id, board.id)
    panel._select_pinboard(board.id)
    panel.show_pinboard_manager()
    question = Mock(return_value=QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(QMessageBox, "question", question)
    panel._delete_pinboard(board.id)
    assert panel._storage.get_pinboard(board.id) is not None
    assert panel._pinboard_id == board.id
    assert question.call_args.args[-1] == QMessageBox.StandardButton.Cancel
    question.return_value = QMessageBox.StandardButton.Yes
    panel._delete_pinboard(board.id)
    assert panel._storage.get_pinboard(board.id) is None
    assert panel._pinboard_id is None
    assert panel._storage.get(item.id) is not None
    assert panel._model.rowCount() == 1
    assert board.id not in panel._pinboard_buttons


def test_external_board_deletion_refreshes_selection_and_editor(panel):
    board = panel._storage.create_pinboard("external synthetic")
    panel._select_pinboard(board.id)
    panel.show_pinboard_manager()
    panel._storage.delete_pinboard(board.id)
    panel.refresh()
    assert panel._pinboard_id is None
    assert panel._editing_id is None
    assert panel._editor is None
    assert panel._right_heading.text() == "Tus pinboards"


def test_admin_shortcuts_cannot_paste_hidden_cards(panel, paste_service):
    panel._storage.capture("text", "synthetic")
    panel.refresh()
    panel.show_pinboard_manager()
    panel._quick_paste(1)
    assert paste_service.written == []
    assert paste_service.paste_count == 0
    panel._toggle_expanded()  # Tab on the admin page is inert
    assert panel._pages.currentIndex() == 1


def test_context_menu_manage_route_and_sidebar_selection(panel, qapp, monkeypatch):
    board = panel._storage.create_pinboard("Mercado Libre synthetic")
    panel._storage.capture("text", "synthetic card")
    panel.refresh()
    panel.show_and_focus()
    qapp.processEvents()

    class SelectionMenu(QMenu):
        def exec(self, _position):
            pinboards = next(action.menu() for action in self.actions() if action.text() == "Pinboards")
            return next(action for action in pinboards.actions() if action.text() == "Administrar pinboards…")

    monkeypatch.setattr(timeline_window, "QMenu", SelectionMenu)
    index = panel._model.index(0, 0)
    panel._show_item_menu(panel._list.visualRect(index).center())
    assert panel._pages.currentIndex() == 1
    panel._sidebar._list.setCurrentRow(0)
    assert panel._pinboard_id == board.id
    assert panel._editing_id == board.id
    assert panel._editor._name.text() == board.name
    panel._sidebar._add_btn.click()
    assert panel._editing_id is None
    assert len(panel._storage.list_pinboards()) == 1


@pytest.mark.parametrize("font_points", [10, 13, 16])
@pytest.mark.parametrize("work_area", [
    QRect(0, 0, 1920, 1040), QRect(-2560, 80, 2560, 1360),
    QRect(1920, -100, 1280, 680), QRect(0, 0, 800, 600),
])
def test_geometry_stable_history_board_admin_and_long_names(panel, qapp, monkeypatch, work_area, font_points):
    panel.setFont(QFont("Segoe UI", font_points))
    screen = Mock()
    screen.availableGeometry.return_value = work_area
    monkeypatch.setattr(panel, "resolve_target_screen", lambda: screen)
    board = panel._storage.create_pinboard("Mercado Libre synthetic")
    item = panel._storage.capture("text", "Mercado Libre synthetic card")
    panel._storage.add_to_pinboard(item.id, board.id)
    panel.refresh()
    panel.show_and_focus()
    qapp.processEvents()
    expected = QRect(panel.geometry())
    assert work_area.contains(expected)
    panel._select_pinboard(board.id)
    qapp.processEvents()
    assert panel.geometry() == expected
    panel.show_pinboard_manager()
    qapp.processEvents()
    admin_geometry = QRect(panel.geometry())
    assert work_area.contains(admin_geometry)
    assert admin_geometry.height() >= expected.height()
    # The redesigned wide editor fits inline; narrower layouts must scroll
    # rather than resize the window or make form controls inaccessible.
    content = panel._editor_scroll.widget()
    overflow = max(0, content.height() - panel._editor_scroll.viewport().height())
    assert panel._editor_scroll.verticalScrollBar().maximum() >= overflow
    panel._storage.create_pinboard("long synthetic name " * 60)
    panel.refresh()
    qapp.processEvents()
    assert panel.geometry() == admin_geometry
    assert panel._chip_scroll.horizontalScrollBar().maximum() > 0
    assert panel._chip_scroll.width() < panel.width()
    panel.close_pinboard_manager()
    panel.show_and_focus()
    qapp.processEvents()
    assert panel.geometry() == expected
    panel._toggle_expanded()  # Tab moves focus; it can never resize the window
    qapp.processEvents()
    assert panel.geometry() == expected


def test_show_applies_geometry_once_no_deferred_reposition(panel, qapp, monkeypatch):
    apply_geometry = Mock(wraps=panel._apply_geometry)
    monkeypatch.setattr(panel, "_apply_geometry", apply_geometry)
    panel.show_and_focus()
    immediate = QRect(panel.geometry())
    qapp.processEvents()
    assert apply_geometry.call_count == 1
    assert panel.geometry() == immediate


def test_no_screen_does_not_show_or_change_geometry(panel, monkeypatch):
    monkeypatch.setattr(panel, "resolve_target_screen", lambda: None)
    before = QRect(panel.geometry())
    panel.show_and_focus()
    assert not panel.isVisible()
    assert panel.geometry() == before


def test_tray_api_and_theme_changes_keep_inline_editor_and_selection(panel, qapp, monkeypatch):
    original_theme = styles.active_theme().key
    monkeypatch.setattr(styles, "_active_key", original_theme)
    board = panel._storage.create_pinboard("theme synthetic")
    panel._select_pinboard(board.id)
    panel.show_pinboard_management()
    qapp.processEvents()
    assert panel.isVisible()
    assert panel._pages.currentIndex() == 1
    editor = panel._editor
    assert editor.styleSheet() == ""
    for key, _label in styles.theme_choices():
        styles.set_active_theme(key)
        panel.setStyleSheet(styles.qss())
        panel.set_theme_selection(key)
        panel.refresh()
        qapp.processEvents()
        assert panel._editor is editor
        assert panel._pinboard_id == board.id
        assert editor.window() is panel
        assert all(action.isChecked() == (action_key == key)
                   for action, action_key in panel._theme_actions.items())
    styles.set_active_theme(original_theme)
    styles.qss()


def test_manager_empty_draft_edit_save_cancel_and_right_heading(panel, qapp):
    board = panel._storage.create_pinboard("Existing synthetic")
    panel.show_and_focus()
    panel.show_pinboard_manager()
    qapp.processEvents()
    assert panel._editor is None
    assert panel._right_heading.text() == "Tus pinboards"
    assert panel._sidebar._list.count() == 1  # no History row
    assert panel._sidebar._list.currentItem() is None
    panel._sidebar._list.setCurrentRow(0)
    assert panel._right_heading.text() == board.name
    assert panel._editor._name.text() == board.name
    panel._sidebar._add_btn.click()
    qapp.processEvents()
    assert panel._sidebar._list.currentItem() is None
    assert panel._right_heading.text() == "Nuevo pinboard"
    assert panel._pages.accessibleName() != "Nuevo pinboard"
    heading_x = panel._right_heading.mapTo(panel, QPoint()).x()
    sidebar_right = panel._sidebar.mapTo(panel, panel._sidebar.rect().topRight()).x()
    assert heading_x > sidebar_right
    assert panel._right_heading.parent().objectName() == "pinboardDetail"
    assert panel._sidebar.objectName() == "pinboardNavigation"
    assert len(panel._storage.list_pinboards()) == 1
    panel._editor._name.setText("Draft cancelled")
    panel._editor.reject()
    assert len(panel._storage.list_pinboards()) == 1
    panel.show_pinboard_manager(creating=True)
    panel._editor._name.setText("Saved synthetic")
    panel._editor._save()
    assert len(panel._storage.list_pinboards()) == 2
    assert panel._right_heading.text() == "Saved synthetic"
    assert panel._sidebar._list.currentItem().data(Qt.ItemDataRole.UserRole) == panel._editing_id


def test_manager_hides_history_search_and_restores_it_on_both_exit_routes(panel, qapp):
    board = panel._storage.create_pinboard("Navigation fixture")
    panel.show_and_focus()
    assert panel._search.isVisible()
    panel.show_pinboard_manager(creating=True)
    assert panel._search.isHidden() and not panel._search.isEnabled()
    assert panel._pages.accessibleName() == "Pinboards"
    panel._navigate_board(board.id)
    assert panel._search.isVisible() and panel._search.isEnabled()
    assert panel._pages.accessibleName() == board.name
    panel.show_pinboard_manager()
    panel.close_pinboard_manager()
    assert panel._search.isVisible() and panel._search.isEnabled()
    margins = panel._chrome.layout().contentsMargins()
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (0, 0, 0, 0)


@pytest.mark.parametrize("width", [480, 900])
@pytest.mark.parametrize("font_points", [11, 15])
def test_detail_responsive_padding_no_horizontal_overflow(panel, qapp, monkeypatch, width, font_points):
    # Override font sizes as well as QFont: production QSS has explicit px
    # sizes, so QFont alone would not actually exercise text scaling.
    panel.setStyleSheet(windows_design.qss() + f"\nQWidget {{ font-size: {font_points}pt; }}")
    panel.setFont(QFont("Segoe UI", font_points))
    screen = Mock()
    screen.availableGeometry.return_value = QRect(0, 0, width + 48, 1040)
    monkeypatch.setattr(panel, "resolve_target_screen", lambda: screen)
    panel.show_and_focus()
    panel.show_pinboard_manager(creating=True)
    # Exercise exact panel widths independently of desktop width policy.
    panel.resize(width, panel.height())
    for _ in range(3):
        qapp.processEvents()
    assert panel._pages.height() == 440
    assert 360 <= panel._editor_scroll.viewport().height() <= 440
    assert panel._editor_scroll.horizontalScrollBar().maximum() == 0
    margins = panel._surface_layout.contentsMargins()
    assert [margins.left(), margins.top(), margins.right(), margins.bottom()] == [36] * 4
    assert margins.left() - 8 == 28  # unchanged native paint/shadow inset
    assert panel._surface_layout.spacing() == 20
    assert panel._chrome.layout().spacing() == 12
    assert panel._editor._name.font().pointSize() == font_points
    for widget in (panel._editor._name, panel._editor._emoji_toggle, panel._editor._color_toggle,
                   *[b for b in panel._editor.findChildren(QPushButton) if b.text() in ("Guardar", "Cancelar")]):
        viewport = panel._editor_scroll.viewport()
        panel._editor_scroll.ensureWidgetVisible(widget, 0, 0)
        qapp.processEvents()
        rect = QRect(widget.mapTo(viewport, QPoint()), widget.size())
        assert rect.left() >= 0 and rect.right() < viewport.width()
        assert widget.height() >= widget.sizeHint().height()
        assert viewport.rect().contains(rect.center())


@pytest.mark.parametrize("work_area", [QRect(0, 0, 480, 1000), QRect(0, 0, 1000, 580)])
def test_short_portrait_admin_bounds_and_save_reachable(panel, qapp, monkeypatch, work_area):
    screen = Mock()
    screen.availableGeometry.return_value = work_area
    monkeypatch.setattr(panel, "resolve_target_screen", lambda: screen)
    panel.show_and_focus()
    panel.show_pinboard_manager(creating=True)
    qapp.processEvents()
    assert work_area.contains(panel.geometry())
    assert panel.geometry().left() >= work_area.left() + 24
    assert panel.geometry().bottom() == work_area.bottom() - 28
    assert panel._editor_scroll.horizontalScrollBar().maximum() == 0
    save = next(b for b in panel._editor.findChildren(QPushButton) if b.text() == "Guardar")
    save.setFocus(Qt.FocusReason.TabFocusReason)
    qapp.processEvents()
    viewport = panel._editor_scroll.viewport()
    assert viewport.rect().contains(QRect(save.mapTo(viewport, QPoint()), save.size()))


def test_stack_uses_same_visible_padding_and_block_gap(panel, qapp, paste_service):
    from nexus_linux.windows_stack import WindowsClipStack, WindowsStackWindow

    stack = WindowsClipStack(panel._storage, paste_service, sequence=lambda: 10)
    window = WindowsStackWindow(stack)
    try:
        margins = window._surface.layout().contentsMargins()
        assert [margins.left(), margins.top(), margins.right(), margins.bottom()] == [36] * 4
        assert margins.left() - 8 == 28
        assert window._surface.layout().spacing() == 20
    finally:
        window.hide()
        window.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_navigation_shows_only_history_and_real_boards_plus_options(panel):
    panel._storage.create_pinboard("Mercadolibre", "#D8B273", "⭐")
    panel._storage.create_pinboard("Prompts", "#FF2BD6", "📌")
    panel.refresh()
    buttons = []
    for index in range(panel._pinboard_row.count()):
        widget = panel._pinboard_row.itemAt(index).widget()
        if isinstance(widget, QPushButton) and widget.objectName() == "pinboardChipCompact":
            buttons.append(widget)
    texts = [button.text() for button in buttons]
    assert len(texts) == 3
    assert texts[0] == "Historial"
    assert texts[1].endswith("Mercadolibre")
    assert texts[2].endswith("Prompts")
    assert "+ Pinboard" not in texts
    assert "Pinboards" not in texts
    options = panel._management_btn
    assert options is not None and options.accessibleName() == "Más opciones: Pinboards y Configuración"
    assert options.text() == "" and not options.icon().isNull()   # a drawn icon, not a Unicode glyph
    assert [action.text() for action in options.menu().actions()] == ["Administrar Pinboards"]
    options.menu().actions()[0].trigger()
    assert panel._pages.currentIndex() == 1
    assert panel._pages.accessibleName() == "Pinboards"


def test_pinboard_chips_use_only_their_emoji_and_reserve_bold_label_width(panel):
    material = panel._storage.create_pinboard("Mercadolibre", "#D8B273", "⭐")
    panel._storage.create_pinboard("Prompts", "#FF2BD6", "📌")
    panel.refresh()
    buttons = [
        panel._pinboard_row.itemAt(index).widget()
        for index in range(panel._pinboard_row.count())
        if isinstance(panel._pinboard_row.itemAt(index).widget(), QPushButton)
        and panel._pinboard_row.itemAt(index).widget().objectName() == "pinboardChipCompact"
    ]
    assert buttons[0].text() == "Historial"
    assert not buttons[0].icon().isNull()
    for button in buttons[1:]:
        assert button.icon().isNull(), button.text()
        assert button.minimumWidth() >= (
            QFontMetrics(_bold(button.font())).horizontalAdvance(button.text()) + 24 + 2
        )
    panel._select_pinboard(material.id)
    panel._update_chip_state()
    selected = panel._pinboard_buttons[material.id]
    assert selected.isChecked()
    assert selected.minimumWidth() >= (
        QFontMetrics(_bold(selected.font())).horizontalAdvance(selected.text()) + 24 + 2
    )


def _bold(font):
    bold = QFont(font)
    bold.setBold(True)
    return bold
