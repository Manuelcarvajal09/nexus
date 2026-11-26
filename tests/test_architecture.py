"""Layering of src/nexus: core <- ui <- platform.

core must not import ui or platform; ui must not import platform. The few
existing exceptions are listed here on purpose (each is a follow-up, see
ROADMAP_1.0.md); anything new fails this test.
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "nexus"

KNOWN_EXCEPTIONS = {
    ("core/storage.py", "nexus.platform.linux.settings"),          # Linux retention opt-in
    ("ui/design.py", "nexus.platform.windows"),                    # Windows light/dark preference
    ("ui/icons.py", "nexus.platform.windows"),                     # sandbox taskbar identity
    ("ui/stack_base.py", "nexus.platform.windows.win32"),          # native window drag
    ("ui/timeline.py", "nexus.platform.windows.backdrop"),         # Acrylic host
    ("ui/timeline.py", "nexus.platform.windows.win32"),            # return focus to the previous app
}


def _imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)


def test_layers_only_depend_downwards():
    forbidden = {"core": ("nexus.ui", "nexus.platform"), "ui": ("nexus.platform",)}
    violations = set()
    for layer, prefixes in forbidden.items():
        for path in (SRC / layer).rglob("*.py"):
            rel = path.relative_to(SRC).as_posix()
            for module in _imports(path):
                if module.startswith(prefixes):
                    violations.add((rel, module))
    assert violations == KNOWN_EXCEPTIONS, {"new": violations - KNOWN_EXCEPTIONS, "fixed": KNOWN_EXCEPTIONS - violations}


def test_platforms_do_not_import_each_other():
    for this, other in (("windows", "linux"), ("linux", "windows")):
        for path in (SRC / "platform" / this).rglob("*.py"):
            bad = [m for m in _imports(path) if m.startswith(f"nexus.platform.{other}")]
            allowed = this == "linux" and path.name in ("shared.py", "stack.py")   # reuse the Windows-born shared UI pieces
            assert not bad or allowed, (path.name, bad)


def test_the_windows_exe_does_not_exclude_a_qt_module_its_code_imports():
    """Regression: ui/row_widget pulled QtSvg while the PyInstaller spec excluded it,
    so Nexus.exe crashed at start (caught only by launching the built exe)."""
    import re
    spec = (SRC.parents[1] / "packaging" / "windows" / "nexus.spec").read_text(encoding="utf-8")
    excluded = set(re.findall(r'"PySide6\.(Qt\w+)"', spec))
    shipped = set()
    for layer in ("core", "ui", "platform/windows"):
        for path in (SRC / layer).rglob("*.py"):
            shipped |= {m.split(".")[1] for m in _imports(path) if m.startswith("PySide6.Qt")}
    assert not shipped & excluded, shipped & excluded
