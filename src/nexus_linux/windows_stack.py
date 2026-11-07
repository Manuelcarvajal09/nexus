"""Windows-only opt-in Stack; release requests execute serial UI transactions."""
from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import logging, os, time, uuid

from PySide6.QtCore import QEvent, QObject, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QSizePolicy, QWidget

from .stack import ClipStack
from .stack_window import StackWindow
from .windows_platform import clipboard_sequence
from .windows_keyboard import PasteRequest
from . import windows_design
from .windows_material import upgrade_surface
from .windows_motion import WindowsMotion
from .windows_optical import OpticalGlass
from .windows_backdrop import configure_native_host, handle_native_host_event
from .windows_platform import activate_foreground_window

POST_PASTE_SETTLE_MS = 180
MAX_PENDING_REQUESTS = 16
log = logging.getLogger("nexus.stack")


@dataclass(frozen=True)
class _Available:
    item_id: int
    sequence: int
    armed: bool


class _WindowsRowHover(QObject):
    """Use the Windows surface states, not the shared row's animated blur.

    No per-row effects or animations remain running after a hover, and the
    system reduced-motion preference is not bypassed by a Linux row effect.
    """
    def eventFilter(self, row, event):
        if event.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            row.setProperty("hovered", "true" if event.type() == QEvent.Type.Enter else "false")
            row.style().unpolish(row)
            row.style().polish(row)
            row.update()
            return True
        return False


