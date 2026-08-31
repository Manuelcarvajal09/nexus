from nexus_linux import windows_paste
from nexus_linux.windows_platform import FocusTarget
from nexus_linux.windows_settings import DEFAULTS, vk_for
from nexus_linux.windows_paste import PasteResult


def test_windows_hotkey_format_is_explicit_and_configurable():
    assert vk_for(DEFAULTS["panel_hotkey"]) == ord("V")
    assert vk_for("Ctrl+Shift+X") == ord("X")


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
