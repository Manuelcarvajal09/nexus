"""Global hotkey registration and the settings backend, with every native call faked."""
from unittest.mock import Mock

import pytest

from nexus_linux import paths, windows_app as app
from nexus_linux.windows_settings import DEFAULTS, load_settings
from nexus_linux.windows_shortcuts import MOD_ALT, MOD_CONTROL, MOD_SHIFT, ShortcutStore


class Native:
    """Fake RegisterHotKey: succeeds unless the combination is `taken`."""

    def __init__(self, monkeypatch, taken=()):
        self.taken, self.active, self.calls, self.removed = set(taken), {}, [], []
        monkeypatch.setattr(app, "register_hotkey", self.register)
        monkeypatch.setattr(app, "unregister_hotkey", self.unregister)
        monkeypatch.setattr(app, "last_hotkey_error", lambda: 1409)

    def register(self, hwnd, identifier, key, modifiers=MOD_CONTROL | MOD_SHIFT):
        self.calls.append((identifier, modifiers, key))
        combo = (modifiers, key)
        if combo in self.taken or identifier in self.active:
            return False
        self.active[identifier] = combo
        return True

    def unregister(self, hwnd, identifier):
        self.removed.append(identifier)
        self.active.pop(identifier, None)


@pytest.fixture
def make(qapp, monkeypatch):
    def build(settings=None, taken=()):
        native = Native(monkeypatch, taken)
        settings = dict(DEFAULTS) if settings is None else settings
        saved = []
        hotkeys = app.GlobalHotkeys(1, ShortcutStore(settings), lambda: saved.append(dict(settings)))
        return hotkeys, native, settings, saved
    return build


def test_default_registration_uses_ctrl_shift(make):
    hotkeys, native, _, _ = make()
    hotkeys.register_all()
    assert native.active == {1: (MOD_CONTROL | MOD_SHIFT, ord("V")), 2: (MOD_CONTROL | MOD_SHIFT, ord("C"))}
    assert not hotkeys.failures and not hotkeys.notices


def test_unusable_configured_hotkey_falls_back_to_the_default_and_says_so(make):
    settings = {**DEFAULTS, "panel_hotkey": "Ctrl+Alt+H"}
    hotkeys, native, settings, saved = make(settings, taken={(MOD_CONTROL | MOD_ALT, ord("H"))})
    hotkeys.register_all()
    assert native.active[1] == (MOD_CONTROL | MOD_SHIFT, ord("V"))
    assert settings["panel_hotkey"] == "Ctrl+Shift+V" and saved  # repaired and persisted
    assert any("Ctrl+Alt+H" in n and "Otra aplicación" in n and "Ctrl+Shift+V" in n for n in hotkeys.notices)


def test_when_even_the_default_is_taken_the_rest_keeps_working(make):
    hotkeys, native, _, _ = make(taken={(MOD_CONTROL | MOD_SHIFT, ord("V"))})
    hotkeys.register_all()
    assert 2 in native.active and 1 not in native.active
    assert "Otra aplicación" in hotkeys.failures["panel"]
    assert any("no está disponible" in n for n in hotkeys.notices)


def test_change_applies_live_and_persists(make):
    hotkeys, native, settings, saved = make()
    hotkeys.register_all()
    result = hotkeys.change("panel", "ctrl+alt+h")
    assert result.ok and result.value == "Ctrl+Alt+H"
    assert native.active[1] == (MOD_CONTROL | MOD_ALT, ord("H"))
    assert settings["panel_hotkey"] == "Ctrl+Alt+H" and saved
    assert hotkeys.registered["panel"] == "Ctrl+Alt+H"


def test_change_to_a_taken_combination_restores_the_previous_one(make):
    hotkeys, native, settings, saved = make(taken={(MOD_CONTROL | MOD_ALT, ord("H"))})
    hotkeys.register_all()
    saved.clear()
    result = hotkeys.change("panel", "Ctrl+Alt+H")
    assert not result.ok and "Otra aplicación" in result.message
    assert native.active[1] == (MOD_CONTROL | MOD_SHIFT, ord("V"))   # still the old one
    assert settings["panel_hotkey"] == "Ctrl+Shift+V" and not saved


def test_invalid_or_conflicting_change_never_reaches_windows(make):
    hotkeys, native, _, _ = make()
    hotkeys.register_all()
    calls = len(native.calls)
    assert not hotkeys.change("panel", "Shift+H").ok
    assert not hotkeys.change("panel", "Ctrl+Shift+C").ok  # Stack's
    assert not hotkeys.change("panel", "Alt+F4").ok
    assert len(native.calls) == calls


