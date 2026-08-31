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
    def test_pinboard_identity_and_order_are_fully_persistent(self, storage):
        work = storage.create_pinboard("Work", color_hex="#007AFF", emoji="💼")
        ideas = storage.create_pinboard("Ideas", color_hex="#FF9500", emoji="💡")
        archive = storage.create_pinboard("Archive", color_hex="#8E8E93")

        storage.update_pinboard(work.id, name="Client Work", color_hex="#34C759", emoji=None)
        updated = storage.get_pinboard(work.id)
        assert updated.name == "Client Work"
        assert updated.color_hex == "#34C759"
        assert updated.emoji is None

        storage.reorder_pinboards([archive.id, work.id, ideas.id])
        assert [board.id for board in storage.list_pinboards()] == [archive.id, work.id, ideas.id]

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


class TestPreviewCache:
    def test_miss_on_empty_cache(self, storage):
        assert storage.get_preview_cache("https://example.com") is None

    def test_save_then_get_round_trips(self, storage):
        storage.save_preview_cache(
            "https://example.com", domain="example.com", title="Example",
            description="A description", favicon=b"ICO", og_image=b"PNG",
        )
        cached = storage.get_preview_cache("https://example.com")
        assert cached["domain"] == "example.com"
        assert cached["title"] == "Example"
        assert cached["description"] == "A description"
        assert cached["favicon"] == b"ICO"
        assert cached["og_image"] == b"PNG"
        assert cached["status"] == "ok"

    def test_different_urls_do_not_collide(self, storage):
        storage.save_preview_cache("https://a.com", domain="a.com", title="A")
        storage.save_preview_cache("https://b.com", domain="b.com", title="B")
        assert storage.get_preview_cache("https://a.com")["title"] == "A"
        assert storage.get_preview_cache("https://b.com")["title"] == "B"

    def test_saving_again_overwrites_not_duplicates(self, storage):
        storage.save_preview_cache("https://example.com", domain="example.com", title="Old")
        storage.save_preview_cache("https://example.com", domain="example.com", title="New")
        assert storage.get_preview_cache("https://example.com")["title"] == "New"
        count = storage._conn.execute("SELECT COUNT(*) FROM url_preview_cache").fetchone()[0]
        assert count == 1

    def test_youtube_fields_round_trip(self, storage):
        storage.save_preview_cache(
            "https://youtube.com/watch?v=x", domain="youtube.com", title="A Video",
            channel="A Channel", is_youtube=True, og_image=b"THUMB",
        )
        cached = storage.get_preview_cache("https://youtube.com/watch?v=x")
        assert cached["is_youtube"] is True
        assert cached["channel"] == "A Channel"

    def test_failed_status_stored_and_retrieved(self, storage):
        storage.save_preview_cache("https://down.example.com", domain="down.example.com", status="failed")
        cached = storage.get_preview_cache("https://down.example.com")
        assert cached["status"] == "failed"

    def test_expired_ok_entry_is_a_miss(self, storage):
        storage.save_preview_cache("https://example.com", domain="example.com", title="Stale")
        # Backdate fetched_at past the OK ttl.
        storage._conn.execute(
            "UPDATE url_preview_cache SET fetched_at = ? WHERE url = ?",
            (time.time() - 8 * 86400, "https://example.com"),
        )
        storage._conn.commit()
        assert storage.get_preview_cache("https://example.com") is None

    def test_expired_failed_entry_is_a_miss_sooner_than_ok(self, storage):
        storage.save_preview_cache("https://down.example.com", domain="down.example.com", status="failed")
        storage._conn.execute(
            "UPDATE url_preview_cache SET fetched_at = ? WHERE url = ?",
            (time.time() - 7200, "https://down.example.com"),  # 2h ago, past the 1h failed TTL
        )
        storage._conn.commit()
        assert storage.get_preview_cache("https://down.example.com") is None

    def test_cache_size_is_capped(self, storage, monkeypatch):
        import nexus_linux.storage as storage_module
        monkeypatch.setattr(storage_module, "PREVIEW_CACHE_MAX_ENTRIES", 3)
        for i in range(5):
            storage.save_preview_cache(f"https://site{i}.com", domain=f"site{i}.com", title=str(i))
        count = storage._conn.execute("SELECT COUNT(*) FROM url_preview_cache").fetchone()[0]
        assert count == 3
        # The most recently saved entries survive eviction, not the oldest.
        assert storage.get_preview_cache("https://site4.com") is not None
        assert storage.get_preview_cache("https://site0.com") is None


class TestEnrichSearchText:
    def test_fetched_title_becomes_searchable(self, storage):
        item = storage.capture(kind="url", text_content="https://youtu.be/dQw4w9WgXcQ")
        assert storage.recent(limit=10, query="rick astley") == []

        storage.enrich_search_text(item.id, "Rick Astley - Never Gonna Give You Up")

        results = storage.recent(limit=10, query="rick astley")
        assert len(results) == 1
        assert results[0].id == item.id

    def test_original_content_still_searchable_after_enrichment(self, storage):
        item = storage.capture(kind="url", text_content="https://youtu.be/dQw4w9WgXcQ")
        storage.enrich_search_text(item.id, "Rick Astley")
        assert len(storage.recent(limit=10, query="youtu.be")) == 1

    def test_enriching_nonexistent_item_does_not_raise(self, storage):
        storage.enrich_search_text(999_999, "some title")  # must not raise

    def test_calling_twice_with_same_title_does_not_duplicate(self, storage):
        item = storage.capture(kind="url", text_content="https://example.com")
        storage.enrich_search_text(item.id, "Example Title")
        storage.enrich_search_text(item.id, "Example Title")
        row = storage._conn.execute("SELECT search_text FROM clip_items WHERE id = ?", (item.id,)).fetchone()
        assert row[0].count("example title") == 1

    def test_accented_title_matches_unaccented_query(self, storage):
        item = storage.capture(kind="url", text_content="https://example.com/reunion")
        storage.enrich_search_text(item.id, "Página de la Reunión")
        results = storage.recent(limit=10, query="reunion")
        assert len(results) == 1
        assert results[0].id == item.id
