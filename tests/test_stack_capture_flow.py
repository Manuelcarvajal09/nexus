"""Copy -> clipboard monitor -> capture gate -> Stack, wired like windows_app."""
from PySide6.QtCore import QMimeData

from nexus_linux import windows_paste as paste
from nexus_linux.windows_app import StackCaptureGate
from nexus_linux.windows_stack import WindowsClipStack, WindowsStackWindow
from tests.test_windows_clipboard_safety import provider


def test_copies_while_stack_is_open_are_collected(qapp, storage, monkeypatch):
    cb, seq, callbacks, monitor, captured = provider(monkeypatch, storage)
    stack = WindowsClipStack(storage, paste.WindowsPasteService(), sequence=lambda: seq[0])
    window = WindowsStackWindow(stack)
    gate = StackCaptureGate(monitor)
    window.collecting_changed.connect(lambda active: gate.activated() if active else None)
    monitor.captured_versioned.connect(lambda item, s: window.add_capture(item, s) if gate.accepts() else None)
    window.show_and_focus()
    window.start_collecting()
    for text in ("uno", "dos", "tres"):
        seq[0] += 1
        cb.mime = QMimeData(); cb.mime.setText(text)
        cb.dataChanged.emit()
    assert [i.text_content for i in captured] == ["uno", "dos", "tres"]
    assert [i.text_content for i in stack.items()] == ["uno", "dos", "tres"]
    window.close()
