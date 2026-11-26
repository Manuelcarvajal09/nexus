"""Live theme chooser with palette previews; no clipboard content involved."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QFont
from PySide6.QtWidgets import QDialog, QVBoxLayout, QGridLayout, QLabel, QPushButton, QCheckBox, QPlainTextEdit, QScrollArea, QWidget
from nexus.ui import styles


class ThemePreview(QPushButton):
    def __init__(self, key, apply, parent=None):
        super().__init__(parent)
        self.key = key
        self.setMinimumWidth(220)
        self.setStyleSheet("min-height: 100px; max-height: 100px; padding: 0; border: none;")
        self.setAccessibleName(styles.PRESETS[key].display_name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(lambda: apply(key))

    def paintEvent(self, event):
        t = styles.PRESETS[self.key]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(t.bg))
        p.setPen(QColor(t.accent if styles.active_theme().key == self.key else t.text_secondary))
        p.drawRoundedRect(self.rect().adjusted(3, 3, -3, -3), 12, 12)
        font = QFont(); font.setPixelSize(13); font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font); p.setPen(QColor(t.text_primary))
        p.drawText(self.rect().adjusted(16, 12, -16, -12), Qt.AlignmentFlag.AlignTop, t.display_name)
        for i, colour in enumerate((t.card_bg, t.card_bg_hover, t.accent, t.secondary, t.text_primary)):
            p.setBrush(QColor(colour)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(16 + i * 37,  50, 29, 29, 7, 7)
        p.end()


class AppearanceDialog(QDialog):
    def __init__(self, apply, parent=None, capture_changed=None):
        super().__init__(parent)
        self.setWindowTitle("Apariencia · Nexus")
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        columns = 3 if screen and screen.availableGeometry().width() >= 900 else 2
        self.resize(740 if columns == 3 else 510, 650)
        layout = QVBoxLayout(self)
        label = QLabel("Elige una paleta. Los cambios se guardan y se aplican al instante.")
        label.setWordWrap(True); layout.addWidget(label)
        area = QScrollArea(); area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setMinimumHeight(224); area.setMaximumHeight(336)
        palettes = QWidget(); grid = QGridLayout(palettes)
        area.setWidget(palettes); layout.addWidget(area)
        self.previews = []
        def choose(key):
            apply(key)
            for preview in self.previews:
                preview.update()
        for i, (key, _) in enumerate(styles.theme_choices()):
            preview = ThemePreview(key, choose, self)
            self.previews.append(preview); grid.addWidget(preview, i // columns, i % columns)
        note = QLabel("Automático usa la paleta local de Caelestia; sin ella usa Material 3.\nLiquid Glass adapta la paleta original sin depender de desenfoque.")
        note.setWordWrap(True); note.setObjectName("productMeta"); layout.addWidget(note)
        from nexus.platform.linux.settings import load_settings, save_capture_preferences, capture_is_validated
        from nexus.core import paths
        settings = load_settings()
        capture = QCheckBox("Capturar el portapapeles")
        capture.setChecked(settings.capture_enabled)
        capture.setEnabled(capture_is_validated())
        capture.setToolTip("Requiere validar Wayland y el destino de producción antes de habilitarse")
        layout.addWidget(capture)
        exclusion_label = QLabel("Excluir textos que contengan (uno por línea):")
        layout.addWidget(exclusion_label)
        exclusions = QPlainTextEdit("\n".join(settings.excluded_patterns))
        exclusions.setMaximumHeight(72)
        exclusions.setAccessibleName("Textos excluidos de captura")
        layout.addWidget(exclusions)
        def save_capture():
            save_capture_preferences(capture.isChecked(), [s.strip() for s in exclusions.toPlainText().splitlines() if s.strip()])
            if capture_changed:
                capture_changed(load_settings().capture_enabled)
        capture.toggled.connect(lambda _: save_capture())
        self.finished.connect(lambda _: save_capture())
        close = QPushButton("Listo"); close.clicked.connect(self.accept); layout.addWidget(close)
