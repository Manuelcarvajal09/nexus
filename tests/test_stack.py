"""Regression suite for Nexus Stack. The scenario in test_ten_items_* is the
historical P0 bug: items could be pasted but never left the Stack, so the
next item never advanced automatically and the only way forward was the
manual "Pegar siguiente" button. Root cause was in the UI wiring
(StackWindow wasn't told to refresh after EvdevStackWatcher's real-paste
signal), not in ClipStack itself -- but ClipStack's consume/advance
semantics are the part that must never regress, so that's what's pinned
down here."""

import logging

from nexus_linux.stack import ClipStack


def _text(storage, content: str, source_app: str | None = None):
    return storage.capture(kind="text", text_content=content, source_app=source_app)


def _url(storage, url: str):
    return storage.capture(kind="url", text_content=url)


class TestBasicSequencing:
    def test_first_captured_item_is_not_rewritten_but_remains_fifo_head(self, storage, paste_service):
        stack = ClipStack(storage=storage, paste_service=paste_service)
        first, second = _text(storage, "FIRST"), _text(storage, "SECOND")

        stack.add(first, already_on_clipboard=True)
        assert paste_service.written == []

        stack.add(second, already_on_clipboard=True)
        assert paste_service.written[-1].id == first.id
        assert stack.consume_after_real_paste().id == first.id

    def test_single_item_add_and_consume_empties_stack(self, storage, paste_service):
        stack = ClipStack(storage=storage, paste_service=paste_service)
        a = _text(storage, "hello")
        stack.add(a)

        assert [i.id for i in stack.items()] == [a.id]
        consumed = stack.consume_after_real_paste()
        assert consumed.id == a.id
        assert stack.items() == []

    def test_two_items_fifo_consumes_in_copy_order(self, storage, paste_service):
        stack = ClipStack(storage=storage, paste_service=paste_service)
        a, b = _text(storage, "A"), _text(storage, "B")
        stack.add(a)
        stack.add(b)

        assert [i.id for i in stack.items()] == [a.id, b.id]
        assert stack.consume_after_real_paste().id == a.id
        assert [i.id for i in stack.items()] == [b.id]
        assert stack.consume_after_real_paste().id == b.id
        assert stack.items() == []

    def test_ten_items_sequential_real_paste_fully_drains_in_order(self, storage, paste_service):
        """The exact shape of the historical bug report: A, B, C, ... pasted
        one real Ctrl+V at a time must each disappear and the stack must
        reach empty on its own -- no leftover head, no manual nudge needed."""
        stack = ClipStack(storage=storage, paste_service=paste_service)
        items = [_text(storage, f"item-{i}") for i in range(10)]
        for item in items:
            stack.add(item)

        assert len(stack.items()) == 10

        consumed_order = []
        for _ in range(10):
            before = len(stack.items())
            consumed = stack.consume_after_real_paste()
            after = len(stack.items())
            assert after == before - 1, "each real paste must remove exactly one item"
            consumed_order.append(consumed.id)

        assert consumed_order == [i.id for i in items]
        assert stack.items() == []
        # An 11th consume on an empty stack must be a safe no-op, not a crash
        # or a re-delivery of the last item.
        assert stack.consume_after_real_paste() is None

    def test_consume_after_real_paste_on_empty_stack_is_safe_noop(self, storage, paste_service):
        stack = ClipStack(storage=storage, paste_service=paste_service)
        assert stack.consume_after_real_paste() is None
        assert stack.items() == []


class TestManualAdvanceButton:
    def test_paste_next_and_advance_synthesizes_and_consumes(self, storage, paste_service):
        stack = ClipStack(storage=storage, paste_service=paste_service)
        a, b = _text(storage, "A"), _text(storage, "B")
        stack.add(a)
        stack.add(b)

        returned = stack.paste_next_and_advance()
        assert returned.id == a.id
        assert paste_service.paste_count == 1
        assert [i.id for i in stack.items()] == [b.id]
        # arm_next() is called for both the initial add() and each advance,
        # so B (the new head) must be the most recent thing written.
        assert paste_service.written[-1].id == b.id

    def test_paste_next_and_advance_on_empty_stack_does_not_synthesize_paste(self, storage, paste_service):
        stack = ClipStack(storage=storage, paste_service=paste_service)
        assert stack.paste_next_and_advance() is None
        assert paste_service.paste_count == 0


