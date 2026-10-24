"""Hook decisions -> queued UI slot -> real transaction, no system clipboard/input."""
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent

from nexus_linux import windows_keyboard as keyboard, windows_paste as paste
from nexus_linux.windows_platform import FocusTarget, NEXUS_INPUT_MARKER
from nexus_linux.windows_stack import WindowsClipStack, WindowsStackWindow, MAX_PENDING_REQUESTS


@pytest.fixture
def transaction(qapp, storage, monkeypatch):
    r = SimpleNamespace(sequence=10, target=FocusTarget(101, 102, 103, -1), timers=[],
                        writes=[], sends=[], modifiers=False, write_ok=True, send_ok=True)
    def schedule(delay, callback):
        r.timers.append((delay, callback))
    def step():
        _, callback = r.timers.pop(0)
        callback()
    def flush():
        for _ in range(1000):
            if not r.timers:
                return
            step()
        pytest.fail("unbounded timer processing")
    r.step, r.flush = step, flush
    monkeypatch.setattr(paste.QTimer, "singleShot", schedule)
    monkeypatch.setattr(paste, "clipboard_sequence", lambda: r.sequence)
    monkeypatch.setattr(paste, "capture_focus_target", lambda: r.target)
    r.service = paste.WindowsPasteService()
    def write(item):
        if not r.write_ok:
            return False
        r.sequence += 1
        r.service._last_write_sequence = r.sequence
        r.writes.append((item.id, item.kind, item.text_content, item.image_data, item.file_path))
        return True
    def send():
        r.sends.append(r.writes[-1])
        return r.send_ok
    monkeypatch.setattr(r.service, "write_to_clipboard", write)
    monkeypatch.setattr(r.service, "_send_ctrl_v", send)
    monkeypatch.setattr(r.service, "modifiers_down", lambda: r.modifiers)
    r.stack = WindowsClipStack(storage, r.service, sequence=lambda: r.sequence)
    r.window = WindowsStackWindow(r.stack)
    r.hook = keyboard.WindowsPasteWatcher()
    r.hook.paste_detected.connect(r.window.schedule_real_paste)
    r.window.collecting_changed.connect(r.hook.set_enabled)
    r.window.start_collecting()
    def copy(text):
        r.sequence += 1
        item = storage.capture(kind="text", text_content=text)
        r.window.add_capture(item, r.sequence)
        return item
    def chord():
        assert r.hook.decide(keyboard.WM_KEYDOWN, keyboard.VK_V, ctrl=True, target=r.target)
        assert r.hook.decide(keyboard.WM_KEYUP, keyboard.VK_V)
    r.copy, r.chord = copy, chord
    yield r
    r.window.close()
    r.window.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("lifo", [False, True])
def test_actual_hook_ui_transactions_fifo_lifo_abc(transaction, storage, lifo):
    r = transaction
    items = [r.copy(text) for text in "ABC"]
    if lifo:
        r.stack.toggle_order()
    assert r.writes == []
    for _ in items:
        r.chord()  # queue three genuinely distinct releases before dispatch
    r.flush()
    expected = items[::-1] if lifo else items
    assert [entry[0] for entry in r.sends] == [i.id for i in expected]
    assert r.writes == r.sends and r.stack.items() == []
    assert all(storage.get(i.id) is not None for i in items)
    assert storage.load_stack_state()[0] == []


def test_own_marked_input_cannot_reset_held_chord_or_repeat(transaction):
    r = transaction
    r.copy("A")
    assert r.hook.decide(keyboard.WM_KEYDOWN, keyboard.VK_V, ctrl=True, target=r.target)
    for message in (keyboard.WM_KEYDOWN, keyboard.WM_KEYUP):
        assert not r.hook.decide(message, keyboard.VK_V, NEXUS_INPUT_MARKER)
    for _ in range(10):
        assert r.hook.decide(keyboard.WM_KEYDOWN, keyboard.VK_V)
    assert not r.writes
    assert r.hook.decide(keyboard.WM_KEYUP, keyboard.VK_V)  # Ctrl already up
    r.flush()
    assert len(r.sends) == 1


def test_inactive_native_empty_active_suppressed_and_restart(transaction):
    r = transaction
    r.window.stop_collecting()
    assert not r.hook.decide(keyboard.WM_KEYDOWN, keyboard.VK_V, ctrl=True, target=r.target)
    assert not r.hook.decide(keyboard.WM_KEYUP, keyboard.VK_V)
    r.window.start_collecting()
    r.chord()
    r.flush()
    assert not r.sends
    r.copy("fresh")
    r.chord()
    r.flush()
    assert len(r.sends) == 1