class WindowsClipStack(ClipStack):
    def __init__(self, storage, paste_service, reversed_order=False, *, sequence=None):
        super().__init__(storage, paste_service, reversed_order)
        self._sequence = sequence or clipboard_sequence
        self.active = False
        # Capture acceptance is separate from an in-flight paste transaction:
        # hiding the window must stop new insertions immediately, while an
        # already-dispatched paste may still need to settle and consume.
        self.accepting_captures = False
        self.generation = 0
        self._available: _Available | None = None
        self._pending = None
        self._manual_pending = None
        self._checked_changes = None
        self._invalidated_at = time.perf_counter()
        self._requests = deque()
        self._seen_events = deque(maxlen=256)
        self._event_floor = 0.0
        self._session = uuid.uuid4().hex[:12]
        self._activation_sequence = 0
        self._restored_ids = set()

    def diagnostic(self, state, request=None, item=None, result=""):
        if os.environ.get("NEXUS_STACK_DIAGNOSTICS") == "1":
            log.info("session=%s op=%s event=%s item=%s kind=%s order=%s state=%s sequence=%s hwnd=%s result=%s",
                     self._session, getattr(request, "event_id", "-"), getattr(request, "event_id", "-"),
                     getattr(item, "id", "-"), getattr(item, "kind", "-"),
                     "LIFO" if self.reversed_order else "FIFO", state, self._sequence(),
                     getattr(getattr(request, "target", None), "window", 0), result)

    def _prune_missing(self):
        # History deletion/retention cascades SQLite stack_state, but cannot
        # update these in-memory ClipItems. Check IDs only (never reload image
        # blobs) when this UI-thread connection has actually changed.
        connection = self.storage._conn
        if self._checked_changes == connection.total_changes:
            return
        self._checked_changes = connection.total_changes
        ids = [item.id for item in self._items]
        present = set()
        for offset in range(0, len(ids), 500):
            batch = ids[offset:offset + 500]
            placeholders = ",".join("?" for _ in batch)
            present.update(row[0] for row in connection.execute(
                f"SELECT id FROM clip_items WHERE id IN ({placeholders})", batch))
        if len(present) != len(ids):
            self._items = [item for item in self._items if item.id in present]
            self._invalidate()

    def items(self):
        self._prune_missing()
        return super().items()

    def _persist(self):
        self._prune_missing()
        # An unsuccessful rewrite must roll back DELETE stack_state too.
        with self.storage._conn:
            super()._persist()

    def _invalidate(self):
        self.generation += 1
        self._invalidated_at = time.perf_counter()
        self._requests.clear()
        self._available = None
        self._pending = None

    def start_collecting(self):
        if not self.active:
            self._invalidate()
            self._session = uuid.uuid4().hex[:12]
            self._activation_sequence = self._sequence()
            self.active = True
        # Reopening or resuming always re-enables capture; this is deliberately
        # idempotent when a paste transaction kept `active` true while hidden.
        self.accepting_captures = True
        self.diagnostic("active", result="explicit_activation")

    def stop_collecting(self):
        self.active = False
        self.accepting_captures = False
        self._invalidate()
        self.diagnostic("inactive", result="capture_and_dispatch_disabled")

    def suspend_capture(self):
        """Stop new insertions without invalidating an in-flight paste."""
        self.accepting_captures = False

    def load_persisted(self):
        self.stop_collecting()
        ids, self.reversed_order = self.storage.load_stack_state()
        self._items = [item for item_id in ids if (item := self.storage.get(item_id)) is not None]
        self._restored_ids = {item.id for item in self._items}
        self._manual_pending = None
        log.info("stack.baseline session=%s active=False restored_ids=%s count=%s", self._session,
                 [item.id for item in self._items], len(self._items))
        # Missing rows are skipped in memory, not repaired during startup.
        return len(self._items)

    def add(self, item, already_on_clipboard=False, arm_existing_head=False, captured_sequence=None):
        if not self.active or not self.accepting_captures:
            return False
        if captured_sequence is not None and self._activation_sequence:
            delta = (captured_sequence - self._activation_sequence) & 0xFFFFFFFF
            if not 0 < delta < 0x80000000:
                return False
        self._invalidate()
        previous = self._items[:]
        try:
            added = super().add(item, already_on_clipboard=True, arm_existing_head=False)
        except Exception as exc:
            self._items = previous
            log.error("stack.capture_save_failed item=%s exception_type=%s", item.id, type(exc).__name__)
            raise
        if already_on_clipboard:
            sequence = self._sequence()
            existing = next((i for i in self._items if i.content_hash == item.content_hash), None)
            if existing is not None and sequence and (captured_sequence is None or captured_sequence == sequence):
                self._available = _Available(existing.id, sequence, False)
        self.diagnostic("capture", item=item, result="added" if added else "duplicate")
        return added

    def _write_selected(self, item):
        self._invalidate()
        before = self._sequence()
        if not before or not self.paste_service.write_to_clipboard(item):
            return None
        after = self._sequence()
        # Prefer the sequence captured and checked inside the writer. Sampling
        # only here could accidentally bless an intervening external copy as
        # the Stack's clipboard contents.
        written = getattr(self.paste_service, "_last_write_sequence", None)
        if written is not None:
            if not written or after != written:
                return None
        elif not after or after == before:
            return None
        self._available = _Available(item.id, after, True)
        return item

    def arm_next(self):
        self._prune_missing()
        index = self._next_index()
        if not self.active or index is None:
            return None
        return self._write_selected(self._items[index])

    def remove_persistent(self, item_id):
        if any(i.id == item_id for i in self._items):
            self._invalidate()
            previous = self._items[:]
            try:
                super().remove_persistent(item_id)
            except Exception:
                self._items = previous
                raise

    def consume_id(self, item_id):
        item = next((i for i in self._items if i.id == item_id), None)
        if item is not None:
            self.remove_persistent(item_id)
        return item

    def consume_next(self):
        index = self._next_index()
        return None if index is None else self.consume_id(self._items[index].id)

    def clear(self):
        self._invalidate()
        if self._items:
            previous = self._items[:]
            try:
                super().clear()
            except Exception as exc:
                self._items = previous
                log.error("stack.clear_save_failed exception_type=%s", type(exc).__name__)
                raise

    def toggle_order(self):
        self._invalidate()
        previous = self.reversed_order
        self.reversed_order = not self.reversed_order
        try:
            self._persist()
        except Exception as exc:
            self.reversed_order = previous
            log.error("stack.order_save_failed exception_type=%s", type(exc).__name__)
            raise
        # Order controls affect manual selection, not the user's last copy.

    def _valid(self, generation, available):
        self._prune_missing()
        return (generation == self.generation and available is not None
                and available == self._available and available.sequence == self._sequence()
                and any(i.id == available.item_id for i in self._items))

    def schedule_real_paste(self, sequence=None, *, schedule=None, on_consumed=None):
        # Observer-era sequence notifications are not proof of a dispatched
        # transaction. Keep the entry point harmless for older integrations.
        return False

    def request_paste(self, request, *, schedule=None, on_consumed=None):
        if not isinstance(request, PasteRequest):
            return False
        if (not self.active or not self.accepting_captures
                or request.pressed_at < self._invalidated_at
                or request.pressed_at <= self._event_floor
                or request.event_id in self._seen_events or self._manual_pending is not None
                or len(self._requests) >= MAX_PENDING_REQUESTS):
            return False
        # Physical requests arrive in key-release order on one hook thread.
        # A timestamp floor keeps ancient replays rejected even after the
        # bounded ID cache evicts them.
        if len(self._seen_events) == self._seen_events.maxlen:
            self._event_floor = request.pressed_at
        self._seen_events.append(request.event_id)
        self._requests.append((request, self.generation))
        self.diagnostic("queued", request)
        self._run_request(schedule or QTimer.singleShot, on_consumed)
        return True

    def _run_request(self, schedule, on_consumed):
        if self._pending is not None or self._manual_pending is not None or not self._requests:
            return
        request, generation = self._requests.popleft()
        self._prune_missing()
        service = self.paste_service
        def valid():
            self._prune_missing()
            return (self.active and self.generation == generation
                    and service.target_matches(request.target))
        index = self._next_index()
        if not valid() or index is None:
            self._requests.clear()
            self.diagnostic("rejected", request)
            return
        item = self._items[index]
        token = object()
        self._pending = token
        completed = False
        self.diagnostic("dispatch", request, item)

        def release(ok):
            if self._pending is not token:
                return
            self._pending = None
            if not ok:
                self._requests.clear()
            self.diagnostic("settled", request, item, str(ok))
            self._run_request(schedule, on_consumed)

        def completion(ok):
            nonlocal completed
            if completed:
                return
            completed = True
            sequence = getattr(service, "_write_sequence", None)
            if not ok or not sequence:
                release(False)
                return

            def settle():
                if self._pending is not token:
                    return
                self._prune_missing()
                success = valid() and self._sequence() == sequence
                if success and any(i.id == item.id for i in self._items):
                    # A successful transaction changes membership, not session
                    # generation: distinct queued chords select the NEW head.
                    previous = self._items[:]
                    try:
                        ClipStack.remove_persistent(self, item.id)
                    except Exception as exc:
                        self._items = previous
                        self.diagnostic("persistence_failed", request, item, type(exc).__name__)
                        log.error("stack.consume_failed item=%s exception_type=%s", item.id, type(exc).__name__)
                        release(False)
                        return
                    self._available = None
                    if on_consumed:
                        on_consumed()
                release(success)

            schedule(POST_PASTE_SETTLE_MS, settle)

        try:
            accepted = service.paste_request(item, request.target, completion, valid)
        except Exception:
            log.warning("stack.transaction_failed event=%s", request.event_id)
            accepted = False
        if not accepted and not completed:
            release(False)

    def consume_after_real_paste(self):
        # Compatibility entry point is deferred and guarded too, never FIFO-pop.
        return self.schedule_real_paste()

    def paste_next_and_advance(self):
        raise RuntimeError("Windows manual paste requires WindowsStackWindow.advance()")


