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

from nexus.core.paste import PasteService
from nexus.core.storage import ClipItem, StorageService

log = logging.getLogger("nexus.stack")


@dataclass
class ClipStack:
    storage: StorageService
    paste_service: PasteService
    reversed_order: bool = False
    _items: list[ClipItem] = field(default_factory=list)
    active: bool = False
    pending_confirmation: Optional[int] = None
    _armed_id: Optional[int] = None

    def activate(self) -> None:
        """Idempotent activation; visibility never owns collection state."""
        self.active = True


    def add(
        self, item: ClipItem, already_on_clipboard: bool = False,
        arm_existing_head: bool = False,
    ) -> bool:
        """Collect and persist; recopying identical content within this session is a no-op.

        By default the user's clipboard is not touched (Linux: clipboard
        preparation is an explicit paste action). Windows passes its own flags
        through WindowsClipStack and re-arms the head itself.
        """
        if any(existing.content_hash == item.content_hash for existing in self._items):
            return False
        self.pending_confirmation = None
        self._items.append(item)
        self._persist()
        if arm_existing_head and not (already_on_clipboard and len(self._items) == 1):
            self.arm_next()
        return True

    def remove_persistent(self, item_id: int) -> None:
        self._items = [i for i in self._items if i.id != item_id]
        self._persist()
        # Removing from Stack must never delete the historical clip.
        if self.pending_confirmation == item_id:
            self.pending_confirmation = None

    def consume_id(self, item_id: int) -> Optional[ClipItem]:
        item = next((candidate for candidate in self._items if candidate.id == item_id), None)
        if item is None:
            return None
        self._items = [candidate for candidate in self._items if candidate.id != item_id]
        self._persist()
        self.arm_next()
        return item

    def items(self) -> list[ClipItem]:
        return list(self._items)

    def clear(self) -> None:
        self._items.clear()
        self.active = False
        self.pending_confirmation = None
        self._armed_id = None
        self._persist()

    def toggle_order(self) -> None:
        """Persist FIFO/LIFO without replacing the current clipboard selection."""
        self.pending_confirmation = None
        self.reversed_order = not self.reversed_order
        self._persist()

    def _persist(self) -> None:
        self.storage.save_stack_state([i.id for i in self._items], self.reversed_order)

    def load_persisted(self, *, arm: bool = True) -> int:
        """Called once at startup, after construction: restores whatever
        session was still pending when Nexus last stopped. Skips (and drops)
        ids whose underlying clip_items row is gone -- deleted from history
        while it sat in the Stack, e.g. via the 30-day retention purge or a
        manual delete elsewhere in the app. Returns how many items were
        actually restored, so the caller can log it."""
        item_ids, reversed_order = self.storage.load_stack_state()
        self.reversed_order = reversed_order
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
        if self._items and arm:
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
        self._armed_id = None
        if not self.paste_service.write_to_clipboard(item):
            # Item stays the logical head either way (the caller's next real
            # Ctrl+V still targets it) -- but a failed write means the system
            # clipboard doesn't actually hold what the Stack thinks it does
            # (temporary clipboard ownership loss, wl-copy briefly
            # unavailable, etc). Previously silent; a polished app leaves a
            # trace instead of pretending the arm succeeded.
            log.warning("No se pudo escribir el item #%s al portapapeles al armarlo.", item.id)
            return None
        self._armed_id = item.id
        return item

    def consume_next(self) -> Optional[ClipItem]:
        idx = self._next_index()
        if idx is None:
            return None
        item = self._items.pop(idx)
        self._persist()
        if not self._items:
            self.active = False
        self.pending_confirmation = None
        return item

    def consume_after_real_paste(self) -> Optional[ClipItem]:
        """Low-level consumption after explicit receipt acknowledgement only.

        Never connect a keypress detector to this method on Wayland.
        """
        item = self.consume_next()
        if item is not None:
            self.arm_next()
        return item

    def prepare_manual_paste(self) -> Optional[ClipItem]:
        """Prepare without assuming a keypress or clipboard read is a receipt."""
        self.pending_confirmation = None
        item = self.arm_next()
        if item is not None:
            self.pending_confirmation = item.id
        return item

    def confirm_pending(self) -> Optional[ClipItem]:
        """Explicit user acknowledgement; never consumes a different head."""
        idx = self._next_index()
        if idx is None or self._items[idx].id != self.pending_confirmation:
            self.pending_confirmation = None
            return None
        return self.consume_after_real_paste()

    def paste_next_and_advance(self) -> Optional[ClipItem]:
        """Legacy name: dispatch is NOT application receipt. Await confirmation."""
        self.pending_confirmation = None
        item = self.arm_next()
        if item is None:
            return None
        if not self.paste_service.synthetic_paste():
            self.pending_confirmation = None
            return None
        self.pending_confirmation = item.id
        return item
