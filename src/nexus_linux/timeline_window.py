"""Nexus Timeline: the Ctrl+Shift+V experience -- a surface deliberately
docked to the bottom of the active screen (see PASTE_PRODUCT_ANALYSIS.md §5
for why this shape), not a normal window that happens to open down there.
FramelessWindowHint on purpose: no title bar means no drag handle and no
resize grip to begin with, so there's no affordance for the user to
accidentally reposition or resize it, and the window never has a stored
position to fall out of sync with reality -- geometry is recomputed from
scratch by panel_geometry.py on every single open (see
resolve_target_screen()/show_and_focus() below), never persisted.

Root cause note kept for posterity (an earlier version of this window WAS
frameless, appeared to fail to hold Wayland keyboard focus, and was
temporarily reverted to a decorated window over it -- see git history):
the actual bug was that requesting child-widget focus immediately after
show(), or even one deferred event-loop tick later via
QTimer.singleShot(0, ...), races Wayland's own activation sequence.
Re-tested with FramelessWindowHint restored ALONGSIDE the real fix (tying
the focus claim to the window's genuine QEvent.ActivationChange, see
changeEvent below) and confirmed live: frameless holds focus fine. The
frameless flag was never the problem.

Keyboard model mirrors Paste's own (PASTE_PRODUCT_ANALYSIS.md §11): the
search field has focus once the window is genuinely active; Return moves
focus into the card list where Left/Right navigate and Return pastes;
typing while the list has focus redirects back to search instead of being
swallowed. Ctrl+1..9 is Quick Paste, positional over whatever's currently
displayed, independent of focus location."""

import logging

from PySide6.QtCore import QEvent, QRect, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import panel_geometry, styles, type_detection
from .paste_service import POST_HIDE_PASTE_DELAY_MS, PasteService
from .preview_service import PreviewService
from .storage import StorageService
from .timeline_delegate import CARD_HEIGHT, ClipCardDelegate
from .timeline_model import TimelineModel

log = logging.getLogger("nexus.timeline")

MAX_ITEMS = 200
# Click-outside-to-dismiss is debounced by this much before it actually
# hides the panel: confirmed live on a busy desktop (another process
# actively grabbing focus in the background) that a single transient
# deactivation blip can fire and immediately re-lose activation within the
# same event-loop tick, closing Nexus before the user's own keypress (e.g.
# Return) ever reached it. Debouncing means a GENUINE click-away (which
# stays deactivated) still dismisses promptly, while a one-frame blip that
# reactivates within the window gets cancelled by
# _dismiss_if_still_inactive's own isActiveWindow() check.
DISMISS_DEBOUNCE_MS = 150
ALL_PINBOARDS_ID = None  # sentinel: no pinboard filter, i.e. full history


class _TimelineListView(QListView):
    def __init__(self, on_type_to_search, on_activate, parent=None):
        super().__init__(parent)
        self._on_type_to_search = on_type_to_search
        self._on_activate = on_activate

    def keyPressEvent(self, event) -> None:
        key = event.key()
        # QAbstractItemView does not activate the current item on Return by
        # default in this configuration (confirmed live: it bubbled to the
        # window unhandled) -- handled explicitly instead of relying on the
        # `activated` signal firing on its own.
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            index = self.currentIndex()
            if index.isValid():
                self._on_activate(index)
            return
        text = event.text()
        no_relevant_modifier = not (
            event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
        )
        if text and text.isprintable() and no_relevant_modifier:
            self._on_type_to_search(text)
            return
        super().keyPressEvent(event)


