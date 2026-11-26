"""Regression tests for the 2026-11 reliability/security/UX audit fixes."""
import time

import pytest

from nexus_linux import web_preview, windows_keyboard as keyboard, windows_paste as paste
from nexus_linux.windows_platform import FocusTarget


# -- stale modifiers (Stack stopped intercepting after Win+L / UAC) ----------

def test_fresh_recorded_modifier_outranks_lagging_async_state():
    assert keyboard.modifier_down((True, time.monotonic()), physical=False)
    assert not keyboard.modifier_down((False, time.monotonic()), physical=True)


def test_stale_recorded_modifier_yields_to_real_key_state():
    old = time.monotonic() - keyboard.MODIFIER_FRESH_S - 1
    # Win key-up never reached the hook (Win+L): it must not block Ctrl+V forever.
    assert not keyboard.modifier_down((True, old), physical=False)
    assert keyboard.modifier_down((False, old), physical=True)
    assert keyboard.modifier_down(None, physical=True)
    assert not keyboard.modifier_down(None, physical=False)


# -- paste target: never the taskbar ----------------------------------------

def test_taskbar_is_not_remembered_as_paste_target(monkeypatch):
    service = paste.WindowsPasteService()
    app = FocusTarget(0x111, 0x112, 1, -10)
    taskbar = FocusTarget(0x222, 0x222, 2, -20)
    classes = {0x111: "Notepad", 0x222: "Shell_TrayWnd"}
    monkeypatch.setattr(paste, "is_shell_surface", lambda hwnd: classes.get(hwnd) == "Shell_TrayWnd")
    monkeypatch.setattr(paste, "capture_focus_target", lambda: app)
    assert service.remember_target() == app
    monkeypatch.setattr(paste, "capture_focus_target", lambda: taskbar)
    assert service.remember_target() == app   # opened from the tray: keep the real app


def test_paste_failure_reasons_say_what_to_do():
    service = paste.WindowsPasteService()
    for result in (paste.PasteResult.TARGET_UNAVAILABLE, paste.PasteResult.FOCUS_RESTORE_FAILED,
                   paste.PasteResult.CLIPBOARD_WRITE_FAILED, paste.PasteResult.INPUT_FAILED):
        reason = service._reason(result)
        assert reason.startswith("No se pegó") and ("Ctrl+V" in reason or "otra vez" in reason or "de nuevo" in reason)


def test_tray_notice_skips_cancellations_and_rate_limits():
    from nexus_linux.windows_app import TrayController
    shown = []
    tray = TrayController.__new__(TrayController)
    tray.tray = type("T", (), {"showMessage": lambda self, *a: shown.append(a[1])})()
    tray.notify_paste_failure("Operación cancelada para conservar el portapapeles actual")
    tray.notify_paste_failure("No se pegó: uno")
    tray.notify_paste_failure("No se pegó: dos")   # within the interval
    assert shown == ["No se pegó: uno"]


# -- link previews: SSRF and one-time links ---------------------------------

@pytest.mark.parametrize("ip, safe", [
    ("8.8.8.8", True), ("100.64.1.1", False), ("10.0.0.1", False), ("127.0.0.1", False),
    ("::ffff:127.0.0.1", False), ("169.254.169.254", False), ("2606:4700::1111", True),
])
def test_only_global_addresses_are_fetched(ip, safe):
    assert web_preview._is_safe_ip(ip) is safe


@pytest.mark.parametrize("url, sensitive", [
    ("https://example.com/article?id=3", False),
    ("https://app.example.com/reset-password?token=abc", True),
    ("https://example.com/download?X-Amz-Signature=1", True),
    ("https://user:pw@example.com/", True),
    ("https://example.com/auth/magic", True),
    ("https://www.youtube.com/watch?v=abc", False),
])
def test_sensitive_links_are_detected(url, sensitive):
    assert web_preview.is_sensitive_url(url) is sensitive


def test_sensitive_link_preview_makes_no_request(monkeypatch):
    monkeypatch.setattr(web_preview, "fetch_url", lambda url: pytest.fail("network used"))
    preview = web_preview.fetch_preview("https://example.com/login?code=123", is_youtube=False)
    assert preview.domain == "example.com" and preview.title is None


def test_network_errors_become_preview_errors(monkeypatch):
    def boom(url):
        raise TimeoutError("timed out")
    monkeypatch.setattr(web_preview, "_fetch_once", boom)
    with pytest.raises(web_preview.PreviewError):
        web_preview.fetch_url("https://example.com/")


def test_https_to_http_redirect_is_rejected(monkeypatch):
    def redirect(url):
        raise web_preview._Redirect("http://example.com/")
    monkeypatch.setattr(web_preview, "_fetch_once", redirect)
    with pytest.raises(web_preview.PreviewError):
        web_preview.fetch_url("https://example.com/")


# -- storage: deleted content does not linger --------------------------------

def test_secure_delete_is_enabled_and_bloated_file_is_compacted(storage, monkeypatch):
    assert storage._conn.execute("PRAGMA secure_delete").fetchone()[0] == 1
    for n in range(40):
        storage.capture(kind="text", text_content=("x" * 50_000) + str(n))
    storage._conn.execute("DELETE FROM clip_items")
    storage._conn.commit()
    monkeypatch.setattr(type(storage), "COMPACT_MIN_FREE_BYTES", 1)
    assert storage.compact_if_bloated()
    assert storage._conn.execute("PRAGMA freelist_count").fetchone()[0] == 0


# -- UI: plain words, readable errors, visible overflow ----------------------

def test_light_theme_error_text_is_readable():
    from nexus_linux import windows_design
    from tests.test_windows_motion_final import luminance
    ink, panel = windows_design.STATUS_ERROR_LIGHT, windows_design.LIQUID_GLASS_LIGHT.panel
    ratio = (max(luminance(ink), luminance(panel)) + .05) / (min(luminance(ink), luminance(panel)) + .05)
    assert ratio >= 4.5


def test_stack_order_uses_plain_words(qapp, storage):
    from nexus_linux.windows_stack import WindowsClipStack, WindowsStackWindow
    window = WindowsStackWindow(WindowsClipStack(storage, paste.WindowsPasteService()))
    assert window._order_btn.text() == "En orden"
    window._stack.toggle_order()
    window._set_order_control()
    assert window._order_btn.text() == "Inverso" and "FIFO" not in window._order_btn.toolTip()
    window.close()
