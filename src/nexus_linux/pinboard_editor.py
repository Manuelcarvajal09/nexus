"""Icon editor dialog for a pinboard: emoji picker + full color swatch grid,
toggled by a segmented control -- mirrors PinboardEditorView's IconMode
picker (emoji vs. color) and ColorSwatchGrid in PinboardSidebar.swift,
completing what was originally just an emoji-only submenu."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .i18n import plural, tr
from . import styles
from .storage import AVAILABLE_COLORS, Pinboard, SUGGESTED_EMOJI

SWATCH_SIZE = 28
COLUMNS = 5


def _swatch_pixmap(hex_color: str, selected: bool) -> QPixmap:
    pixmap = QPixmap(SWATCH_SIZE, SWATCH_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(hex_color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(1, 1, SWATCH_SIZE - 2, SWATCH_SIZE - 2)
    if selected:
        painter.setPen(QColor("#FFFFFF"))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        font = painter.font()
        font.setBold(True)
        font.setPointSize(12)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "✓")
    painter.end()
    return pixmap


class PinboardIconEditor(QDialog):
    """Returns via .selected_emoji / .selected_color after exec() == Accepted
    (exactly one of the two is set, matching the original's IconMode toggle)."""

    def __init__(self, board: Pinboard, parent=None, creating: bool = False):
        super().__init__(parent)
        self.setWindowTitle(tr("Nuevo pinboard") if creating else tr("Editar {name}", name=board.name))
        self.setFixedWidth(300)
        self.setStyleSheet(styles.qss())

        self.selected_emoji: str | None = board.emoji
        self.selected_color: str = board.color_hex

        root = QVBoxLayout(self)
        root.setSpacing(14)

        root.addWidget(QLabel(tr("Nombre")))
        self._name = QLineEdit(board.name)
        self._name.setPlaceholderText(tr("Nombre del pinboard"))
        self._name.selectAll()
        root.addWidget(self._name)

        root.addWidget(QLabel(tr("Identidad visual")))

        toggle_row = QHBoxLayout()
        self._emoji_toggle = QPushButton(tr("Emoji"))
        self._color_toggle = QPushButton(tr("Color"))
        for btn in (self._emoji_toggle, self._color_toggle):
            btn.setCheckable(True)
        self._emoji_toggle.clicked.connect(lambda: self._switch_mode("emoji"))
        self._color_toggle.clicked.connect(lambda: self._switch_mode("color"))
        toggle_row.addWidget(self._emoji_toggle)
        toggle_row.addWidget(self._color_toggle)
        root.addLayout(toggle_row)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_emoji_grid())
        self._stack.addWidget(self._build_color_grid())
        root.addWidget(self._stack)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_btn = QPushButton(tr("Cancelar"))
        cancel_btn.setObjectName("flat")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        save_btn = QPushButton(tr("Guardar"))
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save)
        buttons.addWidget(save_btn)
        root.addLayout(buttons)

        self._switch_mode("emoji" if board.emoji else "color")

    def _save(self) -> None:
        if not self._name.text().strip():
            QMessageBox.warning(self, "Nombre requerido", "Escribe un nombre para el pinboard.")
            self._name.setFocus()
            return
        self.accept()

    def _switch_mode(self, mode: str) -> None:
        self._emoji_toggle.setChecked(mode == "emoji")
        self._color_toggle.setChecked(mode == "color")
        self._stack.setCurrentIndex(0 if mode == "emoji" else 1)

    def _build_emoji_grid(self) -> QWidget:
        page = QWidget()
        grid = QGridLayout(page)
        grid.setSpacing(8)
        for index, candidate in enumerate(SUGGESTED_EMOJI):
            btn = QPushButton(candidate)
            btn.setFixedSize(SWATCH_SIZE + 8, SWATCH_SIZE + 8)
            btn.clicked.connect(lambda _checked, e=candidate: self._pick_emoji(e))
            grid.addWidget(btn, index // COLUMNS, index % COLUMNS)
        return page

    def _pick_emoji(self, emoji: str) -> None:
        self.selected_emoji = emoji

    def _build_color_grid(self) -> QWidget:
        page = QWidget()
        grid = QGridLayout(page)
        grid.setSpacing(8)
        for index, hex_color in enumerate(AVAILABLE_COLORS):
            btn = QPushButton()
            btn.setFixedSize(SWATCH_SIZE + 8, SWATCH_SIZE + 8)
            btn.setFlat(True)
            btn.setStyleSheet("border: none; background: transparent;")
            btn.setIcon(QPixmap.fromImage(_swatch_pixmap(hex_color, hex_color == self.selected_color).toImage()))
            btn.setIconSize(_swatch_pixmap(hex_color, False).size())
            btn.clicked.connect(lambda _checked, c=hex_color: self._pick_color(c))
            grid.addWidget(btn, index // COLUMNS, index % COLUMNS)
        return page

    def _pick_color(self, hex_color: str) -> None:
        self.selected_color = hex_color
        self._stack.removeWidget(self._stack.widget(1))
        new_page = self._build_color_grid()
        self._stack.insertWidget(1, new_page)
        self._stack.setCurrentIndex(1)

    def result_kwargs(self) -> dict:
        """What to pass to StorageService.update_pinboard: emoji mode clears
        back to None if nothing was picked, color mode always has a value
        since a swatch is always selected."""
        if self._emoji_toggle.isChecked():
            return {"name": self._name.text().strip(), "emoji": self.selected_emoji, "color_hex": self.selected_color}
        return {"name": self._name.text().strip(), "emoji": None, "color_hex": self.selected_color}
