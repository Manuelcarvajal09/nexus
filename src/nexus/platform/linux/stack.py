"""The shared Nexus Stack window on Linux (unification phase 2b).

The window, its layout, themes, languages, order control, clear confirmation
and motion are the Windows ones (WindowsStackWindow). Only delivery differs:

* Capture never writes the clipboard (Wayland apps may read a payload late,
  so eager writes race them). The head is written right before pasting.
* "Paste next" / activating a row: write, hide, synthetic paste through the
  Linux PasteService, then remove the item. Like on Windows, an accepted paste
  is not proof the application inserted it; History keeps the clip.
* The global Ctrl+V keeps going through SequentialStack (Hyprland transport,
  D-Bus StackPaste), which journals every delivery. The window only starts and
  pauses it with the session.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QTimer

from nexus.platform.linux.paste_service import POST_HIDE_PASTE_DELAY_MS
from nexus.core.stack import ClipStack
from nexus.platform.windows.stack import WindowsStackWindow

log = logging.getLogger("nexus.stack")


class LinuxStackModel(ClipStack):
    """ClipStack with the collecting contract the shared window expects."""

    def __init__(self, storage, paste_service, reversed_order=False):
        super().__init__(storage=storage, paste_service=paste_service, reversed_order=reversed_order)
        self.accepting_captures = False
        self._restored_ids: set[int] = set()

    def load_persisted(self, *, arm: bool = False) -> int:
        count = super().load_persisted(arm=False)   # never touch the clipboard at startup
        self._restored_ids = {item.id for item in self._items}
        self.active = self.accepting_captures = False
        return count

    def start_collecting(self) -> None:
        self.activate()
        self.accepting_captures = True

    def stop_collecting(self) -> None:
        self.active = self.accepting_captures = False

    def suspend_capture(self) -> None:
        self.accepting_captures = False

    def add(self, item, already_on_clipboard=False, arm_existing_head=False, captured_sequence=None) -> bool:
        if not (self.active and self.accepting_captures):
            return False
        return super().add(item)   # dedups and persists; no clipboard write

    def request_paste(self, *args, **kwargs) -> bool:
        return False   # the global Ctrl+V is SequentialStack's job on Linux

    def head(self):
        index = self._next_index()
        return None if index is None else self._items[index]


class LinuxStackWindow(WindowsStackWindow):
    """WindowsStackWindow with the Linux delivery path."""

    def __init__(self, model: LinuxStackModel, sequential=None, parent=None):
        self.sequential = sequential
        self._delivering = False
        self._syncing = False
        super().__init__(model, parent)

    # -- session ---------------------------------------------------------
    # SequentialStack.enable()/pause() call back into the app (changed ->
    # refresh/fade_hide -> stop_collecting): guard against re-entering.
    def start_collecting(self):
        super().start_collecting()
        if self.sequential is not None and not getattr(self.sequential, "enabled", False) and not self._syncing:
            self._syncing = True
            try:
                self.sequential.enable()
            finally:
                self._syncing = False

    def stop_collecting(self):
        super().stop_collecting()
        if self.sequential is not None and getattr(self.sequential, "enabled", True) and not self._syncing:
            self._syncing = True
            try:
                self.sequential.pause()
            finally:
                self._syncing = False

    def add_capture(self, item, sequence=None):
        if self.is_collecting() and self._stack.add(item):
            self.refresh()

    def schedule_real_paste(self, sequence=None):
        return False

    # -- manual delivery ("Paste next", activating a row) ----------------
    def _paste_item(self, item_id):
        stack = self._stack
        item = next((i for i in stack.items() if i.id == item_id), None)
        if item is None or self._delivering:
            return
        paste = stack.paste_service
        if not paste.write_to_clipboard(item):
            log.warning("stack.linux_write_failed item=%s", item_id)
            return   # the paste service already told the user why
        self._delivering = True
        self._paste_hide = True
        try:
            self.hide()   # hideEvent suspends capture; the session stays licensed
        finally:
            self._paste_hide = False
        QTimer.singleShot(POST_HIDE_PASTE_DELAY_MS, lambda: self._finish_delivery(item_id))

    def _finish_delivery(self, item_id):
        try:
            sent = bool(self._stack.paste_service.synthetic_paste())
        except Exception as exc:  # noqa: BLE001 - a paste helper failure must not kill the session
            log.warning("stack.linux_paste_exception exception_type=%s", type(exc).__name__)
            sent = False
        finally:
            self._delivering = False
        if sent:
            # remove_persistent, not consume_id: the next head is written only
            # when it is pasted (no eager clipboard writes on Wayland).
            self._stack.remove_persistent(item_id)
            log.info("stack.linux_delivered item=%s remaining=%s (sin acuse de aplicación)", item_id, len(self._stack.items()))
            self.refresh()
            self._stop_if_hidden()
        else:
            # Nothing consumed: bring the Stack back so the user can retry.
            self.show_and_focus()
            if self._stack.active:
                self._stack.accepting_captures = True
            self.refresh()

    def _do_advance(self):
        head = self._stack.head() if hasattr(self._stack, "head") else None
        if head is not None:
            self._paste_item(head.id)
