from nexus_linux.windows_settings import DEFAULTS, vk_for
from nexus_linux import windows_paste

def test_windows_hotkey_format_is_explicit_and_configurable():
    assert vk_for(DEFAULTS["panel_hotkey"]) == ord("V")
    assert vk_for("Ctrl+Shift+X") == ord("X")


def test_paste_refuses_to_send_keys_when_target_focus_cannot_be_restored(monkeypatch):
    service = windows_paste.WindowsPasteService()
    service._target = 123
    monkeypatch.setattr(windows_paste, "activate_window", lambda hwnd: False)
    assert service.synthetic_paste() is False


def test_paste_sends_only_after_foreground_verification(monkeypatch):
    service = windows_paste.WindowsPasteService()
    service._target = 123
    monkeypatch.setattr(windows_paste, "activate_window", lambda hwnd: True)
    monkeypatch.setattr(windows_paste, "foreground_window", lambda: 123)
    monkeypatch.setattr(windows_paste.user32, "SendInput", lambda count, events, size: 4)
    assert service.synthetic_paste() is True
