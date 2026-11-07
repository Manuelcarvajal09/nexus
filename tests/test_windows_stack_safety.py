"""No native clipboard access: injected sequence, writes and timer queue."""
from unittest.mock import Mock
import threading
import time, uuid

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest

from nexus_linux.windows_stack import POST_PASTE_SETTLE_MS, WindowsClipStack, WindowsStackWindow
from nexus_linux.windows_keyboard import PasteRequest
from nexus_linux.windows_platform import FocusTarget


def request():
    return PasteRequest(uuid.uuid4().hex, FocusTarget(9, 10, 11, 12), time.perf_counter())


@pytest.fixture
def rig(storage):
    class Rig:
        sequence = 10
        writes = []
        timers = []
        fail_write = False
        fail_paste = False
        paste_count = 0
        focus_ok = True

        def target_matches(self, target):
            return self.focus_ok

        def paste_request(self, item, target, completion, valid):
            if not valid() or not self.write_to_clipboard(item):
                completion(False)
                return False
            self._write_sequence = self.sequence
            self.paste_count += 1
            completion(not self.fail_paste)
            return True

        def request(self):
            return self.stack.request_paste(request(), schedule=self.schedule)

        def write_to_clipboard(self, item):
            if self.fail_write:
                return False
            self.writes.append(item.id)
            self.sequence += 1
            return True

        def synthetic_paste(self):
            raise AssertionError("Manual paste must use the central paste_item")

        def paste_item(self, item, overlay, completion):
            self.manual_calls = getattr(self, "manual_calls", []) + [item.id]
            if self.fail_write:
                completion(False)
                return False
            assert self.write_to_clipboard(item)
            self._write_sequence = self.sequence
            self.completion = completion
            self.overlay = overlay
            overlay.hide()
            return True

        def complete(self, ok=True):
            if ok:
                self.paste_count += 1
            else:
                self.overlay.show_and_focus()  # Central failure UI recovery.
            self.completion(ok)

        def schedule(self, delay, callback):
            self.timers.append((delay, callback))

        def flush(self):
            pending, self.timers = self.timers, []
            for _, callback in pending:
                callback()

        def copy(self, text):
            self.sequence += 1
            item = storage.capture(kind="text", text_content=text)
            self.stack.add(item, already_on_clipboard=True)
            return item

    r = Rig()
    r.stack = WindowsClipStack(storage, r, sequence=lambda: r.sequence)
    return r


def ids(rig):
    return [i.id for i in rig.stack.items()]


def test_capture_provenance_must_match_the_live_version(rig, storage):
    rig.stack.start_collecting()
    item = storage.capture(kind="text", text_content="synthetic older snapshot")
    before = rig.sequence
    rig.sequence += 1
    assert not rig.stack.add(item, already_on_clipboard=True, captured_sequence=before)
    assert rig.stack._available is None
    assert not rig.stack.schedule_real_paste(rig.sequence, schedule=rig.schedule)
    assert ids(rig) == [] and rig.writes == []


def test_history_deletion_drops_stale_stack_references_before_next_save(rig, storage):
    rig.stack.start_collecting()
    first = rig.copy("synthetic deleted history")
    second = rig.copy("synthetic retained history")
    storage.delete(first.id)
    third = rig.copy("synthetic after deletion")
    assert ids(rig) == [second.id, third.id]
    assert storage.load_stack_state()[0] == [second.id, third.id]


def test_history_deletion_invalidates_an_already_scheduled_consumption(rig, storage):
    rig.stack.start_collecting()
    item = rig.copy("synthetic deleted during grace")
    assert rig.request()
    storage.delete(item.id)
    rig.flush()
    assert ids(rig) == [] and rig.writes == [item.id]


def test_restore_is_read_only_inactive_even_missing_and_empty(storage, rig):
    a = storage.capture(kind="text", text_content="A")
    storage.load_stack_state = Mock(return_value=([a.id, 999999], True))
    storage.save_stack_state = Mock()
    assert rig.stack.load_persisted() == 1
    assert ids(rig) == [a.id]
    assert rig.stack.reversed_order
    assert not rig.stack.active
    assert rig.stack.arm_next() is None
    storage.save_stack_state.assert_not_called()
    assert rig.writes == []
    storage.load_stack_state.return_value = ([], True)
    assert rig.stack.load_persisted() == 0
    assert rig.stack.reversed_order
    storage.save_stack_state.assert_not_called()


