"""SQLite persistence, mirroring Nexus's Core Data schema (ClipItem +
Pinboard) and its capture rule: dedup by SHA-256, re-copying identical
content updates timestamp/source instead of creating a new row.

Pinboards mirror Models/Pinboard.swift: named, colored/emoji boards; an item
can belong to any number of them (many-to-many, unlike a single boolean
"pinned" flag); membership exempts an item from the 30-day retention purge."""

import hashlib
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import paths


def normalize_for_search(text: str) -> str:
    """Lowercase + strip accents (NFKD, drop combining marks) so searching
    "reunion" finds "Reunión" -- SQLite's LIKE is ASCII-case-insensitive only,
    it does nothing for accents, which matters a lot for Spanish text."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


SCHEMA = """
CREATE TABLE IF NOT EXISTS clip_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('text', 'url', 'image', 'file')),
    text_content TEXT,
    image_data BLOB,
    file_path TEXT,
    source_app TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    search_text TEXT
);
CREATE INDEX IF NOT EXISTS idx_clip_items_updated_at ON clip_items(updated_at DESC);

CREATE TABLE IF NOT EXISTS pinboards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    color_hex TEXT NOT NULL,
    emoji TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pinboard_items (
    pinboard_id INTEGER NOT NULL REFERENCES pinboards(id) ON DELETE CASCADE,
    item_id INTEGER NOT NULL REFERENCES clip_items(id) ON DELETE CASCADE,
    PRIMARY KEY (pinboard_id, item_id)
);
"""

# Same palette/suggestions as Pinboard.swift, so a board created here looks
# like one created in the original.
AVAILABLE_COLORS = [
    "#FF3B30", "#FF9500", "#FFCC00", "#34C759", "#00C7BE", "#32ADE6",
    "#007AFF", "#5856D6", "#AF52DE", "#FF2D55", "#8E8E93", "#A2845E",
    "#D8B273", "#B8943C", "#00A6FF", "#7C4DFF",
    "#FF4D8D", "#00C853", "#FF6D00", "#00BCD4",
]
SUGGESTED_EMOJI = ["📌", "⭐️", "🔥", "💡", "🎯", "📎", "🗂️", "🎨", "🚀", "❤️"]


@dataclass
class ClipItem:
    id: int
    content_hash: str
    kind: str
    text_content: Optional[str]
    image_data: Optional[bytes]
    file_path: Optional[str]
    source_app: Optional[str]
    created_at: float
    updated_at: float
    pinboard_ids: list[int] = field(default_factory=list)

    @property
    def is_pinned(self) -> bool:
        return len(self.pinboard_ids) > 0

    def preview(self, max_len: int = 80) -> str:
        if self.kind == "image":
            return "[imagen]"
        if self.kind == "file":
            return self.file_path or "[archivo]"
        text = (self.text_content or "").replace("\n", " ").strip()
        return text[:max_len] + ("…" if len(text) > max_len else "")


@dataclass
class Pinboard:
    id: int
    name: str
    color_hex: str
    emoji: Optional[str]
    sort_order: int
    created_at: float

    @property
    def display_name(self) -> str:
        if self.emoji:
            return f"{self.emoji} {self.name}"
        return self.name


class StorageService:
    def __init__(self, db_path: Path = paths.DB_PATH):
        paths.ensure_dirs()
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._migrate_search_text()

    def _migrate_search_text(self) -> None:
        """CREATE TABLE IF NOT EXISTS never adds columns to a table that
        already existed (Manuel's real history predates search_text) --
        ALTER TABLE + a one-time backfill covers that, cheaply skipped on
        every later startup once every row already has it."""
        columns = [row[1] for row in self._conn.execute("PRAGMA table_info(clip_items)")]
        if "search_text" not in columns:
            self._conn.execute("ALTER TABLE clip_items ADD COLUMN search_text TEXT")
            self._conn.commit()

        # Column is guaranteed to exist by this point either way (fresh DB:
        # CREATE TABLE already had it; existing DB: ALTER TABLE just added
        # it), so the index can always be created here, once.
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_clip_items_search_text ON clip_items(search_text)"
        )
        self._conn.commit()

        rows = self._conn.execute(
            "SELECT id, text_content, file_path FROM clip_items WHERE search_text IS NULL"
        ).fetchall()
        for item_id, text_content, file_path in rows:
            combined = " ".join(part for part in (text_content, file_path) if part)
            self._conn.execute(
                "UPDATE clip_items SET search_text = ? WHERE id = ?",
                (normalize_for_search(combined), item_id),
            )
        if rows:
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def hash_of(kind: str, text: Optional[str], image_data: Optional[bytes], file_path: Optional[str]) -> str:
        h = hashlib.sha256()
        h.update(kind.encode())
        if text is not None:
            h.update(text.encode("utf-8", "surrogatepass"))
        if image_data is not None:
            h.update(image_data)
        if file_path is not None:
            h.update(file_path.encode())
        return h.hexdigest()

    def capture(
        self,
        kind: str,
        text_content: Optional[str] = None,
        image_data: Optional[bytes] = None,
        file_path: Optional[str] = None,
        source_app: Optional[str] = None,
    ) -> ClipItem:
        content_hash = self.hash_of(kind, text_content, image_data, file_path)
        now = time.time()
        cur = self._conn.execute("SELECT id FROM clip_items WHERE content_hash = ?", (content_hash,))
        row = cur.fetchone()
        if row:
            self._conn.execute(
                "UPDATE clip_items SET updated_at = ?, source_app = ? WHERE id = ?",
                (now, source_app, row[0]),
            )
            self._conn.commit()
            return self.get(row[0])
        search_text = normalize_for_search(" ".join(part for part in (text_content, file_path) if part))
        cur = self._conn.execute(
            """INSERT INTO clip_items
               (content_hash, kind, text_content, image_data, file_path, source_app,
                created_at, updated_at, pinned, search_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (content_hash, kind, text_content, image_data, file_path, source_app, now, now, search_text),
        )
        self._conn.commit()
        return self.get(cur.lastrowid)

    def get(self, item_id: int) -> Optional[ClipItem]:
        row = self._conn.execute("SELECT * FROM clip_items WHERE id = ?", (item_id,)).fetchone()
        return self._row_to_item(row)

    def recent(self, limit: int = 200, query: str = "", pinboard_id: Optional[int] = None) -> list[ClipItem]:
        where = []
        params: list = []
        joins = ""
        if pinboard_id is not None:
            joins = "JOIN pinboard_items pi ON pi.item_id = clip_items.id"
            where.append("pi.pinboard_id = ?")
            params.append(pinboard_id)
        if query:
            where.append("search_text LIKE ?")
            params.append(f"%{normalize_for_search(query)}%")
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(limit)
        rows = self._conn.execute(
            f"""SELECT clip_items.* FROM clip_items {joins} {where_sql}
                ORDER BY updated_at DESC LIMIT ?""",
            params,
        ).fetchall()
        return self._rows_to_items(rows)

    def delete(self, item_id: int) -> None:
        self._conn.execute("DELETE FROM clip_items WHERE id = ?", (item_id,))
        self._conn.commit()

    def purge_older_than(self, days: int) -> int:
        cutoff = time.time() - days * 86400
        cur = self._conn.execute(
            """DELETE FROM clip_items WHERE updated_at < ? AND id NOT IN
               (SELECT DISTINCT item_id FROM pinboard_items)""",
            (cutoff,),
        )
        self._conn.commit()
        return cur.rowcount

    # -- Pinboards --------------------------------------------------------

    def list_pinboards(self) -> list[Pinboard]:
        rows = self._conn.execute("SELECT * FROM pinboards ORDER BY sort_order, id").fetchall()
        return [self._row_to_pinboard(r) for r in rows]

    def create_pinboard(self, name: str, color_hex: Optional[str] = None, emoji: Optional[str] = None) -> Pinboard:
        count = self._conn.execute("SELECT COUNT(*) FROM pinboards").fetchone()[0]
        color_hex = color_hex or AVAILABLE_COLORS[count % len(AVAILABLE_COLORS)]
        cur = self._conn.execute(
            "INSERT INTO pinboards (name, color_hex, emoji, sort_order, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, color_hex, emoji, count, time.time()),
        )
        self._conn.commit()
        return self.get_pinboard(cur.lastrowid)

    def get_pinboard(self, pinboard_id: int) -> Optional[Pinboard]:
        row = self._conn.execute("SELECT * FROM pinboards WHERE id = ?", (pinboard_id,)).fetchone()
        return self._row_to_pinboard(row)

    def update_pinboard(self, pinboard_id: int, name: Optional[str] = None,
                         color_hex: Optional[str] = None, emoji: Optional[str] = None) -> None:
        board = self.get_pinboard(pinboard_id)
        if board is None:
            return
        self._conn.execute(
            "UPDATE pinboards SET name = ?, color_hex = ?, emoji = ? WHERE id = ?",
            (name if name is not None else board.name,
             color_hex if color_hex is not None else board.color_hex,
             emoji if emoji is not None else board.emoji,
             pinboard_id),
        )
        self._conn.commit()

    def reorder_pinboards(self, ordered_ids: list[int]) -> None:
        """Persists a full new order at once -- used after a drag-and-drop
        reorder in the sidebar, as opposed to move_pinboard's single-step
        swap for the "Mover arriba/abajo" menu actions."""
        for index, pinboard_id in enumerate(ordered_ids):
            self._conn.execute(
                "UPDATE pinboards SET sort_order = ? WHERE id = ?", (index, pinboard_id)
            )
        self._conn.commit()

    def move_pinboard(self, pinboard_id: int, direction: int) -> None:
        """direction: -1 to move up (earlier), +1 to move down (later)."""
        boards = self.list_pinboards()
        index = next((i for i, b in enumerate(boards) if b.id == pinboard_id), None)
        if index is None:
            return
        swap_with = index + direction
        if not (0 <= swap_with < len(boards)):
            return
        a, b = boards[index], boards[swap_with]
        self._conn.execute("UPDATE pinboards SET sort_order = ? WHERE id = ?", (b.sort_order, a.id))
        self._conn.execute("UPDATE pinboards SET sort_order = ? WHERE id = ?", (a.sort_order, b.id))
        self._conn.commit()

    def delete_pinboard(self, pinboard_id: int) -> None:
        self._conn.execute("DELETE FROM pinboards WHERE id = ?", (pinboard_id,))
        self._conn.commit()

    def add_to_pinboard(self, item_id: int, pinboard_id: int) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO pinboard_items (pinboard_id, item_id) VALUES (?, ?)",
            (pinboard_id, item_id),
        )
        self._conn.commit()

    def remove_from_pinboard(self, item_id: int, pinboard_id: int) -> None:
        self._conn.execute(
            "DELETE FROM pinboard_items WHERE pinboard_id = ? AND item_id = ?", (pinboard_id, item_id)
        )
        self._conn.commit()

    def pinboards_for_item(self, item_id: int) -> list[int]:
        rows = self._conn.execute(
            "SELECT pinboard_id FROM pinboard_items WHERE item_id = ?", (item_id,)
        ).fetchall()
        return [r[0] for r in rows]

    def _pinboards_for_items(self, item_ids: list[int]) -> dict[int, list[int]]:
        """Batched version of pinboards_for_item -- recent() used to call the
        single-item version once per row (an N+1 query: up to 201 SELECTs for
        one 200-row refresh), which added up on every search keystroke and
        every capture. One IN (...) query for the whole page instead."""
        if not item_ids:
            return {}
        placeholders = ",".join("?" * len(item_ids))
        rows = self._conn.execute(
            f"SELECT item_id, pinboard_id FROM pinboard_items WHERE item_id IN ({placeholders})",
            item_ids,
        ).fetchall()
        result: dict[int, list[int]] = {item_id: [] for item_id in item_ids}
        for item_id, pinboard_id in rows:
            result[item_id].append(pinboard_id)
        return result

    # -----------------------------------------------------------------

    def _row_to_item(self, row) -> Optional[ClipItem]:
        if row is None:
            return None
        item_id = row[0]
        return ClipItem(
            id=item_id,
            content_hash=row[1],
            kind=row[2],
            text_content=row[3],
            image_data=row[4],
            file_path=row[5],
            source_app=row[6],
            created_at=row[7],
            updated_at=row[8],
            pinboard_ids=self.pinboards_for_item(item_id),
        )

    def _rows_to_items(self, rows) -> list[ClipItem]:
        """Batch equivalent of _row_to_item for a whole result page -- see
        _pinboards_for_items for why this matters."""
        item_ids = [row[0] for row in rows]
        pinboard_map = self._pinboards_for_items(item_ids)
        return [
            ClipItem(
                id=row[0], content_hash=row[1], kind=row[2], text_content=row[3],
                image_data=row[4], file_path=row[5], source_app=row[6],
                created_at=row[7], updated_at=row[8],
                pinboard_ids=pinboard_map.get(row[0], []),
            )
            for row in rows
        ]

    @staticmethod
    def _row_to_pinboard(row) -> Optional[Pinboard]:
        if row is None:
            return None
        return Pinboard(
            id=row[0], name=row[1], color_hex=row[2], emoji=row[3],
            sort_order=row[4], created_at=row[5],
        )
