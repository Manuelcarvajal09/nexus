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
import sys

from PySide6.QtCore import QEvent, QRect, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QMessageBox,
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
                self._on_activate(index, plain_text=bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
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
        self._persistent_when_inactive = sys.platform == "win32"
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.setInterval(DISMISS_DEBOUNCE_MS)
        self._dismiss_timer.timeout.connect(self._dismiss_if_still_inactive)

        if preview_service is not None:
            preview_service.preview_ready.connect(self._on_preview_ready)

        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        # Without this, #panelRoot's QSS border-radius only clips the
        # PAINTED fill -- the actual window surface stays a plain opaque
        # rectangle, so the four corners outside the rounded fill show up
        # as small square patches of the platform's default window
        # background instead of real desktop showing through. Confirmed via
        # PySide6 docs/behavior for frameless top-level widgets, not just
        # theory: this is the standard attribute for a custom-shaped window.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("overlayWindow")
        self._expanded = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        self._surface = QWidget(self)
        self._surface.setObjectName("panelSurface")
        outer.addWidget(self._surface)
        root = QVBoxLayout(self._surface)
        self._surface_layout = root
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # Everything above the card row lives in one container so its real
        # rendered height can be MEASURED (self._chrome.sizeHint().height())
        # instead of guessed -- a hardcoded "chrome is roughly Npx" constant
        # is exactly what let the Quick Shelf balloon to ~300px/28% of the
        # screen in practice despite the geometry formula targeting ~240px:
        # generic QPushButton/QLineEdit padding alone was already close to
        # 150px before a single card was drawn. See panel_geometry.py.
        self._chrome = QWidget()
        chrome_layout = QVBoxLayout(self._chrome)
        chrome_layout.setContentsMargins(0, 0, 0, 0)
        chrome_layout.setSpacing(9)

        # Single merged header row (compact wordmark + pinboard chips +
        # counter + Expanded View toggle) -- one row instead of the two
        # separate "header" and "pinboard chips" rows this used to be,
        # since every extra row directly adds to the height the Quick Shelf
        # is supposed to minimize.
        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel("Nexus")
        title.setObjectName("titleCompact")
        header.addWidget(title)

        self._pinboard_row = QHBoxLayout()
        self._pinboard_row.setSpacing(4)
        header.addLayout(self._pinboard_row)
        header.addStretch()

        self._count_badge = QLabel("0")
        self._count_badge.setObjectName("badgeCompact")
        header.addWidget(self._count_badge)

        self._expand_btn = QPushButton("Expandir  ⌄")
        self._expand_btn.setObjectName("expandToggle")
        self._expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._expand_btn.clicked.connect(self._toggle_expanded)
        header.addWidget(self._expand_btn)
        self._close_btn = QPushButton("×")
        self._close_btn.setObjectName("closeButton")
        self._close_btn.setToolTip("Ocultar Nexus (Esc)")
        self._close_btn.setAccessibleName("Ocultar Nexus")
        self._close_btn.clicked.connect(self.fade_hide)
        header.addWidget(self._close_btn)
        chrome_layout.addLayout(header)

        self._search = QLineEdit()
        self._search.setObjectName("quickSearch")
        self._search.setPlaceholderText("Buscar, o simplemente empieza a escribir…")
        self._search.textChanged.connect(self._on_search_text_changed)
        self._search.returnPressed.connect(self._focus_list)
        self._search.installEventFilter(self)
        chrome_layout.addWidget(self._search)

        root.addWidget(self._chrome)

        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(150)
        self._search_debounce.timeout.connect(self.refresh)

        self._model = TimelineModel(self)
        self._list = _TimelineListView(self._type_to_search, self._on_activated)
        self._list.setObjectName("timelineList")
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
        self._list.setSpacing(2)
        self._list.setViewportMargins(6, 4, 6, 4)
        # No visible scrollbar -- navigation is arrow-keys/wheel/trackpad,
        # matching the filmstrip feel from PASTE_PRODUCT_ANALYSIS.md §5
        # rather than looking like a file manager.
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setFixedHeight(CARD_HEIGHT + 16)
        self._list.doubleClicked.connect(self._on_activated)
        self._list.installEventFilter(self)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_item_menu)
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
        focus_search = QShortcut(QKeySequence("Ctrl+F"), self)
        focus_search.activated.connect(lambda: (self._search.setFocus(), self._search.selectAll()))

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

    def _select_and_paste(self, item, plain_text: bool = False) -> None:
        """Write -> hide -> (deferred) synthesize, never the reverse.
        Synthesizing while Nexus is still the active window sends the
        Ctrl+V into Nexus itself (its own search field, typically) instead
        of the app the user meant to paste into -- confirmed as a real bug
        before this fix, not a hypothetical. See paste_service.py's
        POST_HIDE_PASTE_DELAY_MS docstring for the live measurement behind
        the delay. write_to_clipboard() itself needs no delay -- it doesn't
        touch window focus at all."""
        log.info("select_and_paste: item #%s", item.id)
        if hasattr(self._paste, "paste_item"):
            self._paste.paste_item(item, self, plain_text=plain_text)
        else:
            self._paste.write_to_clipboard(item)
            self.fade_hide()
            QTimer.singleShot(POST_HIDE_PASTE_DELAY_MS, self._paste.synthetic_paste)

    def _on_activated(self, index, plain_text: bool = False) -> None:
        item = self._model.item_at(index.row())
        if item is None:
            return
        self._select_and_paste(item, plain_text)

    def _show_item_menu(self, pos) -> None:
        index = self._list.indexAt(pos)
        item = self._model.item_at(index.row()) if index.isValid() else None
        if item is None:
            return
        menu = QMenu(self)
        paste_action = menu.addAction("Pegar")
        plain_action = menu.addAction("Pegar como texto plano")
        copy_action = menu.addAction("Copiar nuevamente")
        pin_action = menu.addAction("Fijar/Desfijar")
        details_action = menu.addAction("Detalles")
        menu.addSeparator()
        delete_action = menu.addAction("Eliminar")
        chosen = menu.exec(self._list.viewport().mapToGlobal(pos))
        if chosen == paste_action:
            self._select_and_paste(item)
        elif chosen == plain_action:
            self._select_and_paste(item, True)
        elif chosen == copy_action:
            self._paste.write_to_clipboard(item)
        elif chosen == pin_action:
            boards = self._storage.list_pinboards()
            if boards and boards[0].id in item.pinboard_ids:
                self._storage.remove_from_pinboard(item.id, boards[0].id)
            elif boards:
                self._storage.add_to_pinboard(item.id, boards[0].id)
            self.refresh()
        elif chosen == details_action:
            QMessageBox.information(self, "Detalles", f"Tipo: {item.kind}\nOrigen: {item.source_app or 'desconocido'}\nID local: {item.id}")
        elif chosen == delete_action:
            self._storage.delete(item.id)
            self.refresh()

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
        if event.key() == Qt.Key.Key_Tab:
            self._toggle_expanded()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event) -> bool:
        # Tab is swallowed by Qt's own focus-chain handling (QWidget.event())
        # before it ever reaches a focused child's keyPressEvent or bubbles
        # up to this window's -- confirmed live, this window's own
        # keyPressEvent above never sees Tab while the search field or the
        # card list has focus. An event filter on those two widgets is the
        # reliable way to intercept it regardless of which one currently
        # holds focus, per Qt's documented event-filter mechanism.
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Tab:
            self._toggle_expanded()
            return True
        return super().eventFilter(obj, event)

    def _toggle_expanded(self) -> None:
        """Quick Shelf (default) <-> Expanded View. Opt-in, never the
        default on open -- see show_and_focus() resetting this to False on
        every fresh Ctrl+Shift+V. Expanded grows the panel to ~40% of the
        screen and lets the card row wrap into a grid (more cards visible,
        not bigger cards); collapsing goes back to a single filmstrip row
        anchored to the bottom edge."""
        self._expanded = not self._expanded
        self._list.setWrapping(self._expanded)
        self._list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded if self._expanded else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._expand_btn.setText("Colapsar  ⌃" if self._expanded else "Expandir  ⌄")
        self._apply_geometry()

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
            elif not self._persistent_when_inactive and not self._pending_focus_claim and self.isVisible():
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
        if not self._persistent_when_inactive and not self.isActiveWindow() and self.isVisible():
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
        all_btn.setObjectName("pinboardChipCompact")
        all_btn.setCheckable(True)
        all_btn.setChecked(self._current_pinboard_id == ALL_PINBOARDS_ID)
        all_btn.clicked.connect(lambda: self._select_pinboard(ALL_PINBOARDS_ID))
        self._pinboard_row.addWidget(all_btn)
        self._pinboard_buttons[ALL_PINBOARDS_ID] = all_btn

        for board in self._storage.list_pinboards():
            btn = QPushButton(board.display_name)
            btn.setObjectName("pinboardChipCompact")
            btn.setCheckable(True)
            btn.setChecked(self._current_pinboard_id == board.id)
            btn.clicked.connect(lambda _checked, bid=board.id: self._select_pinboard(bid))
            self._pinboard_row.addWidget(btn)
            self._pinboard_buttons[board.id] = btn

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
        # Measured, not guessed: self._chrome.sizeHint() is the real,
        # currently-rendered height of everything above the card row (header
        # row + search field, with whatever fonts/padding/DPI apply right
        # now), plus the root layout's own margins and the one spacing gap
        # between the chrome container and the card row below it. A
        # hardcoded "chrome is about Npx" constant is what let the Quick
        # Shelf balloon well past its target in practice -- see the
        # docstring on self._chrome above and panel_geometry.py.
        outer_margins = self.layout().contentsMargins()
        margins = self._surface_layout.contentsMargins()
        root_overhead = (outer_margins.top() + outer_margins.bottom() +
                         margins.top() + margins.bottom() + self._surface_layout.spacing())
        chrome_height = self._chrome.sizeHint().height() + root_overhead
        geo = panel_geometry.calculate_panel_geometry(
            avail.x(), avail.y(), avail.width(), avail.height(),
            chrome_height=chrome_height, expanded=self._expanded,
        )
        self._delegate.set_card_height(geo.card_height)
        list_height = geo.height - chrome_height
        self._list.setFixedHeight(list_height)
        self._empty_label.setFixedHeight(list_height)
        # Confirmed live: growing the window via setGeometry() (collapsed ->
        # Expanded View) takes effect immediately, but shrinking it back
        # (Expanded View -> collapsed) silently did NOT -- self.height()
        # right after setGeometry() still reported the old, larger size,
        # and the extra space rendered as empty background at the bottom of
        # the panel. The layout's cached minimum size from the taller
        # (wrapped/multi-row) configuration was never invalidated by
        # setFixedHeight() alone. Forcing a synchronous relayout before
        # asking for the smaller geometry fixes it -- growing never needed
        # this, only shrinking did.
        self.layout().invalidate()
        self.layout().activate()
        self.setGeometry(QRect(geo.x, geo.y, geo.width, geo.height))
        # Qt may enlarge a top-level widget to honour a DPI-scaled minimum
        # size after setGeometry(). Clamp the *actual* resulting height so
        # no card is ever rendered below Windows' usable work area.
        if sys.platform == "win32":
            # Keep a visual breathing zone above the taskbar; this also
            # accommodates Windows' scaled compositor shadow extents.
            max_y = avail.y() + avail.height() - self.height() - 190
            self.move(self.x(), max(avail.y() + 14, min(self.y(), max_y)))
        return True

    def show_and_focus(self) -> None:
        # Show first (with whatever the model already holds from last time),
        # then enrich: the fresh query/model-reset is deferred one event-loop
        # tick so the window is guaranteed to already be painted on screen
        # before that work runs -- "mostrar primero, después enriquecer"
        # applied literally, not just in spirit. Geometry is recomputed from
        # scratch every time (see _apply_geometry/panel_geometry.py) -- never
        # a remembered/persisted position.
        if self._expanded:
            # Quick Shelf is the default on every fresh Ctrl+Shift+V --
            # Expanded View is opt-in per-session, never sticky across opens.
            self._expanded = False
            self._list.setWrapping(False)
            self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._expand_btn.setText("Expandir  ⌄")
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
        # Native Windows font/DPI metrics settle only after the first show;
        # recompute then so the complete card row remains above the taskbar.
        QTimer.singleShot(0, self._apply_geometry)
        QTimer.singleShot(0, self.refresh)

    def fade_hide(self) -> None:
        # See MainWindow.fade_hide()'s comment: real opacity animation fails
        # on this Qt/Wayland combination for top-level windows.
        self.hide()
