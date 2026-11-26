"""SQLite persistence, mirroring Nexus's Core Data schema (ClipItem +
Pinboard) and its capture rule: dedup by SHA-256, re-copying identical
content updates timestamp/source instead of creating a new row.

Pinboards mirror Models/Pinboard.swift: named, colored/emoji boards; an item
can belong to any number of them (many-to-many, unlike a single boolean
"pinned" flag); membership exempts an item from the 30-day retention purge."""

import hashlib
import sys
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from nexus.core import paths
from nexus.platform.linux.settings import load_settings


def normalize_for_search(text: str) -> str:
    """Lowercase + strip accents (NFKD, drop combining marks) so searching
    "reunion" finds "Reunión" -- SQLite's LIKE is ASCII-case-insensitive only,
    it does nothing for accents, which matters a lot for Spanish text."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


TITLE_MAX_LENGTH = 80


def normalize_title(title: Optional[str]) -> Optional[str]:
    """A title is a one-line label: whitespace (including newlines) collapses
    to single spaces, it is cut at TITLE_MAX_LENGTH, and blank means "no title"."""
    if not title:
        return None
    cleaned = " ".join(title.split())[:TITLE_MAX_LENGTH].rstrip()
    return cleaned or None


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
    search_text TEXT,
    title TEXT,
    title_search TEXT
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

-- Nexus Stack's collection session is otherwise purely in-memory (ClipStack
-- in stack.py): if Nexus is killed or crashes while items are still
-- pending, they were previously just gone on next launch with no trace --
-- "arbitrary loss" the audit explicitly called out as unacceptable. This
-- mirrors just enough state (the ordered id list + FIFO/LIFO flag) to
-- restore it; ClipStack overwrites this table on every mutation so it never
-- drifts out of sync with the in-memory list it describes.
CREATE TABLE IF NOT EXISTS stack_state (
    position INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES clip_items(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS stack_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Link/YouTube preview cache (Phase 2.5). Keyed by a hash of the URL
-- string itself, not content_hash -- the same URL can appear in a
-- kind='url' item (captured via uri-list) and a kind='text' item (typed or
-- pasted as plain text) with two different content_hash values, and both
-- should share one fetch. status='failed' rows are kept (not just
-- discarded) with a short TTL so a transient offline moment doesn't cause
-- an immediate, indefinite retry storm -- see preview_service.py.
CREATE TABLE IF NOT EXISTS url_preview_cache (
    url_hash TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    domain TEXT,
    title TEXT,
    description TEXT,
    favicon BLOB,
    og_image BLOB,
    is_youtube INTEGER NOT NULL DEFAULT 0,
    channel TEXT,
    status TEXT NOT NULL,
    fetched_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_url_preview_cache_fetched_at ON url_preview_cache(fetched_at);
"""

PREVIEW_CACHE_MAX_ENTRIES = 1000
PREVIEW_CACHE_TTL_OK_S = 7 * 86400
PREVIEW_CACHE_TTL_FAILED_S = 3600

# Same palette/suggestions as Pinboard.swift, so a board created here looks
# like one created in the original.
AVAILABLE_COLORS = [
    "#FF3B30", "#FF9500", "#FFCC00", "#34C759", "#00C7BE", "#32ADE6",
    "#007AFF", "#5856D6", "#AF52DE", "#FF2D55", "#8E8E93", "#A2845E",
    "#D8B273", "#B8943C", "#00A6FF", "#7C4DFF",
    "#FF4D8D", "#00C853", "#FF6D00", "#00BCD4",
]
SUGGESTED_EMOJI = ["ðŸ“Œ", "â­ï¸", "ðŸ”¥", "ðŸ’¡", "ðŸŽ¯", "ðŸ“Ž", "ðŸ—‚ï¸", "ðŸŽ¨", "ðŸš€", "â¤ï¸"]
_UNSET = object()


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
    # User-assigned label shown in the UI; never part of the pasted payload.
    title: Optional[str] = None

    @property
    def is_pinned(self) -> bool:
        return len(self.pinboard_ids) > 0

    def load_image_data(self) -> Optional[bytes]:
        """The full image (same as .image_data); named for call sites that want
        the load to be explicit, e.g. before the row is deleted."""
        return self.image_data

    def preview(self, max_len: int = 80) -> str:
        if self.kind == "image":
            return "[imagen]"
        if self.kind == "file":
            return self.file_path or "[archivo]"
        text = (self.text_content or "").replace("\n", " ").strip()
        return text[:max_len] + ("…" if len(text) > max_len else "")


