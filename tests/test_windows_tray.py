"""Tray menu: real QMenu/QSystemTrayIcon objects, fake panel and Stack, temp settings."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject, QEvent, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QSystemTrayIcon

from nexus_linux import paths, styles, windows_app as app, windows_design, windows_icons, windows_sandbox
from nexus_linux.storage import StorageService
from nexus_linux.windows_settings import DEFAULTS, load_settings
from nexus_linux.windows_shortcuts import ShortcutStore
from nexus_linux.windows_timeline import WindowsTimelineWindow


class FakeStack(QObject):
    collecting_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.collecting, self.visible = False, False

    def is_collecting(self): return self.collecting
    def isVisible(self): return self.visible


@pytest.fixture
def env(qapp, tmp_path, monkeypatch, paste_service):
    monkeypatch.setattr(paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(styles, "_THEME_FILE", tmp_path / "theme.json")
    monkeypatch.delenv(windows_sandbox.ENV_FLAG, raising=False)
    quit_ = Mock()
    monkeypatch.setattr(qapp, "quit", quit_, raising=False)
    settings = load_settings()
    store = ShortcutStore(settings)
    backend = app.SettingsBackend(settings, store, Mock(failures={}), Mock())
    stack = FakeStack()
    calls = Mock()
    controller = app.TrayController(
        qapp, backend, stack, show_panel=calls.panel, show_stack=calls.stack, show_pinboards=calls.boards,
        show_settings=calls.settings, apply_theme=calls.theme)
    yield SimpleNamespace(controller=controller, backend=backend, stack=stack, calls=calls, settings=settings, quit=quit_)
    controller.close()


def menu_texts(menu):
    return ["-" if a.isSeparator() else a.text() for a in menu.actions()]


def test_menu_has_the_quick_actions_in_order(env):
    assert menu_texts(env.controller.menu) == [
        "Abrir Nexus", "Administrar pinboards y biblioteca…", "Nexus Stack · en pausa", "-",
        "Pausar captura del portapapeles", "-", "Configuración…", "Atajos de teclado…", "Estilo", "-", "Salir de Nexus",
    ]
    themes = [a.text() for a in env.controller.menu.actions()[8].menu().actions()]
    assert themes == [label for _key, label in windows_design.theme_choices()]


def test_every_entry_runs_its_action(env):
    actions = {a.text(): a for a in env.controller.menu.actions() if not a.isSeparator()}
    actions["Abrir Nexus"].trigger(); env.calls.panel.assert_called_once()
    actions["Administrar pinboards y biblioteca…"].trigger(); env.calls.boards.assert_called_once()
    actions["Nexus Stack · en pausa"].trigger(); env.calls.stack.assert_called_once()
    actions["Configuración…"].trigger(); actions["Atajos de teclado…"].trigger()
    assert env.calls.settings.call_count == 2
    actions["Salir de Nexus"].trigger(); env.quit.assert_called_once()


def test_left_click_toggles_the_panel_and_other_reasons_do_not(env):
    env.controller.tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    env.calls.panel.assert_called_once()
    for reason in (QSystemTrayIcon.ActivationReason.DoubleClick, QSystemTrayIcon.ActivationReason.Context,
                   QSystemTrayIcon.ActivationReason.MiddleClick):
        env.controller.tray.activated.emit(reason)
    env.calls.panel.assert_called_once()


def test_theme_choice_is_forwarded_and_checks_follow_the_active_theme(env):
    actions = env.controller.theme_actions
    action = next(a for a, key in actions.items() if key == "tech_neon")
    action.trigger()
    env.calls.theme.assert_called_once_with("tech_neon")
    env.controller.set_theme_checked("storem")
    assert [key for a, key in actions.items() if a.isChecked()] == ["storem"]


def test_pausing_capture_persists_and_changes_text_tooltip_and_icon(env):
    controller = env.controller
    normal_icon = controller.tray.icon().pixmap(32).toImage()
    assert controller.tray.toolTip() == "Nexus" and not env.settings["paused"]
    controller.capture_action.trigger()
    assert env.settings["paused"] is True and load_settings()["paused"] is True
    assert controller.capture_action.text() == "Reanudar captura del portapapeles"
    assert controller.tray.toolTip() == "Nexus · captura en pausa"
    assert controller.tray.icon().pixmap(32).toImage() != normal_icon
    controller.capture_action.trigger()
    assert env.settings["paused"] is False and controller.capture_action.text() == "Pausar captura del portapapeles"
    assert controller.tray.icon().pixmap(32).toImage() == normal_icon


def test_a_capture_already_paused_at_startup_shows_as_paused(qapp, env):
    env.settings["paused"] = True
    controller = app.TrayController(qapp, env.backend, env.stack, show_panel=Mock(), show_stack=Mock(),
                                    show_pinboards=Mock(), show_settings=Mock(), apply_theme=Mock())
    assert controller.tray.toolTip() == "Nexus · captura en pausa"
    assert controller.capture_action.text().startswith("Reanudar")
    controller.close()


def test_stack_entry_shows_active_and_hidden_states(env):
    env.stack.collecting, env.stack.visible = True, True
    env.stack.collecting_changed.emit(True)
    assert env.controller.stack_action.text() == "Stack activo"
    env.stack.visible = False
    env.controller.refresh()
    assert env.controller.stack_action.text() == "Stack activo · oculto"
    env.stack.collecting = False
    env.stack.collecting_changed.emit(False)
    assert env.controller.stack_action.text() == "Nexus Stack · en pausa"


def test_sandbox_tooltip_says_so(qapp, env, monkeypatch):
    monkeypatch.setenv(windows_sandbox.ENV_FLAG, "1")
    controller = app.TrayController(qapp, env.backend, env.stack, show_panel=Mock(), show_stack=Mock(),
                                    show_pinboards=Mock(), show_settings=Mock(), apply_theme=Mock())
    assert controller.tray.toolTip() == "Nexus (sandbox)"
    controller.close()


def test_pause_state_stays_in_sync_between_tray_and_settings_page(qapp, env, paste_service, tmp_path):
    storage = StorageService(tmp_path / "tray.sqlite")
    window = WindowsTimelineWindow(storage, paste_service)
    window.setFont(QFont("Segoe UI", 10))
    window.configure_settings(env.backend)
    toggle = window._settings_page.capture_toggle
    assert toggle.isChecked()
    env.controller.capture_action.trigger()                  # tray -> page
    assert not toggle.isChecked() and toggle.text() == "Captura en pausa"
    toggle.click()                                           # page -> tray
    assert env.settings["paused"] is False and env.controller.capture_action.text().startswith("Pausar")
    assert env.controller.tray.toolTip() == "Nexus"
    window._search_debounce.stop(); window._dismiss_timer.stop(); window.close(); window.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    storage.close()


def test_close_hides_the_icon_and_stops_refreshing(env):
    env.controller.tray.show()
    env.controller.close()
    assert not env.controller._timer.isActive() and not env.controller.tray.isVisible()


def test_missing_system_tray_is_reported_not_fatal(qapp, env, monkeypatch, caplog):
    monkeypatch.setattr(app.QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    with caplog.at_level("WARNING", logger="nexus.tray"):
        controller = app.TrayController(qapp, env.backend, env.stack, show_panel=Mock(), show_stack=Mock(),
                                        show_pinboards=Mock(), show_settings=Mock(), apply_theme=Mock())
    assert controller.available is False and "not available" in caplog.text
    controller.close()


def test_startup_notices_are_shown_as_a_tray_message(env, monkeypatch):
    shown = []
    monkeypatch.setattr(env.controller.tray, "showMessage", lambda *args: shown.append(args))
    env.controller.show(["Atajo no disponible"])
    assert shown and "Atajo no disponible" in shown[0][1]
    shown.clear()
    env.controller.show([])
    assert not shown
