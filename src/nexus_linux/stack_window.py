"""UI for Nexus Stack: mirrors PasteStack.swift -- opens as a fresh, empty
collection session; everything copied while it's open gets added (skipping
same-session duplicates by content hash) and the next pending item stays
armed on the clipboard. Ctrl+Shift+C is a pure show/hide toggle (see app.py);
a real Ctrl+V anywhere consumes the head and advances -- on macOS via a
global Accessibility key monitor, on Linux via evdev_stack_watcher.py when
enabled (see MIGRATION_LINUX.md, "Stack secuencial"). The "Pegar siguiente"
button stays as a manual fallback for when that watcher isn't running.

Phase 2 ("Stack 2.0"): the head item now gets its own "NEXT" hero card,
separate from the remaining-items list, so which item a real Ctrl+V is
about to consume is obvious without reading position numbers. This file
only changes *presentation* -- every mutation still goes through ClipStack
(stack.py), whose state machine and the 30 tests pinning it down in
tests/test_stack.py are untouched. The hero card is a plain ClipRowWidget
with set_selected(True): that reuses the existing accent-bordered "selected"
QSS treatment instead of inventing new styling for emphasis."""

from pathlib import Path
import sys

from PySide6.QtCore import QEvent, QSize, QTimer, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .paste_service import POST_HIDE_PASTE_DELAY_MS
from .row_widget import ClipRowWidget
from .stack import ClipStack
from . import styles

RESOURCES_DIR = Path(__file__).resolve().parent.parent.parent / "resources"
# Kept in sync with timeline_window.DISMISS_DEBOUNCE_MS by convention, not
# import, to avoid a window-module-to-window-module dependency for one
# constant -- see that module's comment for the live-observed reasoning.
DISMISS_DEBOUNCE_MS = 150


