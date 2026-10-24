import ctypes
import pytest
from PySide6.QtCore import QMimeData

from nexus_linux import windows_paste
from nexus_linux.windows_platform import FocusTarget
from nexus_linux.windows_settings import DEFAULTS, vk_for
from nexus_linux.windows_paste import PasteResult
from nexus_linux import windows_platform
from nexus_linux import windows_clipboard
from nexus_linux.windows_app import effective_retention_days
from nexus_linux.windows_app import toggle_stack_window


@pytest.fixture(autouse=True)
def isolated_clipboard(qapp, monkeypatch):
    # These are unit tests, not integration with the user's Windows clipboard.
    # The offscreen QClipboard also has different ownership/teardown semantics
    # for Python-created QMimeData. Test it through a deterministic provider.
    class Notification:
        def __init__(self): self.slots = []
        def connect(self, slot): self.slots.append(slot)
        def disconnect(self, slot): self.slots.remove(slot)
    class Clipboard:
        def __init__(self):
            self.dataChanged = Notification()
            self.mime = QMimeData()
        def setMimeData(self, mime): self.mime = mime
        def mimeData(self): return self.mime
        def ownsClipboard(self): return True
    clipboard = Clipboard()
    monkeypatch.setattr(windows_paste.QGuiApplication, "clipboard", lambda: clipboard)
    yield clipboard
    clipboard.dataChanged.slots.clear()


def test_windows_hotkey_format_is_explicit_and_configurable():
    assert vk_for(DEFAULTS["panel_hotkey"]) == ord("V")
    assert vk_for("Ctrl+Shift+X") == ord("X")


def test_windows_retention_never_drops_below_thirty_days():
    assert effective_retention_days({}) == 30
    assert effective_retention_days({"retention_days": 7}) == 30
    assert effective_retention_days({"retention_days": 90}) == 90
    assert effective_retention_days({"retention_days": "invalid"}) == 30


def test_win32_input_structure_has_native_size():
    expected=40 if ctypes.sizeof(ctypes.c_void_p)==8 else 28
    assert ctypes.sizeof(windows_paste.INPUT) == expected


def test_nexus_synthetic_input_has_private_marker(monkeypatch):
    captured=[]
    monkeypatch.setattr(windows_paste.user32,"SendInput",lambda count,events,size: captured.extend(events) or count)
    service=windows_paste.WindowsPasteService()
    assert service._send_ctrl_v() is True
    assert {int(event.ki.dwExtraInfo) for event in captured} == {windows_paste.NEXUS_INPUT_MARKER}


def test_stale_clipboard_suppression_expires_before_next_user_copy(qapp, storage, monkeypatch):
    callbacks=[]
    monkeypatch.setattr(windows_clipboard.QTimer,"singleShot",lambda delay,callback: callbacks.append(callback))
    monitor=windows_clipboard.WindowsClipboardMonitor(storage)
    monitor.suppress_next_change()
    assert monitor._suppressed is True
    callbacks.pop()()
    assert monitor._suppressed is False


def _target():
    return FocusTarget(window=100, focused_control=101, thread_id=7, process_id=8)


def test_paste_refuses_to_send_keys_when_target_focus_cannot_be_restored(monkeypatch):
    service = windows_paste.WindowsPasteService(); service._target = _target()
    monkeypatch.setattr(windows_paste, "restore_focus_target", lambda target, pid: False)
    assert service.synthetic_paste() is False


def test_paste_sends_only_after_foreground_verification(monkeypatch):
    service = windows_paste.WindowsPasteService(); service._target = _target()
    monkeypatch.setattr(windows_paste, "restore_focus_target", lambda target, pid: True)
    monkeypatch.setattr(service, "_send_ctrl_v", lambda: True)
    assert service.synthetic_paste() is True


def test_reentrant_double_activation_is_rejected(qapp, monkeypatch, storage):
    item = storage.capture(kind="text", text_content="one paste only")
    service = windows_paste.WindowsPasteService(); service._target = _target()
    class Overlay:
        def hide(self): pass
        def show_and_focus(self): pass
    monkeypatch.setattr(windows_paste.QTimer, "singleShot", lambda delay, callback: None)
    assert service.paste_item(item, Overlay()) is True
    assert service.paste_item(item, Overlay()) is False


