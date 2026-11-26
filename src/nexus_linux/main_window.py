"""Main panel: searchable clip history + pinboards sidebar, mirrors Nexus's
MainPanel + PinboardSidebar. Borderless-panel-with-drag-handle from the Swift
version is replaced with a plain frameless-on-demand QWidget -- KDE Wayland
doesn't support the non-activating floating panel trick AppKit used, so we
keep it simple and stable per the migration priority order (stability over
visual parity)."""

import logging
import time
from pathlib import Path

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .paste_service import POST_HIDE_PASTE_DELAY_MS, PasteService
from .pinboard_sidebar import PinboardSidebar
from .row_widget import ClipRowWidget
from . import desktop
from .storage import ClipItem, StorageService

RESOURCES_DIR = Path(__file__).resolve().parent.parent.parent / "resources"
log = logging.getLogger("nexus.window")


class MainWindow(QWidget):
    def __init__(self, storage: StorageService, paste_service: PasteService, parent=None):
        super().__init__(parent)
        self._storage = storage
        self._paste = paste_service
        self._current_pinboard_id: int | None = None

        self.setWindowTitle("Nexus Library")
        # Compact default footprint: wide enough for the full pinboard rail
        # and actions, without dominating the active application behind it.
        self.resize(520, 560)
        self.setMinimumSize(440, 420)
        self.setMaximumWidth(760)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(0)

        self._sidebar = PinboardSidebar(storage)
        self._sidebar.board_selected.connect(self._on_board_selected)

        # A splitter makes the desktop panel genuinely responsive: users can
        # give a long board list more room, while the history keeps all of its
        # usable width when the panel is made small. It avoids a brittle,
        # permanently fixed 160px rail.
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(1)
        self._splitter.addWidget(self._sidebar)

        content_widget = QWidget()
        content = QVBoxLayout(content_widget)
        content.setContentsMargins(14, 0, 0, 0)
        content.setSpacing(10)

        # A bare QHBoxLayout can be assigned surplus vertical space by its
        # parent QVBoxLayout on Qt/Wayland. That made the title and count pill
        # look like a tall, empty column whenever a pinboard had no results.
        # A fixed-height container gives the header the compact toolbar
        # geometry expected from a native macOS-style utility panel.
        header_widget = QWidget()
        header_widget.setFixedHeight(38)
        header = QHBoxLayout(header_widget)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(9)
        brand = QLabel()
        brand_pixmap = QPixmap(str(RESOURCES_DIR / "nexus-icon.png"))
        if not brand_pixmap.isNull():
            brand.setPixmap(brand_pixmap.scaled(
                26, 26, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            ))
        header.addWidget(brand)
        self._title = QLabel("Historial")
        self._title.setObjectName("title")
        header.addWidget(self._title)
        header.addStretch()
        self._count_badge = QLabel("0")
        self._count_badge.setObjectName("badge")
        header.addWidget(self._count_badge)
        content.addWidget(header_widget)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Buscar en el historial…")
        self._search.textChanged.connect(self._on_search_text_changed)
        content.addWidget(self._search)

        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(150)
        self._search_debounce.timeout.connect(self.refresh)

        self._list = QListWidget()
        self._list.setSpacing(0)
        self._list.setUniformItemSizes(False)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Reserve a deliberate gutter for the overlay-style scrollbar used by
        # the neon theme. Without it, the thumb paints over the right border
        # of every history card.
        self._list.setViewportMargins(0, 0, 12, 0)
        self._list.itemActivated.connect(self._on_item_activated)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.currentItemChanged.connect(self._on_list_selection_changed)

        # Results and the empty state must occupy the same flexible region.
        # Hiding a stretched QListWidget left its stretch allocation behind,
        # which is what produced the large visual gap in an empty pinboard.
        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(self._list)

        empty_page = QWidget()
        empty_layout = QVBoxLayout(empty_page)
        empty_layout.setContentsMargins(24, 12, 24, 12)
        empty_layout.addStretch()
        self._empty_label = QLabel()
        self._empty_label.setObjectName("emptyState")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        empty_layout.addWidget(self._empty_label)
        empty_layout.addStretch()
        self._content_stack.addWidget(empty_page)
        content.addWidget(self._content_stack, stretch=1)

        self._actions_widget = QWidget()
        actions = QHBoxLayout(self._actions_widget)
        actions.setContentsMargins(0, 0, 0, 0)
        self._pin_btn = QPushButton("📌 Agregar a pinboard")
        self._pin_btn.setEnabled(False)
        self._pin_btn.clicked.connect(self._on_pin_button_clicked)
        actions.addWidget(self._pin_btn)
        actions.addStretch()
        self._delete_btn = QPushButton("Eliminar")
        self._delete_btn.setObjectName("danger")
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._on_delete_button_clicked)
        actions.addWidget(self._delete_btn)
        content.addWidget(self._actions_widget)

        self._splitter.addWidget(content_widget)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([136, 374])
        outer.addWidget(self._splitter)

        self.refresh()

    def _on_board_selected(self, board) -> None:
        self._current_pinboard_id = board.id if board is not None else None
        self._title.setText(board.display_name if board is not None else "Historial")
        self.refresh()

    def refresh_sidebar_icons(self) -> None:
        """Re-renders the sidebar's theme-tinted icons (its "Historial" row
        icon) after a theme switch; pinboard rows keep their own per-board
        colors, unaffected by theme."""
        self._sidebar.refresh()

    def refresh(self) -> None:
        t_query_start = time.perf_counter()
        query = self._search.text().strip()
        items = self._storage.recent(limit=200, query=query, pinboard_id=self._current_pinboard_id)
        t_build_start = time.perf_counter()
        self._list.clear()
        for item in items:
            list_item = QListWidgetItem()
            list_item.setData(Qt.ItemDataRole.UserRole, item.id)
            row = ClipRowWidget(item)
            # Width belongs to the viewport, not to the longest clipboard
            # line. This prevents long commands/URLs from creating a hidden
            # horizontal canvas and a needless bottom scrollbar.
            list_item.setSizeHint(QSize(0, row.sizeHint().height()))
            self._list.addItem(list_item)
            self._list.setItemWidget(list_item, row)

        t_done = time.perf_counter()
        log.debug(
            "refresh(): query=%.1fms build=%.1fms total=%.1fms items=%d",
            (t_build_start - t_query_start) * 1000, (t_done - t_build_start) * 1000,
            (t_done - t_query_start) * 1000, len(items),
        )
        self._count_badge.setText(str(len(items)))
        empty = len(items) == 0
        if empty and query:
            self._empty_label.setText(f"Sin resultados para «{query}».")
        elif empty and self._current_pinboard_id is not None:
            self._empty_label.setText(
                "Este pinboard está vacío.\n"
                "Selecciona clips en Historial para agregarlos aquí."
            )
        elif empty:
            self._empty_label.setText("Tu historial está vacío. Copia algo para empezar.")
        self._content_stack.setCurrentIndex(1 if empty else 0)
        # Contextual actions only make sense with a selected result; hiding
        # them in an empty view removes two inert controls from the panel.
        self._actions_widget.setVisible(not empty)

    def _on_search_text_changed(self) -> None:
        self._search_debounce.start()

    def resizeEvent(self, event) -> None:
        """Progressively compact controls instead of letting labels clip when
        Nexus is resized toward its minimum practical width."""
        compact = self.width() < 560
        self._sidebar.set_compact(compact)
        self._pin_btn.setText("📌 Pinboard" if compact else "📌 Agregar a pinboard")
        if compact and self._splitter.sizes()[0] > 116:
            self._splitter.setSizes([124, max(1, self.width() - 156)])
        elif not compact and self._splitter.sizes()[0] > 130:
            self._splitter.setSizes([136, max(1, self.width() - 156)])
        super().resizeEvent(event)

    def _selected_item(self) -> ClipItem | None:
        current = self._list.currentItem()
        if current is None:
            return None
        item_id = current.data(Qt.ItemDataRole.UserRole)
        return self._storage.get(item_id)

    def _on_item_activated(self, list_item: QListWidgetItem) -> None:
        item_id = list_item.data(Qt.ItemDataRole.UserRole)
        item = self._storage.get(item_id)
        if item is None:
            return
        # Write -> hide -> deferred synthesize, never the reverse -- see
        # paste_service.POST_HIDE_PASTE_DELAY_MS docstring. Synthesizing
        # while this window is still active sends the Ctrl+V back into
        # Nexus itself instead of the app the user meant to paste into.
        if hasattr(self._paste, "paste_item"):
            self._paste.paste_item(item, self)
            return
        if not self._paste.write_to_clipboard(item):
            return
        self.fade_hide()
        QTimer.singleShot(POST_HIDE_PASTE_DELAY_MS, self._paste.synthetic_paste)

    def _on_list_selection_changed(self, current: QListWidgetItem, previous: QListWidgetItem) -> None:
        if previous is not None:
            row = self._list.itemWidget(previous)
            if row is not None:
                row.set_selected(False)
        if current is not None:
            row = self._list.itemWidget(current)
            if row is not None:
                row.set_selected(True)
        has_selection = current is not None
        self._pin_btn.setEnabled(has_selection)
        self._delete_btn.setEnabled(has_selection)

    def _show_pinboard_menu(self, item: ClipItem, global_pos) -> None:
        """Shared by the right-click menu and the visible "Agregar a
        pinboard" button, so there are two independent, equally-functional
        ways to reach the same action."""
        menu = QMenu(self)
        boards = self._storage.list_pinboards()
        board_actions = {}
        if boards:
            for board in boards:
                action = menu.addAction(board.display_name)
                action.setCheckable(True)
                action.setChecked(board.id in item.pinboard_ids)
                board_actions[action] = board.id
        else:
            no_boards_action = menu.addAction("No tienes pinboards todavía")
            no_boards_action.setEnabled(False)
        if item.is_pinned:
            menu.addSeparator()
            remove_all_action = menu.addAction("Quitar de todos los pinboards")
        else:
            remove_all_action = None
        chosen = menu.exec(global_pos)

        if chosen in board_actions:
            board_id = board_actions[chosen]
            if board_id in item.pinboard_ids:
                self._storage.remove_from_pinboard(item.id, board_id)
            else:
                self._storage.add_to_pinboard(item.id, board_id)
            self.refresh()
        elif chosen == remove_all_action:
            for board_id in item.pinboard_ids:
                self._storage.remove_from_pinboard(item.id, board_id)
            self.refresh()

    def _on_pin_button_clicked(self) -> None:
        item = self._selected_item()
        if item is None:
            return
        if not self._storage.list_pinboards():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "Sin pinboards",
                "Todavía no tienes ningún pinboard. Créalo con el botón "
                "\"+ Nuevo pinboard\" en el panel izquierdo.",
            )
            return
        self._show_pinboard_menu(item, self._pin_btn.mapToGlobal(self._pin_btn.rect().bottomLeft()))

    def _on_delete_button_clicked(self) -> None:
        item = self._selected_item()
        if item is None:
            return
        self._storage.delete(item.id)
        self.refresh()

    def _on_context_menu(self, pos) -> None:
        list_item = self._list.itemAt(pos)
        if list_item is None:
            return
        item_id = list_item.data(Qt.ItemDataRole.UserRole)
        item = self._storage.get(item_id)
        if item is None:
            return

        menu = QMenu(self)
        boards = self._storage.list_pinboards()
        board_actions = {}
        if boards:
            add_menu = menu.addMenu("Agregar a pinboard")
            for board in boards:
                action = add_menu.addAction(board.display_name)
                action.setCheckable(True)
                action.setChecked(board.id in item.pinboard_ids)
                board_actions[action] = board.id
        if item.is_pinned:
            remove_all_action = menu.addAction("Quitar de todos los pinboards")
        else:
            remove_all_action = None
        menu.addSeparator()
        delete_action = menu.addAction("Eliminar")
        chosen = menu.exec(self._list.mapToGlobal(pos))

        if chosen in board_actions:
            board_id = board_actions[chosen]
            if board_id in item.pinboard_ids:
                self._storage.remove_from_pinboard(item.id, board_id)
            else:
                self._storage.add_to_pinboard(item.id, board_id)
            self.refresh()
        elif chosen == remove_all_action:
            for board_id in item.pinboard_ids:
                self._storage.remove_from_pinboard(item.id, board_id)
            self.refresh()
        elif chosen == delete_action:
            self._storage.delete(item.id)
            self.refresh()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.fade_hide()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            item = self._selected_item()
            if item is not None:
                self._storage.delete(item.id)
                self.refresh()
            return
        super().keyPressEvent(event)

    def show_and_focus(self) -> None:
        desktop.center(self, preferred=(620, 560))
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()
        desktop.present(self)
        self._search.setFocus()

    def fade_hide(self) -> None:
        # Not actually animated -- see MIGRATION_LINUX.md, "Animaciones":
        # both QWidget.windowOpacity() and QGraphicsOpacityEffect applied to
        # a top-level window failed on this Qt/Wayland combination (the
        # latter renders solid black). Named fade_hide() rather than plain
        # .hide() at every call site so a future, safer approach (fading an
        # inner content wrapper instead of the window itself) has one place
        # to change instead of several.
        self.hide()