class TestDeduplicationWithinSession:
    def test_recopying_identical_content_in_same_session_is_a_noop(self, storage, paste_service):
        """Mirrors PasteStack.swift's collectLatestCopy: intentional, not an
        arbitrary dedup rule -- recopying the same thing while the Stack is
        open must not duplicate the row."""
        stack = ClipStack(storage=storage, paste_service=paste_service)
        a1 = _text(storage, "same content")
        a2 = storage.capture(kind="text", text_content="same content")  # storage-level dedup: same row
        assert a1.id == a2.id  # confirms storage already collapsed these to one row

        assert stack.add(a1) is True
        assert stack.add(a2) is False  # same-session duplicate: rejected
        assert len(stack.items()) == 1

    def test_distinct_content_never_collapses(self, storage, paste_service):
        stack = ClipStack(storage=storage, paste_service=paste_service)
        a, b = _text(storage, "A"), _text(storage, "B")
        assert stack.add(a) is True
        assert stack.add(b) is True
        assert len(stack.items()) == 2


class TestContentVariety:
    def test_multiline_text(self, storage, paste_service):
        stack = ClipStack(storage=storage, paste_service=paste_service)
        item = _text(storage, "line one\nline two\nline three")
        stack.add(item)
        assert stack.consume_after_real_paste().text_content == "line one\nline two\nline three"

    def test_emoji_and_unicode_content(self, storage, paste_service):
        stack = ClipStack(storage=storage, paste_service=paste_service)
        payload = "café ☕️ 日本語 🚀 naïve résumé"
        item = _text(storage, payload)
        stack.add(item)
        assert stack.consume_after_real_paste().text_content == payload

    def test_url_kind(self, storage, paste_service):
        stack = ClipStack(storage=storage, paste_service=paste_service)
        item = _url(storage, "https://example.com/path?q=1")
        stack.add(item)
        consumed = stack.consume_after_real_paste()
        assert consumed.kind == "url"
        assert consumed.text_content == "https://example.com/path?q=1"

    def test_large_text_blob(self, storage, paste_service):
        stack = ClipStack(storage=storage, paste_service=paste_service)
        big = "x" * 500_000
        item = _text(storage, big)
        stack.add(item)
        assert len(stack.consume_after_real_paste().text_content) == 500_000


class TestRemovalAndClear:
    def test_remove_intermediate_item_preserves_order_of_remainder(self, storage, paste_service):
        stack = ClipStack(storage=storage, paste_service=paste_service)
        a, b, c = _text(storage, "A"), _text(storage, "B"), _text(storage, "C")
        for item in (a, b, c):
            stack.add(item)

        stack.remove_persistent(b.id)
        assert [i.id for i in stack.items()] == [a.id, c.id]
        # Stack removal is session-only; general clipboard history survives.
        assert storage.get(b.id) is not None

    def test_removing_unknown_id_is_a_safe_noop(self, storage, paste_service):
        stack = ClipStack(storage=storage, paste_service=paste_service)
        a = _text(storage, "A")
        stack.add(a)
        stack.remove_persistent(999_999)
        assert [i.id for i in stack.items()] == [a.id]

    def test_clear_empties_stack_without_touching_history(self, storage, paste_service):
        stack = ClipStack(storage=storage, paste_service=paste_service)
        a, b = _text(storage, "A"), _text(storage, "B")
        stack.add(a)
        stack.add(b)
        stack.clear()
        assert stack.items() == []
        # clear() is Stack-session-only; the underlying history rows survive.
        assert storage.get(a.id) is not None
        assert storage.get(b.id) is not None