@pytest.mark.parametrize("change", ["stop", "restart", "copy", "order", "focus", "sequence", "delete"])
def test_waiting_transaction_invalidates_without_sending(transaction, storage, change):
    r = transaction
    a = r.copy("A")
    r.modifiers = True
    r.chord()
    r.chord()
    r.step()  # physical Ctrl still held; timer, not GUI sleep
    if change == "stop":
        r.window.stop_collecting()
    elif change == "restart":
        r.window.stop_collecting(); r.window.start_collecting()
    elif change == "copy":
        r.copy("new copy")
    elif change == "order":
        r.stack.toggle_order()
    elif change == "focus":
        r.target = FocusTarget(201, 202, 203, -1)
    elif change == "sequence":
        r.sequence += 1
    else:
        storage.delete(a.id)
    r.modifiers = False
    r.flush()
    assert not r.sends
    if change != "delete":
        assert a.id in [i.id for i in r.stack.items()]


def test_modifiers_release_then_dispatch_and_focus_loss_during_settle(transaction):
    r = transaction
    a = r.copy("A")
    r.modifiers = True
    r.chord()
    r.step()
    assert not r.sends and r.timers[0][0] == 10
    r.modifiers = False
    r.step()
    assert len(r.sends) == 1
    r.target = FocusTarget(201, 202, 203, -1)
    r.flush()
    assert [i.id for i in r.stack.items()] == [a.id]


def test_modifier_deadline_retains_history_and_queue(transaction, monkeypatch):
    r = transaction
    a = r.copy("A")
    r.modifiers = True
    r.chord()
    monkeypatch.setattr(paste.time, "monotonic", lambda: 10**12)
    r.flush()
    assert not r.sends and [i.id for i in r.stack.items()] == [a.id]


@pytest.mark.parametrize("failure", ["write", "send", "partial"])
def test_failed_dispatch_never_consumes(transaction, failure):
    r = transaction
    a = r.copy("A")
    if failure == "write":
        r.write_ok = False
    else:
        r.send_ok = False  # includes SendInput's partial-count rejection
    r.chord()
    r.flush()
    assert [i.id for i in r.stack.items()] == [a.id]


def test_queue_is_bounded_and_event_replay_never_dispatches_twice(transaction):
    r = transaction
    r.copy("A")
    requests = []
    r.hook.paste_detected.connect(requests.append)
    for _ in range(MAX_PENDING_REQUESTS + 20):
        r.chord()
    assert len(r.stack._requests) == MAX_PENDING_REQUESTS
    assert not r.window.schedule_real_paste(requests[0])
    r.flush()
    assert len(r.sends) == 1 and not r.stack._requests
    assert not r.window.schedule_real_paste(requests[0])


def test_observer_schedule_cannot_consume_without_transaction(transaction):
    r = transaction
    a = r.copy("A")
    assert not r.stack.schedule_real_paste(r.sequence)
    assert not r.stack.consume_after_real_paste()
    r.flush()
    assert not r.writes and [i.id for i in r.stack.items()] == [a.id]


def test_persistence_failure_rolls_back_visible_membership_and_keeps_history(transaction, storage, monkeypatch):
    r = transaction
    a = r.copy("synthetic persistence recovery")
    saved = storage.load_stack_state()[0]
    monkeypatch.setattr(storage, "save_stack_state", lambda *args: (_ for _ in ()).throw(OSError("synthetic failure")))
    r.chord(); r.flush()
    assert [i.id for i in r.stack.items()] == [a.id]
    assert storage.load_stack_state()[0] == saved
    assert storage.get(a.id) is not None
    assert r.stack._pending is None and not r.stack._requests


def test_partial_sendinput_attempts_marked_keyup_cleanup_without_success(monkeypatch):
    import ctypes
    calls = []
    def send(count, events, size):
        calls.append((count, [(int(e.ki.wVk), int(e.ki.dwFlags), int(e.ki.dwExtraInfo)) for e in events]))
        return 2
    monkeypatch.setattr(paste.user32, "SendInput", send)
    service = paste.WindowsPasteService()
    assert not service._send_ctrl_v()
    assert [call[0] for call in calls] == [4, 2]
    assert all(flags == paste.KEYEVENTF_KEYUP and marker == NEXUS_INPUT_MARKER
               for _, flags, marker in calls[1][1])


@pytest.mark.parametrize("operation", ["add", "clear", "order"])
def test_mutation_persistence_failure_restores_memory_and_saved_queue(transaction, storage, monkeypatch, operation):
    r = transaction
    a = r.copy("saved A")
    b = storage.capture(kind="text", text_content="new B")
    saved = storage.load_stack_state()
    def fail(*args):
        raise OSError("synthetic storage failure")
    monkeypatch.setattr(storage, "save_stack_state", fail)
    with pytest.raises(OSError):
        if operation == "add":
            r.stack.add(b)
        elif operation == "clear":
            r.stack.clear()
        else:
            r.stack.toggle_order()
    assert [item.id for item in r.stack.items()] == [a.id]
    assert storage.load_stack_state() == saved
    assert r.stack.reversed_order == saved[1]


