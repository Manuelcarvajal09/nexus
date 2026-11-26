"""English catalog completeness and the language plumbing."""
import ast
import os
import re
import string
import subprocess
import sys
from pathlib import Path

import pytest

from nexus_linux import i18n
from nexus_linux.i18n_en import CATALOG

SRC = Path(__file__).resolve().parents[1] / "src" / "nexus"


def _keys():
    keys = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) in ("tr", "plural"):
                name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                for arg in (node.args[:1] if name == "tr" else node.args[1:3]):
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        keys.add(arg.value)
    return keys


def _fields(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_interface_text_has_an_english_translation():
    missing = sorted(_keys() - set(CATALOG))
    assert not missing, missing


@pytest.mark.parametrize("source", sorted(CATALOG))
def test_translations_keep_their_placeholders(source):
    assert _fields(source) == _fields(CATALOG[source]), (source, CATALOG[source])


def test_english_catalog_has_no_spanish_left():
    spanish = re.compile(r"[áéíóúñ¿¡]|\b(el|los|las|del|para|con|una|pulsa|pegar)\b")   # "Del" (the key) is English
    assert not [v for v in CATALOG.values() if spanish.search(v)]


def test_tr_and_plural_follow_the_active_language():
    try:
        i18n.set_language("en")
        assert i18n.tr("Pegar") == "Paste"
        assert i18n.tr("Sin resultados para «{query}».", query="x") == "No results for “x”."
        assert i18n.plural(1, "{n} elemento", "{n} elementos") == "1 item"
        assert i18n.plural(3, "{n} elemento", "{n} elementos") == "3 items"
        i18n.set_language("es")
        assert i18n.tr("Pegar") == "Pegar" and i18n.plural(2, "{n} elemento", "{n} elementos") == "2 elementos"
    finally:
        i18n.set_language("es")


def test_language_resolution(monkeypatch):
    assert i18n.resolve("es") == "es" and i18n.resolve("en") == "en"
    monkeypatch.setattr(i18n, "_system_language", lambda: "en")
    assert i18n.resolve("auto") == "en" and i18n.resolve(None) == "en" and i18n.resolve("fr") == "en"


def test_the_whole_interface_builds_in_english(tmp_path):
    """Import every UI module and build the main windows with NEXUS_LANGUAGE=en."""
    code = (
        "import os, sys; sys.path.insert(0, r'%s');"
        "from PySide6.QtWidgets import QApplication; app = QApplication([]);"
        "from nexus_linux import i18n, windows_settings_page, windows_shortcuts;"
        "from nexus_linux.storage import StorageService;"
        "from nexus_linux.windows_paste import WindowsPasteService;"
        "from nexus_linux.windows_timeline import WindowsTimelineWindow;"
        "from nexus_linux.windows_stack import WindowsClipStack, WindowsStackWindow;"
        "assert i18n.language() == 'en';"
        "s = StorageService(r'%s'); w = WindowsTimelineWindow(s, WindowsPasteService());"
        "st = WindowsStackWindow(WindowsClipStack(s, WindowsPasteService()));"
        "print(w._search.placeholderText(), '|', st._order_btn.text(), '|', windows_shortcuts.SPECS[0].label)"
    ) % (SRC.parent, tmp_path / "en.sqlite")
    env = {**os.environ, "NEXUS_LANGUAGE": "en", "QT_QPA_PLATFORM": "offscreen", "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "Search your history… | In order | Show or hide Nexus"