class WindowsStackWindow(StackWindow):
    collecting_changed = Signal(bool)

    def __init__(self, clip_stack: WindowsClipStack, parent=None):
        self._paste_hide = False
        self._row_hover_filter = None
        clip_stack.stop_collecting()
        super().__init__(clip_stack, parent)
        configure_native_host(self)
        self._collecting = False
        self._pause_btn.setText("Reanudar")
        self._motion = WindowsMotion(self)
        self._surface = upgrade_surface(self, self._surface)
        self._surface.layout().setContentsMargins(*([windows_design.SURFACE_MARGIN] * 4))
        self._surface.layout().setSpacing(windows_design.BLOCK_GAP)
        # Preserve room for the hero plus two remaining rows after applying
        # the shared breathing-room tokens; short work areas still clamp/scroll.
        self.resize(620, 590)
        labels = self._header_widget.findChildren(QLabel)
        self._stack_brand = labels[0] if labels else None
        if self._stack_brand is not None:
            self._stack_brand.setFixedSize(24, 24)
            self._stack_brand.setPixmap(windows_design.icon("brand", windows_design.palette().accent, 24).pixmap(24, 24))
        for button in self.findChildren(QPushButton):
            if button.text() == "×":
                button.setToolTip("Ocultar y detener Stack; conservar la lista")
                button.setAccessibleName("Cerrar y detener Stack")
                button.setText("")
                button.setIcon(windows_design.icon("close", windows_design.palette().muted, 16))
        self._height_timer = QTimer(self)
        self._height_timer.setSingleShot(True)
        self._height_timer.timeout.connect(self._adapt_height)
        # "Vaciar Stack" discards the whole pending queue: ask for a second
        # click (inline, no modal that would steal focus) before doing it.
        self._clear_armed = False
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.timeout.connect(self._disarm_clear)
        self._paste_next_btn.setText("Pegar siguiente")
        self._optical = OpticalGlass(self, self._optical_controls)
        self._paste_next_btn.setAccessibleName("Pegar el siguiente elemento del Stack")
        self._hint_label = next((label for label in self.findChildren(QLabel)
                                 if label.text().startswith("Clic derecho")), None)
        if self._hint_label is not None:
            # No always-visible legend: the same guidance lives in tooltips.
            self._hint_label.hide()
        self._paste_next_btn.setToolTip("Pegar el siguiente elemento · Ctrl+V")
        self._list.setToolTip("Clic derecho para quitar un elemento del Stack")
        self._next_label = next((label for label in self.findChildren(QLabel)
                                 if label.text() == "SIGUIENTE"), None)
        self._clear_btn = next((button for button in self.findChildren(QPushButton)
                                if button.text() == "Vaciar Stack"), None)
        self._list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.refresh()

    def apply_visual_theme(self):
        self._surface.update()
        if self._stack_brand is not None:
            self._stack_brand.setPixmap(windows_design.icon("brand", windows_design.palette().accent, 24).pixmap(24, 24))
        for button in self.findChildren(QPushButton):
            if button.objectName() == "closeButton":
                button.setIcon(windows_design.icon("close", windows_design.palette().muted, 16))
        self.refresh()

    def refresh(self):
        super().refresh()
        count = len(self._stack.items())
        self._subtitle.setText(
            f"{'Sesión activa' if self.is_collecting() else 'En pausa'} · "
            f"{count} elemento{'s' if count != 1 else ''}"
        )
        recovered = sum(item.id in self._stack._restored_ids for item in self._stack.items())
        if recovered:
            self._subtitle.setText(self._subtitle.text() + f" · {recovered} pendiente(s) recuperado(s)")
        if count:
            self._empty_label.hide()
        else:
            self._empty_label.setText(
                "Todo listo. Copia algo para comenzar." if self.is_collecting()
                else "Captura en pausa.\nReanuda para continuar."
            )
        next_label = getattr(self, "_next_label", None)
        if next_label is not None:
            next_label.setVisible(count > 0)
        clear_btn = getattr(self, "_clear_btn", None)
        if clear_btn is not None:
            clear_btn.setVisible(count > 0)
        if self._next_widget is not None:
            self._next_widget.layout().setContentsMargins(12, 12, 12, 12)
        items = self._stack.items()
        ordered = list(reversed(items)) if self._stack.reversed_order else items
        rows = ([self._next_widget] if self._next_widget is not None else []) + [self._list.itemWidget(self._list.item(i)) for i in range(self._list.count())]
        for row, item in zip(rows, ordered):
            if self._row_hover_filter is None:
                self._row_hover_filter = _WindowsRowHover(self)
            row.installEventFilter(self._row_hover_filter)
            title = row.findChild(QLabel, "rowTitle")
            if title is not None:
                title.setTextFormat(Qt.TextFormat.PlainText)
            if item.kind == "image" and item.image_data:
                continue
            badge = row.findChild(QWidget, "iconBadge")
            if badge is not None:
                label = badge.findChild(QLabel)
                if label is not None:
                    label.setPixmap(windows_design.icon({"file": "file", "url": "link"}.get(item.kind, "text"), windows_design.palette().accent, 18).pixmap(18, 18))
        self._adapt_height()
        # Layout hints settle one event-loop tick after rows are recreated.
        # A private timer avoids interfering with the paste transaction's
        # QTimer.singleShot scheduling.
        timer = getattr(self, "_height_timer", None)
        if timer is not None:
            timer.start(0)

    def _adapt_height(self):
        """Compact empty/paused states; grow with rows and cap to the work area."""
        rows = self._list.count()
        visible = min(rows, 6)
        row_h = self._list.sizeHintForRow(0) if rows else 0
        if row_h <= 0:
            row_h = 56
        list_h = 0 if visible == 0 else visible * row_h + max(0, visible - 1) * self._list.spacing() + 8
        self._list.setFixedHeight(list_h)
        self._list.setVisible(visible > 0)
        self._list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded if rows > visible else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._surface.layout().activate()
        screen = self.screen() or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        target = max(280, min(self._surface.sizeHint().height(), area.height() - 80))
        if abs(self.height() - target) > 3:
            old_center = self.frameGeometry().center()
            self.resize(self.width(), target)
            if self.isVisible():
                x = old_center.x() - self.width() // 2
                y = old_center.y() - target // 2
                x = max(area.x(), min(x, area.right() - self.width()))
                y = max(area.y(), min(y, area.bottom() - target))
                self.move(x, y)

    def _set_order_control(self):
        reverse = self._stack.reversed_order
        self._order_btn.setFixedWidth(72)
        self._order_btn.setText("LIFO" if reverse else "FIFO")
        self._order_btn.setStyleSheet("padding: 4px 8px;")
        self._order_btn.setIcon(windows_design.icon("up" if reverse else "down", windows_design.palette().accent, 18))
        self._order_btn.setToolTip("LIFO · El último copiado se pega primero" if reverse else "FIFO · El primero copiado se pega primero")
        self._order_btn.setAccessibleName("Orden LIFO" if reverse else "Orden FIFO")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._paste_next_btn.setText("Pegar siguiente")

    def _optical_controls(self):
        buttons = [self._pause_btn, self._paste_next_btn]
        buttons += [b for b in self.findChildren(QPushButton) if b.objectName() == "closeButton"]
        if getattr(self, "_clear_btn", None) is not None:
            buttons.append(self._clear_btn)
        return [(b, 14 if b.objectName() == "closeButton" else 10) for b in buttons]

    def show_and_focus(self):
        self.refresh()
        self._reposition_to_active_screen()
        self._pending_activation = True
        if not self.isVisible():
            self._optical.capture()   # before show(): Nexus itself must not be in the pixels
        self._motion.show(self)
        self.raise_()
        self.activateWindow()
        if QApplication.platformName() == "windows":
            activate_foreground_window(int(self.internalWinId()))
        if self.isActiveWindow():
            self._pending_activation = False

    def nativeEvent(self, event_type, message):
        result = handle_native_host_event(self, event_type, message)
        if result is not None:
            return result
        return super().nativeEvent(event_type, message)

    def paintEvent(self, event):
        if getattr(getattr(self, "_surface", None), "_native_backdrop", False):
            painter = QPainter(self)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(event.rect(), QColor(0, 0, 0, 0))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            self._surface.paint_native_backdrop(painter)
            optical = getattr(self, "_optical", None)
            if optical is not None:
                optical.paint_controls(painter)
            painter.end()
        else:
            painter = QPainter(self)
            painter.fillRect(event.rect(), QColor(windows_design.palette().panel))
            painter.end()

    def start_collecting(self):
        changed = not self.is_collecting()
        self._stack.start_collecting()
        self._collecting = True
        self._pause_btn.setText("Pausar")
        if changed:
            self.collecting_changed.emit(True)
        self._subtitle.setText(f"Sesión activa · {len(self._stack.items())} elementos")
        if hasattr(self, "_empty_label"):
            self._empty_label.setText("Todo listo. Copia algo para comenzar.")

    def stop_collecting(self):
        changed = self.is_collecting()
        self._stack.stop_collecting()
        self._collecting = False
        self._pause_btn.setText("Reanudar")
        # Stopping cancels not only Stack's advance timer but any focus retry
        # already scheduled by the central paste service for THIS overlay.
        cancel = getattr(self._stack.paste_service, "cancel_pending", None)
        if cancel is not None:
            cancel(self)
        if changed:
            self.collecting_changed.emit(False)
        self._subtitle.setText(f"En pausa · {len(self._stack.items())} elementos")
        if hasattr(self, "_empty_label"):
            self._empty_label.setText("Captura en pausa.\nReanuda para continuar.")

    def toggle_collecting(self):
        if self.is_collecting():
            self.stop_collecting()
        else:
            self.start_collecting()

    def capture_state(self):
        """CLOSED / ACTIVE / PAUSED; visibility and capture are one contract."""
        if not self.isVisible():
            return "CLOSED"
        return "ACTIVE" if self.is_collecting() else "PAUSED"

    def is_collecting(self):
        # Single source of truth for "may accept new captures".
        return bool(self._stack.active and self._stack.accepting_captures)

    @Slot(object, object)
    def add_capture(self, item, sequence=None):
        if self.is_collecting():
            self._stack.add(item, already_on_clipboard=True, captured_sequence=sequence)
            self.refresh()

    @Slot(object)
    def schedule_real_paste(self, sequence=None):
        return self._stack.request_paste(sequence, on_consumed=self.refresh)

    CLEAR_CONFIRM_MS = 3000

    def _on_clear(self):
        if self._clear_armed or len(self._stack.items()) < 2:
            self._disarm_clear()
            super()._on_clear()
            return
        self._clear_armed = True
        button = getattr(self, "_clear_btn", None)
        if button is not None:
            button.setText("¿Vaciar? Clic otra vez")
            button.setAccessibleName("Confirmar: vaciar todos los elementos del Stack")
        self._clear_timer.start(self.CLEAR_CONFIRM_MS)

    def _disarm_clear(self):
        self._clear_armed = False
        self._clear_timer.stop()
        button = getattr(self, "_clear_btn", None)
        if button is not None:
            button.setText("Vaciar Stack")
            button.setAccessibleName("Vaciar Stack")

    def fade_hide(self):
        self.stop_collecting()
        self._motion.hide(self)

    def is_dismissing(self):
        return self._motion.is_dismissing()

    def hideEvent(self, event):
        self._disarm_clear()
        optical = getattr(self, "_optical", None)
        if optical is not None:
            optical.release()
        if self._paste_hide:
            # The paste service hides the overlay before sending Ctrl+V. Keep
            # the transaction license, but refuse every new insertion now.
            self._stack.suspend_capture()
        else:
            self.stop_collecting()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.stop_collecting()
        super().closeEvent(event)

    def _stop_if_hidden(self):
        # The overlay is hidden during a paste so the target receives Ctrl+V.
        # Once the guarded transaction has settled, a hidden Stack is CLOSED.
        if not self.isVisible() and (self._stack.active or self._stack.accepting_captures):
            self.stop_collecting()

    def _paste_item(self, item_id):
        # Programmatic/manual one-shot is guarded by the UI button and by the
        # central paste service. Global Ctrl+V is rejected separately when
        # Stack is closed or paused (request_paste checks active state).
        stack = self._stack
        item = next((i for i in stack.items() if i.id == item_id), None)
        if item is None or stack._manual_pending is not None or stack._pending is not None:
            return
        stack._invalidate()
        generation, was_active = stack.generation, stack.active
        token = object()
        stack._manual_pending = token
        callback_seen = False

        def release():
            if stack._manual_pending is token:
                stack._manual_pending = None

        def track_write():
            # The central service records this immediately after its write,
            # before focus retries. Never infer ownership from current data.
            sequence = getattr(stack.paste_service, "_write_sequence", None)
            if (generation != stack.generation or not sequence
                    or sequence != stack._sequence()):
                return None
            available = _Available(item_id, sequence, True)
            stack._available = available
            return available

        def completed(ok):
            nonlocal callback_seen
            if callback_seen:
                return
            callback_seen = True
            available = track_write() if ok else None
            if available is None:
                release()
                self._stop_if_hidden()
                return

            def finish():
                if stack._manual_pending is not token:
                    return
                release()
                if not stack._valid(generation, available):
                    self._stop_if_hidden()
                    return
                # Automatic policy: consume only after the writer's verified
                # clipboard version, restored target, complete SendInput batch,
                # and unchanged ownership survive a short input-settle window.
                # This cannot prove insertion in arbitrary apps; History keeps
                # the original row as recovery when a target ignores Ctrl+V.
                if stack.consume_id(item_id) is None:
                    self._stop_if_hidden()
                    return
                self.refresh()
                self._stop_if_hidden()

            QTimer.singleShot(POST_PASTE_SETTLE_MS, finish)

        self._paste_hide = True
        try:
            accepted = stack.paste_service.paste_item(item, self, completed)
        except Exception:
            release()
            raise
        finally:
            self._paste_hide = False
        if not accepted:
            release()
        elif not callback_seen:
            # The central service may report completion asynchronously. Keep
            # the transaction license until finished() consumes or invalidates;
            # capture is already suspended by hideEvent.
            track_write()

    def _do_advance(self):
        self.advance()