def test_only_active_copies_accumulate_never_write(rig, storage):
    rig.copy("ignored")
    assert ids(rig) == []
    rig.stack.start_collecting()
    a, b, c = [rig.copy(text) for text in ("A", "B", "C")]
    image = storage.capture(kind="image", image_data=b"mock PNG")
    rig.sequence += 1
    rig.stack.add(image, already_on_clipboard=True)
    assert ids(rig) == [a.id, b.id, c.id, image.id]
    assert rig.writes == []
    assert rig.request()
    assert rig.timers[0][0] == 180
    rig.flush()
    assert ids(rig) == [b.id, c.id, image.id]
    assert rig.writes == [a.id]


def test_paste_selects_fifo_not_last_copy_and_needs_no_prearm(rig):
    # Migrated observer regression: last-copy consumption was the bug, not a
    # supported policy. Each actual request now writes and consumes FIFO.
    rig.stack.start_collecting()
    a, b, c = [rig.copy(text) for text in ("A", "B", "C")]
    assert rig.request()
    assert rig.request()
    rig.flush()
    assert ids(rig) == [b.id, c.id]
    assert rig.writes == [a.id, b.id]
    rig.flush()
    assert ids(rig) == [c.id]
    assert rig.request()
    rig.flush()
    assert ids(rig) == []
    assert rig.writes == [a.id, b.id, c.id]


@pytest.mark.parametrize("change", ["stop", "restart", "copy", "sequence", "order", "remove", "clear"])
def test_delayed_paste_invalidated(rig, change):
    rig.stack.start_collecting()
    a, b = rig.copy("A"), rig.copy("B")
    assert rig.request()
    if change == "stop":
        rig.stack.stop_collecting()
    elif change == "restart":
        rig.stack.stop_collecting()
        rig.stack.start_collecting()
    elif change == "copy":
        rig.copy("C")
    elif change == "sequence":
        rig.sequence += 1  # Uncaptured/excluded clipboard change.
    elif change == "order":
        rig.stack.toggle_order()
    elif change == "remove":
        rig.stack.remove_persistent(a.id)
    else:
        rig.stack.clear()
    before = ids(rig)
    rig.flush()
    assert ids(rig) == before
    assert rig.writes == [a.id]


def test_ownership_checked_at_notification_not_just_timer(rig):
    rig.stack.start_collecting()
    rig.copy("A")
    rig.sequence += 1
    assert not rig.stack.schedule_real_paste(schedule=rig.schedule)
    assert rig.timers == []


def test_hook_snapshot_rejected_when_queued_notification_is_stale(window, rig):
    window.start_collecting()
    rig.copy("A")
    hook_request = request()
    rig.copy("B")
    assert not window.schedule_real_paste(hook_request)
    assert rig.timers == []
    assert not window.schedule_real_paste(0)
    assert window.schedule_real_paste(request())
    rig.flush()
    assert len(ids(rig)) == 1


def test_collecting_signal_only_on_transitions(window):
    transitions = []
    window.collecting_changed.connect(transitions.append)
    window.stop_collecting()
    window.start_collecting()
    window.start_collecting()
    window.fade_hide()
    window.stop_collecting()
    window.toggle_collecting()
    assert transitions == [True, False, True]


def test_old_callback_cannot_consume_new_generation_pending_paste(rig):
    rig.stack.start_collecting()
    a = rig.copy("A")
    rig.request()
    b = rig.copy("B")
    rig.request()
    old, new = rig.timers
    old[1]()
    assert ids(rig) == [a.id, b.id]
    new[1]()
    assert ids(rig) == [b.id]
    assert rig.writes == [a.id, a.id]


def test_new_copy_after_explicit_arm_cancels_advance(rig):
    rig.stack.start_collecting()
    a, b = rig.copy("A"), rig.copy("B")
    rig.stack.arm_next()
    rig.request()
    c = rig.copy("C")
    rig.flush()
    assert ids(rig) == [a.id, b.id, c.id]
    assert rig.writes == [a.id, a.id]


def test_restored_window_starts_inactive(qapp, storage, rig):
    a = storage.capture(kind="text", text_content="persisted")
    storage.save_stack_state([a.id], False)
    rig.stack.load_persisted()
    window = WindowsStackWindow(rig.stack)
    try:
        assert not window.is_collecting()
        window.show_and_focus()
        assert not window.is_collecting()  # Showing alone is not a start.
        assert rig.writes == []
        assert ids(rig) == [a.id]
    finally:
        window.close()
        window.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("lifo", [False, True])
def test_transactions_advance_in_selected_order_without_arming(rig, lifo):
    rig.stack.start_collecting()
    a, b, c = [rig.copy(text) for text in ("A", "B", "C")]
    if lifo:
        rig.stack.toggle_order()
    assert rig.writes == []
    ordered = [c, b, a] if lifo else [a, b, c]
    for item in ordered:
        assert rig.request()
        rig.flush()
        assert item.id not in ids(rig)
    assert rig.writes == [item.id for item in ordered]


