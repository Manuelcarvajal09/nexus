"""Editable clip titles: a label only -- never part of what gets pasted."""
import sqlite3
import time

import pytest

from nexus_linux.storage import StorageService, TITLE_MAX_LENGTH, normalize_title

LEGACY_SCHEMA = """
CREATE TABLE clip_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('text', 'url', 'image', 'file')),
    text_content TEXT, image_data BLOB, file_path TEXT, source_app TEXT,
    created_at REAL NOT NULL, updated_at REAL NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0, search_text TEXT
);
CREATE TABLE pinboards (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, color_hex TEXT NOT NULL,
    emoji TEXT, sort_order INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
);
CREATE TABLE pinboard_items (
    pinboard_id INTEGER NOT NULL REFERENCES pinboards(id) ON DELETE CASCADE,
    item_id INTEGER NOT NULL REFERENCES clip_items(id) ON DELETE CASCADE,
    PRIMARY KEY (pinboard_id, item_id)
);
"""


def _age(storage, item_id, days):
    storage._conn.execute(
        "UPDATE clip_items SET updated_at = ? WHERE id = ?", (time.time() - days * 86400, item_id)
    )
    storage._conn.commit()


def test_new_items_have_no_title(storage):
    item = storage.capture(kind="text", text_content="hola")
    assert item.title is None


@pytest.mark.parametrize("raw,expected", [
    ("  Auditoría completa  ", "Auditoría completa"),
    ("Línea uno\nlínea dos\r\n\ttres", "Línea uno línea dos tres"),
    ("a    b", "a b"),
    ("🚀 Lanzamiento ✨", "🚀 Lanzamiento ✨"),
    ("", None),
    ("   \n\t ", None),
    (None, None),
    ("x" * 200, "x" * TITLE_MAX_LENGTH),
])
def test_normalize_title(raw, expected):
    assert normalize_title(raw) == expected
    assert TITLE_MAX_LENGTH == 80


def test_set_title_persists_and_returns_stored_value(storage):
    item = storage.capture(kind="text", text_content="prompt largo")
    assert storage.set_title(item.id, "  Auditoría completa de ciberseguridad ") == "Auditoría completa de ciberseguridad"
    assert storage.get(item.id).title == "Auditoría completa de ciberseguridad"
    assert storage.recent()[0].title == "Auditoría completa de ciberseguridad"


def test_empty_title_removes_it_and_unknown_id_is_harmless(storage):
    item = storage.capture(kind="text", text_content="prompt")
    storage.set_title(item.id, "Algo")
    assert storage.set_title(item.id, "   ") is None
    assert storage.get(item.id).title is None
    assert storage.set_title(999999, "no existe") is None


def test_title_never_changes_pasted_content_or_identity(storage):
    item = storage.capture(kind="text", text_content="contenido original\ncon dos líneas")
    before = (item.text_content, item.content_hash, item.kind, item.file_path, item.image_data)
    storage.set_title(item.id, "Etiqueta")
    after = storage.get(item.id)
    assert (after.text_content, after.content_hash, after.kind, after.file_path, after.image_data) == before


@pytest.mark.parametrize("kind,kwargs", [
    ("text", {"text_content": "texto"}),
    ("url", {"text_content": "https://example.com/a"}),
    ("image", {"image_data": b"\x89PNG-synthetic"}),
    ("file", {"file_path": r"C:\tmp\a.txt"}),
])
def test_every_kind_can_be_titled_without_touching_payload(storage, kind, kwargs):
    item = storage.capture(kind=kind, **kwargs)
    storage.set_title(item.id, "Título")
    again = storage.get(item.id)
    assert again.title == "Título"
    assert (again.text_content, again.image_data, again.file_path) == (item.text_content, item.image_data, item.file_path)


def test_recopying_the_same_content_keeps_the_title_and_the_row(storage):
    first = storage.capture(kind="text", text_content="repetido", source_app="a.exe")
    storage.set_title(first.id, "Mi título")
    second = storage.capture(kind="text", text_content="repetido", source_app="b.exe")
    assert second.id == first.id and second.title == "Mi título"
    assert len(storage.recent()) == 1


def test_duplicate_titles_are_allowed(storage):
    a = storage.capture(kind="text", text_content="uno")
    b = storage.capture(kind="text", text_content="dos")
    storage.set_title(a.id, "Igual")
    storage.set_title(b.id, "Igual")
    assert [i.title for i in storage.recent()] == ["Igual", "Igual"]


def test_search_finds_by_title_ignoring_accents_and_case(storage):
    item = storage.capture(kind="text", text_content="Eres un experto. Revisa todo el sistema paso a paso.")
    storage.capture(kind="text", text_content="otra cosa distinta")
    assert storage.recent(query="ciberseguridad") == []
    storage.set_title(item.id, "Auditoría completa de CIBERSEGURIDAD")
    assert [i.id for i in storage.recent(query="ciberseguridad")] == [item.id]
    assert [i.id for i in storage.recent(query="auditoria")] == [item.id]