_NOT_LOADED = object()


class _LazyImageClipItem(ClipItem):
    """A history row whose PNG is read only when something actually needs it.

    Listing used to pull every image blob of the 200 newest rows on each panel
    open and each search keystroke (screenshots are 1-5 MB each). Thumbnails
    are cached by content_hash, so most opens never touch the blob at all."""

    _loader = None

    @property
    def image_data(self):
        value = self.__dict__.get("_image_data", _NOT_LOADED)
        if value is _NOT_LOADED:
            value = self._loader(self.id) if self._loader is not None else None
            self.__dict__["_image_data"] = value
        return value

    @image_data.setter
    def image_data(self, value):
        self.__dict__["_image_data"] = value


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
    def __init__(self, db_path: Path = paths.DB_PATH, *, allow_retention: bool | None = None):
        paths.ensure_dirs()
        # Kept for callers that need their own connection to the same file
        # on a different thread (sqlite3 connections are not thread-safe to
        # share -- see preview_service.py's _FetchTask, which opens its own
        # StorageService(db_path=storage.db_path) rather than touching this
        # instance's self._conn from a worker thread).
        self.db_path = db_path
        if allow_retention is None:
            # Windows has always purged unpinned clips after the retention period;
            # the Linux build makes it an explicit opt-in in its own settings.
            allow_retention = True if sys.platform == "win32" else load_settings().allow_retention
        self.allow_retention = allow_retention is True
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA foreign_keys = ON")
        # WAL lets a future reader (backup, diagnostics) run concurrently with
        # this connection's writes instead of blocking; busy_timeout replaces
        # an instant "database is locked" error with a bounded wait, since the
        # default (0) turns any brief contention into a visible failure.
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        # Deleted clips (passwords copied by mistake, purged history) are zeroed
        # on disk instead of lingering in free pages until overwritten.
        self._conn.execute("PRAGMA secure_delete = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._migrate_search_text()
        self._migrate_title()
        self._fts = self._ensure_fts()

    FTS_SCHEMA = """
    CREATE VIRTUAL TABLE clip_fts USING fts5(
        search_text, title_search, content='clip_items', content_rowid='id', tokenize='trigram');
    CREATE TRIGGER IF NOT EXISTS clip_fts_ai AFTER INSERT ON clip_items BEGIN
        INSERT INTO clip_fts(rowid, search_text, title_search) VALUES (new.id, new.search_text, new.title_search);
    END;
    CREATE TRIGGER IF NOT EXISTS clip_fts_ad AFTER DELETE ON clip_items BEGIN
        INSERT INTO clip_fts(clip_fts, rowid, search_text, title_search) VALUES ('delete', old.id, old.search_text, old.title_search);
    END;
    CREATE TRIGGER IF NOT EXISTS clip_fts_au AFTER UPDATE OF search_text, title_search ON clip_items BEGIN
        INSERT INTO clip_fts(clip_fts, rowid, search_text, title_search) VALUES ('delete', old.id, old.search_text, old.title_search);
        INSERT INTO clip_fts(rowid, search_text, title_search) VALUES (new.id, new.search_text, new.title_search);
    END;
    """

    def _ensure_fts(self) -> bool:
        """Trigram full-text index over the normalized search columns.

        LIKE '%q%' scanned every row on each keystroke (~45 ms for a rare word in
        a 3,400-clip history, growing with it). Additive and idempotent; kept in
        sync by triggers, so older Nexus versions opening the file keep it right.
        Falls back to LIKE when this SQLite has no FTS5/trigram."""
        try:
            exists = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'clip_fts'").fetchone()
            if exists and "title_search" not in {row[1] for row in self._conn.execute("PRAGMA table_info(clip_fts)")}:
                # Early Linux builds indexed content only: rebuild with titles.
                self._conn.executescript(
                    "DROP TRIGGER IF EXISTS clip_fts_ai; DROP TRIGGER IF EXISTS clip_fts_ad;"
                    "DROP TRIGGER IF EXISTS clip_fts_au; DROP TABLE clip_fts;")
                exists = None
            if not exists:
                self._conn.executescript(self.FTS_SCHEMA)
                self._conn.execute("INSERT INTO clip_fts(clip_fts) VALUES ('rebuild')")
                self._conn.commit()
            return True
        except sqlite3.Error:
            self._conn.rollback()
            return False

    @staticmethod
    def _fts_phrase(column: str, normalized: str) -> str:
        return f'{column} : "{normalized.replace(chr(34), chr(34) * 2)}"'

    def _migrate_title(self) -> None:
        """Additive and idempotent: databases created before titles existed get
        two nullable columns; no existing row is rewritten. Linux ignores them."""
        columns = [row[1] for row in self._conn.execute("PRAGMA table_info(clip_items)")]
        for name in ("title", "title_search"):
            if name not in columns:
                self._conn.execute(f"ALTER TABLE clip_items ADD COLUMN {name} TEXT")
        self._conn.commit()
        columns = [row[1] for row in self._conn.execute("PRAGMA table_info(clip_items)")]
        self._title_index = columns.index("title")

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

    def enrich_search_text(self, item_id: int, extra_text: str) -> None:
        """Appends normalized text (a fetched page/video title, arriving
        asynchronously well after capture -- see preview_service.py) to an
        item's existing search_text, so 'Rick Astley' can find a YouTube
        link whose clipboard content was only ever the bare URL. A no-op if
        the item is gone (deleted between capture and the preview finishing)
        or the addition is already present (safe to call more than once for
        the same title without growing search_text unboundedly)."""
        row = self._conn.execute("SELECT search_text FROM clip_items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            return
        current = row[0] or ""
        addition = normalize_for_search(extra_text)
        if addition and addition not in current:
            self._conn.execute(
                "UPDATE clip_items SET search_text = ? WHERE id = ?",
                (f"{current} {addition}".strip(), item_id),
            )
            self._conn.commit()

    def set_title(self, item_id: int, title: Optional[str]) -> Optional[str]:
        """Sets (or, when blank, removes) the clip's label and returns what was
        stored. Only the label columns change: the pasted content, its hash and
        its timestamps are untouched."""
        stored = normalize_title(title)
        cur = self._conn.execute(
            "UPDATE clip_items SET title = ?, title_search = ? WHERE id = ?",
            (stored, normalize_for_search(stored) if stored else None, item_id),
        )
        self._conn.commit()
        return stored if cur.rowcount else None

    def _list_columns(self) -> str:
        """Every clip_items column in table order (indexes stay valid), except the
        image blob, read on demand by _LazyImageClipItem."""
        cached = getattr(self, "_list_columns_sql", None)
        if cached is None:
            names = [row[1] for row in self._conn.execute("PRAGMA table_info(clip_items)")]
            cached = ", ".join("NULL AS image_data" if n == "image_data" else f"clip_items.{n}" for n in names)
            self._list_columns_sql = cached
        return cached

    def _select_recent(self, joins: str, where: list, params: list, limit: int) -> list:
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        return self._conn.execute(
            f"""SELECT {self._list_columns()} FROM clip_items {joins} {where_sql}
                ORDER BY updated_at DESC, clip_items.id DESC LIMIT ?""",
            [*params, limit],
        ).fetchall()

    def recent(self, limit: int = 200, query: str = "", pinboard_id: Optional[int] = None, kind: Optional[str] = None) -> list[ClipItem]:
        where: list = []
        params: list = []
        joins = ""
        if pinboard_id is not None:
            joins = "JOIN pinboard_items pi ON pi.item_id = clip_items.id"
            where.append("pi.pinboard_id = ?")
            params.append(pinboard_id)
        if kind is not None:
            where.append("kind = ?")
            params.append(kind)
        if not query:
            return self._rows_to_items(self._select_recent(joins, where, params, limit))
        # Title matches first, then content-only matches, each most-recent-first.
        # Two ordered passes keep the updated_at index usable (a CASE in ORDER BY
        # would sort every match) and stop early once `limit` rows are found.
        normalized = normalize_for_search(query)
        pattern = f"%{normalized}%"
        # Trigrams need >= 3 characters; shorter queries match almost everything
        # anyway, so LIKE stops after `limit` hits quickly. FTS also matches
        # literally: LIKE treated "_" and "%" in the query as wildcards.
        use_fts = self._fts and len(normalized) >= 3
        fts = "clip_items.id IN (SELECT rowid FROM clip_fts WHERE clip_fts MATCH ?)"
        title_cond, title_arg = (fts, self._fts_phrase("title_search", normalized)) if use_fts else ("title_search LIKE ?", pattern)
        content_cond, content_arg = (fts, self._fts_phrase("search_text", normalized)) if use_fts else ("search_text LIKE ?", pattern)
        try:
            rows = self._select_recent(joins, [*where, title_cond], [*params, title_arg], limit)
        except sqlite3.OperationalError:
            if not use_fts:
                raise
            # A query FTS5 cannot parse (control characters...): plain LIKE instead.
            self._fts, fallback = False, None
            try:
                fallback = self.recent(limit, query, pinboard_id, kind)
            finally:
                self._fts = True
            return fallback
        remaining = limit - len(rows)
        if remaining > 0:
            content_where, content_params = [*where, content_cond], [*params, content_arg]
            if rows:
                content_where.append(f"clip_items.id NOT IN ({','.join('?' * len(rows))})")
                content_params.extend(row[0] for row in rows)
            rows = rows + self._select_recent(joins, content_where, content_params, remaining)
        return self._rows_to_items(rows)

    def delete(self, item_id: int) -> None:
        self._conn.execute("DELETE FROM clip_items WHERE id = ?", (item_id,))
        self._conn.commit()

    def restore_item(self, item: "ClipItem") -> Optional["ClipItem"]:
        """Undo of delete(): puts the same clip back with its title, timestamps and
        the pinboards that still exist. If the same content was copied again in the
        meantime that newer row is kept and returned instead (hash is UNIQUE)."""
        row = self._conn.execute("SELECT id FROM clip_items WHERE content_hash = ?", (item.content_hash,)).fetchone()
        if row:
            return self.get(row[0])
        keep_id = self._conn.execute("SELECT 1 FROM clip_items WHERE id = ?", (item.id,)).fetchone() is None
        search_text = normalize_for_search(" ".join(part for part in (item.text_content, item.file_path) if part))
        title = normalize_title(item.title)
        columns = "content_hash, kind, text_content, image_data, file_path, source_app, created_at, updated_at, pinned, search_text, title, title_search"
        values = [item.content_hash, item.kind, item.text_content, item.image_data, item.file_path, item.source_app,
                  item.created_at, item.updated_at, 0, search_text, title, normalize_for_search(title) if title else None]
        if keep_id:
            columns, values = "id, " + columns, [item.id] + values
        cur = self._conn.execute(f"INSERT INTO clip_items ({columns}) VALUES ({', '.join('?' * len(values))})", values)
        new_id = item.id if keep_id else cur.lastrowid
        for board_id in item.pinboard_ids:
            if self._conn.execute("SELECT 1 FROM pinboards WHERE id = ?", (board_id,)).fetchone():
                self._conn.execute("INSERT OR IGNORE INTO pinboard_items (pinboard_id, item_id) VALUES (?, ?)", (board_id, new_id))
        self._conn.commit()
        return self.get(new_id)

    def pinboard_counts(self) -> dict:
        """{pinboard_id: number of clips} plus None -> total history size."""
        counts = {row[0]: row[1] for row in self._conn.execute(
            "SELECT pinboard_id, COUNT(*) FROM pinboard_items GROUP BY pinboard_id")}
        counts[None] = self._conn.execute("SELECT COUNT(*) FROM clip_items").fetchone()[0]
        return counts

    def purge_older_than(self, days: int) -> int:
        if not self.allow_retention:
            return 0
        if type(days) is not int or days < 1:
            raise ValueError("retention days must be a positive integer")
        cutoff = time.time() - days * 86400
        cur = self._conn.execute(
            """DELETE FROM clip_items WHERE updated_at < ? AND title IS NULL AND id NOT IN
               (SELECT DISTINCT item_id FROM pinboard_items)""",
            (cutoff,),
        )
        self._conn.commit()
        self.compact_if_bloated()
        return cur.rowcount

    COMPACT_MIN_FREE_BYTES = 32 * 1024 * 1024
    COMPACT_MIN_FREE_RATIO = 0.4

    def compact_if_bloated(self) -> bool:
        """VACUUM once free pages dominate the file (never ran before: a real
        history reached 187 MB holding ~55 MB of clips). Also drops content
        deleted before secure_delete existed. Best effort: skipped when busy."""
        try:
            page_size = self._conn.execute("PRAGMA page_size").fetchone()[0]
            pages = self._conn.execute("PRAGMA page_count").fetchone()[0]
            free = self._conn.execute("PRAGMA freelist_count").fetchone()[0]
            if free * page_size < self.COMPACT_MIN_FREE_BYTES or free < pages * self.COMPACT_MIN_FREE_RATIO:
                return False
            self._conn.execute("VACUUM")
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return True
        except sqlite3.Error:
            return False

    # -- Nexus Stack session recovery --------------------------------------

    def save_stack_state(self, item_ids: list[int], reversed_order: bool, *, commit: bool = True) -> None:
        """Overwrites the persisted Stack session wholesale -- called after
        every ClipStack mutation (add/consume/remove/clear/reorder), so a
        crash between two mutations can only lose the DB write for the
        mutation in progress, never leave stale/half-updated ids behind."""
        self._conn.execute("DELETE FROM stack_state")
        self._conn.executemany(
            "INSERT INTO stack_state (position, item_id) VALUES (?, ?)",
            list(enumerate(item_ids)),
        )
        self._conn.execute(
            "INSERT INTO stack_meta (key, value) VALUES ('reversed_order', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("1" if reversed_order else "0",),
        )
        if commit:
            self._conn.commit()

    def load_stack_state(self) -> tuple[list[int], bool]:
        rows = self._conn.execute(
            "SELECT item_id FROM stack_state ORDER BY position"
        ).fetchall()
        row = self._conn.execute(
            "SELECT value FROM stack_meta WHERE key = 'reversed_order'"
        ).fetchone()
        reversed_order = row is not None and row[0] == "1"
        return [r[0] for r in rows], reversed_order

    # -- Link/YouTube preview cache ----------------------------------------

    @staticmethod
    def _url_hash(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8", "surrogatepass")).hexdigest()

    def get_preview_cache(self, url: str) -> Optional[dict]:
        """Returns None on a cache miss OR a stale entry (expired TTL,
        status-dependent -- see PREVIEW_CACHE_TTL_*) so the caller always
        treats both the same way: go fetch it. Never raises; a corrupt or
        missing row is just a miss, not an error the UI needs to handle."""
        row = self._conn.execute(
            "SELECT url, domain, title, description, favicon, og_image, is_youtube, channel, status, fetched_at "
            "FROM url_preview_cache WHERE url_hash = ?",
            (self._url_hash(url),),
        ).fetchone()
        if row is None:
            return None
        (_url, domain, title, description, favicon, og_image,
         is_youtube, channel, status, fetched_at) = row
        ttl = PREVIEW_CACHE_TTL_OK_S if status == "ok" else PREVIEW_CACHE_TTL_FAILED_S
        if time.time() - fetched_at > ttl:
            return None
        return {
            "domain": domain, "title": title, "description": description,
            "favicon": favicon, "og_image": og_image, "is_youtube": bool(is_youtube),
            "channel": channel, "status": status,
        }

    def save_preview_cache(
        self, url: str, *, domain: Optional[str], title: Optional[str] = None,
        description: Optional[str] = None, favicon: Optional[bytes] = None,
        og_image: Optional[bytes] = None, is_youtube: bool = False,
        channel: Optional[str] = None, status: str = "ok",
    ) -> None:
        self._conn.execute(
            """INSERT INTO url_preview_cache
               (url_hash, url, domain, title, description, favicon, og_image, is_youtube, channel, status, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(url_hash) DO UPDATE SET
                   url=excluded.url, domain=excluded.domain, title=excluded.title,
                   description=excluded.description, favicon=excluded.favicon,
                   og_image=excluded.og_image, is_youtube=excluded.is_youtube,
                   channel=excluded.channel, status=excluded.status, fetched_at=excluded.fetched_at""",
            (self._url_hash(url), url, domain, title, description, favicon, og_image,
             int(is_youtube), channel, status, time.time()),
        )
        # Cap total cache size -- evict oldest-fetched rows beyond the limit
        # rather than letting it grow unboundedly over a long-running session.
        self._conn.execute(
            """DELETE FROM url_preview_cache WHERE url_hash NOT IN (
                   SELECT url_hash FROM url_preview_cache ORDER BY fetched_at DESC LIMIT ?
               )""",
            (PREVIEW_CACHE_MAX_ENTRIES,),
        )
        self._conn.commit()

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
                         color_hex: Optional[str] = None, emoji=_UNSET) -> None:
        board = self.get_pinboard(pinboard_id)
        if board is None:
            return
        self._conn.execute(
            "UPDATE pinboards SET name = ?, color_hex = ?, emoji = ? WHERE id = ?",
            (name if name is not None else board.name,
             color_hex if color_hex is not None else board.color_hex,
              board.emoji if emoji is _UNSET else emoji,
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
            title=row[self._title_index],
        )

    def _rows_to_items(self, rows) -> list[ClipItem]:
        """Batch equivalent of _row_to_item for a whole result page -- see
        _pinboards_for_items for why this matters."""
        item_ids = [row[0] for row in rows]
        pinboard_map = self._pinboards_for_items(item_ids)
        items = []
        for row in rows:
            lazy = row[2] == "image" and row[4] is None   # listed without its blob
            item = (_LazyImageClipItem if lazy else ClipItem)(
                id=row[0], content_hash=row[1], kind=row[2], text_content=row[3],
                image_data=_NOT_LOADED if lazy else row[4], file_path=row[5], source_app=row[6],
                created_at=row[7], updated_at=row[8],
                pinboard_ids=pinboard_map.get(row[0], []),
                title=row[self._title_index],
            )
            if lazy:
                item._loader = self._load_image_data
            items.append(item)
        return items

    def _load_image_data(self, item_id: int) -> Optional[bytes]:
        row = self._conn.execute("SELECT image_data FROM clip_items WHERE id = ?", (item_id,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _row_to_pinboard(row) -> Optional[Pinboard]:
        if row is None:
            return None
        return Pinboard(
            id=row[0], name=row[1], color_hex=row[2], emoji=row[3],
            sort_order=row[4], created_at=row[5],
        )
