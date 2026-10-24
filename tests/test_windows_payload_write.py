"""Verify write acknowledgement/format mapping without using the OS clipboard."""
import pytest
from PySide6.QtCore import QBuffer, QIODevice, QMimeData
from PySide6.QtGui import QImage
from nexus_linux import windows_paste as paste


class Clipboard:
    owned = True
    accept = True
    sequence = 10

    def __init__(self):
        self.mime = QMimeData()
        self.mime.setText("synthetic old value")
        self.writes = 0

    def setMimeData(self, mime):
        self.writes += 1
        if self.accept:
            self.mime = mime
            self.sequence += 1

    def mimeData(self): return self.mime
    def ownsClipboard(self): return self.owned


@pytest.fixture
def clipboard(qapp, monkeypatch):
    cb = Clipboard()
    monkeypatch.setattr(paste.QGuiApplication, "clipboard", lambda: cb)
    monkeypatch.setattr(paste.QGuiApplication, "platformName", lambda: "windows")
    monkeypatch.setattr(paste, "clipboard_sequence", lambda: cb.sequence)
    return cb


def test_failed_native_ownership_is_not_reported_as_success(storage, clipboard):
    clipboard.owned = False
    service = paste.WindowsPasteService()
    item = storage.capture(kind="text", text_content="synthetic selection")
    assert service.write_to_clipboard(item) is False


def test_stale_qt_payload_is_not_mistaken_for_success(storage, clipboard):
    clipboard.accept = False
    service = paste.WindowsPasteService()
    item = storage.capture(kind="text", text_content="synthetic selection")
    assert service.write_to_clipboard(item) is False
    assert clipboard.mime.text() == "synthetic old value"


def test_explicit_text_file_and_image_writes_keep_native_qt_formats(storage, clipboard):
    service = paste.WindowsPasteService()
    text = storage.capture(kind="text", text_content="synthetic text")
    assert service.write_to_clipboard(text) and clipboard.mime.text() == text.text_content
    file = storage.capture(kind="file", file_path="C:/synthetic.txt")
    assert service.write_to_clipboard(file)
    assert clipboard.mime.urls()[0].toLocalFile() == file.file_path
    image = QImage(1700, 20, QImage.Format.Format_ARGB32); image.fill(0xFF00FF00)
    buffer = QBuffer(); buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    clip = storage.capture(kind="image", image_data=bytes(buffer.data()))
    assert service.write_to_clipboard(clip)
    assert clipboard.mime.hasImage() and clipboard.mime.imageData().size() == image.size()
    assert clipboard.mime.hasFormat(paste.WRITE_MARKER)


def test_cancelled_focus_timer_cannot_send_keys_for_a_new_operation(storage, clipboard, monkeypatch):
    callbacks, keys, results = [], [], []
    monkeypatch.setattr(paste.QTimer, "singleShot", lambda delay, callback: callbacks.append(callback))
    monkeypatch.setattr(paste, "restore_focus_target", lambda *args: True)
    monkeypatch.setattr(paste, "foreground_window", lambda: 7)
    service = paste.WindowsPasteService()
    monkeypatch.setattr(service, "_send_ctrl_v", lambda: keys.append(True) or True)
    class Target: window = 7
    class Overlay:
        def hide(self): pass
        def isVisible(self): return False
        def show_and_focus(self): pytest.fail("explicit cancellation must not reopen the overlay")
    service._target = Target()
    overlay = Overlay()
    item = storage.capture(kind="text", text_content="synthetic selected item")
    assert service.paste_item(item, overlay, results.append)
    old = callbacks.pop()
    service.cancel_pending(overlay)
    assert results == [False] and not service._busy
    assert service.paste_item(item, overlay, results.append)
    old()
    assert keys == [] and service._busy
    callbacks.pop()()
    assert results == [False, True] and keys == [True]


def test_intervening_external_clipboard_change_is_not_blessed_as_our_write(storage, clipboard, monkeypatch):
    service = paste.WindowsPasteService()
    item = storage.capture(kind="text", text_content="stack item")
    calls = 0

    def sequence_with_intervening_copy():
        nonlocal calls
        calls += 1
        if calls == 3:
            clipboard.sequence += 1
            external = QMimeData()
            external.setText("external copy")
            clipboard.mime = external
        return clipboard.sequence

    monkeypatch.setattr(paste, "clipboard_sequence", sequence_with_intervening_copy)
    assert service.write_to_clipboard(item) is False
    assert service._last_write_sequence is None
    assert clipboard.mime.text() == "external copy"
