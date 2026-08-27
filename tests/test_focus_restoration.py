"""Regression suite for the Phase 2.6 focus-restoration fix.

The bug (found by re-reading code, not by a failing test, then confirmed
live on Erwin): every "select an item and paste it" call site used to
write the clipboard and synthesize the Ctrl+V *before* hiding the Nexus
window. Synthesizing while Nexus was still the active window sent the
keystroke back into Nexus itself (its own search field, typically)
instead of the application the user actually meant to paste into.

The fix is a strict ordering -- write, then hide, then (deferred by
POST_HIDE_PASTE_DELAY_MS) synthesize -- applied identically at all three
call sites: TimelineWindow._select_and_paste, MainWindow._on_item_activated,
and StackWindow.advance()/_do_advance(). These tests pin that ordering
down so it can never silently regress back to write+synthesize+hide.

Uses the offscreen Qt platform plugin (set in conftest.py before any
QApplication is created) -- no real display or compositor needed, since
what's being verified here is *call order*, not the Wayland-specific
activation timing itself (that part can only be confirmed live; see
paste_service.py's module docstring for the live measurement behind
POST_HIDE_PASTE_DELAY_MS)."""

from PySide6.QtTest import QTest

from nexus_linux.main_window import MainWindow
from nexus_linux.stack import ClipStack
from nexus_linux.stack_window import StackWindow
from nexus_linux.timeline_window import TimelineWindow


class OrderTrackingPasteService:
    """Like conftest's FakePasteService, but also records the relative
    order of write/synthesize against a shared list a test can interleave
    its own 'hide' marker into -- the whole point here is ORDER, not just
    that each call happened."""

    def __init__(self, calls: list):
        self.calls = calls

    def write_to_clipboard(self, item) -> bool:
        self.calls.append("write")
        return True

    def synthetic_paste(self) -> bool:
        self.calls.append("synthesize")
        return True


def _text(storage, content: str):
    return storage.capture(kind="text", text_content=content)


class TestTimelineSelectAndPasteOrder:
    def test_hide_happens_before_synthesize_never_after(self, qapp, storage, monkeypatch):
        calls: list = []
        window = TimelineWindow(storage=storage, paste_service=OrderTrackingPasteService(calls))
        monkeypatch.setattr(window, "fade_hide", lambda: calls.append("hide"))
        item = _text(storage, "hello")

        window._select_and_paste(item)
        # Synthesize is deferred via QTimer.singleShot -- must not have
        # fired yet, and write+hide must already be in the correct order.
        assert calls == ["write", "hide"]

        QTest.qWait(120)
        assert calls == ["write", "hide", "synthesize"]


class TestMainWindowItemActivatedOrder:
    def test_hide_happens_before_synthesize_never_after(self, qapp, storage, monkeypatch):
        calls: list = []
        window = MainWindow(storage=storage, paste_service=OrderTrackingPasteService(calls))
        monkeypatch.setattr(window, "fade_hide", lambda: calls.append("hide"))
        item = _text(storage, "hello")

        from PySide6.QtWidgets import QListWidgetItem
        from PySide6.QtCore import Qt as _Qt

        list_item = QListWidgetItem()
        list_item.setData(_Qt.ItemDataRole.UserRole, item.id)

        window._on_item_activated(list_item)
        assert calls == ["write", "hide"]

        QTest.qWait(120)
        assert calls == ["write", "hide", "synthesize"]


class TestStackWindowAdvanceOrder:
    def test_hide_happens_before_paste_next_and_advance(self, qapp, storage, monkeypatch):
        calls: list = []
        clip_stack = ClipStack(storage=storage, paste_service=OrderTrackingPasteService(calls))
        a, b = _text(storage, "A"), _text(storage, "B")
        clip_stack.add(a)
        clip_stack.add(b)
        calls.clear()  # drop the arm_next() writes from add() above -- only advance() matters here

        window = StackWindow(clip_stack=clip_stack)
        monkeypatch.setattr(window, "fade_hide", lambda: calls.append("hide"))

        window.advance()
        # _do_advance (which calls paste_next_and_advance -> synthesize) is
        # deferred; hide must already have happened and nothing else yet.
        assert calls == ["hide"]

        QTest.qWait(120)
        assert calls[0] == "hide"
        assert "synthesize" in calls
        assert calls.index("hide") < calls.index("synthesize")
