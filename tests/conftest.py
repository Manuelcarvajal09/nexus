"""Shared fixtures. StorageService against ':memory:' gives tests a real
schema/dedup/search engine with none of the filesystem persistence -- far
more trustworthy than hand-rolling a fake storage layer would be, since the
whole point of these tests is to catch real interactions with SQLite
(UNIQUE constraint, foreign keys, retention query) breaking.

FakePasteService replaces PasteService: the real one shells out to
wl-copy/ydotool, which would mutate the actual system clipboard and require
a live Wayland session + ydotoold -- wrong for a unit test. It just records
what was asked of it."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Must be set before any QApplication/QWidget is constructed anywhere in the
# test session (a handful of tests instantiate real QWidget subclasses, e.g.
# test_focus_restoration.py, to pin down call ORDER -- not visuals). Offscreen
# needs no real display/compositor, so this is safe in CI and on a headless
# runner alike.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# Even a plain pytest invocation must never touch the user profile.
_test_profile = tempfile.TemporaryDirectory(prefix="nexus-tests-")
os.environ.setdefault("NEXUS_PROFILE_ROOT", _test_profile.name)

from nexus_linux.storage import StorageService


class FakePasteService:
    def __init__(self, fail_write: bool = False, fail_paste: bool = False):
        self.written: list = []
        self.paste_count = 0
        self.fail_write = fail_write
        self.fail_paste = fail_paste

    def write_to_clipboard(self, item) -> bool:
        if self.fail_write:
            return False
        self.written.append(item)
        return True

    def synthetic_paste(self) -> bool:
        self.paste_count += 1
        return not self.fail_paste


@pytest.fixture
def storage():
    svc = StorageService(db_path=Path(":memory:"), allow_retention=True)
    yield svc
    svc.close()


@pytest.fixture
def file_storage(tmp_path):
    """A real file-backed database, not ':memory:' -- needed by anything
    that opens a SECOND connection to the same data from another thread
    (preview_service.py's background fetch tasks). Separate connections to
    ':memory:' are each their own independent, unreachable database (that's
    what ':memory:' means), so this fixture exists specifically for tests
    that need real cross-connection/cross-thread sharing; everything else
    should keep using the faster, fully-isolated `storage` fixture above."""
    svc = StorageService(db_path=tmp_path / "test.sqlite", allow_retention=True)
    yield svc
    svc.close()


@pytest.fixture
def paste_service():
    return FakePasteService()


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication (offscreen, see QT_QPA_PLATFORM above)
    for the small number of tests that need a real QWidget to pin down
    call order (e.g. write-before-hide-before-synthesize). Session-scoped
    because QApplication is a process-wide singleton -- Qt does not support
    tearing one down and rebuilding another within the same process."""
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture
def make_paste_service():
    """Factory for tests that need a FakePasteService configured to fail
    (fail_write/fail_paste), rather than the default-happy one above.
    Exposed as a fixture instead of `from tests.conftest import
    FakePasteService` inside test files: that import's resolution depends on
    how pytest was invoked (rootdir, import-mode, cwd) and is not reliable
    across environments -- a fixture always works."""
    return FakePasteService


@pytest.fixture(autouse=True)
def dispose_test_surfaces(request):
    """Flush Qt deletion in the owning test, rather than a later event loop.

    Parentless widgets otherwise survive through Python signal cycles and are
    finalized during an unrelated QTest.qWait, after their storage fixture closed.
    """
    if 'qapp' not in request.fixturenames:
        yield
        return
    app = request.getfixturevalue('qapp')
    before = set(app.topLevelWidgets())
    yield
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid
    windows = [w for w in app.topLevelWidgets() if w not in before and isValid(w)]
    for window in windows:
        window.hide()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
