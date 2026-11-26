"""Horizontal, virtualized Nexus history and integrated pinboards with persistent native window geometry."""

import logging

from PySide6.QtCore import QEvent, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QMenu, QDialog, QPlainTextEdit,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nexus.ui import panel_geometry

from nexus.ui import styles

from nexus.core import type_detection
from nexus.platform.linux.paste_service import POST_HIDE_PASTE_DELAY_MS, PasteService
from nexus.core.preview_service import PreviewService
from nexus.platform.linux import desktop
from nexus.platform.linux import product_ui
from nexus.platform.linux.shelf_delegate import ShelfDelegate
from nexus.platform.linux.board_panel import BoardPanel
from nexus.core.storage import StorageService
from nexus.ui.timeline_delegate import CARD_HEIGHT, ClipCardDelegate
from nexus.core.timeline_model import TimelineModel

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


class TimelineWindow(product_ui.Surface):
    library_requested = Signal()
    appearance_requested = Signal()
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
        self._kind_filter = None
        self._pinboard_buttons: dict = {}
        self._pending_focus_claim = False
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.setInterval(DISMISS_DEBOUNCE_MS)
        self._dismiss_timer.timeout.connect(self._dismiss_if_still_inactive)

        if preview_service is not None:
            preview_service.preview_ready.connect(self._on_preview_ready)

        self.setWindowTitle("Nexus History")
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
        self.setObjectName("panelRoot")
        self._expanded = False

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(12)

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
        chrome_layout.setSpacing(3)

        # Single merged header row (compact wordmark + pinboard chips +
        # counter + Expanded View toggle) -- one row instead of the two
        # separate "header" and "pinboard chips" rows this used to be,
        # since every extra row directly adds to the height the Quick Shelf
        # is supposed to minimize.
        header = QHBoxLayout()
        header.setSpacing(6)
        title = product_ui.DragTitle("Nexus")
        title.setObjectName("productTitle")
        header.addWidget(title)

        self._pinboard_row = QHBoxLayout()
        self._pinboard_row.setSpacing(4)
        # Collection selection lives inside the integrated Pinboards view.
        header.addStretch()

        self._count_badge = QLabel("0")
        self._count_badge.setObjectName("badgeCompact")
        header.addWidget(self._count_badge)

        self._expand_btn = QPushButton("Expandir  ⌄")
        self._expand_btn.setObjectName("expandToggle")
        self._expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._expand_btn.hide()
        self._expand_btn.clicked.connect(self._toggle_expanded)
        header.addWidget(self._expand_btn)
        library = QPushButton("⋯")
        library.setFixedWidth(32)
        library.setAccessibleName("Abrir biblioteca y pinboards")
        library.setToolTip("Administrar pinboards y biblioteca")
        library.clicked.connect(self.library_requested.emit)
        library.hide()
        header.addWidget(library)
        appearance = QPushButton("◐")
        appearance.setFixedWidth(36)
        appearance.setAccessibleName("Apariencia")
        appearance.setToolTip("Tema y colores de Nexus")
        appearance.clicked.connect(self.appearance_requested.emit)
        header.addWidget(appearance)
        header.addWidget(product_ui.CloseButton(self))
        chrome_layout.addLayout(header)

        self._search = QLineEdit()
        self._search.setObjectName("quickSearch")
        self._search.setPlaceholderText("Buscar, o simplemente empieza a escribir…")
        self._search.textChanged.connect(self._on_search_text_changed)
        self._search.returnPressed.connect(self._focus_list)
        self._search.installEventFilter(self)
        self._search.setMinimumWidth(180)
        header.insertWidget(1,self._search,1)
        self._history_tab=QPushButton("History");self._boards_tab=QPushButton("Pinboards")
        for tab in (self._history_tab,self._boards_tab):tab.setCheckable(True);header.insertWidget(header.count()-2,tab)
        self._history_tab.setChecked(True)
        self._history_tab.clicked.connect(self.show_history)
        self._boards_tab.clicked.connect(self.show_pinboards)

        root.addWidget(self._chrome)
        filters = QHBoxLayout()
        self._filter_buttons = {}
        for label, kind in (("Todos",None),("Texto","text"),("Imágenes","image"),("Archivos","file"),("Enlaces","url")):
            button=QPushButton(label);button.setCheckable(True);button.setChecked(kind is None)
            button.clicked.connect(lambda checked=False,k=kind:self._set_kind(k))
            filters.addWidget(button);self._filter_buttons[kind]=button
        filters.addStretch()
        root.addLayout(filters)

        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(150)
        self._search_debounce.timeout.connect(self.refresh)

        self._model = TimelineModel(self)
        self._list = _TimelineListView(self._type_to_search, self._on_activated)
        self._list.setModel(self._model)
        self._delegate = ShelfDelegate(self._list)
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
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setMinimumHeight(218)
        self._list.activated.connect(self._on_activated)
        self._list.setMouseTracking(True)
        self._list.setToolTip("Enter para pegar · Clic derecho para copiar, previsualizar y organizar en pinboards")
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._context_menu)
        self._list.installEventFilter(self)
        content=QHBoxLayout()
        self._board_panel=BoardPanel(storage,self);self._board_panel.hide()
        self._board_panel.selected.connect(self._select_pinboard)
        self._board_panel.changed.connect(self.refresh)
        content.addWidget(self._board_panel)
        content.addWidget(self._list,1)
        root.addLayout(content,1)

        self._empty_label = QLabel()
        self._empty_label.setObjectName("emptyState")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setMinimumHeight(120)
        content.addWidget(self._empty_label, 1)

        for n in range(1, 10):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{n}"), self)
            shortcut.activated.connect(lambda n=n: self._quick_paste(n))

        prev_board = QShortcut(QKeySequence("Ctrl+Left"), self)
        prev_board.activated.connect(lambda: self._cycle_pinboard(-1))
        next_board = QShortcut(QKeySequence("Ctrl+Right"), self)
        next_board.activated.connect(lambda: self._cycle_pinboard(1))

        product_ui.attach(self)
        self.setMinimumSize(760,380)
        desktop.track(self)
        footer = QLabel("← → Navegar · Enter Pegar · Clic derecho Organizar · Esc Ocultar")
        footer.setToolTip("Se muestran hasta 200 coincidencias. La búsqueda consulta todo el historial.")
        footer.setObjectName("productMeta")
        root.addWidget(footer)
        context = QShortcut(QKeySequence("Shift+F10"), self._list)
        context.activated.connect(lambda:self._context_menu(self._list.visualRect(self._list.currentIndex()).center()))
        self.refresh()

    # -- keyboard-first plumbing -------------------------------------------

    def show_history(self):
        self._history_tab.setChecked(True);self._boards_tab.setChecked(False)
        self._board_panel.hide();self._current_pinboard_id=None;self.refresh()

    def show_pinboards(self):
        self._history_tab.setChecked(False);self._boards_tab.setChecked(True)
        self._board_panel.refresh();self._board_panel.show()
        boards=self._storage.list_pinboards()
        if self._current_pinboard_id is None and boards:self._current_pinboard_id=boards[0].id
        self._board_panel.current=self._current_pinboard_id;self._board_panel.refresh();self.refresh()

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
        if not self._paste.write_to_clipboard(item):
            return
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
        if event.key() == Qt.Key.Key_F6:
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
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_F6:
            self._toggle_expanded()
            return True
        return super().eventFilter(obj, event)

    def _toggle_expanded(self) -> None:
        """F6 focuses clip navigation; Tab retains Qt's normal focus chain."""
        self._focus_list()

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
            elif product_ui.hide_on_focus_loss() and not self._pending_focus_claim and self.isVisible():
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
        if product_ui.hide_on_focus_loss() and not self.isActiveWindow() and self.isVisible():
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
        self._board_panel.current=pinboard_id
        if pinboard_id is None:
            self.show_history()
        else:
            self.show_pinboards()

    def _rebuild_pinboard_chips(self) -> None:
        while self._pinboard_row.count():
            child=self._pinboard_row.takeAt(0)
            if child.widget(): child.widget().deleteLater()
        self._pinboard_buttons={None:None}
        combo=QComboBox();combo.setMaximumWidth(140);combo.setAccessibleName("Pinboard")
        combo.addItem("Historial",None)
        for board in self._storage.list_pinboards():
            combo.addItem(board.display_name,board.id);self._pinboard_buttons[board.id]=None
        combo.setCurrentIndex(max(0,combo.findData(self._current_pinboard_id)))
        combo.currentIndexChanged.connect(lambda index:self._select_pinboard(combo.itemData(index)))
        self._pinboard_row.addWidget(combo)

    def _set_kind(self,kind):
        self._kind_filter=kind
        for k,b in self._filter_buttons.items(): b.setChecked(k==kind)
        self.refresh()

    def _context_menu(self,pos):
        item=self._model.item_at(self._list.indexAt(pos).row())
        if item is None:return
        menu=QMenu(self)
        copy=menu.addAction("Copiar sin cerrar")
        paste=menu.addAction("Pegar en la aplicación anterior")
        preview=menu.addAction("Ver contenido completo")
        boards=menu.addMenu("Pinboards"); mapping={}
        for board in self._storage.list_pinboards():
            action=boards.addAction(board.display_name);action.setCheckable(True);action.setChecked(board.id in item.pinboard_ids);mapping[action]=board.id
        manage=menu.addAction("Administrar biblioteca")
        choice=menu.exec(self._list.viewport().mapToGlobal(pos))
        if choice==copy:
            self._paste.write_to_clipboard(item)
        elif choice==paste:
            self._select_and_paste(item)
        elif choice==preview:
            dialog=QDialog(self);dialog.setWindowTitle("Contenido del clip");dialog.resize(480,420)
            layout=QVBoxLayout(dialog)
            if item.kind=="image" and item.image_data:
                from PySide6.QtGui import QPixmap
                image=QPixmap();image.loadFromData(item.image_data)
                label=QLabel();label.setPixmap(image.scaled(420,320,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation));layout.addWidget(label)
            else:
                text=QPlainTextEdit();text.setReadOnly(True);text.setPlainText(item.text_content or item.file_path or "Sin contenido");layout.addWidget(text)
            dialog.exec()
        elif choice==manage:self.show_pinboards()
        elif choice in mapping:
            board_id=mapping[choice]
            if board_id in item.pinboard_ids:self._storage.remove_from_pinboard(item.id,board_id)
            else:self._storage.add_to_pinboard(item.id,board_id)
            self.refresh()

    # -- data -----------------------------------------------------------

    def refresh(self) -> None:
        query = self._search.text().strip()
        previous=self._model.item_at(self._list.currentIndex().row())
        selected_id=previous.id if previous is not None else None
        items = self._storage.recent(limit=MAX_ITEMS, query=query, pinboard_id=self._current_pinboard_id, kind=self._kind_filter)
        self._model.set_items(items)
        self._update_card_width()
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
        if self._board_panel.isVisible():self._board_panel.refresh()
        if items:
            selected_row=next((i for i,item in enumerate(items) if item.id==selected_id),0)
            self._list.setCurrentIndex(self._model.index(selected_row, 0))

    def _update_card_width(self):
        count = max(1, self._model.rowCount())
        available = max(248, self._list.viewport().width())
        width = min(360, max(248, available // min(count, 4)))
        if self._delegate.card_width != width:
            self._delegate.card_width = width
            self._list.doItemsLayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_delegate"):
            self._update_card_width()

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
        return desktop.active_screen()

    def _apply_geometry(self) -> bool:
        return desktop.restore_shelf(self)

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
        desktop.present(self)
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
        desktop.save(self)
        self.hide()
