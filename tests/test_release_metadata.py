"""The version lives in two places (pyproject, package); an RC must not ship them disagreeing."""
import re
from pathlib import Path

import nexus_linux

ROOT = Path(__file__).resolve().parents[1]


def _pyproject():
    return (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_package_and_pyproject_versions_agree():
    declared = re.search(r'^version = "([^"]+)"', _pyproject(), re.M).group(1)
    assert declared == nexus_linux.__version__


def test_version_is_a_valid_pep440_release_candidate():
    assert re.fullmatch(r"\d+\.\d+\.\d+(rc\d+)?", nexus_linux.__version__)


def test_only_the_qt_modules_that_are_shipped_are_imported():
    """Dependency is PySide6-Essentials: no source file may import a module that wheel lacks."""
    allowed = {"QtCore", "QtGui", "QtWidgets", "QtDBus", "QtTest"}
    imported = set()
    for path in (ROOT / "src").rglob("*.py"):
        imported |= set(re.findall(r"PySide6\.(Qt\w+)", path.read_text(encoding="utf-8")))
    assert imported <= allowed, imported - allowed


def test_runtime_dependency_is_the_essentials_wheel():
    assert '"PySide6-Essentials>=' in _pyproject()
