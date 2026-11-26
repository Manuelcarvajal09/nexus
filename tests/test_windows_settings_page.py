"""Configuración page and rebindable shortcuts inside the real Windows panel (offscreen)."""
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import QLabel, QPushButton

from nexus_linux import paths, styles, windows_app as app, windows_design
from nexus_linux.storage import StorageService
from nexus_linux.windows_settings import DEFAULTS, load_settings, save_settings
from nexus_linux.windows_settings_page import SettingsPage, _KeyEdit
from nexus_linux.windows_shortcuts import SPECS, ShortcutStore
from nexus_linux.windows_timeline import WindowsTimelineWindow


class Native:
    def __init__(self, monkeypatch):
        self.taken, self.active = set(), {}
        monkeypatch.setattr(app, "register_hotkey", self.register)
        monkeypatch.setattr(app, "unregister_hotkey", lambda hwnd, identifier: self.active.pop(identifier, None))
        monkeypatch.setattr(app, "last_hotkey_error", lambda: 1409)

    def register(self, hwnd, identifier, key, modifiers=0):
        if (modifiers, key) in self.taken or identifier in self.active:
            return False
        self.active[identifier] = (modifiers, key)
        return True


@pytest.fixture
def env(qapp, tmp_path, monkeypatch, paste_service):
    monkeypatch.setattr(paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(styles, "_THEME_FILE", tmp_path / "theme.json")
    native = Native(monkeypatch)
    storage = StorageService(tmp_path / "settings.sqlite")
    window = WindowsTimelineWindow(storage, paste_service)
    window.setFont(QFont("Segoe UI", 10))
    window.setStyleSheet(windows_design.qss())
    settings = load_settings()
    store = ShortcutStore(settings)
    hotkeys = app.GlobalHotkeys(1, store, lambda: save_settings(settings))
    hotkeys.register_all()
    backend = app.SettingsBackend(settings, store, hotkeys, window, ["Aviso de arranque de prueba"])
    window.configure_settings(backend)
    yield window, backend, native, settings, tmp_path
    window._search_debounce.stop()
    window._dismiss_timer.stop()
    window.close()
    window.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    storage.close()


def rows(window):
    return window._settings_page.rows


def type_into(row, text):
    row.edit.setKeySequence(QKeySequence(text, QKeySequence.SequenceFormat.PortableText))
    row.edit.editingFinished.emit()


def local_keys(window):
    found = {}
    for shortcut in window.findChildren(QShortcut):
        found[shortcut.key().toString()] = shortcut
    return found


# -- the page -------------------------------------------------------------

def test_page_lists_every_shortcut_with_scope_and_marks_fixed_ones(env):
    window, *_ = env
    page = window._settings_page
    assert set(page.rows) == {s.id for s in SPECS}
    for spec in SPECS:
        row = page.rows[spec.id]
        assert row.scope.text() == ("Global" if spec.scope == "global" else "En Nexus")
        assert (row.edit is not None) == spec.editable
        assert row.description.text()
        if not spec.editable:
            assert row.fixed.toolTip() == spec.reason
    assert page.rows["panel"].edit.keySequence().toString() == "Ctrl+Shift+V"
    assert page.rows["stack_remove"].fixed.text() == "Supr / Retroceso"
    assert "Aviso de arranque de prueba" in page.notice.text() and not page.notice.isHidden()


def test_settings_is_reachable_from_the_options_menu_and_esc_returns(env, qapp):
    window, *_ = env
    window.show_and_focus(); qapp.processEvents()
    actions = [a.text() for a in window._management_btn.menu().actions()]
    assert actions == ["Administrar Pinboards", "Configuración"]
    window._management_btn.menu().actions()[1].trigger()
    assert window._pages.currentWidget() is window._settings_page
    assert window._search.isHidden() and window._pages.accessibleName() == "Configuración"
    assert window._management_btn.isChecked()
    window.keyPressEvent(__import__("PySide6.QtGui", fromlist=["QKeyEvent"]).QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert window._pages.currentIndex() == 0 and window._search.isVisible()


def test_settings_page_has_the_same_window_geometry_as_pinboard_administration(env, qapp):
    window, *_ = env
    window.show_and_focus(); qapp.processEvents()
    window.show_pinboard_manager(); qapp.processEvents()
    admin = window.geometry()
    window.show_settings(); qapp.processEvents()
    assert window.geometry() == admin
    window.close_pinboard_manager(); qapp.processEvents()
    assert window._pages.currentIndex() == 0 and window.geometry().height() < admin.height()


def test_pinboard_shortcuts_do_nothing_while_settings_is_open(env, qapp):
    window, *_ = env
    board = window._storage.create_pinboard("Uno")
    window.show_and_focus(); window.show_settings(); qapp.processEvents()
    window._cycle_pinboard(1)
    assert window._current_pinboard_id is None
    window.close_pinboard_manager()
    window._cycle_pinboard(1)
    assert window._current_pinboard_id == board.id


def test_capturing_a_valid_combination_saves_applies_and_confirms(env):
    window, backend, _, settings, _ = env
    row = rows(window)["search"]
    type_into(row, "Ctrl+K")
    assert backend.store.get("search") == "Ctrl+K"
    assert row.edit.keySequence().toString() == "Ctrl+K"
    assert "Guardado" in row.status.text() and row.status.objectName() == "statusOk"
    assert load_settings()["shortcuts"] == {"search": "Ctrl+K"}
    assert row.reset_button.isEnabled() and window._settings_page.reset_all_button.isEnabled()
    keys = local_keys(window)
    assert "Ctrl+K" in keys and "Ctrl+F" not in keys


def test_an_invalid_or_conflicting_combination_is_explained_and_reverted(env):
    window, backend, _, _, _ = env
    row = rows(window)["search"]
    type_into(row, "Ctrl+Shift+C")  # Stack's global hotkey
    assert row.status.objectName() == "statusError" and "Activar Stack" in row.status.text()
    assert row.edit.keySequence().toString() == "Ctrl+F" and backend.store.get("search") == "Ctrl+F"
    type_into(row, "Ctrl+Meta+F")
    assert "Windows" in row.status.text()
    type_into(row, "Return")
    assert "reservada" in row.status.text() and "Ctrl+F" in local_keys(window)


def test_a_global_hotkey_windows_refuses_is_reported_and_the_old_one_keeps_working(env):
    window, backend, native, _, _ = env
    native.taken.add((0x2 | 0x1, ord("H")))
    row = rows(window)["panel"]
    type_into(row, "Ctrl+Alt+H")
    assert "Otra aplicación" in row.status.text()
    assert row.edit.keySequence().toString() == "Ctrl+Shift+V" and native.active[1] == (0x2 | 0x4, ord("V"))
    type_into(row, "Ctrl+Alt+J")
    assert backend.store.get("panel") == "Ctrl+Alt+J" and native.active[1] == (0x2 | 0x1, ord("J"))


def test_reset_one_reset_all_and_their_enabled_state(env):
    window, backend, native, _, _ = env
    page = window._settings_page
    assert not page.reset_all_button.isEnabled() and not rows(window)["rename"].reset_button.isEnabled()
    type_into(rows(window)["rename"], "F3")
    type_into(rows(window)["panel"], "Ctrl+Alt+H")
    rows(window)["rename"].reset_button.click()
    assert backend.store.get("rename") == "F2" and "F2" in local_keys(window)
    page.reset_all_button.click()
    assert all(backend.store.is_default(s.id) for s in SPECS if s.editable)
    assert rows(window)["panel"].edit.keySequence().toString() == "Ctrl+Shift+V"
    assert "restablecidos" in page.summary.text().lower() and not page.reset_all_button.isEnabled()
    assert native.active[1] == (0x2 | 0x4, ord("V"))


def test_fixed_rows_have_no_editor_and_explain_why(env):
    window, backend, *_ = env
    for identifier in ("paste", "close", "focus_toggle", "stack_remove", "stack_paste"):
        assert rows(window)[identifier].edit is None
        assert not backend.change_shortcut(identifier, "Ctrl+Alt+Q").ok


def test_capturing_keys_suspends_every_shortcut_and_leaving_restores_them(env, qapp):
    window, backend, native, _, _ = env
    window.show_and_focus(); window.show_settings(); qapp.processEvents()
    edit = rows(window)["search"].edit
    edit.capture_started.emit()
    assert native.active == {} and not window._rename_shortcut.isEnabled()
    edit.capture_finished.emit()
    assert set(native.active) == {1, 2} and window._rename_shortcut.isEnabled()
    edit.capture_started.emit()
    window.close_pinboard_manager()          # leaving the page mid-capture must not leave things off
    qapp.processEvents()
    assert set(native.active) == {1, 2} and window._rename_shortcut.isEnabled()


def test_escape_while_capturing_cancels_without_changing_anything(env):
    window, backend, *_ = env
    row = rows(window)["search"]
    row.edit.setKeySequence(QKeySequence("Ctrl+K"))
    row.edit.cancelled.emit()
    assert row.edit.keySequence().toString() == "Ctrl+F" and backend.store.get("search") == "Ctrl+F"


def test_capture_toggle_pauses_and_resumes_the_clipboard_monitor_setting(env):
    window, backend, _, settings, _ = env
    toggle = window._settings_page.capture_toggle
    assert toggle.isChecked() and toggle.text() == "Captura activa" and settings["paused"] is False
    toggle.click()
    assert settings["paused"] is True and toggle.text() == "Captura en pausa" and load_settings()["paused"] is True
    toggle.click()
    assert settings["paused"] is False


def test_link_preview_toggle_saves_and_stops_network_requests(env):
    window, backend, _, settings, _ = env
    from types import SimpleNamespace
    preview = SimpleNamespace(network_enabled=True)
    backend.previews = (preview,)
    toggle = window._settings_page.previews_toggle
    assert toggle.isChecked() and toggle.text() == "Vista previa de enlaces activada"
    toggle.click()
    assert settings["link_previews"] is False and load_settings()["link_previews"] is False
    assert preview.network_enabled is False and toggle.text() == "Vista previa de enlaces desactivada"
    toggle.click()
    assert preview.network_enabled is True


def test_theme_buttons_request_the_theme_and_follow_external_changes(env):
    window, *_ = env
    requested = []
    window.theme_requested.connect(requested.append)
    page = window._settings_page
    page.theme_buttons["tech_neon"].click()
    assert requested == ["tech_neon"]
    window.set_theme_selection("storem")
    assert page.theme_buttons["storem"].isChecked() and not page.theme_buttons["tech_neon"].isChecked()


# -- rebinding the real panel shortcuts ---------------------------------------

def test_quick_paste_modifier_can_be_changed_and_pastes_the_right_card(env, qapp):
    window, backend, _, _, _ = env
    for n in range(3):
        window._storage.capture(kind="text", text_content=f"clip {n}")
    window.refresh()
    assert backend.change_shortcut("quick_paste", "Alt+1").ok
    keys = local_keys(window)
    assert all(f"Alt+{n}" in keys for n in range(1, 10)) and not any(f"Ctrl+{n}" in keys for n in range(1, 10))
    keys["Alt+2"].activated.emit()
    assert window._paste.written and window._paste.written[-1].text_content == window._model.item_at(1).text_content


def test_board_and_rename_shortcuts_follow_the_settings(env):
    window, backend, *_ = env
    backend.change_shortcut("board_next", "Ctrl+Down"); backend.change_shortcut("rename", "F4")
    keys = local_keys(window)
    assert "Ctrl+Down" in keys and "Ctrl+Right" not in keys and "F4" in keys and "F2" not in keys
    window._storage.capture(kind="text", text_content="a")
    window.refresh()
    window._list.setCurrentIndex(window._model.index(0, 0))
    keys["F4"].activated.emit()
    assert window._title_editor is not None


def test_new_window_applies_saved_shortcuts_after_a_restart(env, qapp, tmp_path, paste_service):
    window, backend, *_ = env
    backend.change_shortcut("search", "Ctrl+K"); backend.change_shortcut("quick_paste", "Alt+1")
    settings = load_settings()                        # what the next launch reads
    store = ShortcutStore(settings)
    assert not store.sanitize()
    second = WindowsTimelineWindow(window._storage, paste_service)
    second.configure_settings(app.SettingsBackend(settings, store, Mock(), second))
    keys = local_keys(second)
    assert "Ctrl+K" in keys and "Alt+9" in keys and "Ctrl+F" not in keys and "Ctrl+9" not in keys
    second.close(); second.deleteLater()


def test_every_default_shortcut_still_works_without_any_customization(env):
    window, *_ = env
    keys = local_keys(window)
    assert {"Ctrl+F", "F2", "Ctrl+Left", "Ctrl+Right", *(f"Ctrl+{n}" for n in range(1, 10))} <= set(keys)


def test_page_widgets_are_styled_by_the_windows_theme(env):
    window, *_ = env
    sheet = windows_design.qss()
    for selector in ("scopePill", "keyPill", "statusError", "keyEdit", "captureToggle", "settingsHeading"):
        assert selector in sheet


def test_settings_page_names_itself(env):
    window, *_ = env
    title = window._settings_page.findChild(QLabel, "settingsPageTitle")
    assert title is not None and title.text() == "Configuración"


def test_search_placeholder_follows_the_open_pinboard(env):
    window, _, _, _, _ = env
    board = window._storage.create_pinboard("Mercado Libre", "#F0C56E", None)
    window.refresh()
    assert window._search.placeholderText() == "Buscar en tu historial…"
    window._select_pinboard(board.id)
    assert "Mercado Libre" in window._search.placeholderText()
    window._select_pinboard(None)
    assert window._search.placeholderText() == "Buscar en tu historial…"


def test_settings_hides_the_navigation_row_and_keeps_the_window_size(env, qapp):
    window, *_ = env
    window.show_and_focus(); window.show_pinboard_manager(); qapp.processEvents()
    admin = window.geometry()
    assert not window._chip_scroll.isHidden()
    window.show_settings(); qapp.processEvents()
    assert window._chip_scroll.isHidden()
    assert window.geometry().height() == admin.height()
    window.close_pinboard_manager(); qapp.processEvents()
    assert not window._chip_scroll.isHidden()
