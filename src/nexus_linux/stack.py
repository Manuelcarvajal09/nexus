"""Mirrors Nexus Stack: a collection session that arms the next clip on the
pasteboard and consumes FIFO by default or LIFO when reversed.

Unlike the Swift original (an in-memory-only session that resets when
closed), the ordered id list + FIFO/LIFO flag are persisted to
storage.stack_state on every mutation and restored on the next launch (see
load_persisted()) -- a crash or `kill` mid-session must not silently lose
whatever the user had collected but not yet pasted."""

import logging
from dataclasses import dataclass, field
from typing import Optional

from .paste_service import PasteService
from .storage import ClipItem, StorageService

log = logging.getLogger("nexus.stack")


@dataclass
class ClipStack:
    storage: StorageService
    paste_service: PasteService
    reversed_order: bool = False
    _items: list[ClipItem] = field(default_factory=list)

    def add(self, item: ClipItem) -> bool:
        """Mirrors PasteStack.swift's collectLatestCopy: skips an item
        already present in THIS session by content hash (recopying the same
        thing twice while the stack is open doesn't duplicate the row), then
        arms it on the clipboard immediately so a real Ctrl+V works without
        any extra step. Returns False if it was a same-session duplicate."""
        if any(existing.content_hash == item.content_hash for existing in self._items):
            return False
        self._items.append(item)
        self._persist()
        self.arm_next()
        return True

    def remove_persistent(self, item_id: int) -> None:
        self._items = [i for i in self._items if i.id != item_id]
        self._persist()
        self.storage.delete(item_id)

    def items(self) -> list[ClipItem]:
        return list(self._items)

    def clear(self) -> None:
        self._items.clear()
        self._persist()

    def toggle_order(self) -> None:
        """Flips FIFO/LIFO and re-arms so the new head is what's actually on
        the clipboard. Goes through ClipStack (not a bare attribute set on
        reversed_order) so the flip is persisted like every other mutation."""
        self.reversed_order = not self.reversed_order
        self._persist()
        self.arm_next()

    def _persist(self) -> None:
        self.storage.save_stack_state([i.id for i in self._items], self.reversed_order)

    def load_persisted(self) -> int:
        """Called once at startup, after construction: restores whatever
        session was still pending when Nexus last stopped. Skips (and drops)
        ids whose underlying clip_items row is gone -- deleted from history
        while it sat in the Stack, e.g. via the 30-day retention purge or a
        manual delete elsewhere in the app. Returns how many items were
        actually restored, so the caller can log it."""
        item_ids, reversed_order = self.storage.load_stack_state()
        if not item_ids:
            return 0
        restored = []
        missing = 0
        for item_id in item_ids:
            item = self.storage.get(item_id)
            if item is None:
                missing += 1
                continue
            restored.append(item)
        self._items = restored
        self.reversed_order = reversed_order
        if missing:
            log.warning("%d item(s) del Stack persistido ya no existen en el historial; se omiten.", missing)
        self._persist()  # drop the missing ids from stack_state now, not on the next mutation
        if self._items:
            self.arm_next()
        return len(self._items)

    def _next_index(self) -> Optional[int]:
        if not self._items:
            return None
        return len(self._items) - 1 if self.reversed_order else 0

    def arm_next(self) -> Optional[ClipItem]:
        """Writes the next clip (FIFO head / LIFO tail) to the system
        clipboard without consuming it, so a real Ctrl+V picks it up."""
        idx = self._next_index()
        if idx is None:
            return None
        item = self._items[idx]
        if not self.paste_service.write_to_clipboard(item):
            # Item stays the logical head either way (the caller's next real
            # Ctrl+V still targets it) -- but a failed write means the system
            # clipboard doesn't actually hold what the Stack thinks it does
            # (temporary clipboard ownership loss, wl-copy briefly
            # unavailable, etc). Previously silent; a polished app leaves a
            # trace instead of pretending the arm succeeded.
            log.warning("No se pudo escribir el item #%s al portapapeles al armarlo.", item.id)
        return item

    def consume_next(self) -> Optional[ClipItem]:
        idx = self._next_index()
        if idx is None:
            return None
        item = self._items.pop(idx)
        self._persist()
        return item

    def consume_after_real_paste(self) -> Optional[ClipItem]:
        """Called when EvdevStackWatcher detects a genuine external Ctrl+V:
        the paste already happened for real, so -- unlike
        paste_next_and_advance -- this must NOT synthesize another one, only
        drop the consumed head and arm the following item. Mirrors
        PasteStackView.consumeAfterPaste() exactly."""
        item = self.consume_next()
        if item is not None:
            self.arm_next()
        return item

    def paste_next_and_advance(self) -> Optional[ClipItem]:
        """One call = mirrors what a real Ctrl+V does in the original: paste
        the current head into whatever window has focus, remove it from the
        stack, and leave the following item armed on the clipboard ready for
        the next call. Returns the item that was pasted, or None if empty."""
        item = self.arm_next()
        if item is None:
            return None
        if not self.paste_service.synthetic_paste():
            return None
        self.consume_next()
        self.arm_next()  # ready the new head immediately, no extra step needed
        return item
