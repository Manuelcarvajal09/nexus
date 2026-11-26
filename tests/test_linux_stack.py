"""Unification phase 2b: the shared Stack window with the Linux delivery path."""
import pytest
from PySide6.QtCore import QEvent

from nexus_linux import linux_stack
from nexus_linux.linux_stack import LinuxStackModel, LinuxStackWindow


class LinuxPaste:
    """The Linux PasteService surface the Stack uses (no clipboard, no input)."""

    def __init__(self, write_ok=True, paste_ok=True):
        self.written, self.pastes, self.write_ok, self.paste_ok = [], 0, write_ok, paste_ok

    def write_to_clipboard(self, item):
        self.written.append(item.id)
        return self.write_ok

    def synthetic_paste(self):
        self.pastes += 1
        return self.paste_ok


class Sequential:
    def __init__(self):
        self.calls = []

    def enable(self):
        self.calls.append("enable")

    def pause(self):
        self.calls.append("pause")


@pytest.fixture
def rig(qapp, storage, monkeypatch):
    timers = []
    monkeypatch.setattr(linux_stack.QTimer, "singleShot", lambda delay, callback: timers.append(callback))
    paste, sequential = LinuxPaste(), Sequential()
    model = LinuxStackModel(storage, paste)
    window = LinuxStackWindow(model, sequential)
    def copy(text):
        item = storage.capture(kind="text", text_content=text)
        window.add_capture(item)
        return item
    def flush():
        while timers:
            timers.pop(0)()
    yield window, model, paste, sequential, copy, flush
    window.close()
    window.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_collects_only_while_open_and_never_writes_on_capture(rig):
    window, model, paste, sequential, copy, _ = rig
    copy("before")
    assert model.items() == []
    window.show_and_focus(); window.start_collecting()
    a, b = copy("A"), copy("B")
    copy("A")   # same-session duplicate
    assert [i.id for i in model.items()] == [a.id, b.id]
    assert paste.written == []   # Wayland: no eager clipboard writes
    assert sequential.calls == ["enable"]
    window.stop_collecting()
    assert sequential.calls == ["enable", "pause"] and not model.active


def test_paste_next_writes_the_head_pastes_and_removes_it(rig):
    window, model, paste, _, copy, flush = rig
    window.show_and_focus(); window.start_collecting()
    a, b = copy("A"), copy("B")
    window.advance()
    assert paste.written == [a.id] and not window.isVisible()
    flush()
    assert paste.pastes == 1 and [i.id for i in model.items()] == [b.id]
    assert paste.written == [a.id]   # the next head is written only when it is pasted


def test_failed_paste_keeps_the_item_and_brings_the_stack_back(rig):
    window, model, paste, _, copy, flush = rig
    paste.paste_ok = False
    window.show_and_focus(); window.start_collecting()
    a = copy("A")
    window.advance()
    flush()
    assert [i.id for i in model.items()] == [a.id] and window.isVisible() and window.is_collecting()


def test_reversed_order_pastes_the_last_copy_first(rig):
    window, model, paste, _, copy, flush = rig
    window.show_and_focus(); window.start_collecting()
    a, b = copy("A"), copy("B")
    model.toggle_order()
    window.advance(); flush()
    assert paste.written == [b.id] and [i.id for i in model.items()] == [a.id]


def test_restored_session_does_not_touch_the_clipboard(storage):
    paste = LinuxPaste()
    first = LinuxStackModel(storage, paste); first.start_collecting()
    item = storage.capture(kind="text", text_content="pending")
    first.add(item)
    again = LinuxStackModel(storage, paste)
    assert again.load_persisted() == 1 and again._restored_ids == {item.id}
    assert paste.written == [] and not again.active


def test_sequential_callbacks_do_not_loop_back_into_the_window(qapp, storage):
    """Regression: SequentialStack.pause() calls changed(), whose app handler hides the
    Stack, which stopped collecting, which paused again... (RecursionError on Linux)."""
    class RealisticSequential:
        enabled = False
        def __init__(self):
            self.changed, self.pauses = (lambda: None), 0
        def enable(self):
            self.enabled = True; self.changed()
        def pause(self):
            self.enabled = False; self.pauses += 1; self.changed()
    sequential = RealisticSequential()
    model = LinuxStackModel(storage, LinuxPaste())
    window = LinuxStackWindow(model, sequential)
    def stack_changed():   # what app.py does
        window.refresh()
        if not model.active and not model.items():
            window.fade_hide()
    sequential.changed = stack_changed
    window.show_and_focus(); window.start_collecting()
    window.stop_collecting()
    assert sequential.pauses == 1 and not sequential.enabled
    window.close()