@pytest.mark.parametrize("failure", ["write", "unchanged", "zero"])
def test_failed_arm_never_consumes(rig, failure):
    rig.stack.start_collecting()
    a = rig.copy("A")
    if failure == "write":
        rig.fail_write = True
    elif failure == "unchanged":
        rig.write_to_clipboard = lambda item: True
    else:
        rig.sequence = 0
    assert rig.stack.arm_next() is None
    assert ids(rig) == [a.id]


def test_arm_rejects_external_copy_between_write_ack_and_baseline(rig):
    rig.stack.start_collecting()
    item = rig.copy("A")

    def write_then_lose_ownership(selected):
        assert selected.id == item.id
        rig.sequence += 1  # the acknowledged Nexus write
        rig._last_write_sequence = rig.sequence
        rig.sequence += 1  # an external copy before the Stack can arm it
        return True

    rig.stack.paste_service.write_to_clipboard = write_then_lose_ownership
    assert rig.stack.arm_next() is None
    assert ids(rig) == [item.id]
    assert rig.stack._available is None


def test_model_manual_api_cannot_bypass_central_service(rig):
    with pytest.raises(RuntimeError, match="WindowsStackWindow.advance"):
        rig.stack.paste_next_and_advance()


@pytest.fixture
def window(qapp, rig, monkeypatch):
    monkeypatch.setattr("nexus_linux.windows_stack.QTimer.singleShot", rig.schedule)
    win = WindowsStackWindow(rig.stack)
    yield win
    win.close()
    win.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_window_resume_explicit_no_write(window, rig):
    assert not window.is_collecting()
    window._pause_btn.click()
    assert window.is_collecting()
    item = rig.copy("A")
    window.add_capture(item)
    assert rig.writes == []
    assert rig.timers == []
    window.fade_hide()
    assert not window.is_collecting()
    assert ids(rig) == [item.id]
    window._pause_btn.click()
    assert window.is_collecting()
    assert rig.writes == []
    assert not window.schedule_real_paste()  # Resume is not ownership.


def test_hook_thread_signal_runs_stack_slot_on_the_ui_thread(window, rig, qapp, monkeypatch):
    class HookSignals(QObject):
        pasted = Signal(object)
    source = HookSignals()
    threads = []
    monkeypatch.setattr(rig.stack, "request_paste", lambda *args, **kwargs: threads.append(threading.get_ident()))
    source.pasted.connect(window.schedule_real_paste)
    worker = threading.Thread(target=lambda: source.pasted.emit(rig.sequence))
    worker.start(); worker.join(timeout=2)
    assert not worker.is_alive() and threads == []
    qapp.processEvents()
    assert threads == [threading.get_ident()]


@pytest.mark.parametrize("dismiss", ["fade", "hide", "close", "escape"])
def test_window_dismiss_stops_and_invalidates(window, rig, dismiss):
    window.show()
    window.start_collecting()
    a = rig.copy("A")
    assert window.schedule_real_paste(request())
    if dismiss == "fade":
        window.fade_hide()
    elif dismiss == "hide":
        window.hide()
    elif dismiss == "close":
        window.close()
    else:
        window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    rig.flush()
    assert not window.is_collecting()
    assert not rig.stack.active
    assert ids(rig) == [a.id]


@pytest.mark.parametrize("change", [None, "copy", "restart", "stop", "fade", "close", "order"])
@pytest.mark.parametrize("when", ["before_callback", "after_callback"])
def test_manual_selection_guarded_after_hide(window, rig, change, when):
    window.show()
    window.start_collecting()
    a, b = rig.copy("A"), rig.copy("B")
    window._paste_item(b.id)
    assert not window.is_collecting()   # hidden overlay accepts no new captures
    assert rig.stack.active             # the guarded transaction may settle
    assert not window.isVisible()
    assert rig.stack._available.item_id == b.id
    assert rig.stack._available.sequence == rig.sequence
    assert ids(rig) == [a.id, b.id]
    assert not window.schedule_real_paste(rig.sequence)
    window._paste_item(a.id)  # No duplicate during focus retry or grace.
    assert rig.manual_calls == [b.id]
    if when == "after_callback":
        rig.complete()
        assert rig.timers[0][0] == 180
        assert ids(rig) == [a.id, b.id]
        window._paste_item(a.id)
        assert rig.manual_calls == [b.id]
        assert not window.schedule_real_paste(rig.sequence)
    if change == "copy":
        c = rig.copy("C")
    elif change == "restart":
        window.stop_collecting()
        window.start_collecting()
    elif change == "stop":
        window.stop_collecting()
    elif change == "fade":
        window.fade_hide()
    elif change == "close":
        window.close()
    elif change == "order":
        rig.stack.toggle_order()
    if when == "before_callback":
        rig.complete()
    rig.flush()
    if change:
        # A hidden overlay accepts no new capture, including a copy that races
        # with the paste-hide/settle window.
        assert ids(rig) == [a.id, b.id]
        assert rig.writes == [b.id]
    else:
        assert ids(rig) == [a.id]
        assert rig.writes == [b.id]
        assert rig.paste_count == 1
        assert not window.is_collecting()   # hidden after paste == CLOSED
    assert rig.stack._manual_pending is None


