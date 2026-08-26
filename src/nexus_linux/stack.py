"""Mirrors Nexus Stack: a temporary collection session (not persisted beyond
the clip_items table itself -- the "stack" is just an ordered list of ids)
that arms the next clip on the pasteboard and consumes FIFO by default or
LIFO when reversed. Resets when closed, same as the Swift version."""

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
        self.arm_next()
        return True

    def remove_persistent(self, item_id: int) -> None:
        self._items = [i for i in self._items if i.id != item_id]
        self.storage.delete(item_id)

    def items(self) -> list[ClipItem]:
        return list(self._items)

    def clear(self) -> None:
        self._items.clear()

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
        return self._items.pop(idx)

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
        self.paste_service.synthetic_paste()
        self.consume_next()
        self.arm_next()  # ready the new head immediately, no extra step needed
        return item