class TestOrderToggle:
    def test_reversed_order_consumes_lifo(self, storage, paste_service):
        stack = ClipStack(storage=storage, paste_service=paste_service)
        a, b, c = _text(storage, "A"), _text(storage, "B"), _text(storage, "C")
        for item in (a, b, c):
            stack.add(item)

        stack.reversed_order = True
        stack.arm_next()  # StackWindow._toggle_order() re-arms after flipping order

        assert stack.consume_after_real_paste().id == c.id
        assert stack.consume_after_real_paste().id == b.id
        assert stack.consume_after_real_paste().id == a.id
        assert stack.items() == []


class TestClipboardWriteFailureResilience:
    """Mirrors 'pérdida temporal de clipboard ownership' from the test plan:
    wl-copy can transiently fail (compositor hiccup, race with another
    writer). The Stack's in-memory item list must never be corrupted or
    silently drop an item just because one write attempt failed."""

    def test_failed_arm_write_does_not_lose_the_item(self, storage, make_paste_service):
        flaky = make_paste_service(fail_write=True)
        stack = ClipStack(storage=storage, paste_service=flaky)
        a = _text(storage, "A")

        assert stack.add(a) is True
        assert [i.id for i in stack.items()] == [a.id]  # still logically present
        assert flaky.written == []  # write genuinely failed, nothing recorded

    def test_failed_arm_write_is_logged_not_silent(self, storage, caplog, make_paste_service):
        flaky = make_paste_service(fail_write=True)
        stack = ClipStack(storage=storage, paste_service=flaky)
        a = _text(storage, "A")
        with caplog.at_level(logging.WARNING, logger="nexus.stack"):
            stack.add(a)
        assert any("no se pudo escribir" in r.message.lower() for r in caplog.records)

    def test_failed_synthetic_paste_does_not_prevent_manual_advance(self, storage, make_paste_service):
        flaky = make_paste_service(fail_paste=True)
        stack = ClipStack(storage=storage, paste_service=flaky)
        a, b = _text(storage, "A"), _text(storage, "B")
        stack.add(a)
        stack.add(b)

        # On Windows we only consume after SendInput reports that the paste
        # was actually delivered; otherwise the remaining stack is preserved.
        returned = stack.paste_next_and_advance()
        assert returned is None
        assert [i.id for i in stack.items()] == [a.id, b.id]