@pytest.mark.parametrize("lifo", [False, True])
def test_manual_button_respects_order(window, rig, lifo):
    window.start_collecting()
    a, b = rig.copy("A"), rig.copy("B")
    if lifo:
        rig.stack.toggle_order()
    window.advance()
    assert ids(rig) == [a.id, b.id]
    rig.complete()
    rig.flush()
    assert rig.writes == ([b.id] if lifo else [a.id])
    assert ids(rig) == [a.id if lifo else b.id]
    # A hidden Stack no longer intercepts Ctrl+V. Reopening explicitly
    # restores dispatch for the remaining item.
    assert not window.schedule_real_paste(request())
    window.show(); window.start_collecting()
    assert window.schedule_real_paste(request())
    rig.flush()
    assert ids(rig) == []


def test_manual_paste_auto_consumes_but_preserves_history(window, rig, storage):
    window.start_collecting()
    first, second = rig.copy("A"), rig.copy("B")
    window.advance()

    assert ids(rig) == [first.id, second.id]
    assert rig.manual_calls == [first.id]
    rig.complete()
    assert ids(rig) == [first.id, second.id]  # settle callback is still queued
    assert rig.timers[-1][0] == 180
    rig.flush()

    assert ids(rig) == [second.id]
    assert storage.get(first.id) is not None  # History remains the recovery copy.
    assert storage.load_stack_state()[0] == [second.id]
    assert rig.writes == [first.id]  # no speculative next clipboard write
    assert rig.stack._manual_pending is None


@pytest.mark.parametrize("when", ["before_callback", "after_callback"])
def test_manual_completion_does_not_consume_after_ownership_loss(window, rig, when):
    window.start_collecting()
    a = rig.copy("A")
    window._paste_item(a.id)
    if when == "after_callback":
        rig.complete()
    rig.sequence += 1  # Excluded/uncaptured external copy.
    if when == "before_callback":
        rig.complete()
    rig.flush()
    assert ids(rig) == [a.id]
    assert rig.writes == [a.id]


def test_inactive_selection_is_one_shot_without_resume(window, rig):
    window.start_collecting()
    a, b = rig.copy("A"), rig.copy("B")
    window.stop_collecting()
    window.show()
    window._paste_item(b.id)
    assert not window.is_collecting()
    rig.complete()
    assert ids(rig) == [a.id, b.id]
    rig.flush()
    assert ids(rig) == [a.id]
    assert rig.writes == [b.id]
    assert not rig.stack.active


def test_failed_central_paste_preserves_items_and_failure_ui(window, rig):
    window.start_collecting()
    a = rig.copy("A")
    window.show()
    window._paste_item(a.id)
    rig.complete(False)
    assert window.isVisible()
    assert not window.is_collecting()  # failure recovery must not resume capture
    assert ids(rig) == [a.id]
    assert rig.timers == []
    assert rig.stack._manual_pending is None
    window._paste_item(a.id)  # Failure unlocks retry.
    assert rig.manual_calls == [a.id, a.id]


def test_immediate_central_write_failure_unlocks_manual(window, rig):
    window.start_collecting()
    a = rig.copy("A")
    rig.fail_write = True
    window._paste_item(a.id)
    assert ids(rig) == [a.id]
    assert rig.stack._manual_pending is None
    assert rig.timers == []
    assert rig.writes == []


def test_busy_central_rejection_without_callback_unlocks_manual(window, rig):
    window.start_collecting()
    a = rig.copy("A")
    rig.paste_item = Mock(return_value=False)
    window._paste_item(a.id)
    assert ids(rig) == [a.id]
    assert rig.stack._manual_pending is None
    assert rig.timers == []


