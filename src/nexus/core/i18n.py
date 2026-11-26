"""Interface language: Spanish source strings, English catalog.

Source code keeps the Spanish text and wraps it in tr(); the English text lives
in i18n_en.CATALOG keyed by that Spanish text. The language is fixed for the
life of the process (module-level labels are built at import time), so it is
resolved once, before any UI module is imported:

1. NEXUS_LANGUAGE environment variable ("es" / "en"), used by tests and tools;
2. "language" in settings.json ("auto", "es", "en");
3. "auto": Spanish if the Windows display language is Spanish, else English.

Changing the language in Settings is saved and applies on the next start.
"""
from __future__ import annotations

import json
import os

LANGUAGES = {"es": "Español", "en": "English"}
DEFAULT = "en"


def _system_language() -> str:
    try:
        from PySide6.QtCore import QLocale
        return "es" if QLocale.system().name().lower().startswith("es") else "en"
    except Exception:  # noqa: BLE001 - never fail startup over the locale
        return DEFAULT


def _setting() -> str:
    try:
        from nexus.core import paths
        data = json.loads((paths.CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))
        return str(data.get("language", "auto"))
    except Exception:  # noqa: BLE001 - missing/unreadable settings: automatic
        return "auto"


def resolve(choice: str | None = None) -> str:
    choice = (choice or "auto").lower()
    return choice if choice in LANGUAGES else _system_language()


def _initial() -> str:
    forced = os.environ.get("NEXUS_LANGUAGE", "").lower()
    return forced if forced in LANGUAGES else resolve(_setting())


_language = _initial()


def language() -> str:
    return _language


def set_language(code: str) -> None:
    """For tests and tools only: UI built before the change keeps its texts."""
    global _language
    _language = code if code in LANGUAGES else DEFAULT


def tr(text: str, /, **values) -> str:
    """The interface text in the active language; {name} placeholders are filled from values."""
    if _language == "en":
        from nexus.core.i18n_en import CATALOG
        text = CATALOG.get(text, text)
    return text.format(**values) if values else text


def plural(count: int, one: str, many: str, /) -> str:
    """tr() of the singular or plural form, with {n} replaced by count."""
    return tr(one if count == 1 else many, n=count)
