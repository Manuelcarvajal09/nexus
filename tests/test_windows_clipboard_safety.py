"""Synthetic providers only: these tests never claim the native clipboard."""
import pytest
from PySide6.QtCore import QEvent, QObject, QMimeData, QUrl, Signal
from PySide6.QtGui import QImage

from nexus_linux import windows_clipboard as module
from nexus_linux import windows_paste

_monitors = []


@pytest.fixture(autouse=True)
def dispose_monitors(qapp):
    yield
    for monitor in _monitors:
        monitor.stop()
        monitor.deleteLater()
    _monitors.clear()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class Clipboard(QObject):
    dataChanged = Signal()

    def __init__(self):
        super().__init__()
        self.mime = QMimeData()
        self.owned = False
        self.bitmap = QImage()

    def ownsClipboard(self): return self.owned
    def mimeData(self): return self.mime
    def image(self): return self.bitmap


def provider(monkeypatch, storage):
    clipboard = Clipboard()
    sequence = [1]
    scheduled = []
    monkeypatch.setattr(module.QGuiApplication, "clipboard", lambda: clipboard)
    monkeypatch.setattr(module, "clipboard_sequence", lambda: sequence[0])
    monkeypatch.setattr(module, "foreground_process_name", lambda: "synthetic.exe")
    monkeypatch.setattr(module, "clipboard_requests_exclusion", lambda: False)
    monkeypatch.setattr(module.QTimer, "singleShot", lambda delay, callback: scheduled.append(callback))
    monitor = module.WindowsClipboardMonitor(storage)
    _monitors.append(monitor)
    captured = []
    monitor.captured.connect(captured.append)
    return clipboard, sequence, scheduled, monitor, captured


def test_external_copy_is_not_swallowed_by_own_suppression(qapp, storage, monkeypatch):
    cb, seq, callbacks, monitor, captured = provider(monkeypatch, storage)
    monitor.suppress_next_change()
    cb.mime.setText("synthetic external")
    cb.dataChanged.emit()
    assert len(captured) == 1
    assert captured[0].text_content == "synthetic external"


def test_native_versions_deduplicate_notifications_not_genuine_recopies(qapp, storage, monkeypatch):
    cb, seq, callbacks, monitor, captured = provider(monkeypatch, storage)
    cb.mime.setText("synthetic repeat")
    cb.dataChanged.emit(); cb.dataChanged.emit()
    assert len(captured) == 1
    seq[0] += 1
    cb.dataChanged.emit()
    assert len(captured) == 2
    assert captured[0].id == captured[1].id


def test_three_consecutive_images_are_read_without_writes_or_downscaling(qapp, storage, monkeypatch):
    cb, seq, callbacks, monitor, captured = provider(monkeypatch, storage)
    for color in (0xFFFF0000, 0xFF00FF00, 0xFF0000FF):
        seq[0] += 1
        cb.bitmap = QImage(1800, 80, QImage.Format.Format_ARGB32)
        cb.bitmap.fill(color)
        cb.mime.setImageData(cb.bitmap)
        cb.dataChanged.emit()
        restored = QImage.fromData(captured[-1].image_data, "PNG")
        assert restored.size() == cb.bitmap.size()
        assert restored.pixel(0, 0) == color
    assert len({item.content_hash for item in captured}) == 3
    assert cb.owned is False  # provider has NO setter available to the monitor


def test_delayed_image_retry_is_bounded_and_cancelled_by_new_copy(qapp, storage, monkeypatch):
    cb, seq, callbacks, monitor, captured = provider(monkeypatch, storage)
    cb.mime.setImageData(QImage(1, 1, QImage.Format.Format_RGB32))
    cb.dataChanged.emit()
    assert len(callbacks) == 1 and not captured
    old_retry = callbacks.pop()
    seq[0] += 1
    cb.mime = QMimeData(); cb.mime.setText("new synthetic copy")
    cb.dataChanged.emit()
    old_retry()
    assert len(captured) == 1 and captured[0].kind == "text"
    cb.mime = QMimeData(); seq[0] += 1; cb.dataChanged.emit()
    count = 0
    while callbacks:
        callbacks.pop(0)(); count += 1
        assert count <= len(monitor.RETRY_DELAYS)
    assert len(captured) == 1


def test_delayed_image_provider_recovers_same_version(qapp, storage, monkeypatch):
    cb, seq, callbacks, monitor, captured = provider(monkeypatch, storage)
    cb.mime.setImageData(QImage(1, 1, QImage.Format.Format_RGB32))
    cb.dataChanged.emit()
    cb.bitmap = QImage(3, 4, QImage.Format.Format_RGB32); cb.bitmap.fill(0xFF00FF00)
    callbacks.pop()()
    assert captured[0].kind == "image"


