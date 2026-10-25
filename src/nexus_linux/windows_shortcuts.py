"""Catalogue, validation and persistence of Nexus keyboard shortcuts (Windows).

Two scopes exist and are never mixed up:

* GLOBAL  -- registered with RegisterHotKey, work from any application.
* LOCAL   -- only while a Nexus window has the focus.

Some keys are fixed on purpose (Enter, Esc, Tab, Del, and the system Ctrl+V used
by Stack): they are handled by Qt widgets and by the paste engine, and changing
them could break typing in search, pasting or Stack. They are listed, with the
reason, but cannot be edited.

The model is pure data + Qt key-text parsing; it never touches the OS. Native
registration lives in windows_app.GlobalHotkeys.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QKeySequence

GLOBAL, LOCAL = "global", "local"
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8
_MODIFIERS = {"Ctrl": MOD_CONTROL, "Alt": MOD_ALT, "Shift": MOD_SHIFT, "Meta": MOD_WIN}


@dataclass(frozen=True)
class Spec:
    id: str
    label: str
    description: str
    scope: str
    default: str
    editable: bool = True
    reason: str = ""
    aliases: tuple = ()  # extra key texts the same action answers to (Enter/Return...)


SPECS = (
    Spec("panel", "Mostrar u ocultar Nexus", "Abre o cierra el historial desde cualquier aplicación.", GLOBAL, "Ctrl+Shift+V"),
    Spec("stack", "Activar Stack", "Muestra Stack y empieza a recoger lo que copies, en orden.", GLOBAL, "Ctrl+Shift+C"),
    Spec("search", "Buscar", "Lleva el cursor al campo de búsqueda.", LOCAL, "Ctrl+F"),
    Spec("rename", "Renombrar", "Edita el título del elemento seleccionado.", LOCAL, "F2"),
    Spec("quick_paste", "Pegado rápido 1–9", "Pega directamente el elemento número 1 a 9 de la vista actual.", LOCAL, "Ctrl+1"),
    Spec("board_prev", "Pinboard anterior", "Cambia al Pinboard de la izquierda.", LOCAL, "Ctrl+Left"),
    Spec("board_next", "Pinboard siguiente", "Cambia al Pinboard de la derecha.", LOCAL, "Ctrl+Right"),
    Spec("paste", "Pegar", "Pega el elemento seleccionado.", LOCAL, "Enter", False,
         "Es la tecla de activación de la lista; cambiarla impediría pegar con el teclado.", ("Return",)),
    Spec("paste_plain", "Pegar como texto plano", "Pega el elemento sin formato.", LOCAL, "Shift+Enter", False,
         "Va ligada a Enter.", ("Shift+Return",)),
    Spec("close", "Cerrar o volver", "Cierra el panel, o vuelve al historial desde Pinboards y Configuración.", LOCAL, "Esc", False,
         "Escape es la salida segura de cualquier vista y de la edición."),
    Spec("focus_toggle", "Alternar búsqueda y resultados", "Mueve el foco entre el campo de búsqueda y las tarjetas.", LOCAL, "Tab", False,
         "Tab pertenece a la navegación de Windows.", ("Backtab",)),
    Spec("stack_remove", "Quitar del Stack", "Quita del Stack el elemento seleccionado.", LOCAL, "Del", False,
         "Se comparte con la edición de texto.", ("Backspace",)),
    Spec("stack_paste", "Pegar el siguiente del Stack", "Con Stack activo, el pegado normal consume el siguiente elemento.", GLOBAL, "Ctrl+V", False,
         "Es el pegado del propio sistema; Nexus lo observa, no lo reemplaza."),
)
_BY_ID = {s.id: s for s in SPECS}
_GLOBAL_SETTING_KEY = {"panel": "panel_hotkey", "stack": "stack_hotkey"}

# Combinations Windows or its shell own (written in Qt's canonical spelling).
RESERVED_GLOBAL = {"Alt+F4", "Alt+Tab", "Alt+Esc", "Alt+Space", "Ctrl+Esc", "Ctrl+Shift+Esc", "Ctrl+Alt+Del"}
# Editing/navigation keys the panel's own widgets depend on.
RESERVED_LOCAL = {"Return", "Enter", "Esc", "Tab", "Backtab", "Backspace", "Del", "Shift+Return", "Shift+Enter",
                  "Ctrl+A", "Ctrl+C", "Ctrl+V", "Ctrl+X", "Ctrl+Z", "Ctrl+Y"}

_VK_NAMED = {
    "Space": 0x20, "PgUp": 0x21, "PgDown": 0x22, "End": 0x23, "Home": 0x24, "Left": 0x25, "Up": 0x26,
    "Right": 0x27, "Down": 0x28, "Ins": 0x2D, "Del": 0x2E,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0,
    "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}


@dataclass(frozen=True)
class Result:
    ok: bool
    message: str = ""
    value: str = ""


def normalize(text) -> str | None:
    """Canonical Qt spelling of a single key combination, or None."""
    if not text or not str(text).strip():
        return None
    sequence = QKeySequence(str(text).strip(), QKeySequence.SequenceFormat.PortableText)
    if sequence.count() != 1:
        return None
    return sequence.toString(QKeySequence.SequenceFormat.PortableText) or None


def _same(a: str) -> str:
    """Comparison form: Enter and Return are one key."""
    return a.replace("Enter", "Return")


def _split(canonical: str):
    parts = canonical.split("+")
    if canonical.endswith("++"):
        parts = canonical[:-2].split("+") + ["+"]
    return parts[:-1], parts[-1]


def expand_quick_paste(text: str) -> list[str]:
    prefix = text[:-1]
    return [f"{prefix}{n}" for n in range(1, 10)]


def parse_global(text) -> tuple[int, int]:
    """(modifier flags, virtual key) for RegisterHotKey, or ValueError (Spanish)."""
    if text and QKeySequence(str(text), QKeySequence.SequenceFormat.PortableText).count() > 1:
        raise ValueError("Usa una sola combinación de teclas.")
    canonical = normalize(text)
    if canonical is None:
        raise ValueError("Pulsa una combinación de teclas.")
    mods, key = _split(canonical)
    if "Meta" in mods:
        raise ValueError("La tecla Windows está reservada para el sistema.")
    flags = 0
    for name in mods:
        flags |= _MODIFIERS[name]
    if not flags & (MOD_CONTROL | MOD_ALT):
        raise ValueError("Un atajo global necesita Ctrl o Alt (y puede llevar Mayús).")
    if len(key) == 1 and key.isalnum() and key.isascii():
        return flags, ord(key.upper())
    if key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return flags, 0x70 + int(key[1:]) - 1
    if key in _VK_NAMED:
        return flags, _VK_NAMED[key]
    raise ValueError(f"Windows no puede registrar la tecla «{key}» como atajo global.")


class ShortcutStore:
    """Effective shortcuts, backed by (and mutating) the settings dict."""

    def __init__(self, settings: dict, default_overrides: dict | None = None):
        self._settings = settings
        self._defaults = {s.id: s.default for s in SPECS}
        self._defaults.update(default_overrides or {})

    # -- reading ----------------------------------------------------------
    @staticmethod
    def spec(identifier: str) -> Spec:
        return _BY_ID[identifier]

    def default(self, identifier: str) -> str:
        return self._defaults[identifier]

    def get(self, identifier: str) -> str:
        spec = _BY_ID[identifier]
        if not spec.editable:
            return spec.default
        if spec.scope == GLOBAL:
            return normalize(self._settings.get(_GLOBAL_SETTING_KEY[identifier])) or self._defaults[identifier]
        return normalize((self._settings.get("shortcuts") or {}).get(identifier)) or self._defaults[identifier]

    def is_default(self, identifier: str) -> bool:
        return self.get(identifier) == (normalize(self._defaults[identifier]) or self._defaults[identifier])

    def keys_for(self, identifier: str, value: str | None = None) -> list[str]:
        """Every key text an action answers to (quick paste: nine, Enter: two...)."""
        spec = _BY_ID[identifier]
        value = value or self.get(identifier)
        if identifier == "quick_paste":
            return expand_quick_paste(value)
        return [value, *spec.aliases]

    def local_keys(self) -> dict:
        return {s.id: self.get(s.id) for s in SPECS if s.editable and s.scope == LOCAL}

    # -- validation -------------------------------------------------------
    def validate(self, identifier: str, text) -> Result:
        spec = _BY_ID[identifier]
        if not spec.editable:
            return Result(False, f"«{spec.label}» no se puede cambiar. {spec.reason}")
        if text and QKeySequence(str(text), QKeySequence.SequenceFormat.PortableText).count() > 1:
            return Result(False, "Usa una sola combinación de teclas.")
        value = normalize(text)
        if value is None:
            return Result(False, "Pulsa una combinación de teclas.")
        mods, key = _split(value)
        if "Meta" in mods:
            return Result(False, "La tecla Windows está reservada para el sistema.")
        if spec.scope == GLOBAL:
            if value in RESERVED_GLOBAL:
                return Result(False, f"{value} está reservada por Windows.")
            try:
                parse_global(value)
            except ValueError as error:
                return Result(False, str(error))
        else:
            if _same(value) in {_same(r) for r in RESERVED_LOCAL}:
                return Result(False, f"{value} está reservada: la usan la búsqueda, el pegado o la edición.")
            is_function_key = key.startswith("F") and key[1:].isdigit()
            if not mods and not is_function_key:
                return Result(False, "Añade un modificador (Ctrl o Alt): una tecla sola escribiría en la búsqueda.")
            if identifier == "quick_paste":
                if key != "1":
                    return Result(False, "El pegado rápido debe terminar en la tecla 1 (p. ej. Ctrl+1); el resto se deduce.")
                if not {"Ctrl", "Alt"} & set(mods):
                    return Result(False, "El pegado rápido necesita Ctrl o Alt con el número.")
        candidate = {_same(k) for k in self.keys_for(identifier, value)}
        for other in SPECS:
            if other.id == identifier:
                continue
            taken = {_same(normalize(k) or k) for k in self.keys_for(other.id)}
            if candidate & taken:
                return Result(False, f"{value} ya la usa «{other.label}».")
        return Result(True, value=value)

    # -- writing ----------------------------------------------------------
    def set(self, identifier: str, text) -> Result:
        result = self.validate(identifier, text)
        if not result.ok:
            return result
        self._write(identifier, result.value)
        return result

    def _write(self, identifier: str, value: str) -> None:
        spec = _BY_ID[identifier]
        if spec.scope == GLOBAL:
            self._settings[_GLOBAL_SETTING_KEY[identifier]] = value
            return
        overrides = self._settings.setdefault("shortcuts", {})
        if value == (normalize(self._defaults[identifier]) or self._defaults[identifier]):
            overrides.pop(identifier, None)
        else:
            overrides[identifier] = value

    def reset(self, identifier: str) -> None:
        if _BY_ID[identifier].editable:
            self._write(identifier, normalize(self._defaults[identifier]) or self._defaults[identifier])

    def reset_all(self) -> None:
        for spec in SPECS:
            self.reset(spec.id)

    def sanitize(self) -> list[str]:
        """Repair hand-edited or stale settings; returns what was reset."""
        notices = []
        stored = self._settings.get("shortcuts")
        if not isinstance(stored, dict):
            self._settings["shortcuts"] = stored = {}
        for identifier in [k for k in stored if k not in _BY_ID or not _BY_ID[k].editable or _BY_ID[k].scope != LOCAL]:
            stored.pop(identifier, None)
        for spec in SPECS:
            if not spec.editable:
                continue
            raw = (self._settings.get(_GLOBAL_SETTING_KEY[spec.id]) if spec.scope == GLOBAL
                   else stored.get(spec.id))
            if raw is None:
                continue
            # Validate against the others as they stand now, ignoring this entry.
            self.reset(spec.id)
            result = self.validate(spec.id, raw)
            if result.ok:
                self._write(spec.id, result.value)
            else:
                notices.append(f"«{spec.label}»: {result.message} Se restableció a {self._defaults[spec.id]}.")
        return notices