@pytest.mark.parametrize("accepted", [(1, 0, 1, 1), (2, 1, 1), (1, 0, 0, 0)])
def test_partial_cleanup_retries_outstanding_keys_bounded(monkeypatch, caplog, accepted):
    calls = []
    results = iter(accepted)
    monkeypatch.setattr(paste.user32, "GetAsyncKeyState", lambda key: 0)
    def send(count, events, size):
        calls.append([(int(e.ki.wVk), int(e.ki.dwFlags)) for e in events])
        return next(results)
    monkeypatch.setattr(paste.user32, "SendInput", send)
    assert not paste.WindowsPasteService()._send_ctrl_v()
    assert len(calls) <= 4
    assert calls[1][0] == (0x11, paste.KEYEVENTF_KEYUP)
    if accepted == (1, 0, 0, 0):
        assert "release_unconfirmed" in caplog.text
    else:
        assert calls[-1] == [(paste.VK_V, paste.KEYEVENTF_KEYUP)]


def test_user_held_control_uses_only_V_and_never_releases_user_modifier(monkeypatch):
    calls = []
    monkeypatch.setattr(paste.user32, "GetAsyncKeyState", lambda key: 0x8000 if key == 0xA3 else 0)
    def send(count, events, size):
        calls.append([(int(e.ki.wVk), int(e.ki.dwFlags)) for e in events])
        return count
    monkeypatch.setattr(paste.user32, "SendInput", send)
    service = paste.WindowsPasteService()
    assert not service.modifiers_down()
    assert service._send_ctrl_v()
    assert calls == [[(paste.VK_V, 0), (paste.VK_V, paste.KEYEVENTF_KEYUP)]]


@pytest.mark.parametrize("kind", ["text", "url", "image", "file"])
def test_exact_typed_item_reaches_central_writer(transaction, storage, kind):
    r = transaction
    item = storage.capture(kind=kind, text_content="synthetic" if kind in ("text", "url") else None,
                           image_data=b"synthetic png" if kind == "image" else None,
                           file_path="C:/synthetic.txt" if kind == "file" else None)
    r.sequence += 1
    r.window.add_capture(item, r.sequence)
    r.chord(); r.flush()
    assert r.writes == [(item.id, item.kind, item.text_content, item.image_data, item.file_path)]


def test_hook_registration_failure_disables_and_emits_status(qapp, monkeypatch):
    hook = keyboard.WindowsPasteWatcher()
    failures = []
    hook.hook_failed.connect(failures.append)
    hook.set_enabled(True)
    monkeypatch.setattr(keyboard.user32, "PeekMessageW", lambda *args: 0)
    monkeypatch.setattr(keyboard.user32, "SetWindowsHookExW", lambda *args: 0)
    hook._run()
    assert failures and not hook._enabled and hook._thread_id == 0


def test_delayed_inactive_image_read_stays_history_even_after_render_sequence_bump(transaction, storage):
    from nexus_linux.windows_app import StackCaptureGate
    r = transaction
    r.window.stop_collecting()
    monitor = SimpleNamespace(_generation=7)
    gate = StackCaptureGate(monitor)
    image = storage.capture(kind="image", image_data=b"synthetic recovered pending image")
    r.window.start_collecting()
    gate.activated()
    r.sequence += 1  # delayed rendering can advance Win32's sequence itself
    if gate.accepts():
        r.window.add_capture(image, r.sequence)
    assert not r.stack.items() and storage.get(image.id) is not None
    monitor._generation += 1  # a genuinely NEW clipboard notification
    r.sequence += 1
    assert gate.accepts()
    r.window.add_capture(image, r.sequence)
    assert [i.id for i in r.stack.items()] == [image.id]


def test_capture_at_activation_baseline_never_becomes_pending(transaction, storage):
    r = transaction
    image = storage.capture(kind="image", image_data=b"synthetic inactive clipboard")
    r.window.add_capture(image, r.sequence)
    assert not r.stack.items() and storage.get(image.id) is not None


def test_diagnostics_correlate_requests_without_payload(transaction, monkeypatch, caplog):
    r = transaction
    monkeypatch.setenv("NEXUS_STACK_DIAGNOSTICS", "1")
    caplog.set_level("INFO", logger="nexus.stack")
    r.copy("synthetic private payload must never appear")
    r.chord(); r.flush()
    output = caplog.text
    assert "private payload" not in output
    for field in ("session=", "op=", "event=", "item=", "kind=text", "order=FIFO",
                  "state=settled", "sequence=", "hwnd=101", "result=True"):
        assert field in output