class StackWindow(QWidget):
    def __init__(self, clip_stack: ClipStack, parent=None):
        super().__init__(parent)
        self._stack = clip_stack
        self._pending_activation = False
        self._persistent_when_inactive = sys.platform == "win32"
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.setInterval(DISMISS_DEBOUNCE_MS)
        self._dismiss_timer.timeout.connect(self._dismiss_if_still_inactive)

        self.setWindowTitle("Nexus Stack")
        self.resize(320, 500)
        self.setMinimumWidth(300)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(9)
        brand = QLabel()
        brand_pixmap = QPixmap(str(RESOURCES_DIR / "nexus-icon.png"))
        if not brand_pixmap.isNull():
            brand.setPixmap(brand_pixmap.scaled(
                26, 26, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            ))
        header.addWidget(brand)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("Nexus Stack")
        title.setObjectName("title")
        self._subtitle = QLabel("")
        self._subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(self._subtitle)
        header.addLayout(title_box)
        header.addStretch()

        self._order_btn = QPushButton()
        self._order_btn.setFixedSize(36, 32)
        self._set_order_control()
        self._order_btn.clicked.connect(self._toggle_order)
        header.addWidget(self._order_btn)
        root.addLayout(header)

        next_label = QLabel("SIGUIENTE")
        next_label.setObjectName("subtitle")
        root.addWidget(next_label)

        self._next_container = QVBoxLayout()
        self._next_container.setContentsMargins(0, 0, 0, 0)
        self._next_widget: ClipRowWidget | None = None
        root.addLayout(self._next_container)

        self._remaining_label = QLabel("")
        self._remaining_label.setObjectName("subtitle")
        root.addWidget(self._remaining_label)

        self._list = QListWidget()
        self._list.setSpacing(0)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setViewportMargins(0, 0, 10, 0)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.currentItemChanged.connect(self._on_selection_changed)
        root.addWidget(self._list, stretch=1)

        self._empty_label = QLabel("Copia algo (Ctrl+C) para empezar a coleccionar en el Stack.")
        self._empty_label.setObjectName("emptyState")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        root.addWidget(self._empty_label)

        hint = QLabel(
            "Clic derecho en un elemento para eliminarlo del Stack. "
            "Ctrl+V real consume y avanza al siguiente."
        )
        hint.setObjectName("subtitle")
        hint.setWordWrap(True)
        root.addWidget(hint)

        row1 = QHBoxLayout()
        row1.addStretch()
        clear_btn = QPushButton("Vaciar Stack")
        clear_btn.setObjectName("flat")
        clear_btn.clicked.connect(self._on_clear)
        row1.addWidget(clear_btn)
        root.addLayout(row1)

        self._paste_next_btn = QPushButton("📋 Pegar siguiente (manual)")
        self._paste_next_btn.setObjectName("primary")
        self._paste_next_btn.clicked.connect(self.advance)
        root.addWidget(self._paste_next_btn)

        self.refresh()

    def is_collecting(self) -> bool:
        return self.isVisible()

    def has_pending_items(self) -> bool:
        return len(self._stack.items()) > 0

    def add_capture(self, item) -> None:
        self._stack.add(item)  # dedup-by-hash + auto-arm handled in ClipStack
        self.refresh()

    def refresh(self) -> None:
        # Theme switching clears the icon cache; rebuild the direction glyph
        # so it is tinted with the newly active palette too.
        self._set_order_control()
        items = self._stack.items()
        display_order = list(reversed(items)) if self._stack.reversed_order else items
        count = len(items)

        while self._next_container.count():
            child = self._next_container.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._next_widget = None

        if display_order:
            head = display_order[0]
            next_row = ClipRowWidget(head)
            next_row.set_selected(True)  # reuses the accent-bordered QSS treatment for emphasis
            next_row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            next_row.customContextMenuRequested.connect(
                lambda pos, item_id=head.id: self._on_next_context_menu(next_row, item_id, pos)
            )
            next_row.activated.connect(lambda item_id=head.id: self._paste_item(item_id))
            self._next_container.addWidget(next_row)
            self._next_widget = next_row

        remaining = display_order[1:]
        self._list.clear()
        for position, item in enumerate(remaining, start=2):
            list_item = QListWidgetItem()
            list_item.setData(Qt.ItemDataRole.UserRole, item.id)
            row = ClipRowWidget(item, position=position)
            list_item.setSizeHint(QSize(0, row.sizeHint().height()))
            self._list.addItem(list_item)
            self._list.setItemWidget(list_item, row)
            row.activated.connect(lambda item_id=item.id: self._paste_item(item_id))

        self._subtitle.setText(f"{count} elemento{'s' if count != 1 else ''} en total" if count else "Vacío")

        order_label = "FIFO" if not self._stack.reversed_order else "LIFO"
        self._remaining_label.setText(
            f"{len(remaining)} restante{'s' if len(remaining) != 1 else ''} · {order_label}"
            if count else ""
        )
        self._empty_label.setVisible(count == 0)
        self._list.setVisible(len(remaining) > 0)
        self._remaining_label.setVisible(count > 0)
        self._paste_next_btn.setEnabled(count > 0)

    def _on_selection_changed(self, current: QListWidgetItem, previous: QListWidgetItem) -> None:
        if previous is not None:
            row = self._list.itemWidget(previous)
            if row is not None:
                row.set_selected(False)
        if current is not None:
            row = self._list.itemWidget(current)
            if row is not None:
                row.set_selected(True)

    def _toggle_order(self) -> None:
        self._stack.toggle_order()
        self._set_order_control()
        self._order_btn.setObjectName("toggleActive" if self._stack.reversed_order else "")
        self._order_btn.style().unpolish(self._order_btn)
        self._order_btn.style().polish(self._order_btn)
        self.refresh()

    def _set_order_control(self) -> None:
        """Use a compact direction glyph instead of FIFO/LIFO jargon. The
        tooltip retains the exact behavior for discoverability and assistive
        technology, while the arrow makes the control legible at Stack's
        intentionally narrow utility-window size."""
        reversed_order = self._stack.reversed_order
        # Use the theme's secondary accent explicitly. Tech Neon defines it
        # as its magenta neon; relying on the translucent default text color
        # can render a black icon with some Qt icon backends.
        self._order_btn.setIcon(styles.icon(
            "go-up" if reversed_order else "go-down",
            color=styles.active_theme().secondary,
            size=16,
        ))
        self._order_btn.setToolTip(
            "Orden inverso: el último copiado se pega primero (LIFO)"
            if reversed_order else
            "Orden normal: el primero copiado se pega primero (FIFO)"
        )
        self._order_btn.setAccessibleName("Orden LIFO" if reversed_order else "Orden FIFO")

    def advance(self) -> None:
        """Paste the current head into whichever window has focus (not
        necessarily this one) and move to the next item.

        Hides FIRST, defers the actual arm/synthesize/consume/re-arm
        (ClipStack.paste_next_and_advance(), left completely untouched --
        see stack.py) until after that hide has had time to take effect.
        Synthesizing the Ctrl+V while this window is still active would
        send it back into Nexus itself instead of the app the user meant to
        paste into -- see paste_service.POST_HIDE_PASTE_DELAY_MS's
        docstring for the live-measured delay this relies on. Also matches
        the 'smart dismiss' philosophy: once the user has confirmed they're
        pasting from the Stack, it should get out of the way immediately,
        not just once it empties -- it reopens on the next Ctrl+Shift+C."""
        self.fade_hide()
        QTimer.singleShot(POST_HIDE_PASTE_DELAY_MS, self._do_advance)

    def _do_advance(self) -> None:
        self._stack.paste_next_and_advance()
        self.refresh()

    def _paste_item(self, item_id: int) -> None:
        item = next((candidate for candidate in self._stack.items() if candidate.id == item_id), None)
        if item is None or not self._stack.paste_service.write_to_clipboard(item):
            return
        self.fade_hide()
        QTimer.singleShot(POST_HIDE_PASTE_DELAY_MS, lambda: self._finish_item_paste(item_id))

    def _finish_item_paste(self, item_id: int) -> None:
        if self._stack.paste_service.synthetic_paste():
            self._stack._items = [item for item in self._stack.items() if item.id != item_id]
            self._stack._persist()
            self._stack.arm_next()
            self.refresh()

    def _selected_item_id(self):
        current = self._list.currentItem()
        return current.data(Qt.ItemDataRole.UserRole) if current is not None else None

    def _on_context_menu(self, pos) -> None:
        list_item = self._list.itemAt(pos)
        if list_item is None:
            return
        item_id = list_item.data(Qt.ItemDataRole.UserRole)

        menu = QMenu(self)
        delete_action = menu.addAction("Eliminar del Stack")
        chosen = menu.exec(self._list.mapToGlobal(pos))
        if chosen == delete_action:
            self._stack.remove_persistent(item_id)
            self.refresh()

    def _on_next_context_menu(self, anchor_widget, item_id: int, pos) -> None:
        menu = QMenu(self)
        delete_action = menu.addAction("Eliminar del Stack")
        chosen = menu.exec(anchor_widget.mapToGlobal(pos))
        if chosen == delete_action:
            self._stack.remove_persistent(item_id)
            self.refresh()

    def _on_clear(self) -> None:
        self._stack.clear()
        self.refresh()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.fade_hide()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            item_id = self._selected_item_id()
            if item_id is not None:
                self._stack.remove_persistent(item_id)
                self.refresh()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        # Preserve the action at the smallest supported width without making
        # Stack gain a horizontal scrollbar just to show helper wording.
        self._paste_next_btn.setText(
            "Pegar siguiente" if self.width() < 315 else "📋 Pegar siguiente (manual)"
        )
        super().resizeEvent(event)

    def _reposition_to_active_screen(self) -> None:
        """Same monitor-selection policy as Timeline (cursor position, see
        panel_geometry.py's docstring) -- Stack must appear on the screen
        Manuel is actually working on, and never partially off-screen on a
        small/portrait secondary monitor. Centered rather than bottom-
        anchored: Stack is a compact vertical utility panel, not a
        full-width timeline, so a plain centered placement (clamped to the
        target screen's available geometry) is the right fit here. Returns
        False (leaving geometry/position untouched, whatever it last was)
        if there's genuinely no screen -- KWin can transiently report zero
        outputs (observed live on Erwin, likely a DPMS/screen-blank
        recovery hiccup); calling .availableGeometry() on None would crash
        instead of just not repositioning."""
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return False
        avail = screen.availableGeometry()
        width = min(self.width(), avail.width())
        height = min(self.height(), avail.height())
        x = avail.x() + (avail.width() - width) // 2
        y = avail.y() + (avail.height() - height) // 2
        self.setGeometry(x, y, width, height)
        return True

    def show_and_focus(self) -> None:
        self.refresh()
        self._reposition_to_active_screen()
        self._pending_activation = True
        self.show()
        self.raise_()
        self.activateWindow()
        if self.isActiveWindow():
            # Some launches never fire another ActivationChange since the
            # state doesn't change (already active from a prior toggle) --
            # same fallback as TimelineWindow's _pending_focus_claim.
            self._pending_activation = False

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                self._pending_activation = False
                self._dismiss_timer.stop()
            elif not self._persistent_when_inactive and not self._pending_activation and self.isVisible():
                # Click-outside-to-dismiss, debounced -- same philosophy and
                # same reasoning as TimelineWindow.changeEvent()/
                # DISMISS_DEBOUNCE_MS (confirmed live: an undebounced version
                # could self-hide on a single transient focus blip from
                # unrelated background activity before the user's own next
                # keypress arrived).
                self._dismiss_timer.start()
        super().changeEvent(event)

    def _dismiss_if_still_inactive(self) -> None:
        if not self._persistent_when_inactive and not self.isActiveWindow() and self.isVisible():
            self.fade_hide()

    def fade_hide(self) -> None:
        # Not animated -- see MainWindow.fade_hide()'s comment and
        # MIGRATION_LINUX.md, "Animaciones".
        self.hide()