@pytest.mark.parametrize("sequence", [None, 0, 999999])
def test_unproven_central_write_never_consumes(window, rig, sequence):
    window.start_collecting()
    a = rig.copy("A")
    window._paste_item(a.id)
    rig._write_sequence = sequence
    rig.complete()
    rig.flush()
    assert ids(rig) == [a.id]
    assert rig.writes == [a.id]
    assert rig.stack._manual_pending is None


def test_synchronous_central_completion_is_also_delayed(window, rig):
    window.start_collecting()
    a = rig.copy("A")
    central = rig.paste_item

    def immediate(item, overlay, completion):
        accepted = central(item, overlay, completion)
        rig.complete()
        return accepted

    rig.paste_item = immediate
    window._paste_item(a.id)
    assert ids(rig) == [a.id]
    assert rig.timers[0][0] == 180
    rig.flush()
    assert ids(rig) == []


def test_hidden_stack_rejects_new_captures_during_paste_settle(window, rig):
    """The paste hides the overlay; capture must stop before the transaction ends."""
    window.show()
    window.start_collecting()
    first, pending = rig.copy("A"), rig.copy("B")
    window._paste_item(first.id)
    assert not window.isVisible()          # paste service hid the overlay
    assert rig.stack.active                # guarded dispatch may still settle
    assert not window.is_collecting()      # but no new capture is accepted
    late = rig.copy("late")
    assert ids(rig) == [first.id, pending.id]
    assert late.id not in ids(rig)
    rig.complete()
    rig.flush()
    QTest.qWait(POST_PASTE_SETTLE_MS + 40)
    assert not rig.stack.active            # hidden + settled == CLOSED
    # The late copy invalidated the guarded consume, so the first item remains
    # as History recovery; it was not silently duplicated or lost.
    assert ids(rig) == [first.id, pending.id]
    # Reopening is explicit and resumes capture without losing pending items.
    window.show()
    window.start_collecting()
    fresh = rig.copy("fresh")
    assert window.is_collecting()
    assert ids(rig) == [first.id, pending.id, fresh.id]


def test_closed_and_paused_reject_capture_and_paste(window, rig):
    assert not window.is_collecting()
    closed = rig.copy("closed")
    assert closed.id not in ids(rig)
    assert not window.schedule_real_paste(request())
    window.show()
    window.start_collecting()
    active = rig.copy("active")
    assert active.id in ids(rig)
    window.stop_collecting()
    assert not window.is_collecting()
    paused = rig.copy("paused")
    assert paused.id not in ids(rig)
    assert not window.schedule_real_paste(request())
    window.start_collecting()
    resumed = rig.copy("resumed")
    assert resumed.id in ids(rig)
    assert window.schedule_real_paste(request())


def test_paste_hide_does_not_drop_pending_items(window, rig):
    window.show()
    window.start_collecting()
    first, second = rig.copy("A"), rig.copy("B")
    window._paste_item(first.id)
    assert not window.is_collecting()
    rig.complete()
    rig.flush()
    QTest.qWait(POST_PASTE_SETTLE_MS + 40)
    assert ids(rig) == [second.id]
    window.show()
    window.start_collecting()
    third = rig.copy("C")
    assert ids(rig) == [second.id, third.id]


def _window_with(rig, storage, qapp, count):
    items = [storage.capture(kind="text", text_content=f"synthetic {n}") for n in range(count)]
    storage.save_stack_state([i.id for i in items], False)
    rig.stack.load_persisted()
    return WindowsStackWindow(rig.stack)


def _close(window, qapp):
    window.close()
    window.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_clear_stack_needs_a_second_click_for_several_items(qapp, storage, rig):
    window = _window_with(rig, storage, qapp, 3)
    try:
        button = window._clear_btn
        button.click()
        assert len(rig.stack.items()) == 3 and "otra vez" in button.text()  # armed, nothing lost
        window._disarm_clear()  # what the 3 s timer does
        assert button.text() == "Vaciar Stack"
        button.click()
        assert len(rig.stack.items()) == 3
        button.click()
        assert rig.stack.items() == [] and button.text() == "Vaciar Stack"
    finally:
        _close(window, qapp)


def test_clear_stack_disarms_when_hidden_and_skips_confirmation_for_one_item(qapp, storage, rig):
    window = _window_with(rig, storage, qapp, 2)
    try:
        window.show()
        window._clear_btn.click()
        assert window._clear_armed
        window.hide()
        assert not window._clear_armed and window._clear_btn.text() == "Vaciar Stack"
        assert len(rig.stack.items()) == 2
    finally:
        _close(window, qapp)
    single = _window_with(rig, storage, qapp, 1)
    try:
        single._clear_btn.click()
        assert rig.stack.items() == []  # a single item is trivial to recopy
    finally:
        _close(single, qapp)
