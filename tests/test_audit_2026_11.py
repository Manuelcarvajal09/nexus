"""Regression tests for the 2026-11 reliability/security/UX audit fixes."""
import os
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


# -- pending items, second pass ------------------------------------------------

def test_fts_search_is_literal_and_tracks_updates_and_deletes(storage):
    assert storage._fts
    a = storage.capture(kind="text", text_content="reunión con el equipo de diseño")
    b = storage.capture(kind="text", text_content="ore_code helper")
    storage.capture(kind="text", text_content="oreXcode distractor")
    ids = lambda q: {i.id for i in storage.recent(query=q)}
    assert ids("reunion") == {a.id}            # accents folded, like before
    assert ids("ore_c") == {b.id}              # '_' is a character, not a LIKE wildcard
    storage.set_title(b.id, "Snippet favorito")
    assert ids("favorito") == {b.id}
    storage.enrich_search_text(a.id, "Planeación Q4")
    assert ids("planeacion") == {a.id}
    storage.delete(a.id)
    assert ids("reunion") == set()
    assert {i.id for i in storage.recent(query="o")} >= {b.id}   # < 3 chars: LIKE path


def test_fts_unparsable_query_falls_back_to_like(storage):
    item = storage.capture(kind="text", text_content='comillas "raras" y \x01control')
    assert item.id in {i.id for i in storage.recent(query='"raras" y \x01')}


def test_listing_does_not_load_image_blobs_until_needed(storage):
    image = storage.capture(kind="image", image_data=b"\x89PNG-fake-bytes")
    listed = next(i for i in storage.recent() if i.id == image.id)
    assert "_image_data" in listed.__dict__ and listed.__dict__["_image_data"] is not b"\x89PNG-fake-bytes"
    assert listed.image_data == b"\x89PNG-fake-bytes"      # loaded on first access
    storage.delete(image.id)
    assert listed.image_data == b"\x89PNG-fake-bytes"      # already loaded: undo keeps it
    assert storage.restore_item(listed).image_data == b"\x89PNG-fake-bytes"


def test_elevated_target_is_reported_not_faked(monkeypatch):
    service = paste.WindowsPasteService()
    reasons = []
    service._on_paste_failed = reasons.append
    service._target = FocusTarget(0x111, 0x112, 1, 4242)
    service._busy, service._write_sequence, service._started_at = True, 7, time.perf_counter()
    monkeypatch.setattr(paste, "clipboard_sequence", lambda: 7)
    monkeypatch.setattr(paste, "restore_focus_target", lambda target, pid: True)
    monkeypatch.setattr(paste.WindowsPasteService, "modifiers_down", staticmethod(lambda: False))
    monkeypatch.setattr(paste, "input_blocked_by_uipi", lambda pid: pid == 4242)
    monkeypatch.setattr(service, "_send_ctrl_v", lambda: pytest.fail("keys sent to an elevated app"))
    service._restore_and_send()
    assert reasons and "administrador" in reasons[0] and not service._busy


def test_link_previews_can_be_turned_off(storage):
    from nexus_linux.preview_service import PreviewService
    service = PreviewService(storage)
    service.network_enabled = False
    assert service.request(1, "https://example.com/", False) is None and not service._inflight


def test_large_images_encode_off_thread_and_keep_copy_order(qapp, storage, monkeypatch):
    from PySide6.QtCore import QMimeData, QThreadPool
    from PySide6.QtGui import QImage
    from nexus_linux import windows_clipboard as module
    from tests.test_windows_clipboard_safety import provider
    cb, seq, callbacks, monitor, captured = provider(monkeypatch, storage)
    monitor.ASYNC_IMAGE_ENCODE = True
    image = QImage(1200, 800, QImage.Format.Format_ARGB32); image.fill(0xFF336699)
    seq[0] += 1; cb.bitmap = image; cb.mime = QMimeData(); cb.mime.setImageData(image); cb.dataChanged.emit()
    seq[0] += 1; cb.mime = QMimeData(); cb.mime.setText("copied right after"); cb.dataChanged.emit()
    assert captured == []          # the text waits behind the image being encoded
    QThreadPool.globalInstance().waitForDone(5000)
    for _ in range(50):
        qapp.processEvents()
        if len(captured) == 2:
            break
    assert [i.kind for i in captured] == ["image", "text"]
    assert QImage.fromData(captured[0].image_data, "PNG").size() == image.size()


def test_paste_watcher_survives_hook_refresh_cycles(qapp, monkeypatch):
    """Regression (v4/v5): the periodic re-install read MSG.hwnd (the field is hWnd),
    killed the watcher thread after 15 s and stopped the Stack until restart."""
    import sys
    if sys.platform != "win32" or os.environ.get("CI"):
        pytest.skip("Windows low-level hook on an interactive desktop")
    monkeypatch.setattr(keyboard, "HOOK_REFRESH_MS", 40)
    watcher = keyboard.WindowsPasteWatcher()
    failures = []
    watcher.hook_failed.connect(failures.append)
    watcher.start(enabled=False)
    try:
        deadline = time.monotonic() + 0.4
        while time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.02)
        assert watcher._thread.is_alive() and watcher._hook and not failures
    finally:
        watcher.stop()
    assert not watcher._thread.is_alive()


def test_start_with_windows_points_at_nexus_exe_when_frozen(monkeypatch):
    import sys
    from nexus_linux import windows_autostart
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Users\x\AppData\Local\Programs\Nexus\Nexus.exe")
    command = windows_autostart.launch_command()
    assert command == r'"C:\Users\x\AppData\Local\Programs\Nexus\Nexus.exe"'
    assert windows_autostart.is_ours(command) and not windows_autostart.is_ours(r'"C:\Other\App.exe"')