def test_rendering_sequence_change_retries_a_consistent_snapshot(qapp, storage, monkeypatch):
    cb, seq, callbacks, monitor, captured = provider(monkeypatch, storage)
    cb.mime.setText("synthetic delayed format")
    snapshot = monitor._snapshot
    def render():
        result = snapshot()
        seq[0] += 1
        return result
    monkeypatch.setattr(monitor, "_snapshot", render)
    cb.dataChanged.emit()
    assert not captured and len(callbacks) == 1
    monkeypatch.setattr(monitor, "_snapshot", snapshot)
    callbacks.pop()()
    assert len(captured) == 1


def test_copy_during_sqlite_is_not_attributed_to_a_new_clipboard_version(qapp, storage, monkeypatch):
    cb, seq, callbacks, monitor, captured = provider(monkeypatch, storage)
    versions = []
    monitor.captured_versioned.connect(lambda item, version: versions.append(version))
    original = storage.capture
    def capture(**kwargs):
        item = original(**kwargs)
        seq[0] += 1  # another process can change Win32 state while SQLite runs
        return item
    monkeypatch.setattr(storage, "capture", capture)
    cb.mime.setText("synthetic earlier copy")
    cb.dataChanged.emit()
    assert len(captured) == 1  # snapshot remains valid history
    assert versions == []  # but it cannot stand for the newer live clipboard
    monkeypatch.setattr(storage, "capture", original)
    cb.mime.setText("synthetic later copy")
    cb.dataChanged.emit()
    assert versions == [seq[0]]


def test_own_writes_pause_and_exclusion_do_not_enter_history(qapp, storage, monkeypatch):
    cb, seq, callbacks, monitor, captured = provider(monkeypatch, storage)
    cb.mime.setText("synthetic secret placeholder")
    cb.owned = True; cb.dataChanged.emit()
    cb.owned = False; monitor._settings["paused"] = True; cb.dataChanged.emit()
    monitor._settings["paused"] = False
    monkeypatch.setattr(module, "clipboard_requests_exclusion", lambda: True)
    cb.dataChanged.emit()
    assert not captured and not callbacks


def test_nonlocal_urls_fall_back_to_text_and_files_remain_native(qapp, storage, monkeypatch):
    cb, seq, callbacks, monitor, captured = provider(monkeypatch, storage)
    cb.mime.setUrls([QUrl("https://example.invalid/synthetic")])
    cb.mime.setText("https://example.invalid/synthetic")
    cb.dataChanged.emit()
    assert captured[-1].kind == "url"
    seq[0] += 1; cb.mime = QMimeData()
    cb.mime.setUrls([QUrl.fromLocalFile("C:/synthetic.txt")]); cb.dataChanged.emit()
    assert captured[-1].kind == "file"
    assert cb.mime.hasUrls()


def test_stop_disconnects_and_invalidates_retries(qapp, storage, monkeypatch):
    cb, seq, callbacks, monitor, captured = provider(monkeypatch, storage)
    cb.dataChanged.emit(); monitor.stop()
    cb.mime.setText("synthetic after shutdown")
    callbacks.pop()(); cb.dataChanged.emit()
    assert not captured


def test_paste_cancelled_if_external_copy_arrives_during_focus_retry(qapp, storage, monkeypatch):
    service = windows_paste.WindowsPasteService()
    service._target = object()
    callbacks, results, writes, keys = [], [], [], []
    seq = [10]
    monkeypatch.setattr(service, "write_to_clipboard", lambda *args: writes.append(True) or True)
    monkeypatch.setattr(service, "_send_ctrl_v", lambda: keys.append(True) or True)
    monkeypatch.setattr(windows_paste, "clipboard_sequence", lambda: seq[0])
    monkeypatch.setattr(windows_paste.QTimer, "singleShot", lambda delay, callback: callbacks.append(callback))
    class Target:
        window = 7
    service._target = Target()
    class Overlay:
        def hide(self): pass
        def isVisible(self): return False
        def show_and_focus(self): pass
    item = storage.capture(kind="text", text_content="synthetic selection")
    assert service.paste_item(item, Overlay(), results.append)
    seq[0] += 1
    callbacks.pop()()
    assert results == [False] and not keys and len(writes) == 1


def test_invalid_image_does_not_arm_suppression(qapp, storage):
    suppressions = []
    service = windows_paste.WindowsPasteService(on_write=lambda: suppressions.append(True))
    image = storage.capture(kind="image", image_data=b"invalid synthetic PNG")
    assert service.write_to_clipboard(image) is False
    assert not suppressions
