"""PreviewService tests. Most cases are tested without real threading
(direct calls, or QThreadPool.start mocked out) since the logic under test
-- cache-first short-circuit, in-flight dedup, and clearing _inflight on
completion -- doesn't need a real worker thread to verify. One true
end-to-end test runs a real background fetch against a local http.server
and pumps the Qt event loop to receive the cross-thread signal, confirming
the whole pipeline (thread pool -> network -> cache write -> queued signal
back to the main thread) actually works, not just each piece in isolation."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PySide6.QtCore import QCoreApplication, QThreadPool

from nexus_linux import web_preview
from nexus_linux.preview_service import PreviewService, _FetchTask


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    # QCoreApplication (not QApplication/QWidget): no display needed, but
    # required for QThreadPool's queued cross-thread signal delivery to
    # have somewhere to land when we pump processEvents() below.
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


class TestRequestCacheHit:
    def test_cache_hit_returns_data_immediately_no_fetch_scheduled(self, storage, monkeypatch):
        storage.save_preview_cache("https://example.com", domain="example.com", title="Cached Title")
        service = PreviewService(storage)

        started = []
        monkeypatch.setattr(service._pool, "start", lambda task: started.append(task))

        result = service.request(1, "https://example.com", is_youtube=False)

        assert result is not None
        assert result["title"] == "Cached Title"
        assert started == []  # no background fetch needed on a cache hit


class TestRequestCacheMiss:
    def test_cache_miss_returns_none_and_schedules_fetch(self, storage, monkeypatch):
        service = PreviewService(storage)
        started = []
        monkeypatch.setattr(service._pool, "start", lambda task: started.append(task))

        result = service.request(1, "https://not-cached.example.com", is_youtube=False)

        assert result is None
        assert len(started) == 1
        assert "https://not-cached.example.com" in service._inflight

    def test_duplicate_request_for_same_url_does_not_schedule_twice(self, storage, monkeypatch):
        service = PreviewService(storage)
        started = []
        monkeypatch.setattr(service._pool, "start", lambda task: started.append(task))

        service.request(1, "https://dup.example.com", is_youtube=False)
        service.request(2, "https://dup.example.com", is_youtube=False)

        assert len(started) == 1

    def test_different_urls_both_get_scheduled(self, storage, monkeypatch):
        service = PreviewService(storage)
        started = []
        monkeypatch.setattr(service._pool, "start", lambda task: started.append(task))

        service.request(1, "https://one.example.com", is_youtube=False)
        service.request(2, "https://two.example.com", is_youtube=False)

        assert len(started) == 2


class TestInflightCleanup:
    def test_task_completion_clears_inflight_for_its_url(self, storage):
        service = PreviewService(storage)
        service._inflight.add("https://example.com")

        service._on_task_finished(1, "https://example.com", {"domain": "example.com"})

        assert "https://example.com" not in service._inflight

    def test_completion_emits_preview_ready_with_item_id_and_data(self, storage):
        service = PreviewService(storage)
        received = []
        service.preview_ready.connect(lambda item_id, data: received.append((item_id, data)))

        service._on_task_finished(42, "https://example.com", {"title": "X"})

        assert received == [(42, {"title": "X"})]

    def test_failed_fetch_still_clears_inflight_allowing_future_retry(self, storage):
        """The bug this test pins down: an earlier version of
        _on_task_finished took no url parameter and could never remove the
        entry from _inflight, permanently blocking any retry of a URL after
        its first (possibly failed) attempt."""
        service = PreviewService(storage)
        service._inflight.add("https://flaky.example.com")

        service._on_task_finished(1, "https://flaky.example.com", {})  # empty dict: a failed fetch

        assert "https://flaky.example.com" not in service._inflight


# -- Real end-to-end fetch through the actual thread pool -----------------

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = b"<html><head><title>Real Fetch</title></head></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def local_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=2)


class TestEndToEndThroughRealThreadPool:
    def test_background_fetch_reaches_cache_and_fires_signal(self, file_storage, local_server, monkeypatch, qt_app):
        # file_storage, not storage: the background task opens its OWN
        # connection to the same file (see _FetchTask.run()) -- ':memory:'
        # connections can't be shared across connections/threads at all, so
        # this specific test needs the real-file fixture to observe the
        # write from the main thread afterward.
        monkeypatch.setattr(web_preview, "_resolve_safe_ip", lambda host, port: "127.0.0.1")
        url = f"http://127.0.0.1:{local_server.server_port}/page.html"

        service = PreviewService(file_storage)
        received = []
        service.preview_ready.connect(lambda item_id, data: received.append((item_id, data)))

        result = service.request(99, url, is_youtube=False)
        assert result is None  # cache miss, fetch scheduled

        QThreadPool.globalInstance().waitForDone(5000)
        for _ in range(50):
            qt_app.processEvents()
            if received:
                break

        assert len(received) == 1
        item_id, data = received[0]
        assert item_id == 99
        assert data["title"] == "Real Fetch"
        # And the cache now has it, independent of the signal path.
        assert file_storage.get_preview_cache(url)["title"] == "Real Fetch"
        assert url not in service._inflight
