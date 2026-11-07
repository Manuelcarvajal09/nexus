"""Compact "Configuración" page that lives inside the Nexus panel (no extra window).

Sections: keyboard shortcuts (consult, capture a new combination, restore one or
all), clipboard capture (pause/resume) and appearance (theme). The page only
talks to a backend object (windows_app.SettingsBackend), so it is testable
without any native call.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QHBoxLayout, QKeySequenceEdit, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from . import windows_design
from .windows_shortcuts import GLOBAL, SPECS, normalize

_RESET_WIDTH = 92
_DISPLAY = {"Del": "Supr / Retroceso", "Esc": "Esc", "Enter": "Enter", "Shift+Enter": "Mayús+Enter"}


def display_key(text: str) -> str:
    return _DISPLAY.get(text, text)


class _KeyEdit(QKeySequenceEdit):
    """Captures one key combination. While it has focus Nexus' own shortcuts are
    suspended (so pressing an existing one is captured, not executed)."""

    capture_started = Signal()
    capture_finished = Signal()
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumSequenceLength(1)
        self.setClearButtonEnabled(False)
        self.setObjectName("keyEdit")
        self.setFixedWidth(176)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.capture_started.emit()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.capture_finished.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and not event.modifiers():
            event.accept()
            self.cancelled.emit()
            return
        super().keyPressEvent(event)


class _ShortcutRow(QWidget):
    def __init__(self, spec, page):
        super().__init__()
        self.spec, self._page = spec, page
        self.setObjectName("shortcutRow")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 4)
        root.setSpacing(2)
        line = QHBoxLayout()
        line.setSpacing(windows_design.INTERNAL_GAP)
        text = QVBoxLayout()
        text.setSpacing(1)
        self.label = QLabel(spec.label)
        self.label.setObjectName("shortcutLabel")
        self.description = QLabel(spec.description if spec.editable else f"{spec.description} {spec.reason}")
        self.description.setObjectName("shortcutHint")
        self.description.setWordWrap(True)
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        for label in (self.label, self.description):
            label.setMinimumWidth(0)
        text.addWidget(self.label)
        text.addWidget(self.description)
        line.addLayout(text, 1)
        self.scope = QLabel("Global" if spec.scope == GLOBAL else "En Nexus")
        self.scope.setObjectName("scopePill")
        self.scope.setProperty("scope", spec.scope)
        self.scope.setToolTip("Funciona desde cualquier aplicación" if spec.scope == GLOBAL
                              else "Funciona cuando Nexus tiene el foco")
        # Fixed widths keep the key column aligned across editable and fixed rows.
        self.scope.setFixedWidth(68)
        self.scope.setAlignment(Qt.AlignmentFlag.AlignCenter)
        line.addWidget(self.scope, 0, Qt.AlignmentFlag.AlignVCenter)
        self.edit = None
        self.reset_button = None
        if spec.editable:
            self.edit = _KeyEdit()
            self.edit.setAccessibleName(f"Atajo de {spec.label}")
            self.edit.editingFinished.connect(self._finished)
            self.edit.capture_started.connect(page._capture_started)
            self.edit.capture_finished.connect(page._capture_finished)
            self.edit.cancelled.connect(self._cancel)
            line.addWidget(self.edit, 0, Qt.AlignmentFlag.AlignVCenter)
            self.reset_button = QPushButton("Restablecer")
            self.reset_button.setObjectName("flat")
            self.reset_button.setAccessibleName(f"Restablecer el atajo de {spec.label}")
            self.reset_button.clicked.connect(self._reset)
            self.reset_button.setFixedWidth(_RESET_WIDTH)
            line.addWidget(self.reset_button, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            self.fixed = QLabel(display_key(spec.default))
            self.fixed.setObjectName("keyPill")
            self.fixed.setToolTip(spec.reason)
            self.fixed.setFixedWidth(176)
            self.fixed.setAlignment(Qt.AlignmentFlag.AlignCenter)
            line.addWidget(self.fixed, 0, Qt.AlignmentFlag.AlignVCenter)
            spacer = QWidget()
            spacer.setFixedWidth(_RESET_WIDTH)
            line.addWidget(spacer)
        root.addLayout(line)
        self.status = QLabel("")
        self.status.setObjectName("statusOk")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.hide()
        root.addWidget(self.status)

    def refresh(self):
        if self.edit is None:
            return
        store = self._page.backend.store
        self.edit.setKeySequence(QKeySequence(store.get(self.spec.id), QKeySequence.SequenceFormat.PortableText))
        self.reset_button.setEnabled(not store.is_default(self.spec.id))
        problem = self._page.backend.status(self.spec.id)
        if problem:
            self.show_status(f"No registrado: {problem}", error=True)

    def show_status(self, text, error=False):
        self.status.setObjectName("statusError" if error else "statusOk")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _finished(self):
        text = self.edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        current = self._page.backend.store.get(self.spec.id)
        if not text or normalize(text) == current:
            self.refresh()
            return
        result = self._page.backend.change_shortcut(self.spec.id, text)
        self.refresh()
        self.show_status(f"Guardado: {result.value}" if result.ok else result.message, error=not result.ok)
        self._page._after_change()
        self.edit.clearFocus()  # leaving the field resumes every shortcut right away

    def _cancel(self):
        self.refresh()
        self.status.hide()
        self.edit.clearFocus()

    def _reset(self):
        result = self._page.backend.reset_shortcut(self.spec.id)
        self.refresh()
        self.show_status(f"Restablecido: {result.value}" if result.ok else result.message, error=not result.ok)
        self._page._after_change()


class SettingsPage(QWidget):
    theme_selected = Signal(str)
    back_requested = Signal()

    def __init__(self, backend, theme_choices, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self.backend = backend
        self._capturing = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(windows_design.BLOCK_GAP)
        self.back_button = QPushButton("Volver")
        self.back_button.setObjectName("expandToggle")
        self.back_button.clicked.connect(self.back_requested.emit)
        # "Volver" + the page's own name, so Configuración reads as a place.
        top = QHBoxLayout()
        top.setSpacing(windows_design.BLOCK_GAP)
        top.addWidget(self.back_button)
        page_title = QLabel("Configuración")
        page_title.setObjectName("settingsPageTitle")
        top.addWidget(page_title)
        top.addStretch(1)
        outer.addLayout(top)

        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setMinimumSize(0, 0)
        self.scroll = scroll
        host = QWidget()
        body = QVBoxLayout(host)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(windows_design.INTERNAL_GAP)
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)

        # -- shortcuts ----------------------------------------------------
        head = QHBoxLayout()
        title = self._heading("Atajos de teclado")
        head.addWidget(title, 1)
        self.reset_all_button = QPushButton("Restablecer todos")
        self.reset_all_button.setObjectName("flat")
        self.reset_all_button.clicked.connect(self._reset_all)
        head.addWidget(self.reset_all_button)
        body.addLayout(head)
        self.notice = QLabel("\n".join(backend.startup_notices))
        self.notice.setObjectName("statusError")
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setVisible(bool(backend.startup_notices))
        body.addWidget(self.notice)
        self.summary = QLabel("")
        self.summary.setObjectName("statusOk")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.hide()
        body.addWidget(self.summary)
        self.rows = {}
        for scope, caption in ((GLOBAL, "Globales · funcionan desde cualquier aplicación"),
                               ("local", "Con Nexus abierto")):
            body.addWidget(self._sub(caption))
            for spec in SPECS:
                if spec.scope == scope:
                    row = _ShortcutRow(spec, self)
                    self.rows[spec.id] = row
                    body.addWidget(row)

        # -- capture --------------------------------------------------------
        body.addSpacing(windows_design.INTERNAL_GAP)
        body.addWidget(self._heading("Captura del portapapeles"))
        self.capture_toggle = QPushButton()
        self.capture_toggle.setObjectName("captureToggle")
        self.capture_toggle.setCheckable(True)
        self.capture_toggle.toggled.connect(self._capture_toggled)
        body.addWidget(self.capture_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        body.addWidget(self._sub("En pausa, Nexus no guarda nada nuevo en el historial. Lo ya guardado no cambia."))

        # -- appearance -----------------------------------------------------
        body.addSpacing(windows_design.INTERNAL_GAP)
        body.addWidget(self._heading("Apariencia"))
        themes = QHBoxLayout()
        themes.setSpacing(windows_design.INTERNAL_GAP)
        self.theme_buttons = {}
        for key, label in theme_choices:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setAccessibleName(f"Estilo {label}")
            button.clicked.connect(lambda _checked, key=key: self.theme_selected.emit(key))
            themes.addWidget(button)
            self.theme_buttons[key] = button
        themes.addStretch()
        body.addLayout(themes)
        # Optical glass: lens and per-backdrop contrast on controls and cards (Liquid Glass only).
        self.optical_toggle = QPushButton()
        self.optical_toggle.setObjectName("opticalToggle")
        self.optical_toggle.setCheckable(True)
        self.optical_toggle.toggled.connect(self._optical_toggled)
        body.addWidget(self.optical_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        self.optical_hint = self._sub("Al abrir el panel, Nexus lee un instante los píxeles de detrás para dar profundidad "
                                      "al cristal y ajustar el contraste. Solo se guardan en memoria mientras el panel está abierto.")
        body.addWidget(self.optical_hint)
        body.addStretch()
        self.refresh()

    @staticmethod
    def _heading(text):
        label = QLabel(text)
        label.setObjectName("settingsHeading")
        return label

    @staticmethod
    def _sub(text):
        label = QLabel(text)
        label.setObjectName("settingsSub")
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        return label

    # -- state ------------------------------------------------------------
    def refresh(self):
        for row in self.rows.values():
            row.refresh()
        paused = self.backend.capture_paused()
        self.capture_toggle.blockSignals(True)
        self.capture_toggle.setChecked(not paused)
        self.capture_toggle.blockSignals(False)
        self._sync_capture_text()
        self._sync_optical()
        self._after_change()

    def _sync_optical(self):
        available = hasattr(self.backend, "glass_optical")
        self.optical_toggle.setVisible(available)
        self.optical_hint.setVisible(available)
        if available:
            self.optical_toggle.blockSignals(True)
            self.optical_toggle.setChecked(bool(self.backend.glass_optical()))
            self.optical_toggle.blockSignals(False)
            self.optical_toggle.setText("Cristal óptico activado" if self.optical_toggle.isChecked() else "Cristal óptico desactivado")
            self.optical_toggle.setAccessibleName(self.optical_toggle.text())

    def _optical_toggled(self, active):
        self.backend.set_glass_optical(active)
        self._sync_optical()

    def _after_change(self):
        every_default = all(self.backend.store.is_default(s.id) for s in SPECS if s.editable)
        self.reset_all_button.setEnabled(not every_default)

    def sync_theme(self, key):
        for theme_key, button in self.theme_buttons.items():
            button.setChecked(theme_key == key)

    def sync_capture(self):
        """Another surface (the tray menu) changed the pause state."""
        self.capture_toggle.blockSignals(True)
        self.capture_toggle.setChecked(not self.backend.capture_paused())
        self.capture_toggle.blockSignals(False)
        self._sync_capture_text()

    def _sync_capture_text(self):
        self.capture_toggle.setText("Captura activa" if self.capture_toggle.isChecked() else "Captura en pausa")
        self.capture_toggle.setAccessibleName(self.capture_toggle.text())

    def _capture_toggled(self, active):
        self.backend.set_capture_paused(not active)
        self._sync_capture_text()

    # -- actions ----------------------------------------------------------
    def _reset_all(self):
        problems = self.backend.reset_all_shortcuts()
        self.refresh()
        self.summary.setObjectName("statusError" if problems else "statusOk")
        self.summary.style().unpolish(self.summary)
        self.summary.style().polish(self.summary)
        self.summary.setText("\n".join(problems) if problems else "Atajos restablecidos a sus valores originales.")
        self.summary.show()

    def _capture_started(self):
        if not self._capturing:
            self._capturing = True
            self.backend.suspend_shortcuts()

    def _capture_finished(self):
        if self._capturing:
            self._capturing = False
            self.backend.resume_shortcuts()

    def hideEvent(self, event):
        self._capture_finished()
        super().hideEvent(event)