def test_missing_target_reports_typed_failure_without_consuming(qapp, storage):
    results=[]
    service=windows_paste.WindowsPasteService()
    item=storage.capture(kind="text",text_content="keep me")
    class Overlay:
        def hide(self): raise AssertionError("must not hide without a target")
    assert service.paste_item(item,Overlay(),lambda ok: results.append(ok)) is False
    assert results == [False]
    assert service._reason(PasteResult.TARGET_UNAVAILABLE)


def test_sendinput_failure_reports_failure_and_keeps_callback_false(qapp, monkeypatch, storage):
    results=[]; service=windows_paste.WindowsPasteService(); service._target=_target()
    item=storage.capture(kind="text",text_content="do not consume")
    class Overlay:
        def hide(self): pass
        def show_and_focus(self): pass
        def isVisible(self): return False
    monkeypatch.setattr(windows_paste,"restore_focus_target",lambda target,pid: True)
    monkeypatch.setattr(service,"_send_ctrl_v",lambda: False)
    monkeypatch.setattr(windows_paste.QTimer,"singleShot",lambda delay,callback: callback())
    assert service.paste_item(item,Overlay(),lambda ok: results.append(ok)) is True
    assert results == [False]


def test_native_window_drag_uses_windows_caption_loop(monkeypatch):
    calls=[]
    monkeypatch.setattr(windows_platform.user32,"IsWindow",lambda hwnd: True)
    monkeypatch.setattr(windows_platform.user32,"ReleaseCapture",lambda: calls.append("release") or True)
    monkeypatch.setattr(windows_platform.user32,"SendMessageW",lambda hwnd,msg,wparam,lparam: calls.append((hwnd,msg,wparam,lparam)) or 0)
    assert windows_platform.begin_native_window_drag(123) is True
    assert calls == ["release",(123,windows_platform.WM_NCLBUTTONDOWN,windows_platform.HTCAPTION,0)]


def test_explicit_hotkey_activation_attaches_and_detaches_native_gui_threads(monkeypatch):
    foreground = [99]
    calls = []
    threads = {99: 2, 123: 3}
    monkeypatch.setattr(windows_platform.user32, "IsWindow", lambda hwnd: True)
    monkeypatch.setattr(windows_platform, "foreground_window", lambda: foreground[0])
    monkeypatch.setattr(windows_platform.kernel32, "GetCurrentThreadId", lambda: 1)
    monkeypatch.setattr(windows_platform.user32, "GetWindowThreadProcessId", lambda hwnd, pid: threads.get(hwnd, 1))
    monkeypatch.setattr(windows_platform.user32, "AttachThreadInput",
                        lambda current, target, attach: calls.append((current, target, bool(attach))) or True)
    def set_foreground(hwnd):
        foreground[0] = hwnd
        return True
    monkeypatch.setattr(windows_platform.user32, "SetForegroundWindow", set_foreground)
    assert windows_platform.activate_foreground_window(123)
    assert foreground == [123]
    assert calls == [(1, 3, True), (1, 2, True), (1, 2, False), (1, 3, False)]


def test_explicit_hotkey_activation_refuses_invalid_hwnd(monkeypatch):
    calls = []
    monkeypatch.setattr(windows_platform.user32, "IsWindow", lambda hwnd: False)
    monkeypatch.setattr(windows_platform.user32, "SetForegroundWindow", lambda hwnd: calls.append(hwnd))
    assert windows_platform.activate_foreground_window(0) is False
    assert windows_platform.activate_foreground_window(123) is False
    assert not calls


def test_stack_hotkey_reopens_hidden_window_during_active_confirmation():
    from unittest.mock import Mock

    stack = Mock()
    stack.isVisible.return_value = False
    stack.is_dismissing.return_value = False
    stack.is_collecting.return_value = True
    timeline = Mock()
    timeline.isVisible.return_value = False
    paste = Mock()

    assert toggle_stack_window(stack, timeline, paste) is True
    paste.remember_target.assert_called_once_with()
    stack.start_collecting.assert_called_once_with()
    stack.show_and_focus.assert_called_once_with()
    stack.fade_hide.assert_not_called()


def test_stack_hotkey_hides_only_a_visible_panel():
    from unittest.mock import Mock

    stack = Mock()
    stack.isVisible.return_value = True
    stack.is_dismissing.return_value = False
    timeline = Mock()
    timeline.isVisible.return_value = False
    paste = Mock()

    assert toggle_stack_window(stack, timeline, paste) is False
    stack.fade_hide.assert_called_once_with()
    stack.show_and_focus.assert_not_called()
    paste.remember_target.assert_not_called()
