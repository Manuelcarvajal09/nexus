"""Windows Timeline with pinboard administration inside the existing surface.

No library window or persisted geometry is needed. Shared Timeline/storage
behavior is retained; the embedded editor and layout are Windows-only.
"""

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QApplication, QBoxLayout, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QLayout, QLineEdit, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from .pinboard_editor import _swatch_pixmap
from .pinboard_sidebar import HISTORY_ROW_ID, PinboardSidebar
from .storage import AVAILABLE_COLORS, SUGGESTED_EMOJI, TITLE_MAX_LENGTH, Pinboard, normalize_title
from .windows_settings_page import SettingsPage
from .windows_shortcuts import expand_quick_paste
from .timeline_window import TimelineWindow
from . import styles, windows_design
from .windows_card_delegate import WindowsClipCardDelegate
from .windows_material import upgrade_surface
from .windows_optical import OpticalGlass
from .windows_design import MOTION_FAST_MS, MOTION_OPEN_CONTENT_MS
from .windows_motion import SearchFocusHighlight, WindowsMotion, fade_in
from .windows_backdrop import configure_native_host, handle_native_host_event
from .windows_platform import activate_foreground_window


class _InlineEditor(QWidget):
    saved = Signal()
    cancelled = Signal()

    def __init__(self, board, parent, creating=False):
        super().__init__(parent)
        self.selected_emoji = board.emoji
        self.selected_color = board.color_hex
        root = QVBoxLayout(self)
        form = QWidget()
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(windows_design.INTERNAL_GAP)
        form_layout.addWidget(QLabel("Nombre"))
        self._name = QLineEdit(board.name)
        self._name.setPlaceholderText("Nombre del pinboard")
        self._name.setMinimumWidth(0)
        form_layout.addWidget(self._name)
        toggles = QHBoxLayout()
        toggles.setSpacing(windows_design.INTERNAL_GAP)
        self._emoji_toggle = QPushButton("Emoji")
        self._color_toggle = QPushButton("Color")
        for button, mode in ((self._emoji_toggle, "emoji"), (self._color_toggle, "color")):
            button.setCheckable(True)
            button.clicked.connect(lambda _checked, mode=mode: self._switch_mode(mode))
            toggles.addWidget(button)
        form_layout.addLayout(toggles)
        form_layout.addStretch()
        self._name.setStyleSheet("padding: 6px 10px;")
        for button in (self._emoji_toggle, self._color_toggle):
            button.setStyleSheet("padding: 5px 10px;")
        identity = QWidget()
        identity_layout = QVBoxLayout(identity)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.setSpacing(windows_design.INTERNAL_GAP)
        identity_layout.addWidget(QLabel("Identidad visual"))
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_emoji_grid())
        self._stack.addWidget(self._build_color_grid())
        identity_layout.addWidget(self._stack)
        identity_layout.addStretch()
        self._form_body = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._form_body.setSpacing(windows_design.BLOCK_GAP)
        self._form_body.addWidget(form, 4)
        self._form_body.addWidget(identity, 6)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(windows_design.BLOCK_GAP)
        root.addLayout(self._form_body)
        self._actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._actions.setSpacing(windows_design.INTERNAL_GAP)
        self._actions.addStretch()
        for text, name, callback in (("Cancelar", "flat", self.reject), ("Guardar", "primary", self._save)):
            button = QPushButton(text)
            button.setObjectName(name)
            button.clicked.connect(callback)
            self._actions.addWidget(button)
        root.addLayout(self._actions)
        self._columns = None
        self._switch_mode("emoji" if board.emoji else "color")
        # Start with the narrow minimum; a default QWidget width of 640 would
        # otherwise force a ten-column minimum into a narrow scroll viewport.
        self.resize(200, self.height())
        self._reflow()

    def _save(self):
        if not self._name.text().strip():
            self._name.setFocus()
            return
        self.accept()

    def _switch_mode(self, mode):
        self._emoji_toggle.setChecked(mode == "emoji")
        self._color_toggle.setChecked(mode == "color")
        self._stack.setCurrentIndex(0 if mode == "emoji" else 1)

    def _pick_emoji(self, emoji):
        self.selected_emoji = emoji

    def result_kwargs(self):
        return {"name": self._name.text().strip(), "emoji": self.selected_emoji if self._emoji_toggle.isChecked() else None,
                "color_hex": self.selected_color}

    def _build_color_grid(self):
        page = QWidget()
        grid = QGridLayout(page)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        for index, color in enumerate(AVAILABLE_COLORS):
            button = QPushButton()
            button.setFixedSize(28, 28)
            button.setProperty("colorSwatch", True)
            button.setIcon(QIcon(_swatch_pixmap(color, color == self.selected_color)))
            button.setIconSize(button.size())
            button.setToolTip(color)
            button.setAccessibleName(f"Color {color}")
            button.clicked.connect(lambda _checked, color=color: self._pick_color(color))
            grid.addWidget(button, index // 10, index % 10)
        return page

    def _build_emoji_grid(self):
        page = QWidget()
        grid = QGridLayout(page)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        for index, emoji in enumerate(SUGGESTED_EMOJI):
            button = QPushButton(emoji)
            button.setFixedSize(28, 28)
            button.setStyleSheet("padding: 0; font-size: 16px;")
            button.setAccessibleName(f"Emoji {emoji}")
            button.clicked.connect(lambda _checked, emoji=emoji: self._pick_emoji(emoji))
            grid.addWidget(button, index // 10, index % 10)
        return page

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def _reflow(self):
        if not hasattr(self, "_form_body"):
            return
        columns = 10 if self.width() >= 540 else 5
        self._form_body.setDirection(QBoxLayout.Direction.LeftToRight if columns == 10 else QBoxLayout.Direction.TopToBottom)
        self._actions.setDirection(QBoxLayout.Direction.LeftToRight if self.width() >= 330 else QBoxLayout.Direction.TopToBottom)
        if self._columns != columns:
            self._columns = columns
            for page in (self._stack.widget(0), self._stack.widget(1)):
                grid = page.layout()
                widgets = [grid.takeAt(0).widget() for _ in range(grid.count())]
                for index, widget in enumerate(widgets):
                    grid.addWidget(widget, index // columns, index % columns)

    def _pick_color(self, hex_color):
        self.selected_color = hex_color
        old = self._stack.widget(1)
        self._stack.removeWidget(old)
        self._stack.insertWidget(1, self._build_color_grid())
        self._stack.setCurrentIndex(1)
        old.deleteLater()
        self._columns = None
        if hasattr(self, "_form_body"):
            self._reflow()

    def accept(self):
        self.saved.emit()

    def reject(self):
        self.cancelled.emit()


class _InlineSidebar(PinboardSidebar):
    create_requested = Signal()
    edit_requested = Signal(int)
    delete_requested = Signal(int)

    def refresh(self):
        super().refresh()
        self._list.blockSignals(True)
        for row in range(self._list.count() - 1, -1, -1):
            if self._list.item(row).data(Qt.ItemDataRole.UserRole) == HISTORY_ROW_ID:
                self._list.takeItem(row)
        self._list.blockSignals(False)

    def _on_create(self):
        self.create_requested.emit()

    def _show_board_menu(self, row_id, global_pos):
        menu = QMenu(self)
        edit = menu.addAction("Renombrar / color / emoji…")
        up = menu.addAction("Mover arriba")
        down = menu.addAction("Mover abajo")
        menu.addSeparator()
        delete = menu.addAction("Eliminar…")
        chosen = menu.exec(global_pos)
        if chosen == edit:
            self.edit_requested.emit(row_id)
        elif chosen == delete:
            self.delete_requested.emit(row_id)
        elif chosen in (up, down):
            self._storage.move_pinboard(row_id, -1 if chosen == up else 1)
            self.refresh()
            self.changed.emit()


class _TitleEditor(QLineEdit):
    """Inline editor for a clip's title, drawn over the card's title line.

    It owns every key it needs while open: Enter saves (and never pastes), Esc
    cancels (and never hides the panel), Tab stays here, and Ctrl+1..9 / Ctrl+F
    are not hijacked by the panel's global shortcuts."""

    committed = Signal(str)
    cancelled = Signal()

    def __init__(self, text, parent):
        super().__init__(text, parent)
        self.setObjectName("titleEditor")
        self.setMaxLength(TITLE_MAX_LENGTH)
        self.setPlaceholderText("Añadir título")
        self.setAccessibleName("Título del elemento")
        self._finished = False

    def finish(self, commit):
        if self._finished:
            return
        self._finished = True
        if commit:
            self.committed.emit(self.text())
        else:
            self.cancelled.emit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            self.finish(True)
        elif event.key() == Qt.Key.Key_Escape:
            event.accept()
            self.finish(False)
        else:
            super().keyPressEvent(event)

    def event(self, event):
        kind = event.type()
        if kind == QEvent.Type.ShortcutOverride:
            key = event.key()
            ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            if (ctrl and (Qt.Key.Key_1 <= key <= Qt.Key.Key_9 or key == Qt.Key.Key_F)) or key == Qt.Key.Key_F2:
                event.accept()
                return True
        elif kind == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            event.accept()
            return True
        return super().event(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.finish(True)


class _DetailsDialog(QDialog):
    """Compact "Detalles" with the editable title; the panel stays the owner."""

    def __init__(self, item, board_names, parent):
        super().__init__(parent)
        self.setObjectName("detailsDialog")
        self.setWindowTitle("Detalles")
        self.setModal(True)
        self.setMinimumWidth(360)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(windows_design.INTERNAL_GAP)
        root.addWidget(self._key("Título"))
        self.title_edit = QLineEdit(item.title or "")
        self.title_edit.setMaxLength(TITLE_MAX_LENGTH)
        self.title_edit.setPlaceholderText("Sin título")
        self.title_edit.setAccessibleName("Título del elemento")
        root.addWidget(self.title_edit)
        for key, value in (
            ("Tipo", item.kind), ("Origen", item.source_app or "desconocido"),
            ("Pinboards", ", ".join(board_names) or "ninguno"), ("ID local", str(item.id)),
        ):
            root.addWidget(self._key(key))
            label = QLabel(value)
            label.setObjectName("detailValue")
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            root.addWidget(label)
        buttons = QDialogButtonBox()
        save = buttons.addButton("Guardar", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setObjectName("primary")
        buttons.addButton("Cerrar", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.title_edit.setFocus()
        self.title_edit.selectAll()

    @staticmethod
    def _key(text):
        label = QLabel(text)
        label.setObjectName("detailKey")
        return label


class WindowsTimelineWindow(TimelineWindow):
    def __init__(self, storage, paste_service, preview_service=None, parent=None):
        super().__init__(storage, paste_service, preview_service, parent)
        configure_native_host(self)
        self._persistent_when_inactive = True
        self._editor = None
        self._editing_id = None
        self._title_editor = None
        self._title_item_id = None
        rename = QShortcut(QKeySequence("F2"), self)
        rename.activated.connect(self.begin_title_edit)
        # Name the shared panel's anonymous shortcuts so Settings can rebind them.
        self._rename_shortcut = rename
        self._local_shortcuts = {"search": None, "board_prev": None, "board_next": None, "quick_paste": {}}
        for shortcut in self.findChildren(QShortcut):
            key = shortcut.key().toString()
            if key == "Ctrl+F":
                self._local_shortcuts["search"] = shortcut
            elif key == "Ctrl+Left":
                self._local_shortcuts["board_prev"] = shortcut
            elif key == "Ctrl+Right":
                self._local_shortcuts["board_next"] = shortcut
            elif key.startswith("Ctrl+") and key[5:].isdigit():
                self._local_shortcuts["quick_paste"][int(key[5:])] = shortcut
        self._settings_page = None
        self._settings_backend = None
        self._model.modelReset.connect(self._sync_title_editor)
        for bar in (self._list.horizontalScrollBar(), self._list.verticalScrollBar()):
            bar.valueChanged.connect(self._sync_title_editor)
        self.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        # Replace only the chip layout's host. Its natural minimum width must
        # never propagate from arbitrarily long board names to the HWND.
        header = self._chrome.layout().itemAt(0).layout()
        header.takeAt(1)
        chip_host = QWidget()
        chip_host.setLayout(self._pinboard_row)
        self._pinboard_row.setContentsMargins(0, 0, 0, 0)
        self._pinboard_row.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self._chip_scroll = QScrollArea()
        self._chip_scroll.setObjectName("pinboardChipScroll")
        self._chip_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._chip_scroll.setWidgetResizable(True)
        self._chip_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._chip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._chip_scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._chip_scroll.setMinimumWidth(0)
        self._chip_scroll.setWidget(chip_host)
        header.insertWidget(1, self._chip_scroll, 1)
        header.setStretch(2, 0)

        # A fixed-size viewport, not an editor sizeHint, owns panel height.
        self._pages = QStackedWidget()
        self._pages.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        timeline_page = QWidget()
        timeline_layout = QVBoxLayout(timeline_page)
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_layout.setSpacing(0)
        for widget in (self._list, self._empty_label):
            self._surface_layout.removeWidget(widget)
            timeline_layout.addWidget(widget)
        self._pages.addWidget(timeline_page)
        self._admin_page = QWidget()
        admin_layout = QVBoxLayout(self._admin_page)
        admin_layout.setContentsMargins(0, 0, 0, 0)
        admin_layout.setSpacing(windows_design.BLOCK_GAP)
        self._back_btn = QPushButton("← Volver")
        self._back_btn.setObjectName("expandToggle")
        self._back_btn.clicked.connect(self.close_pinboard_manager)
        admin_layout.addWidget(self._back_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        body = QHBoxLayout()
        body.setSpacing(windows_design.BLOCK_GAP)
        self._sidebar = _InlineSidebar(storage)
        self._sidebar.setObjectName("pinboardNavigation")
        self._sidebar.set_compact(True)
        self._sidebar.setMinimumWidth(120)
        self._sidebar.setMaximumWidth(156)
        self._sidebar.layout().setContentsMargins(0, 0, 0, 0)
        self._sidebar.layout().setSpacing(windows_design.INTERNAL_GAP)

        self._sidebar._edit_btn.setAccessibleName("Opciones del pinboard")
        body.addWidget(self._sidebar)
        self._editor_scroll = QScrollArea()
        self._editor_scroll.setObjectName("pinboardDetailScroll")
        self._editor_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._editor_scroll.setWidgetResizable(True)
        self._editor_scroll.setMinimumSize(0, 0)
        body.addWidget(self._editor_scroll, 1)
        admin_layout.addLayout(body, 1)
        self._pages.addWidget(self._admin_page)
        self._surface_layout.addWidget(self._pages)
        self._sidebar.create_requested.connect(lambda: self._open_editor(None))
        self._sidebar.edit_requested.connect(self._open_editor)
        self._sidebar.delete_requested.connect(self._delete_pinboard)
        self._sidebar.board_selected.connect(self._admin_selected)
        self._sidebar.changed.connect(self.refresh)
        self.manage_pinboards_requested.connect(self.show_pinboard_manager)
        self._surface = upgrade_surface(self, self._surface)
        self._surface_layout.setContentsMargins(*([windows_design.SURFACE_MARGIN] * 4))
        self._surface_layout.setSpacing(windows_design.BLOCK_GAP)
        # Surface owns the one outer inset. Extra chrome padding here would
        # double the header/search gutter and misalign it with cards/detail.
        self._chrome.layout().setContentsMargins(0, 0, 0, 0)
        self._chrome.layout().setSpacing(windows_design.INTERNAL_GAP)
        self._motion = WindowsMotion(self)
        old_delegate = self._delegate
        self._delegate = WindowsClipCardDelegate(self._list)
        self._optical = OpticalGlass(self, self._optical_controls)
        self._delegate.set_optical(self._optical, self)
        self._delegate.set_preview_data(self._preview_data)
        self._list.setItemDelegate(self._delegate)
        old_delegate.deleteLater()
        self._list.setViewportMargins(0, 0, 0, 0)
        self._list.setSpacing(2)
        # At compact widths the icon-mode row must remain reachable instead of
        # silently clipping the trailing card; only this Windows view changes.
        self._sync_history_scrollbar()
        self._empty_label.setTextFormat(Qt.TextFormat.PlainText)
        self._empty_label.setWordWrap(True)

        # Brand/utility row, navigation row, search: independent hierarchies.
        header.removeWidget(self._chip_scroll)
        self._chrome.layout().insertWidget(1, self._chip_scroll)
        header.setSpacing(windows_design.INTERNAL_GAP)
        self._brand = QLabel()
        self._brand.setFixedSize(24, 24)
        header.insertWidget(0, self._brand)
        eyebrow = QLabel("PORTAPAPELES")
        eyebrow.setObjectName("contextEyebrow")
        self._eyebrow = eyebrow
        header.insertWidget(2, eyebrow)
        self._count_badge.setFixedHeight(24)
        self._count_badge.setToolTip("Elementos en la vista actual")
        self._style_btn.setAccessibleName("Elegir estilo visual")
        # The Expandir/Contraer control is not part of the Windows header.
        header.removeWidget(self._expand_btn)
        self._expand_btn.deleteLater()
        self._expand_btn = None
        self._close_btn.setText("")
        self._search.setPlaceholderText("Buscar en tu historial…")
        self._search.setAccessibleName("Buscar contenido del portapapeles")
        self._search.setClearButtonEnabled(True)
        self._search_action = self._search.addAction(windows_design.icon("search", windows_design.palette().muted), QLineEdit.ActionPosition.LeadingPosition)
        self._search_highlight = SearchFocusHighlight(self._search)
        # No footer: the keyboard legend is gone (shortcuts keep working) and the
        # brief "Guardado" confirmation now lives in the header, beside the count.
        self._feedback_label = QLabel("Guardado")
        self._feedback_label.setObjectName("successFeedback")
        self._feedback_label.hide()
        header.insertWidget(header.indexOf(self._count_badge), self._feedback_label)
        self._feedback_timer = QTimer(self)
        self._feedback_timer.setSingleShot(True)
        self._feedback_timer.setInterval(1400)
        self._feedback_timer.timeout.connect(self._feedback_label.hide)
        self._pages.setAccessibleName("Historial")
        self.apply_visual_theme()
        self._sync_chip_height()
        QApplication.instance().focusChanged.connect(self._ensure_editor_focus_visible)

    @Slot(QWidget, QWidget)
    def _ensure_editor_focus_visible(self, previous, current):
        # Keep Save/color/emoji actions reachable inside the nested editor on
        # short displays, including explicit keyboard-focus changes.
        if (current is not None and self._pages.currentIndex() == 1
                and self._editor_scroll.isAncestorOf(current)):
            self._editor_scroll.ensureWidgetVisible(current, 8, 8)

    def nativeEvent(self, event_type, message):
        result = handle_native_host_event(self, event_type, message)
        if result is not None:
            return result
        return super().nativeEvent(event_type, message)

    def paintEvent(self, event):
        if getattr(getattr(self, "_surface", None), "_native_backdrop", False):
            painter = QPainter(self)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(event.rect(), QColor(0, 0, 0, 0))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            self._surface.paint_native_backdrop(painter)
            optical = getattr(self, "_optical", None)
            if optical is not None:
                optical.paint_controls(painter)   # lens glass behind the (transparent) controls
            painter.end()
        else:
            painter = QPainter(self)
            painter.fillRect(event.rect(), QColor(windows_design.palette().panel))
            painter.end()

    def _optical_controls(self):
        return [(self._search, 12), (self._style_btn, 10), (self._close_btn, 14), (self._chip_scroll, 14)]

    def hideEvent(self, event):
        # The captured backdrop is only valid for this opening and is never kept.
        optical = getattr(self, "_optical", None)
        if optical is not None:
            optical.release()
        super().hideEvent(event)

    def apply_visual_theme(self):
        p = windows_design.palette()
        self._surface.update()
        self._brand.setPixmap(windows_design.icon("brand", p.accent, 24).pixmap(24, 24))
        self._close_btn.setIcon(windows_design.icon("close", p.muted, 16))
        self._search_action.setIcon(windows_design.icon("search", p.muted))
        # Drawn chevron (right side) instead of the native menu arrow.
        self._style_btn.setIcon(windows_design.icon("chevron_down", p.muted, 12))
        self._style_btn.setIconSize(QSize(12, 12))
        self._style_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._sidebar._edit_btn.setText("")
        self._sidebar._edit_btn.setIcon(windows_design.icon("more", p.muted, 18))
        self._sidebar._edit_btn.setIconSize(QSize(18, 18))
        self._back_btn.setText("Volver")
        self._back_btn.setIcon(windows_design.icon("back", p.muted, 16))
        if getattr(self, "_settings_page", None) is not None:
            self._settings_page.back_button.setIcon(windows_design.icon("back", p.muted, 16))
        labels = dict(windows_design.theme_choices())
        for action, key in self._theme_actions.items():
            action.setText(labels[key])
        self._chip_signature = None
        self.refresh()
        self._sync_header_density()

    def _sync_header_density(self):
        if not hasattr(self, "_brand"):
            return
        self._eyebrow.setVisible(self.width() >= 760)
        self._brand.setVisible(self.width() >= 520)
        self._count_badge.setVisible(self.width() >= 520)

    def _sync_history_scrollbar(self):
        # Normal desktop widths fit all cards; only compact windows may need
        # horizontal scrolling to reach a trailing card.
        self._list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff if self.width() >= 900
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_history_scrollbar()
        self._sync_header_density()

    def _rebuild_pinboard_chips(self):
        boards = self._storage.list_pinboards()
        signature = (windows_design.palette(),
                     tuple((b.id, b.name, b.color_hex, b.emoji) for b in boards))
        if signature == getattr(self, "_chip_signature", None):
            self._update_chip_state()
            return
        super()._rebuild_pinboard_chips()
        self._chip_signature = signature
        # The generic "Pinboards" pill and "+ Pinboard" are intentionally
        # removed from the navigation. Real Pinboards remain first-class tabs.
        for index in range(self._pinboard_row.count() - 1, -1, -1):
            widget = self._pinboard_row.itemAt(index).widget()
            if isinstance(widget, QPushButton) and widget.text() in ("+ Pinboard", "Pinboards"):
                self._pinboard_row.takeAt(index)
                widget.deleteLater()
        p = windows_design.palette()
        for board_id, button in self._pinboard_buttons.items():
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(button.text())
            button.setAccessibleName(button.text())
            button.clicked.disconnect()
            button.clicked.connect(lambda _checked, board_id=board_id: self._navigate_board(board_id))
            # The board name already carries its emoji. A second generic board
            # icon would duplicate it and steal enough width to elide the
            # selected, bold label.
            if board_id is None:
                button.setIcon(windows_design.icon("history", p.accent if button.isChecked() else p.muted, 15))
            else:
                button.setIcon(QIcon())
        self._sync_chip_widths()
        self._pinboard_row.addStretch()
        options = QToolButton()
        options.setObjectName("navigationOptions")
        options.setIconSize(QSize(18, 18))
        options.setToolTip("Más opciones")
        options.setAccessibleName("Más opciones: Pinboards y Configuración")
        options.setCheckable(True)
        options.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(options)
        manage = menu.addAction("Administrar Pinboards")
        manage.triggered.connect(self.manage_pinboards_requested.emit)
        if getattr(self, "_settings_page", None) is not None:
            menu.addAction("Configuración").triggered.connect(self.show_settings)
        options.setMenu(menu)
        self._pinboard_row.addWidget(options)
        self._management_btn = options
        self._update_chip_state()

    def _update_chip_state(self):
        p = windows_design.palette()
        in_manager = hasattr(self, "_pages") and self._pages.currentIndex() != 0
        for board_id, button in self._pinboard_buttons.items():
            button.setChecked(not in_manager and board_id == self._current_pinboard_id)
            if board_id is None:
                button.setIcon(windows_design.icon("history", p.accent if button.isChecked() else p.muted, 15))
            else:
                button.setIcon(QIcon())
        self._sync_chip_widths()
        if hasattr(self, "_management_btn"):
            self._management_btn.setChecked(in_manager)
            self._management_btn.setIcon(windows_design.icon("more", p.accent if in_manager else p.muted, 18))

    def _sync_chip_widths(self):
        """Reserve enough room for the checked (bold) label on every theme.

        QSS applies a heavier weight to the checked chip. QPushButton does not
        automatically grow when that state font changes, so a long board name
        could be elided even though the row has free space. The metrics are
        measured from a bold copy of the current theme font, then padding,
        border and optional icon width are added explicitly.
        """
        for button in self._pinboard_buttons.values():
            button.ensurePolished()
            bold = QFont(button.font())
            bold.setBold(True)
            text_width = QFontMetrics(bold).horizontalAdvance(button.text())
            icon_width = 0
            if not button.icon().isNull():
                icon_width = button.iconSize().width() + 6
            # 12 px padding on both sides, 1 px border on both sides, and a
            # small safety margin for emoji fallback fonts.
            button.setMinimumWidth(text_width + icon_width + 24 + 2 + 4)

    def _new_pinboard(self):
        self.show_pinboard_manager(creating=True)

    def _navigate_board(self, board_id):
        was_admin = hasattr(self, "_pages") and self._pages.currentIndex() != 0
        if hasattr(self, "_pages"):
            self._pages.setCurrentIndex(0)
            self._search.setEnabled(True)
            self._search.show()
        self._select_pinboard(board_id)
        if was_admin:
            self._apply_geometry()
        self._sync_header_density()

    @property
    def _pinboard_id(self):
        """One source of truth for integrations using the library's spelling."""
        return self._current_pinboard_id

    @_pinboard_id.setter
    def _pinboard_id(self, value):
        self._current_pinboard_id = value

    def _sync_chip_height(self):
        # Reserve scrollbar space even before overflow: no header-height jump.
        buttons = self._chip_scroll.widget().findChildren(QPushButton)
        for button in buttons:
            button.ensurePolished()
        height = max((button.sizeHint().height() for button in buttons), default=0)
        height += self._chip_scroll.horizontalScrollBar().sizeHint().height()
        self._chip_scroll.setFixedHeight(height)

    def refresh(self):
        if (self._current_pinboard_id is not None
                and self._storage.get_pinboard(self._current_pinboard_id) is None):
            self._current_pinboard_id = None
        super().refresh()
        # The search box says where it will look (history or the open pinboard).
        board = self._storage.get_pinboard(self._current_pinboard_id) if self._current_pinboard_id is not None else None
        self._search.setPlaceholderText(f"Buscar en «{board.name}»…" if board else "Buscar en tu historial…")
        if self._model.rowCount() == 0:
            query = self._search.text().strip()
            if query:
                self._empty_label.setText(f"Sin resultados para «{query}».\nPrueba con otra palabra.")
            elif self._current_pinboard_id is not None:
                self._empty_label.setText("Este pinboard está vacío.\nGuarda elementos desde el menú del historial.")
            else:
                self._empty_label.setText("Tu historial empieza aquí.\nCopia texto, imágenes o archivos para guardarlos.")
        # Preview payloads include images. The persistent cache can retrieve
        # them again, so retain only the bounded current model in RAM.
        current_ids = {item.id for item in self._model._items}
        for key in list(self._preview_data):
            if key not in current_ids:
                del self._preview_data[key]
        if hasattr(self, "_feedback_label"):
            board = self._storage.get_pinboard(self._current_pinboard_id) if self._current_pinboard_id is not None else None
            self._pages.setAccessibleName("Pinboards" if self._pages.currentIndex() == 1 else board.name if board else "Historial")
            self._count_badge.setAccessibleName(f"{self._model.rowCount()} elementos en la vista actual")
        if hasattr(self, "_sidebar"):
            self._sidebar.refresh()
            board_list = self._sidebar._list
            board_list.blockSignals(True)
            board_list.setCurrentRow(-1)
            selected_id = self._editing_id if self._pages.currentIndex() == 1 else self._current_pinboard_id
            for row in range(board_list.count()):
                row_id = board_list.item(row).data(Qt.ItemDataRole.UserRole)
                if row_id == selected_id:
                    board_list.setCurrentRow(row)
                    break
            board_list.blockSignals(False)
            self._sync_chip_height()
            if (self._editing_id is not None
                    and self._storage.get_pinboard(self._editing_id) is None):
                self._open_editor(self._current_pinboard_id, creating=False)

    def show_pinboard_manager(self, creating=False):
        self._pages.setCurrentWidget(self._admin_page)
        self._update_chip_state()
        self._search.setEnabled(False)
        self._search.hide()
        self._pages.setAccessibleName("Pinboards")
        self._sidebar.refresh()
        self._open_editor(None if creating else self._current_pinboard_id, creating=creating)
        self._apply_geometry()
        if hasattr(self, "_motion"):
            self._motion.transition(self._admin_page)

    def show_pinboard_management(self):
        """Tray entry point: show this same panel, then its internal page."""
        if not self.isVisible():
            self.show_and_focus()
        if self.isVisible():
            self.show_pinboard_manager()

    def close_pinboard_manager(self):
        was_admin = self._pages.currentIndex() != 0
        self._pages.setCurrentIndex(0)
        self._search.setEnabled(True)
        self._search.show()
        self.refresh()
        self._search.setFocus()
        if was_admin:
            self._apply_geometry()
        if hasattr(self, "_motion"):
            self._motion.transition(self._pages.currentWidget())
            self._sync_header_density()

    def _admin_selected(self, board):
        self._select_pinboard(board.id if board else None)

    def _open_editor(self, board_id, creating=True):
        board = self._storage.get_pinboard(board_id) if board_id is not None else None
        self._editing_id = board.id if board else None
        board_list = self._sidebar._list
        board_list.blockSignals(True)
        board_list.setCurrentRow(-1)
        for row in range(board_list.count()):
            if board_list.item(row).data(Qt.ItemDataRole.UserRole) == self._editing_id:
                board_list.setCurrentRow(row)
        board_list.blockSignals(False)
        old = self._editor_scroll.takeWidget()
        if old is not None:
            old.setParent(self._editor_scroll)
            old.deleteLater()
        host = QWidget()
        host.setObjectName("pinboardDetail")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(windows_design.BLOCK_GAP)
        self._right_heading = QLabel(board.name if board else "Nuevo pinboard" if creating else "Tus pinboards")
        self._right_heading.setObjectName("pinboardDetailHeading")
        self._right_heading.setTextFormat(Qt.TextFormat.PlainText)
        self._right_heading.setWordWrap(True)
        heading_font = self.font()
        heading_font.setPointSizeF(max(18, heading_font.pointSizeF() * 1.35))
        heading_font.setBold(True)
        self._right_heading.setFont(heading_font)
        self._right_heading.setStyleSheet("font-size: 24px; font-weight: 600;")
        layout.addWidget(self._right_heading)
        if board is None and not creating:
            self._editor = None
            empty = QLabel("Un lugar para lo importante.\nSelecciona un pinboard o crea uno con + Nuevo.")
            empty.setObjectName("pinboardDetailEmpty")
            empty.setWordWrap(True)
            layout.addWidget(empty)
            layout.addStretch()
            self._editor_scroll.setWidget(host)
            return
        if board is None:
            board = Pinboard(0, "", AVAILABLE_COLORS[0], None, 0, 0)
        self._editor = _InlineEditor(board, host, creating=self._editing_id is None)
        self._editor.saved.connect(self._save_pinboard)
        self._editor.cancelled.connect(self.close_pinboard_manager)
        layout.addWidget(self._editor)
        if self._editing_id is not None:
            delete = QPushButton("Eliminar")
            delete.setObjectName("flat")
            delete.clicked.connect(lambda: self._delete_pinboard(self._editing_id))
            self._editor._actions.insertWidget(0, delete)
        layout.addStretch()
        self._editor_scroll.setWidget(host)
        self._editor._name.setFocus()

    def _save_pinboard(self):
        kwargs = self._editor.result_kwargs()
        if not kwargs["name"]:
            return
        if self._editing_id is None:
            board = self._storage.create_pinboard(**kwargs)
            self._editing_id = board.id
        elif self._storage.get_pinboard(self._editing_id) is not None:
            self._storage.update_pinboard(self._editing_id, **kwargs)
        else:
            self.refresh()
            self._open_editor(None)
            return
        self._select_pinboard(self._editing_id)
        self._feedback_label.show()
        fade_in(self._feedback_label, MOTION_FAST_MS)
        self._feedback_timer.start()

    def _delete_pinboard(self, board_id):
        board = self._storage.get_pinboard(board_id) if board_id is not None else None
        if board is None:
            return
        answer = QMessageBox.question(
            self, "Eliminar pinboard", f"¿Eliminar «{board.name}»? Los clips se conservarán en Historial.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._storage.delete_pinboard(board_id)
        self.refresh()
        self._open_editor(self._current_pinboard_id, creating=False)

    def _select_pinboard(self, pinboard_id):
        previous = self._current_pinboard_id
        super()._select_pinboard(pinboard_id)
        if hasattr(self, "_pages") and self._pages.currentIndex() == 1:
            self._open_editor(self._current_pinboard_id, creating=False)
        if previous != pinboard_id and hasattr(self, "_motion"):
            self._motion.transition(self._pages.currentWidget())

    def _quick_paste(self, n):
        if self._pages.currentIndex() == 0:
            super()._quick_paste(n)

    def _on_preview_ready(self, item_id, data):
        if self._model.row_for_id(item_id) >= 0:
            super()._on_preview_ready(item_id, data)

    # -- settings page and rebindable shortcuts ------------------------------

    def configure_settings(self, backend):
        """Attach the Configuración page (page 2) and apply saved shortcuts."""
        self._settings_backend = backend
        self._settings_page = SettingsPage(backend, windows_design.theme_choices(), self)
        self._settings_page.theme_selected.connect(self.theme_requested.emit)
        self._settings_page.back_requested.connect(self.close_pinboard_manager)
        backend.on_capture_changed(lambda _paused: self._settings_page.sync_capture())
        self._settings_page.sync_theme(styles.active_theme().key)
        self._settings_page.back_button.setIcon(windows_design.icon("back", windows_design.palette().muted, 16))
        self._pages.addWidget(self._settings_page)
        self.apply_shortcuts()
        self._chip_signature = None
        self.refresh()

    def apply_shortcuts(self):
        """Bind the rebindable local shortcuts to the stored combinations."""
        if self._settings_backend is None:
            return
        keys = self._settings_backend.store.local_keys()
        local = self._local_shortcuts
        self._rename_shortcut.setKey(QKeySequence(keys["rename"]))
        for identifier in ("search", "board_prev", "board_next"):
            if local[identifier] is not None:
                local[identifier].setKey(QKeySequence(keys[identifier]))
        for number, text in enumerate(expand_quick_paste(keys["quick_paste"]), start=1):
            if number in local["quick_paste"]:
                local["quick_paste"][number].setKey(QKeySequence(text))

    def set_shortcuts_enabled(self, enabled):
        """Off while Settings captures a key, so existing shortcuts can be re-assigned."""
        shortcuts = [self._rename_shortcut, *self._local_shortcuts["quick_paste"].values()]
        shortcuts += [s for s in (self._local_shortcuts[k] for k in ("search", "board_prev", "board_next")) if s is not None]
        for shortcut in shortcuts:
            shortcut.setEnabled(enabled)

    def show_settings(self):
        if self._settings_page is None:
            return
        self._commit_open_title_edit()
        self._pages.setCurrentWidget(self._settings_page)
        self._search.setEnabled(False)
        self._search.hide()
        self._pages.setAccessibleName("Configuración")
        self._settings_page.refresh()
        self._update_chip_state()
        self._apply_geometry()
        if hasattr(self, "_motion"):
            self._motion.transition(self._settings_page)

    def show_settings_management(self):
        """Tray entry point: show this same panel, then Configuración."""
        if not self.isVisible():
            self.show_and_focus()
        if self.isVisible():
            self.show_settings()

    def set_theme_selection(self, key):
        super().set_theme_selection(key)
        if self._settings_page is not None:
            self._settings_page.sync_theme(key)

    def _cycle_pinboard(self, step):
        if self._pages.currentIndex() == 0:
            super()._cycle_pinboard(step)

    # -- titles ------------------------------------------------------------

    def _extend_item_menu(self, menu, item):
        action = menu.addAction("Renombrar…\tF2")
        return {action: lambda item_id=item.id: self.begin_title_edit(item_id)}

    def begin_title_edit(self, item_id=None):
        """Open the inline title editor over a card (the current one by default)."""
        if self._title_editor is not None or self._pages.currentIndex() != 0:
            return False
        if item_id is None:
            index = self._list.currentIndex()
            item = self._model.item_at(index.row()) if index.isValid() else None
        else:
            item = self._model.item_at(self._model.row_for_id(item_id))
        if item is None:
            return False
        self._list.scrollTo(self._model.index(self._model.row_for_id(item.id), 0))
        editor = _TitleEditor(item.title or "", self._list.viewport())
        editor.committed.connect(lambda text, item_id=item.id: self._finish_title_edit(item_id, text))
        editor.cancelled.connect(lambda item_id=item.id: self._finish_title_edit(item_id, None))
        self._title_editor, self._title_item_id = editor, item.id
        self._delegate.set_editing_item(item.id)
        self._sync_title_editor()
        editor.show()
        fade_in(editor, MOTION_FAST_MS)
        editor.setFocus()
        editor.selectAll()
        self._list.viewport().update()
        return True

    def _sync_title_editor(self, *_):
        """Keep the editor on its card across refreshes, scrolling and resizes."""
        if self._title_editor is None:
            return
        row = self._model.row_for_id(self._title_item_id)
        if row < 0:
            self._title_editor.finish(False)
            return
        rect = self._delegate.title_rect(self._list.visualRect(self._model.index(row, 0))).toRect()
        self._title_editor.setGeometry(rect.adjusted(-4, -2, 4, 2))

    def _commit_open_title_edit(self):
        if self._title_editor is not None:
            self._title_editor.finish(True)

    def _finish_title_edit(self, item_id, text):
        editor, self._title_editor, self._title_item_id = self._title_editor, None, None
        self._delegate.set_editing_item(None)
        if editor is not None:
            editor.hide()
            editor.deleteLater()
        if text is not None:
            self._apply_title(item_id, text)
        else:
            self._list.viewport().update()
        self._list.setFocus()

    def _apply_title(self, item_id, text):
        current = self._storage.get(item_id)
        if current is not None and normalize_title(text) != current.title:
            self._storage.set_title(item_id, text)
            self.refresh()
        row = self._model.row_for_id(item_id)
        if row >= 0:
            self._list.setCurrentIndex(self._model.index(row, 0))

    def _show_item_details(self, item):
        names = [b.display_name for b in self._storage.list_pinboards() if b.id in item.pinboard_ids]
        dialog = _DetailsDialog(item, names, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_title(item.id, dialog.title_edit.text())

    def _confirm_delete(self, item):
        if not item.title:
            return True
        answer = QMessageBox.question(
            self, "Eliminar elemento", f"¿Eliminar «{item.title}»? También se perderá su título.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _toggle_expanded(self):
        """Tab. The shared panel binds it to Expand/Collapse, which Windows
        does not offer; here it moves focus between the search box and the
        results instead, so the whole panel stays keyboard-driven."""
        if self._pages.currentIndex() != 0 or self._title_editor is not None:
            return
        if self._search.hasFocus():
            self._focus_list()
        else:
            self._search.setFocus()
            self._search.selectAll()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._pages.currentIndex() != 0:
            self.close_pinboard_manager()
            return
        super().keyPressEvent(event)

    def _apply_geometry(self):
        """One measured placement in logical pixels, above the work-area edge.

        Polish before measuring, rather than showing at one position then
        moving on a timer. Administration intentionally has a taller viewport;
        neither page can impose its own minimum on the top-level window.
        """
        screen = self.resolve_target_screen()
        if screen is None:
            return False
        self.ensurePolished()
        self._sync_chip_height()
        outer = self.layout().contentsMargins()
        margins = self._surface_layout.contentsMargins()
        chrome = (self._chrome.sizeHint().height() + outer.top() + outer.bottom()
                  + margins.top() + margins.bottom() + self._surface_layout.spacing())
        avail = screen.availableGeometry()
        # Windows owns its edge clearances and content width; do not alter the
        # shared Linux panel geometry policy.
        target_width = min(950, max(760, round(avail.width() * .47)))
        if avail.width() < avail.height():
            target_width = 744
        width = min(target_width, max(1, avail.width() - 2 * windows_design.WORKAREA_SIDE))
        card_height = 184 if avail.height() >= 800 else 160
        wanted = card_height + 16 + chrome
        if self._pages.currentIndex() != 0:
            wanted = chrome + 440
        height = min(wanted, max(1, avail.height() - 2 * windows_design.WORKAREA_BOTTOM))
        if height - chrome < 164:
            card_height = 148
        self._delegate.set_card_height(card_height)
        card_width = 216
        self._delegate.set_card_width(card_width)
        self._list.setGridSize(QSize(card_width + 4, card_height + 4))
        body_height = max(0, height - chrome)
        self._list.setFixedHeight(body_height)
        self._empty_label.setFixedHeight(body_height)
        self._pages.setFixedHeight(body_height)
        self.layout().invalidate()
        self.layout().activate()
        x = avail.x() + (avail.width() - width) // 2
        y = avail.bottom() + 1 - height - windows_design.WORKAREA_BOTTOM
        self.setGeometry(QRect(x, max(avail.top(), y), width, height))
        return True

    def show_and_focus(self):
        self._commit_open_title_edit()
        self.close_pinboard_manager()
        if hasattr(self, "_motion"):
            self._sync_header_density()
        if not self._apply_geometry():
            return
        self._pending_focus_claim = True
        was_visible = self.isVisible()
        if not was_visible:
            self._optical.capture()   # before show(): Nexus itself must not be in the pixels
        self._motion.show(self)
        if not was_visible:
            # The HWND itself must not fade (that would break the DWM Acrylic),
            # so the content eases in instead: brief, from already half visible.
            fade_in(self._pages, MOTION_OPEN_CONTENT_MS, start=.55)
        self.raise_()
        self.activateWindow()
        if QApplication.platformName() == "windows":
            activate_foreground_window(int(self.internalWinId()))
        if self.isActiveWindow():
            self._pending_focus_claim = False
            self._search.setFocus()
            self._search.selectAll()
        # close_pinboard_manager() already refreshed data before measuring.
        # Repeating the query/model reset after show doubles opening work and
        # can overwrite a keyboard selection on the next event-loop turn.

    def fade_hide(self):
        self._commit_open_title_edit()
        self._motion.hide(self)

    def is_dismissing(self):
        return self._motion.is_dismissing()
