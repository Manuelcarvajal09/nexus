"""Sidebar mirroring PinboardSidebar.swift: "Historial" (all items) plus a
list of named/colored/emoji boards. Selecting a board filters the main list;
right-click to rename/change icon/reorder/delete, or drag a board row to
reorder directly (matching the original's onDrag/onDrop reorder -- the "Mover
arriba/abajo" menu actions stay too, as a keyboard-free-mouse-only alternative).
New boards are created via a "+" button (a QInputDialog stands in for the
original's popover editor for naming; PinboardIconEditor covers the
emoji/color picker)."""

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nexus.core.i18n import plural, tr
from nexus.ui import styles
from nexus.ui.pinboard_editor import PinboardIconEditor
from nexus.core.storage import Pinboard, StorageService

HISTORY_ROW_ID = -1


def _color_dot(hex_color: str, size: int = 10) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(hex_color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(0, 0, size, size)
    painter.end()
    return pixmap


class PinboardSidebar(QWidget):
    board_selected = Signal(object)  # Pinboard | None (None = "Historial")
    changed = Signal()  # boards created/renamed/deleted -- caller should refresh

    def __init__(self, storage: StorageService, parent=None):
        super().__init__(parent)
        self._storage = storage
        self._compact = False
        # The containing splitter owns the actual width. Keeping useful bounds
        # lets the navigation rail shrink with the panel without crushing the
        # board names or the content area.
        self.setMinimumWidth(120)
        self.setMaximumWidth(220)

        root = QVBoxLayout(self)
        # A little breathing room makes the navigation rail feel intentional
        # instead of glued to the splitter and window edge.
        root.setContentsMargins(2, 2, 6, 2)
        root.setSpacing(6)

        self._label = QLabel("PINBOARDS")
        self._label.setObjectName("subtitle")
        root.addWidget(self._label)

        self._list = QListWidget()
        self._list.setObjectName("sidebarList")
        # Board names are intentionally secondary navigation labels. On a
        # narrow panel they should elide, never create an ugly horizontal
        # scrollbar that competes with the content area.
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._list.setUniformItemSizes(False)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.currentItemChanged.connect(self._on_selection_changed)
        # Drag-and-drop reorder (mirrors the original's onDrag/onDrop on
        # PinboardSidebar rows); "Historial" opts out via its own item flags
        # in refresh() so it can't be dragged or landed on.
        self._list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._list.model().rowsMoved.connect(self._on_rows_moved)
        root.addWidget(self._list, stretch=1)

        actions_row = QHBoxLayout()
        actions_row.setSpacing(4)
        self._add_btn = QPushButton(tr("+ Nuevo pinboard"))
        self._add_btn.setObjectName("primary")
        self._add_btn.clicked.connect(self._on_create)
        actions_row.addWidget(self._add_btn, stretch=1)

        # Rename/icon/reorder/delete used to live ONLY in the right-click
        # menu -- undiscoverable if you never think to right-click a nav
        # item. This button opens the exact same menu (_show_board_menu) for
        # whichever pinboard is currently selected, so every action has a
        # visible, mouse-discoverable path in addition to right-click.
        self._edit_btn = QPushButton("⋮")
        self._edit_btn.setObjectName("flat")
        self._edit_btn.setFixedWidth(28)
        self._edit_btn.setToolTip(tr("Editar pinboard seleccionado"))
        self._edit_btn.clicked.connect(self._on_edit_button_clicked)
        actions_row.addWidget(self._edit_btn)
        root.addLayout(actions_row)

        self._hint = QLabel(tr("Clic derecho, o el botón ⋮,\npara editar, mover o eliminar."))
        self._hint.setObjectName("subtitle")
        self._hint.setWordWrap(True)
        root.addWidget(self._hint)

        self.refresh()

    def set_compact(self, compact: bool) -> None:
        """Adapt the navigation rail for a narrow Nexus window without
        removing any pinboard capability. The contextual help is useful on a
        roomy panel, but it is the first thing that should yield on a narrow
        layout; the add action keeps a clear label in both modes."""
        if self._compact == compact:
            return
        self._compact = compact
        self._hint.setVisible(not compact)
        self._add_btn.setText(tr("+ Nuevo") if compact else tr("+ Nuevo pinboard"))
        # Width bounds shrink progressively; the 120→132 gap is the minimal
        # navigation rail, 148→220 is the full rail with room for long names.
        self.setMinimumWidth(108 if compact else 120)
        self.setMaximumWidth(136 if compact else 220)

    def refresh(self) -> None:
        current = self._list.currentItem()
        previous_row_id = current.data(Qt.ItemDataRole.UserRole) if current is not None else HISTORY_ROW_ID

        self._list.blockSignals(True)
        self._list.clear()

        history_item = QListWidgetItem(styles.icon("view-history", size=15), tr("Historial"))
        history_item.setData(Qt.ItemDataRole.UserRole, HISTORY_ROW_ID)
        history_item.setFlags(history_item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
        self._list.addItem(history_item)

        restored_row = 0
        for board in self._storage.list_pinboards():
            list_item = QListWidgetItem(board.display_name if board.emoji else f"  {board.name}")
            if not board.emoji:
                list_item.setIcon(_color_dot(board.color_hex))
            list_item.setData(Qt.ItemDataRole.UserRole, board.id)
            self._list.addItem(list_item)
            if board.id == previous_row_id:
                restored_row = self._list.count() - 1

        self._list.setCurrentRow(restored_row)
        self._list.blockSignals(False)

    def _on_rows_moved(self, *_args) -> None:
        """Deferred via singleShot: rowsMoved fires while the view is still
        mid-drop, so rebuilding the list (refresh()) synchronously here would
        mutate the model out from under Qt's own drag-drop handling."""
        QTimer.singleShot(0, self._persist_new_order)

    def _persist_new_order(self) -> None:
        """After a drag reorder: persist the new order (History excluded --
        if it got dragged out of place too, this drops it from the persisted
        list and refresh() below snaps it back to the top)."""
        ordered_ids = []
        for row in range(self._list.count()):
            row_id = self._list.item(row).data(Qt.ItemDataRole.UserRole)
            if row_id != HISTORY_ROW_ID:
                ordered_ids.append(row_id)
        self._storage.reorder_pinboards(ordered_ids)
        self.refresh()
        self.changed.emit()

    def _on_selection_changed(self, current: QListWidgetItem, _previous) -> None:
        if current is None:
            return
        row_id = current.data(Qt.ItemDataRole.UserRole)
        if row_id == HISTORY_ROW_ID:
            self.board_selected.emit(None)
        else:
            self.board_selected.emit(self._storage.get_pinboard(row_id))

    def _on_create(self) -> None:
        count = len(self._storage.list_pinboards())
        board = self._storage.create_pinboard(tr("Pinboard {n}", n=count + 1))
        editor = PinboardIconEditor(board, self, creating=True)
        if editor.exec() == PinboardIconEditor.DialogCode.Accepted:
            self._storage.update_pinboard(board.id, **editor.result_kwargs())
        else:
            self._storage.delete_pinboard(board.id)
        self.refresh()
        self.changed.emit()

    def _on_context_menu(self, pos) -> None:
        list_item = self._list.itemAt(pos)
        if list_item is None:
            return
        row_id = list_item.data(Qt.ItemDataRole.UserRole)
        if row_id == HISTORY_ROW_ID:
            return
        self._show_board_menu(row_id, self._list.mapToGlobal(pos))

    def _on_edit_button_clicked(self) -> None:
        current = self._list.currentItem()
        if current is None:
            return
        row_id = current.data(Qt.ItemDataRole.UserRole)
        if row_id == HISTORY_ROW_ID:
            return
        self._show_board_menu(row_id, self._edit_btn.mapToGlobal(self._edit_btn.rect().bottomLeft()))

    def _show_board_menu(self, row_id: int, global_pos) -> None:
        menu = QMenu(self)
        rename_action = menu.addAction(tr("Renombrar…"))
        icon_action = menu.addAction(tr("Cambiar ícono…"))
        menu.addSeparator()
        move_up_action = menu.addAction(tr("Mover arriba"))
        move_down_action = menu.addAction(tr("Mover abajo"))
        menu.addSeparator()
        delete_action = menu.addAction(tr("Eliminar"))
        chosen = menu.exec(global_pos)

        if chosen == rename_action:
            board = self._storage.get_pinboard(row_id)
            new_name, ok = QInputDialog.getText(self, tr("Renombrar pinboard"), tr("Nombre:"), text=board.name)
            if ok and new_name.strip():
                self._storage.update_pinboard(row_id, name=new_name.strip())
                self.refresh()
                self.changed.emit()
        elif chosen == icon_action:
            board = self._storage.get_pinboard(row_id)
            editor = PinboardIconEditor(board, self)
            if editor.exec() == PinboardIconEditor.DialogCode.Accepted:
                self._storage.update_pinboard(row_id, **editor.result_kwargs())
                self.refresh()
                self.changed.emit()
        elif chosen == move_up_action:
            self._storage.move_pinboard(row_id, -1)
            self.refresh()
            self.changed.emit()
        elif chosen == move_down_action:
            self._storage.move_pinboard(row_id, 1)
            self.refresh()
            self.changed.emit()
        elif chosen == delete_action:
            self._storage.delete_pinboard(row_id)
            self.refresh()
            self.changed.emit()
