from nexus_linux.windows_settings import DEFAULTS, vk_for

def test_windows_hotkey_format_is_explicit_and_configurable():
    assert vk_for(DEFAULTS["panel_hotkey"]) == ord("V")
    assert vk_for("Ctrl+Shift+X") == ord("X")
