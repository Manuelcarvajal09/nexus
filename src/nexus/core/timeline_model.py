"""Backing model for the Timeline (Ctrl+Shift+V) view: a QAbstractListModel
wrapping a plain Python list of ClipItem.

This is the structural fix for the ~280-380ms of remaining Ctrl+Shift+V
latency documented in PERFORMANCE_BASELINE.md as a Phase 2 recommendation:
the old MainWindow rebuilt one real QWidget per history row on every open
(QListWidget.setItemWidget), so opening always paid for constructing up to
200 widgets regardless of how many were actually visible. A QListView bound
to this model only asks its delegate to paint() the rows currently inside
the viewport (typically 6-10 on a 1920px-wide bar) -- the other ~190 rows
cost nothing until they're scrolled into view. set_items() is a single
Python list assignment plus a model reset signal; there is no per-row
construction step left to be slow."""

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

ITEM_ROLE = Qt.ItemDataRole.UserRole + 1


class TimelineModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list = []

    def set_items(self, items: list) -> None:
        self.beginResetModel()
        self._items = items
        self.endResetModel()

    def item_at(self, row: int):
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def row_for_id(self, item_id: int) -> int:
        for row, item in enumerate(self._items):
            if item.id == item_id:
                return row
        return -1

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None
        item = self._items[index.row()]
        if role == ITEM_ROLE:
            return item
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return item.kind + ": " + item.preview(120)
        if role == Qt.ItemDataRole.ToolTipRole:
            return item.preview(400)
        if role == Qt.ItemDataRole.DisplayRole:
            return item.preview(60)
        return None