class TestCrashRecovery:
    """Simulates a Nexus restart: a fresh ClipStack instance backed by the
    SAME storage (a real restart keeps the same SQLite file) must recover
    the exact session a crashed/killed process had in progress -- this is
    the 'no pérdida arbitraria' requirement from the audit, previously
    unmet since ClipStack kept no state outside memory at all."""

    def test_pending_items_survive_a_simulated_restart(self, storage, paste_service):
        first_run = ClipStack(storage=storage, paste_service=paste_service)
        a, b, c = _text(storage, "A"), _text(storage, "B"), _text(storage, "C")
        for item in (a, b, c):
            first_run.add(item)
        first_run.consume_after_real_paste()  # A pasted for real, B is now head

        # "Restart": brand new ClipStack, same underlying storage.
        second_run = ClipStack(storage=storage, paste_service=paste_service)
        assert second_run.items() == []  # nothing until explicitly restored
        restored_count = second_run.load_persisted()

        assert restored_count == 2
        assert [i.id for i in second_run.items()] == [b.id, c.id]
        # The restored head must be re-armed on the clipboard immediately.
        assert paste_service.written[-1].id == b.id

    def test_empty_stack_restarts_empty(self, storage, paste_service):
        ClipStack(storage=storage, paste_service=paste_service)  # nothing added
        second_run = ClipStack(storage=storage, paste_service=paste_service)
        assert second_run.load_persisted() == 0
        assert second_run.items() == []

    def test_reversed_order_flag_survives_restart(self, storage, paste_service):
        first_run = ClipStack(storage=storage, paste_service=paste_service)
        a, b = _text(storage, "A"), _text(storage, "B")
        first_run.add(a)
        first_run.add(b)
        first_run.toggle_order()  # LIFO

        second_run = ClipStack(storage=storage, paste_service=paste_service)
        second_run.load_persisted()
        assert second_run.reversed_order is True
        assert second_run.consume_after_real_paste().id == b.id  # LIFO: last in, first out

    def test_item_deleted_from_history_while_pending_is_dropped_on_restore(self, storage, paste_service):
        """Edge case explicitly called out in the audit: the item a Stack
        session still points to can vanish from history (retention purge,
        manual delete) between sessions. Restoring must skip it, not crash
        or resurrect a ghost entry. In practice the stack_state FK's ON
        DELETE CASCADE already prunes it at delete-time -- this confirms
        that end-to-end rather than just the Python-level fallback below."""
        first_run = ClipStack(storage=storage, paste_service=paste_service)
        a, b = _text(storage, "A"), _text(storage, "B")
        first_run.add(a)
        first_run.add(b)
        storage.delete(a.id)  # simulates retention purge / manual delete elsewhere

        second_run = ClipStack(storage=storage, paste_service=paste_service)
        restored_count = second_run.load_persisted()

        assert restored_count == 1
        assert [i.id for i in second_run.items()] == [b.id]

    def test_orphaned_stack_state_row_is_skipped_defensively(self, storage, paste_service, caplog):
        """Covers load_persisted()'s own missing-item handling directly, for
        a case the FK cascade wouldn't catch -- e.g. a stack_state row left
        over from a connection that had foreign_keys off. foreign_keys is
        toggled off only for this one raw insert, bypassing the cascade on
        purpose to exercise the defensive path in isolation; save_stack_state
        itself can't produce this state while the constraint is enforced."""
        a = _text(storage, "A")
        storage._conn.execute("PRAGMA foreign_keys = OFF")
        storage._conn.execute("INSERT INTO stack_state (position, item_id) VALUES (0, ?), (1, ?)", (a.id, 999_999))
        storage._conn.commit()
        storage._conn.execute("PRAGMA foreign_keys = ON")

        stack = ClipStack(storage=storage, paste_service=paste_service)
        with caplog.at_level(logging.WARNING, logger="nexus.stack"):
            restored_count = stack.load_persisted()

        assert restored_count == 1
        assert [i.id for i in stack.items()] == [a.id]
        assert any("ya no existen" in r.message for r in caplog.records)

    def test_clear_persists_as_empty_so_restart_does_not_resurrect_it(self, storage, paste_service):
        first_run = ClipStack(storage=storage, paste_service=paste_service)
        a = _text(storage, "A")
        first_run.add(a)
        first_run.clear()

        second_run = ClipStack(storage=storage, paste_service=paste_service)
        assert second_run.load_persisted() == 0


class TestStressSequential:
    def test_hundred_sequential_add_then_consume_cycles(self, storage, paste_service):
        """100 copy/paste cycles: order preserved, no duplicates leak
        through, stack always ends empty, no exceptions."""
        stack = ClipStack(storage=storage, paste_service=paste_service)
        produced_ids = []

        for i in range(100):
            item = _text(storage, f"stress-{i}", source_app=f"app-{i % 5}")
            stack.add(item)
            produced_ids.append(item.id)
            consumed = stack.consume_after_real_paste()
            assert consumed.id == item.id
            assert stack.items() == []

        assert len(set(produced_ids)) == 100  # all distinct rows, no id collisions

    def test_hundred_items_added_then_drained_preserves_fifo_order(self, storage, paste_service):
        stack = ClipStack(storage=storage, paste_service=paste_service)
        items = [_text(storage, f"batch-{i}") for i in range(100)]
        for item in items:
            stack.add(item)
        assert len(stack.items()) == 100

        drained = []
        while stack.items():
            drained.append(stack.consume_after_real_paste().id)

        assert drained == [i.id for i in items]
