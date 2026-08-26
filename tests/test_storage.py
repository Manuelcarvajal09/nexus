"""Regression suite for the SQLite persistence layer: dedup-by-hash,
retention, pinboard membership, and accent-insensitive search."""

import time
from pathlib import Path

from nexus_linux.storage import StorageService, normalize_for_search


class TestCaptureAndDedup:
    def test_capture_creates_a_row(self, storage):
        item = storage.capture(kind="text", text_content="hello")
        assert item.id is not None
        assert item.kind == "text"

    def test_recapturing_identical_content_updates_same_row_not_a_new_one(self, storage):
        first = storage.capture(kind="text", text_content="same")
        time.sleep(0.01)
        second = storage.capture(kind="text", text_content="same", source_app="Konsole")

        assert first.id == second.id
        assert second.source_app == "Konsole"
        assert second.updated_at >= first.updated_at
        assert len(storage.recent(limit=10)) == 1

    def test_different_kinds_of_identical_text_are_distinct_rows(self, storage):
        text_item = storage.capture(kind="text", text_content="https://example.com")
        url_item = storage.capture(kind="url", text_content="https://example.com")
        assert text_item.id != url_item.id

    def test_empty_history_after_deleting_the_only_item(self, storage):
        item = storage.capture(kind="text", text_content="x")
        storage.delete(item.id)
        assert storage.get(item.id) is None
        assert storage.recent(limit=10) == []


class TestRetention:
    def test_purge_removes_only_items_older_than_cutoff(self, storage):
        old = storage.capture(kind="text", text_content="old")
        storage._conn.execute(
            "UPDATE clip_items SET updated_at = ? WHERE id = ?",
            (time.time() - 40 * 86400, old.id),
        )
        storage._conn.commit()
        recent = storage.capture(kind="text", text_content="recent")

        purged = storage.purge_older_than(30)
        assert purged == 1
        assert storage.get(old.id) is None
        assert storage.get(recent.id) is not None

    def test_purge_never_removes_pinned_items_even_if_old(self, storage):
        old = storage.capture(kind="text", text_content="old-but-pinned")
        storage._conn.execute(
            "UPDATE clip_items SET updated_at = ? WHERE id = ?",
            (time.time() - 40 * 86400, old.id),
        )
        storage._conn.commit()
        board = storage.create_pinboard("Keepers")
        storage.add_to_pinboard(old.id, board.id)

        purged = storage.purge_older_than(30)
        assert purged == 0
        assert storage.get(old.id) is not None


class TestPinboards:
    def test_item_can_belong_to_multiple_pinboards(self, storage):
        item = storage.capture(kind="text", text_content="shared")
        b1 = storage.create_pinboard("Work")
        b2 = storage.create_pinboard("Ideas")
        storage.add_to_pinboard(item.id, b1.id)
        storage.add_to_pinboard(item.id, b2.id)

        refreshed = storage.get(item.id)
        assert sorted(refreshed.pinboard_ids) == sorted([b1.id, b2.id])
        assert refreshed.is_pinned

    def test_deleting_a_pinboard_does_not_delete_its_items(self, storage):
        item = storage.capture(kind="text", text_content="survives")
        board = storage.create_pinboard("Temp")
        storage.add_to_pinboard(item.id, board.id)
        storage.delete_pinboard(board.id)
        assert storage.get(item.id) is not None
        assert storage.get(item.id).pinboard_ids == []

    def test_recent_filtered_by_pinboard_id(self, storage):
        in_board = storage.capture(kind="text", text_content="in")
        outside = storage.capture(kind="text", text_content="out")
        board = storage.create_pinboard("Filter")
        storage.add_to_pinboard(in_board.id, board.id)

        results = storage.recent(limit=10, pinboard_id=board.id)
        assert [r.id for r in results] == [in_board.id]
        assert outside.id not in [r.id for r in results]


class TestSearch:
    def test_normalize_for_search_strips_accents_and_lowercases(self):
        assert normalize_for_search("Reunión") == "reunion"
        assert normalize_for_search("CAFÉ") == "cafe"

    def test_search_finds_accented_content_via_unaccented_query(self, storage):
        storage.capture(kind="text", text_content="Agenda de la reunión")
        results = storage.recent(limit=10, query="reunion")
        assert len(results) == 1

    def test_search_is_case_insensitive(self, storage):
        storage.capture(kind="text", text_content="Hello World")
        assert len(storage.recent(limit=10, query="hello")) == 1
        assert len(storage.recent(limit=10, query="WORLD")) == 1

    def test_search_with_no_matches_returns_empty(self, storage):
        storage.capture(kind="text", text_content="something")
        assert storage.recent(limit=10, query="nonexistent-xyz") == []


class TestConcurrencySettings:
    def test_wal_mode_and_busy_timeout_are_enabled(self, storage):
        mode = storage._conn.execute("PRAGMA journal_mode").fetchone()[0]
        timeout = storage._conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert mode.lower() == "wal" or mode.lower() == "memory"  # :memory: DBs can't use WAL
        assert timeout == 5000


class TestEdgeCases:
    def test_unicode_and_emoji_round_trip(self, storage):
        payload = "日本語 emoji 🎉 ñ é ü"
        item = storage.capture(kind="text", text_content=payload)
        assert storage.get(item.id).text_content == payload

    def test_empty_text_content_is_still_capturable(self, storage):
        item = storage.capture(kind="text", text_content="")
        assert storage.get(item.id) is not None

    def test_get_nonexistent_id_returns_none(self, storage):
        assert storage.get(999_999) is None

    def test_delete_nonexistent_id_does_not_raise(self, storage):
        storage.delete(999_999)  # must not raise
