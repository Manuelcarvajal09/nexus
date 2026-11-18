"""Floating Stack surface. Visibility is independent of the collection session.

On Hyprland a temporary binding drives automatic dispatch with durable recovery.
Sending a shortcut is not an application receipt; manual controls remain as a
fallback when the temporary binding is paused or unavailable.
"""

from pathlib import Path

from PySide6.QtCore import QEvent, QSize, QTimer, Qt, QPropertyAnimation
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
    QGraphicsOpacityEffect,
)

from .paste_service import POST_HIDE_PASTE_DELAY_MS
from .row_widget import ClipRowWidget
from .stack import ClipStack
from . import styles, desktop, product_ui

RESOURCES_DIR = Path(__file__).resolve().parent.parent.parent / "resources"
# Kept in sync with timeline_window.DISMISS_DEBOUNCE_MS by convention, not
# import, to avoid a window-module-to-window-module dependency for one
# constant -- see that module's comment for the live-observed reasoning.
DISMISS_DEBOUNCE_MS = 150


class StackWindow(product_ui.Surface):
    def __init__(self, clip_stack: ClipStack, parent=None):
        super().__init__(parent)
        self._stack = clip_stack
        self._pending_activation = False
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.setInterval(DISMISS_DEBOUNCE_MS)
        self._dismiss_timer.timeout.connect(self._dismiss_if_still_inactive)

        product_ui.attach(self)
        desktop.track(self)
        self.setWindowTitle("Nexus Stack")
        self.resize(320, 500)
        self.setMinimumWidth(380)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
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
        title = product_ui.DragTitle("Nexus Stack")
        title.setObjectName("title")
        self._subtitle = QLabel("")
        self._subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(self._subtitle)
        header.addLayout(title_box)
        header.addStretch()

        self._order_btn = QPushButton()
        self._order_btn.setFixedSize(72, 32)
        self._set_order_control()
        self._order_btn.clicked.connect(self._toggle_order)
        header.addWidget(self._order_btn)
        close = product_ui.CloseButton(self)
        close.setToolTip('Ocultar sin cancelar ni perder pendientes')
        header.addWidget(close)
        root.addLayout(header)

        next_label = QLabel("01 · SIGUIENTE EN PEGAR")
        next_label.setObjectName("subtitle")
        root.addWidget(next_label)

        self._next_container = QVBoxLayout()
        self._next_container.setContentsMargins(0, 0, 0, 0)
        self._next_widget: ClipRowWidget | None = None
        self._seen_sent_count = 0
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

        from .settings import load_settings
        config = load_settings()
        hint = QLabel("Captura desactivada" if not config.capture_enabled else
                      "Copia con Ctrl+C. Prepara el siguiente, pega y confirma su entrega.")
        self._hint = hint
        hint.setObjectName("subtitle")
        hint.setWordWrap(True)
        root.addWidget(hint)

        row1 = QHBoxLayout()
        row1.addStretch()
        clear_btn = QPushButton("Cancelar sesión")
        clear_btn.setObjectName("flat")
        clear_btn.setToolTip('Finalizar captura y pegado; conservar pendientes para recuperar')
        clear_btn.clicked.connect(self._cancel_session)
        row1.addWidget(clear_btn)
        empty_btn = QPushButton('Vaciar pendientes')
        empty_btn.clicked.connect(self._on_clear)
        row1.addWidget(empty_btn)
        root.addLayout(row1)

        self._paste_next_btn = QPushButton("Enviar siguiente")
        self._paste_next_btn.setObjectName("primary")
        self._paste_next_btn.clicked.connect(self.advance)
        root.addWidget(self._paste_next_btn)

        self._prepare_btn = QPushButton("Preparar para Ctrl+V")
        self._prepare_btn.clicked.connect(self._prepare_manual)
        root.addWidget(self._prepare_btn)
        self._confirm_btn = QPushButton("Confirmar que se pegó")
        self._confirm_btn.setToolTip("Confirma solo después de ver el contenido en la aplicación destino")
        self._confirm_btn.clicked.connect(self._confirm_delivery)
        root.addWidget(self._confirm_btn)
        self.sequential = None
        self._capture_paused = False
        self._capture_btn = QPushButton('Pausar acumulación')
        self._capture_btn.clicked.connect(self._toggle_capture)
        root.addWidget(self._capture_btn)
        self._pause_btn = QPushButton('Pausar pegado secuencial')
        self._pause_btn.clicked.connect(self._toggle_sequential)
        root.addWidget(self._pause_btn)
        self._retry_btn = QPushButton('Reintentar pendiente')
        self._retry_btn.setToolTip('Revisa primero el destino: una entrega incierta puede haberse insertado')
        self._retry_btn.clicked.connect(self._retry_sequential)
        root.addWidget(self._retry_btn)
        self._restore_btn = QPushButton('Recuperar último enviado')
        self._restore_btn.clicked.connect(self._restore_sequential)
        root.addWidget(self._restore_btn)
        self.refresh()

    def is_collecting(self) -> bool:
        return self._stack.active and not self._capture_paused

    def has_pending_items(self) -> bool:
        return len(self._stack.items()) > 0

    def add_capture(self, item) -> None:
        self._stack.add(item)  # Deduplicates and persists without clipboard writes.
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
                child.widget().hide()
                child.widget().deleteLater()
        self._next_widget = None

        if display_order:
            head = display_order[0]
            next_row = ClipRowWidget(head, position=1)
            next_row.set_selected(True)  # reuses the accent-bordered QSS treatment for emphasis
            next_row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            next_row.customContextMenuRequested.connect(
                lambda pos, item_id=head.id: self._on_next_context_menu(next_row, item_id, pos)
            )
            self._next_container.addWidget(next_row)
            self._next_widget = next_row

        remaining = display_order[1:]
        self._list.blockSignals(True)
        self._list.clear()
        for position, item in enumerate(remaining, start=2):
            list_item = QListWidgetItem()
            list_item.setData(Qt.ItemDataRole.UserRole, item.id)
            row = ClipRowWidget(item, position=position)
            list_item.setSizeHint(QSize(0, row.sizeHint().height()))
            self._list.addItem(list_item)
            self._list.setItemWidget(list_item, row)

        self._list.blockSignals(False)
        from .settings import load_settings
        self._hint.setText("Captura desactivada" if not load_settings().capture_enabled else
                           "Copia con Ctrl+C. Prepara, pega y confirma cada entrega.")
        sequential = getattr(self, 'sequential', None)
        if sequential:
            self._pause_btn.show(); self._restore_btn.show()
            self._hint.setText(sequential.error or ('Ctrl+V secuencial activo · avance por envío aceptado, sin acuse de aplicación.'
                               if sequential.enabled else 'Pegado secuencial pausado · la captura de Stack continúa.'))
            self._pause_btn.setText('Pausar pegado secuencial' if sequential.enabled else 'Reanudar pegado secuencial')
            self._retry_btn.setVisible(sequential.blocked)
            self._order_btn.setEnabled(not sequential.busy and not sequential.blocked)
            self._paste_next_btn.setVisible(not sequential.enabled)
            self._prepare_btn.setVisible(not sequential.enabled)
            self._confirm_btn.setVisible(not sequential.enabled)
            if sequential.sent_count != self._seen_sent_count and self._next_widget:
                effect = QGraphicsOpacityEffect(self._next_widget)
                self._next_widget.setGraphicsEffect(effect)
                animation = QPropertyAnimation(effect, b'opacity', effect)
                animation.setDuration(120)
                animation.setStartValue(.35); animation.setEndValue(1.0)
                animation.start()
            self._seen_sent_count = sequential.sent_count
        else:
            self._pause_btn.hide(); self._retry_btn.hide(); self._restore_btn.hide()
        self._subtitle.setText((f"{count} pendiente{'s' if count != 1 else ''}" if count else "Listo para acumular")
                               + (" · Activo" if self._stack.active else " · Inactivo"))

        order_label = "FIFO" if not self._stack.reversed_order else "LIFO"
        self._remaining_label.setText(
            f"{len(remaining)} restante{'s' if len(remaining) != 1 else ''} · {order_label}"
            if count else ""
        )
        self._empty_label.setVisible(count == 0)
        self._list.setVisible(len(remaining) > 0)
        self._remaining_label.setVisible(count > 0)
        self._paste_next_btn.setEnabled(count > 0)
        self._prepare_btn.setEnabled(count > 0)
        self._confirm_btn.setEnabled(self._stack.pending_confirmation is not None)

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
        self._order_btn.setText("LIFO" if reversed_order else "FIFO")
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
        # The exact remembered target is focused by PasteService. Keep Stack
        # mapped while pending; successful dispatcher execution is not receipt.
        self._do_advance()

    def _do_advance(self) -> None:
        self._stack.paste_next_and_advance()
        self.refresh()

    def _prepare_manual(self) -> None:
        self._stack.prepare_manual_paste()
        self.refresh()

    def _confirm_delivery(self) -> None:
        item = self._stack.confirm_pending()
        self.refresh()
        if item is not None and not self.has_pending_items():
            self.fade_hide()

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
        if self.sequential:
            self.sequential.pause()
        self._stack.clear()
        self.refresh()
        self.fade_hide()

    def _toggle_sequential(self):
        if self.sequential:
            self.sequential.pause() if self.sequential.enabled else self.sequential.enable()

    def _toggle_capture(self):
        self._capture_paused = not self._capture_paused
        self._capture_btn.setText('Reanudar acumulación' if self._capture_paused else 'Pausar acumulación')

    def _cancel_session(self):
        if self.sequential:
            self.sequential.pause()
        self._stack.active = False
        self.refresh()
        self.fade_hide()

    def _retry_sequential(self):
        if self.sequential:
            self.sequential.retry_pending()

    def _restore_sequential(self):
        if self.sequential:
            self.sequential.restore_last()

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
            "Enviar siguiente"
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
        return desktop.restore(self, preferred=(400, 500))

    def show_and_focus(self) -> None:
        self._stack.activate()
        self.refresh()
        self._reposition_to_active_screen()
        self._pending_activation = True
        self.show()
        self.raise_()
        self.activateWindow()
        desktop.present(self)
        if self.isActiveWindow():
            # Some launches never fire another ActivationChange since the
            # state doesn't change (already active from a prior toggle) --
            # same fallback as TimelineWindow's _pending_focus_claim.
            self._pending_activation = False

    def changeEvent(self, event) -> None:
        # Stack remains mapped across focus/workspace changes.
        super().changeEvent(event)

    def _dismiss_if_still_inactive(self) -> None:
        pass

    def fade_hide(self) -> None:
        # Not animated -- see MainWindow.fade_hide()'s comment and
        # MIGRATION_LINUX.md, "Animaciones".
        desktop.save(self)
        self.hide()
