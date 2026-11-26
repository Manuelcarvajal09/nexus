"""Background link/YouTube preview fetching, wired for the Timeline
(timeline_window.py). Architecture exactly matches the requested shape:

URL detectada -> guardar item inmediatamente -> mostrar fallback
-> worker de metadata -> cache -> actualizar card

request() is cache-first and always returns immediately (a synchronous
SQLite read, not network): a cache hit returns the data right away for the
caller to render synchronously; a miss returns None (caller renders the
domain-only fallback) and schedules the actual network fetch on a
QThreadPool worker thread. The Qt main thread never blocks on network I/O
-- capture, window-open, and every keystroke stay exactly as fast as
before this feature existed."""

import logging

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from nexus.core import web_preview
from nexus.core.storage import StorageService

log = logging.getLogger("nexus.preview")


class _TaskSignals(QObject):
    finished = Signal(int, str, dict)  # item_id, url, cache-shaped preview dict


class _FetchTask(QRunnable):
    def __init__(self, item_id: int, url: str, is_youtube: bool, db_path):
        super().__init__()
        self.item_id = item_id
        self.url = url
        self.is_youtube = is_youtube
        self._db_path = db_path
        # Created here, on the calling (main) thread, before this task is
        # handed to the thread pool -- gives the signal the correct thread
        # affinity so its emit() from run() (worker thread) safely queues
        # back to whatever connected it on the main thread.
        self.signals = _TaskSignals()

    def run(self) -> None:
        # A fresh StorageService/sqlite3 connection scoped to THIS worker
        # thread -- sqlite3 connections cannot be shared across threads
        # (confirmed live: reusing the main thread's connection here raised
        # "SQLite objects created in a thread can only be used in that same
        # thread"). WAL mode (already set by StorageService.__init__) is
        # exactly what makes this safe: this connection's write and the
        # main thread's reads don't block each other.
        thread_storage = StorageService(db_path=self._db_path)
        try:
            preview = web_preview.fetch_preview(self.url, is_youtube=self.is_youtube)
            has_content = bool(preview.title or preview.og_image or preview.favicon)
            thread_storage.save_preview_cache(
                self.url, domain=preview.domain, title=preview.title,
                description=preview.description, favicon=preview.favicon,
                og_image=preview.og_image, is_youtube=preview.youtube,
                channel=preview.channel, status="ok" if has_content else "failed",
            )
            # The fetched title becomes searchable even though it wasn't
            # known at capture time -- otherwise "rick astley" could never
            # find a clip whose actual clipboard content was just a bare
            # youtube.com URL. Cheap: enrich_search_text() is a no-op past
            # the first call for the same title.
            if preview.title:
                thread_storage.enrich_search_text(self.item_id, preview.title)
            cached = thread_storage.get_preview_cache(self.url)
        except Exception:
            # A network/parsing bug must never crash the worker thread or
            # leave the UI waiting forever -- log it, emit an empty dict so
            # the card just keeps its fallback rendering.
            log.exception("Fallo inesperado obteniendo preview de %s", self.url)
            cached = None
        finally:
            thread_storage.close()
        self.signals.finished.emit(self.item_id, self.url, cached or {})


class PreviewService(QObject):
    preview_ready = Signal(int, dict)  # item_id, cache-shaped preview dict

    def __init__(self, storage: StorageService, parent=None, *, allow_network: bool = True):
        super().__init__(parent)
        self._storage = storage
        self._allow_network = allow_network
        self._pool = QThreadPool.globalInstance()
        self._inflight: set[str] = set()
        self.network_enabled = True

    def request(self, item_id: int, url: str, is_youtube: bool) -> dict | None:
        """Call for every url/youtube card the Timeline is about to render.
        Returns the cached dict immediately on a hit (render it now, no
        wait); returns None on a miss and schedules a background fetch
        (render the domain-only fallback now; preview_ready fires later)."""
        cached = self._storage.get_preview_cache(url)
        if cached is not None:
            # Found and fixed before shipping: a cache HIT (this URL was
            # already fetched for some earlier item) previously skipped
            # enrichment entirely, since that only ran inside _FetchTask for
            # a fresh fetch -- confirmed live, recopying an already-cached
            # YouTube URL produced a new clip item whose search_text never
            # got the video title. Every render path that has a title
            # available enriches, not just the one that just fetched it.
            if cached.get("title"):
                self._storage.enrich_search_text(item_id, cached["title"])
            return cached
        if not self._allow_network or not self.network_enabled:
            return None  # previews off (profile or setting) -> cache only, no request
        if url in self._inflight:
            return None  # already being fetched for another card with the same URL
        self._inflight.add(url)

        task = _FetchTask(item_id, url, is_youtube, self._storage.db_path)
        task.signals.finished.connect(self._on_task_finished)
        self._pool.start(task)
        return None

    def _on_task_finished(self, item_id: int, url: str, data: dict) -> None:
        # Must clear this on every completion, success or failure -- forgetting
        # to would permanently block any retry of this URL after its failed-
        # status cache entry expires (found and fixed before this ever shipped:
        # the first version of this method took no url and could never do this).
        self._inflight.discard(url)
        self.preview_ready.emit(item_id, data)
