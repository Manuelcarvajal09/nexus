"""Nexus Timeline: the Ctrl+Shift+V experience, rebuilt for Phase 2 around a
horizontal card bar docked to the bottom of the screen (see
PASTE_PRODUCT_ANALYSIS.md §5 for why this shape, and the live positioning
test that confirmed the geometry itself is achievable with plain PySide6 on
this KWin/Wayland session -- no layer-shell binding needed).

Deliberately NOT frameless, unlike an early version of this window: a
FramelessWindowHint toplevel measurably failed to reliably hold Wayland
keyboard focus after activation on this KWin session (confirmed live with
changeEvent/focusWidget instrumentation -- see git history on this file),
so typed input and Return intermittently landed on whatever window was
active before Nexus, not on Nexus itself. A normal decorated window
(same WindowStaysOnTopHint MainWindow/StackWindow already use) does not
have that problem. A thin title bar is a real but acceptable trade for
keyboard input actually working.

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

from . import styles
from .paste_service import PasteService
from .storage import StorageService
from .timeline_delegate import CARD_HEIGHT, ClipCardDelegate
from .timeline_model import TimelineModel

log = logging.getLogger("nexus.timeline")

BAR_HEIGHT = 214
MAX_ITEMS = 200
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
    def __init__(self, storage: StorageService, paste_service: PasteService, parent=None):
        super().__init__(parent)
        self._storage = storage
        self._paste = paste_service
        self._current_pinboard_id: int | None = ALL_PINBOARDS_ID
        self._pinboard_buttons: dict = {}
        self._pending_focus_claim = False

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
        self._list.setItemDelegate(ClipCardDelegate(self._list))
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

    def _on_activated(self, index) -> None:
        item = self._model.item_at(index.row())
        if item is None:
            return
        self._paste.paste(item, synthesize=True)
        self.fade_hide()

    def _quick_paste(self, n: int) -> None:
        item = self._model.item_at(n - 1)
        if item is None:
            return
        log.info("Quick Paste #%d -> item #%s", n, item.id)
        self._paste.paste(item, synthesize=True)
        self.fade_hide()

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
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow() and self._pending_focus_claim:
            self._pending_focus_claim = False
            self._search.setFocus()
            self._search.selectAll()
        super().changeEvent(event)

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

    # -- window lifecycle -------------------------------------------------

    def _target_geometry(self) -> QRect:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        return QRect(avail.x(), avail.y() + avail.height() - BAR_HEIGHT, avail.width(), BAR_HEIGHT)

    def show_and_focus(self) -> None:
        # Show first (with whatever the model already holds from last time),
        # then enrich: the fresh query/model-reset is deferred one event-loop
        # tick so the window is guaranteed to already be painted on screen
        # before that work runs -- "mostrar primero, después enriquecer"
        # applied literally, not just in spirit.
        self.setGeometry(self._target_geometry())
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
