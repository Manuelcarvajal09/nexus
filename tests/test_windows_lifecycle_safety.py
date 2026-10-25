import ctypes
import logging
from contextlib import ExitStack

import pytest

from nexus_linux import windows_app as app
from nexus_linux import windows_keyboard as keyboard
from nexus_linux.windows_settings import DEFAULTS
from nexus_linux.windows_platform import FocusTarget


def test_failed_hotkey_never_aborts_startup_and_successful_ones_stay(monkeypatch, qapp):
    registered, removed = [], []
    def register(hwnd, identifier, key, modifiers=None):
        registered.append(identifier)
        return identifier == app.PANEL_HOTKEY
    monkeypatch.setattr(app, "register_hotkey", register)
    monkeypatch.setattr(app, "last_hotkey_error", lambda: 1409)
    monkeypatch.setattr(app, "unregister_hotkey", lambda hwnd, identifier: removed.append(identifier))
    hotkeys = app.GlobalHotkeys(123, app.ShortcutStore(dict(DEFAULTS)), lambda: None)
    hotkeys.register_all()  # must not raise
    assert set(hotkeys.registered) == {"panel"} and "stack" in hotkeys.failures
    assert "Otra aplicación" in hotkeys.failures["stack"] and hotkeys.notices
    hotkeys.close()
    assert removed == [app.PANEL_HOTKEY]


def test_hotkey_conflict_in_settings_is_repaired_before_native_registration():
    settings = {**DEFAULTS, "stack_hotkey": DEFAULTS["panel_hotkey"]}
    store = app.ShortcutStore(settings)
    assert store.sanitize()
    assert store.get("panel") != store.get("stack")


def test_startup_exception_releases_instance_mutex(monkeypatch):
    released = []
    monkeypatch.setattr(app, "acquire_single_instance_mutex", lambda: 987)
    monkeypatch.setattr(app, "release_handle", released.append)
    def fail(cleanup): raise RuntimeError("synthetic startup failure")
    monkeypatch.setattr(app, "_run_windows", fail)
    with pytest.raises(RuntimeError): app.main()
    assert released == [987]


def test_second_instance_never_opens_storage_or_clipboard(monkeypatch):
    monkeypatch.setattr(app, "acquire_single_instance_mutex", lambda: None)
    monkeypatch.setattr(app, "_run_windows", lambda cleanup: pytest.fail("duplicate instance started"))
    assert app.main() == 0


def test_preview_log_filter_removes_source_and_traceback():
    record = logging.LogRecord("nexus.preview", logging.ERROR, "synthetic", 1,
        "Preview failed %s", ("https://example.invalid/private",), (ValueError, ValueError("private"), None))
    app.ClipboardPrivacyFilter().filter(record)
    rendered = logging.Formatter().format(record)
    assert "https://" not in rendered and "Traceback" not in rendered
    assert record.exc_info is None and record.args == ()


@pytest.mark.parametrize("modifiers,changed,expected", [({0x11}, False, 1), ({0x11, 0x10}, False, 0), ({0x11, 0x12}, False, 0), ({0x11}, True, 1)])
def test_hook_intercepts_plain_ctrl_v_and_snapshots_keydown_target(qapp, monkeypatch, modifiers, changed, expected):
    # Clipboard observation could not authorize consumption. Snapshot the
    # keydown target instead, even when Ctrl/focus changes before V release.
    watcher = keyboard.WindowsPasteWatcher()
    watcher.set_enabled(True)
    results, callbacks, unhooked = [], [], []
    watcher.paste_detected.connect(results.append)
    target = FocusTarget(9, 10, 11, -1)
    live_target = [target]
    monkeypatch.setattr(keyboard, "capture_focus_target", lambda: live_target[0])
    monkeypatch.setattr(keyboard.user32, "GetAsyncKeyState", lambda key: 0x8000 if key in modifiers else 0)
    monkeypatch.setattr(keyboard.user32, "PeekMessageW", lambda *args: 0)
    monkeypatch.setattr(keyboard.user32, "SetWindowsHookExW", lambda kind, callback, *args: callbacks.append(callback) or 99)
    monkeypatch.setattr(keyboard.user32, "CallNextHookEx", lambda *args: 0)
    monkeypatch.setattr(keyboard.user32, "UnhookWindowsHookEx", lambda handle: unhooked.append(handle))
    events = [keyboard.WM_KEYDOWN, keyboard.WM_KEYUP]
    def message(*args):
        if not events: return 0
        event = keyboard.KBDLLHOOKSTRUCT(vkCode=keyboard.VK_V)
        callbacks[0](0, events.pop(0), ctypes.addressof(event))
        if changed:
            live_target[0] = FocusTarget(19, 20, 21, -1)
            modifiers.discard(0x11)
        return 1
    monkeypatch.setattr(keyboard.user32, "GetMessageW", message)
    monkeypatch.setattr(keyboard.user32, "TranslateMessage", lambda *args: 1)
    monkeypatch.setattr(keyboard.user32, "DispatchMessageW", lambda *args: 0)
    watcher._run()
    assert len(results) == expected
    if results:
        assert results[0].target == target and results[0].event_id
    assert unhooked == [99] and watcher._hook is None and watcher._thread_id == 0


def test_stop_during_hook_start_does_not_leave_a_listener(qapp, monkeypatch):
    watcher = keyboard.WindowsPasteWatcher()
    watcher.stop()
    installs = []
    monkeypatch.setattr(keyboard.user32, "PeekMessageW", lambda *args: 0)
    monkeypatch.setattr(keyboard.user32, "SetWindowsHookExW", lambda *args: installs.append(args))
    watcher._run()
    assert not installs and watcher._thread_id == 0