def test_reset_returns_to_the_default(make):
    hotkeys, native, settings, _ = make()
    hotkeys.register_all()
    hotkeys.change("stack", "Ctrl+Alt+K")
    assert hotkeys.reset("stack").ok
    assert native.active[2] == (MOD_CONTROL | MOD_SHIFT, ord("C")) and settings["stack_hotkey"] == "Ctrl+Shift+C"


def test_suspend_releases_everything_and_resume_restores_it(make):
    hotkeys, native, _, _ = make()
    hotkeys.register_all()
    hotkeys.suspend()
    assert native.active == {}
    hotkeys.resume()
    assert set(native.active) == {1, 2}


def test_a_change_made_while_suspended_is_probed_then_registered_on_resume(make):
    hotkeys, native, settings, _ = make(taken={(MOD_CONTROL | MOD_ALT, ord("T"))})
    hotkeys.register_all()
    hotkeys.suspend()
    assert not hotkeys.change("panel", "Ctrl+Alt+T").ok         # taken by someone else: refused
    assert hotkeys.change("panel", "Ctrl+Alt+H").ok
    assert native.active == {}                                  # still suspended
    hotkeys.resume()
    assert native.active[1] == (MOD_CONTROL | MOD_ALT, ord("H")) and settings["panel_hotkey"] == "Ctrl+Alt+H"
    assert hotkeys.failures == {}


def test_close_unregisters_in_reverse_order(make):
    hotkeys, native, _, _ = make()
    hotkeys.register_all()
    hotkeys.close()
    assert native.removed == [2, 1] and native.active == {}


# -- backend ----------------------------------------------------------------

@pytest.fixture
def backend(make, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path)
    hotkeys, native, settings, _ = make()
    hotkeys._save = lambda: app.save_settings(settings)
    hotkeys.register_all()
    timeline = Mock()
    return app.SettingsBackend(settings, hotkeys.store, hotkeys, timeline), native, settings, timeline, tmp_path


def test_local_change_is_saved_and_applied_to_the_panel(backend):
    sb, _, settings, timeline, config = backend
    assert sb.change_shortcut("search", "Ctrl+K").ok
    timeline.apply_shortcuts.assert_called_once()
    assert load_settings()["shortcuts"] == {"search": "Ctrl+K"}


def test_failed_local_change_saves_and_applies_nothing(backend):
    sb, _, settings, timeline, _ = backend
    assert not sb.change_shortcut("search", "Ctrl+Shift+V").ok
    timeline.apply_shortcuts.assert_not_called()
    assert not settings.get("shortcuts")


def test_reset_all_restores_globals_and_locals_and_reports_nothing_when_all_work(backend):
    sb, native, settings, _, _ = backend
    sb.change_shortcut("panel", "Ctrl+Alt+H"); sb.change_shortcut("search", "Ctrl+K"); sb.change_shortcut("quick_paste", "Alt+1")
    assert sb.reset_all_shortcuts() == []
    assert all(sb.store.is_default(s) for s in ("panel", "stack", "search", "rename", "quick_paste"))
    assert native.active[1] == (MOD_CONTROL | MOD_SHIFT, ord("V"))
    reloaded = load_settings()
    assert reloaded["panel_hotkey"] == "Ctrl+Shift+V" and not reloaded["shortcuts"]


def test_reset_all_reports_the_one_that_windows_refuses(backend):
    sb, native, _, _, _ = backend
    sb.change_shortcut("panel", "Ctrl+Alt+H")
    native.taken.add((MOD_CONTROL | MOD_SHIFT, ord("V")))   # someone grabbed our default meanwhile
    problems = sb.reset_all_shortcuts()
    assert problems and "Mostrar u ocultar Nexus" in problems[0]
    assert sb.store.get("panel") == "Ctrl+Alt+H"            # unchanged, still working


def test_capture_pause_persists_and_is_seen_by_the_monitor_settings(backend):
    sb, _, settings, _, _ = backend
    assert sb.capture_paused() is False
    sb.set_capture_paused(True)
    assert settings["paused"] is True and load_settings()["paused"] is True


def test_suspend_and_resume_cover_global_and_local_shortcuts(backend):
    sb, native, _, timeline, _ = backend
    sb.suspend_shortcuts()
    assert native.active == {}
    timeline.set_shortcuts_enabled.assert_called_with(False)
    sb.resume_shortcuts()
    assert set(native.active) == {1, 2}
    timeline.set_shortcuts_enabled.assert_called_with(True)