class TimelineWindow(QWidget):
    def __init__(
        self, storage: StorageService, paste_service: PasteService,
        preview_service: PreviewService | None = None, parent=None,
    ):
        super().__init__(parent)
        self._storage = storage
        self._paste = paste_service
        self._preview_service = preview_service
        self._preview_data: dict = {}
        self._current_pinboard_id: int | None = ALL_PINBOARDS_ID
        self._pinboard_buttons: dict = {}
        self._pending_focus_claim = False
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.setInterval(DISMISS_DEBOUNCE_MS)
        self._dismiss_timer.timeout.connect(self._dismiss_if_still_inactive)

        if preview_service is not None:
            preview_service.preview_ready.connect(self._on_preview_ready)

        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setObjectName("panelRoot")

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 12, 18, 14)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(9)
        title = QLabel("Nexus")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()
        self._count_badge = QLabel("0")
        self._count_badge.setObjectName("badge")
        header.addWidget(self._count_badge)
        root.addLayout(header)

        self._pinboard_row = QHBoxLayout()
        self._pinboard_row.setSpacing(6)
        root.addLayout(self._pinboard_row)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Buscar, o simplemente empieza a escribir…")
        self._search.textChanged.connect(self._on_search_text_changed)
        self._search.returnPressed.connect(self._focus_list)
        root.addWidget(self._search)

        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(150)
        self._search_debounce.timeout.connect(self.refresh)

        self._model = TimelineModel(self)
        self._list = _TimelineListView(self._type_to_search, self._on_activated)
        self._list.setModel(self._model)
        self._delegate = ClipCardDelegate(self._list)
        self._delegate.set_preview_data(self._preview_data)
        self._list.setItemDelegate(self._delegate)
        self._list.setViewMode(QListView.ViewMode.IconMode)
        self._list.setFlow(QListView.Flow.LeftToRight)
        self._list.setWrapping(False)
        self._list.setMovement(QListView.Movement.Static)
        self._list.setResizeMode(QListView.ResizeMode.Adjust)
        self._list.setUniformItemSizes(True)
        self._list.setSpacing(0)
        # No visible scrollbar -- navigation is arrow-keys/wheel/trackpad,
        # matching the filmstrip feel from PASTE_PRODUCT_ANALYSIS.md §5
        # rather than looking like a file manager.
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setFixedHeight(CARD_HEIGHT + 16)
        self._list.activated.connect(self._on_activated)
        root.addWidget(self._list)

        self._empty_label = QLabel()
        self._empty_label.setObjectName("emptyState")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setFixedHeight(CARD_HEIGHT + 16)
        root.addWidget(self._empty_label)

        for n in range(1, 10):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{n}"), self)
            shortcut.activated.connect(lambda n=n: self._quick_paste(n))

        prev_board = QShortcut(QKeySequence("Ctrl+Left"), self)
        prev_board.activated.connect(lambda: self._cycle_pinboard(-1))
        next_board = QShortcut(QKeySequence("Ctrl+Right"), self)
        next_board.activated.connect(lambda: self._cycle_pinboard(1))

        self.refresh()

    # -- keyboard-first plumbing -------------------------------------------

    def _type_to_search(self, char: str) -> None:
        self._search.setFocus()
        self._search.setText(self._search.text() + char)
        self._search.end(False)

    def _focus_list(self) -> None:
        if self._model.rowCount() == 0:
            return
        self._list.setFocus()
        if not self._list.currentIndex().isValid():
            self._list.setCurrentIndex(self._model.index(0, 0))

    def _select_and_paste(self, item) -> None:
        """Write -> hide -> (deferred) synthesize, never the reverse.
        Synthesizing while Nexus is still the active window sends the
        Ctrl+V into Nexus itself (its own search field, typically) instead
        of the app the user meant to paste into -- confirmed as a real bug
        before this fix, not a hypothetical. See paste_service.py's
        POST_HIDE_PASTE_DELAY_MS docstring for the live measurement behind
        the delay. write_to_clipboard() itself needs no delay -- it doesn't
        touch window focus at all."""
        log.info("select_and_paste: item #%s", item.id)
        self._paste.write_to_clipboard(item)
        self.fade_hide()
        QTimer.singleShot(POST_HIDE_PASTE_DELAY_MS, self._paste.synthetic_paste)

    def _on_activated(self, index) -> None:
        item = self._model.item_at(index.row())
        if item is None:
            return
        self._select_and_paste(item)

    def _quick_paste(self, n: int) -> None:
        item = self._model.item_at(n - 1)
        if item is None:
            return
        log.info("Quick Paste #%d -> item #%s", n, item.id)
        self._select_and_paste(item)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.fade_hide()
            return
        super().keyPressEvent(event)

    def changeEvent(self, event) -> None:
        # Confirmed live (see module docstring): requesting focus for a
        # child widget any earlier -- immediately after show(), or even one
        # event-loop tick later via QTimer.singleShot(0, ...) -- races
        # Wayland's own activation sequence. isActiveWindow() was still
        # False at that point, so the focus request silently didn't stick,
        # and once real activation landed afterward Qt fell back to some
        # other widget instead. Tying the claim to this actual activation
        # event is what's reliable.
        if event.type() == QEvent.Type.ActivationChange:
            log.debug("ActivationChange: isActiveWindow=%s pending_claim=%s", self.isActiveWindow(), self._pending_focus_claim)
            if self.isActiveWindow():
                self._dismiss_timer.stop()  # reactivated within the debounce window -- cancel any pending dismiss
                if self._pending_focus_claim:
                    self._pending_focus_claim = False
                    self._search.setFocus()
                    self._search.selectAll()
            elif not self._pending_focus_claim and self.isVisible():
                # Click-outside-to-dismiss / alt-tab-away-to-dismiss: once
                # Nexus has genuinely become active this session (not
                # _pending_focus_claim), losing activation means the user
                # switched to something else -- treat it like Escape.
                # Debounced (DISMISS_DEBOUNCE_MS): confirmed live on a busy
                # desktop that a single-frame deactivation blip (some other
                # process briefly grabbing focus) can fire and be reversed
                # within the same tick -- hiding immediately closed Nexus
                # before the user's own next keypress ever reached it.
                # _dismiss_if_still_inactive re-checks isActiveWindow() when
                # the timer fires, so a blip that already reactivated by
                # then is a no-op.
                self._dismiss_timer.start()
        super().changeEvent(event)

    def _dismiss_if_still_inactive(self) -> None:
        if not self.isActiveWindow() and self.isVisible():
            log.info("click-outside-dismiss: perdí activación, ocultando")
            self.fade_hide()

    # -- pinboard filter chips ----------------------------------------------

    def _cycle_pinboard(self, direction: int) -> None:
        ids = [ALL_PINBOARDS_ID] + [b.id for b in self._storage.list_pinboards()]
        try:
            idx = ids.index(self._current_pinboard_id)
        except ValueError:
            idx = 0
        self._select_pinboard(ids[(idx + direction) % len(ids)])

    def _select_pinboard(self, pinboard_id) -> None:
        self._current_pinboard_id = pinboard_id
        for pid, btn in self._pinboard_buttons.items():
            btn.setChecked(pid == pinboard_id)
        self.refresh()

    def _rebuild_pinboard_chips(self) -> None:
        while self._pinboard_row.count():
            child = self._pinboard_row.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._pinboard_buttons.clear()

        all_btn = QPushButton("Historial")
        all_btn.setObjectName("flat")
        all_btn.setCheckable(True)
        all_btn.setChecked(self._current_pinboard_id == ALL_PINBOARDS_ID)
        all_btn.clicked.connect(lambda: self._select_pinboard(ALL_PINBOARDS_ID))
        self._pinboard_row.addWidget(all_btn)
        self._pinboard_buttons[ALL_PINBOARDS_ID] = all_btn

        for board in self._storage.list_pinboards():
            btn = QPushButton(board.display_name)
            btn.setObjectName("flat")
            btn.setCheckable(True)
            btn.setChecked(self._current_pinboard_id == board.id)
            btn.clicked.connect(lambda _checked, bid=board.id: self._select_pinboard(bid))
            self._pinboard_row.addWidget(btn)
            self._pinboard_buttons[board.id] = btn

        self._pinboard_row.addStretch()

    # -- data -----------------------------------------------------------

    def refresh(self) -> None:
        self._rebuild_pinboard_chips()
        query = self._search.text().strip()
        items = self._storage.recent(limit=MAX_ITEMS, query=query, pinboard_id=self._current_pinboard_id)
        self._model.set_items(items)
        self._request_previews(items)

        self._count_badge.setText(str(len(items)))
        empty = len(items) == 0
        if empty and query:
            self._empty_label.setText(f"Sin resultados para «{query}».")
        elif empty and self._current_pinboard_id is not None:
            self._empty_label.setText("Este pinboard está vacío.")
        else:
            self._empty_label.setText("Tu historial está vacío. Copia algo para empezar.")
        self._empty_label.setVisible(empty)
        self._list.setVisible(not empty)
        if items:
            self._list.setCurrentIndex(self._model.index(0, 0))

    def _on_search_text_changed(self) -> None:
        self._search_debounce.start()

    def _request_previews(self, items: list) -> None:
        """Cache-hit results land in self._preview_data synchronously (a
        local SQLite read, not network) so the very first paint() already
        shows enriched cards for anything already fetched -- most items,
        after the first time each URL is seen, since app.py also requests a
        preview at capture time (prefetch), not just when the Timeline
        happens to be opened. A miss just leaves the fallback rendering in
        place until preview_ready fires later."""
        if self._preview_service is None:
            return
        for item in items:
            if item.id in self._preview_data:
                continue
            detected = type_detection.detect(item.kind, item.text_content)
            if detected not in ("url", "youtube"):
                continue
            cached = self._preview_service.request(item.id, item.text_content, is_youtube=(detected == "youtube"))
            if cached is not None:
                self._preview_data[item.id] = cached

    def _on_preview_ready(self, item_id: int, data: dict) -> None:
        self._preview_data[item_id] = data
        row = self._model.row_for_id(item_id)
        if row >= 0:
            self._list.update(self._model.index(row, 0))

    # -- window lifecycle -------------------------------------------------

    def resolve_target_screen(self):
        """Cursor position, not "primary screen" -- see the panel_geometry
        module docstring for why this is the right monitor-selection policy
        under Wayland (a plain client has no fast, portable way to ask which
        OTHER application's window is active)."""
        return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()

    def _apply_geometry(self) -> bool:
        """Returns False (and leaves geometry untouched) if there is
        genuinely no screen to position against -- observed live on Erwin:
        KWin can transiently report zero outputs (supportInformation's own
        "Number of Screens: 0", seen mid-session here, likely a DPMS/screen-
        blank recovery hiccup, not anything Nexus did). Both
        QGuiApplication.screenAt() and primaryScreen() return None in that
        state; calling .availableGeometry() on None would crash the slot
        this runs from instead of just failing to show a panel that
        couldn't be positioned meaningfully anyway."""
        screen = self.resolve_target_screen()
        if screen is None:
            log.warning("Sin pantallas disponibles (KWin reporta 0 outputs); no se puede posicionar Nexus.")
            return False
        avail = screen.availableGeometry()
        geo = panel_geometry.calculate_panel_geometry(avail.x(), avail.y(), avail.width(), avail.height())
        self._delegate.set_card_height(geo.card_height)
        list_height = geo.card_height + 16
        self._list.setFixedHeight(list_height)
        self._empty_label.setFixedHeight(list_height)
        self.setGeometry(QRect(geo.x, geo.y, geo.width, geo.height))
        return True

    def show_and_focus(self) -> None:
        # Show first (with whatever the model already holds from last time),
        # then enrich: the fresh query/model-reset is deferred one event-loop
        # tick so the window is guaranteed to already be painted on screen
        # before that work runs -- "mostrar primero, después enriquecer"
        # applied literally, not just in spirit. Geometry is recomputed from
        # scratch every time (see _apply_geometry/panel_geometry.py) -- never
        # a remembered/persisted position.
        if not self._apply_geometry():
            return
        self._pending_focus_claim = True
        self.show()
        self.raise_()
        self.activateWindow()
        if self.isActiveWindow():
            # Some launches (e.g. already the active window from a prior
            # toggle) never fire another ActivationChange, since the state
            # doesn't change -- claim focus directly rather than waiting for
            # an event that isn't coming.
            self._pending_focus_claim = False
            self._search.setFocus()
            self._search.selectAll()
        QTimer.singleShot(0, self.refresh)

    def fade_hide(self) -> None:
        # See MainWindow.fade_hide()'s comment: real opacity animation fails
        # on this Qt/Wayland combination for top-level windows.
        self.hide()
