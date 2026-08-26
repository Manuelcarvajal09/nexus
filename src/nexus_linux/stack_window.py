"""UI for Nexus Stack: mirrors PasteStack.swift -- opens as a fresh, empty
collection session; everything copied while it's open gets added (skipping
same-session duplicates by content hash) and the next pending item stays
armed on the clipboard. Ctrl+Shift+C is a pure show/hide toggle (see app.py);
a real Ctrl+V anywhere consumes the head and advances -- on macOS via a
global Accessibility key monitor, on Linux via evdev_stack_watcher.py when
enabled (see MIGRATION_LINUX.md, "Stack secuencial"). The "Pegar siguiente"
button stays as a manual fallback for when that watcher isn't running."""

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
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

from .row_widget import ClipRowWidget
from .stack import ClipStack
from . import styles

RESOURCES_DIR = Path(__file__).resolve().parent.parent.parent / "resources"


class StackWindow(QWidget):
    def __init__(self, clip_stack: ClipStack, parent=None):
        super().__init__(parent)
        self._stack = clip_stack

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
        self._list.clear()
        display_order = list(reversed(items)) if self._stack.reversed_order else items
        for position, item in enumerate(display_order, start=1):
            list_item = QListWidgetItem()
            list_item.setData(Qt.ItemDataRole.UserRole, item.id)
            row = ClipRowWidget(item, position=position)
            list_item.setSizeHint(QSize(0, row.sizeHint().height()))
            self._list.addItem(list_item)
            self._list.setItemWidget(list_item, row)

        count = len(items)
        order_label = "FIFO" if not self._stack.reversed_order else "LIFO"
        next_preview = display_order[0].preview(30) if display_order else ""
        subtitle = f"{count} elemento{'s' if count != 1 else ''} · {order_label}"
        if next_preview:
            subtitle += f" · siguiente: {next_preview}"
        self._subtitle.setText(subtitle)
        self._empty_label.setVisible(count == 0)
        self._list.setVisible(count > 0)
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
        self._stack.reversed_order = not self._stack.reversed_order
        self._set_order_control()
        self._order_btn.setObjectName("toggleActive" if self._stack.reversed_order else "")
        self._order_btn.style().unpolish(self._order_btn)
        self._order_btn.style().polish(self._order_btn)
        self._stack.arm_next()  # flipping order changes which item is "next"
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
        necessarily this one) and move to the next item. Mirrors what a real
        Ctrl+V did in the original once accessibility detected it."""
        self._stack.paste_next_and_advance()
        self.refresh()
        if not self.has_pending_items():
            self.fade_hide()  # matches PasteStackView.consumeAfterPaste(): closes once empty

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

    def show_and_focus(self) -> None:
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()

    def fade_hide(self) -> None:
        # Not animated -- see MainWindow.fade_hide()'s comment and
        # MIGRATION_LINUX.md, "Animaciones".
        self.hide()
