"""Shared fixtures. StorageService against ':memory:' gives tests a real
schema/dedup/search engine with none of the filesystem persistence -- far
more trustworthy than hand-rolling a fake storage layer would be, since the
whole point of these tests is to catch real interactions with SQLite
(UNIQUE constraint, foreign keys, retention query) breaking.

FakePasteService replaces PasteService: the real one shells out to
wl-copy/ydotool, which would mutate the actual system clipboard and require
a live Wayland session + ydotoold -- wrong for a unit test. It just records
what was asked of it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

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
    svc = StorageService(db_path=Path(":memory:"))
    yield svc
    svc.close()


@pytest.fixture
def paste_service():
    return FakePasteService()