def test_search_still_finds_by_content_and_never_by_hidden_state(storage):
    item = storage.capture(kind="text", text_content="contraseña del router")
    storage.set_title(item.id, "Red")
    assert [i.id for i in storage.recent(query="router")] == [item.id]
    storage.set_title(item.id, None)
    assert storage.recent(query="red") == []  # old title leaves no ghost match
    assert [i.id for i in storage.recent(query="router")] == [item.id]


def test_title_matches_rank_before_content_matches(storage):
    by_content = storage.capture(kind="text", text_content="menciona factura en el texto")
    by_title = storage.capture(kind="text", text_content="sin relación")
    storage.set_title(by_title.id, "Factura mensual")
    _age(storage, by_title.id, 5)  # older, yet it must still come first
    assert [i.id for i in storage.recent(query="factura")] == [by_title.id, by_content.id]


def test_title_search_works_inside_a_pinboard(storage):
    board = storage.create_pinboard("Prompts", "#FF2D55")
    inside = storage.capture(kind="text", text_content="prompt A")
    outside = storage.capture(kind="text", text_content="prompt B")
    storage.add_to_pinboard(inside.id, board.id)
    storage.set_title(inside.id, "Plan de lanzamiento")
    storage.set_title(outside.id, "Plan de lanzamiento")
    found = storage.recent(query="lanzamiento", pinboard_id=board.id)
    assert [i.id for i in found] == [inside.id]


def test_titled_clip_survives_retention_purge_but_plain_old_clip_does_not(storage):
    titled = storage.capture(kind="text", text_content="importante")
    plain = storage.capture(kind="text", text_content="descartable")
    pinned = storage.capture(kind="text", text_content="en pinboard")
    board = storage.create_pinboard("P", "#007AFF")
    storage.add_to_pinboard(pinned.id, board.id)
    storage.set_title(titled.id, "Conservar")
    for item in (titled, plain, pinned):
        _age(storage, item.id, 90)
    assert storage.purge_older_than(30) == 1
    assert storage.get(titled.id) is not None and storage.get(pinned.id) is not None
    assert storage.get(plain.id) is None


def test_removing_the_title_makes_the_clip_purgeable_again(storage):
    item = storage.capture(kind="text", text_content="x")
    storage.set_title(item.id, "Temporal")
    storage.set_title(item.id, None)
    _age(storage, item.id, 90)
    assert storage.purge_older_than(30) == 1


def test_deleting_a_clip_removes_its_title_and_relations(storage):
    board = storage.create_pinboard("P", "#007AFF")
    item = storage.capture(kind="text", text_content="x")
    storage.add_to_pinboard(item.id, board.id)
    storage.set_title(item.id, "Borrar")
    storage.delete(item.id)
    assert storage.get(item.id) is None
    assert storage.recent(query="borrar") == []


def test_migration_adds_columns_keeps_every_row_and_is_idempotent(tmp_path):
    path = tmp_path / "legacy.sqlite"
    raw = sqlite3.connect(path)
    raw.executescript(LEGACY_SCHEMA)
    now = time.time()
    for n in range(3):
        raw.execute(
            "INSERT INTO clip_items (content_hash, kind, text_content, created_at, updated_at, search_text) "
            "VALUES (?, 'text', ?, ?, ?, ?)", (f"h{n}", f"contenido {n}", now, now, f"contenido {n}"),
        )
    raw.execute("INSERT INTO pinboards (name, color_hex, created_at) VALUES ('P', '#FFF', ?)", (now,))
    raw.execute("INSERT INTO pinboard_items VALUES (1, 2)")
    raw.commit()
    raw.close()

    for _ in range(2):  # opening twice must not fail or duplicate anything
        storage = StorageService(path)
        columns = [r[1] for r in storage._conn.execute("PRAGMA table_info(clip_items)")]
        assert columns.count("title") == 1 and columns.count("title_search") == 1
        items = storage.recent()
        assert sorted(i.text_content for i in items) == ["contenido 0", "contenido 1", "contenido 2"]
        assert all(i.title is None for i in items)
        assert storage.get(2).pinboard_ids == [1]
        assert storage._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        storage.close()

    storage = StorageService(path)
    storage.set_title(1, "Después de migrar")
    assert [i.id for i in storage.recent(query="migrar")] == [1]
    storage.close()


def test_title_survives_reopening_the_database(tmp_path):
    path = tmp_path / "persist.sqlite"
    storage = StorageService(path)
    item = storage.capture(kind="text", text_content="persistente")
    storage.set_title(item.id, "Sobrevive 🔁")
    storage.close()
    reopened = StorageService(path)
    assert reopened.get(item.id).title == "Sobrevive 🔁"
    assert [i.id for i in reopened.recent(query="sobrevive")] == [item.id]
    reopened.close()
