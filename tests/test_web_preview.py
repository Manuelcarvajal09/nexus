"""web_preview.py tests, split in two layers on purpose:

1. SSRF validation logic (_is_safe_ip, _validate_scheme) tested directly --
   this is the actual security boundary, and it must be correct on its own
   terms, not just "assumed correct because fetch_url() behaved once".

2. HTTP mechanics (redirects, size caps, content-type gating) tested
   against a real local http.server instance, with _resolve_safe_ip
   monkeypatched to allow 127.0.0.1 for THESE TESTS ONLY -- the local test
   server is loopback, which _is_safe_ip correctly rejects in production
   (that's not a bug, it's the point), so testing the HTTP-handling code
   path requires deliberately bypassing just the IP-safety gate, not the
   whole module. The real _is_safe_ip/_resolve_safe_ip are never modified;
   only this test file's local view of them is patched, per-test."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from nexus_linux import web_preview
from nexus_linux.web_preview import (
    PreviewError,
    _extract_domain,
    _HeadMetaParser,
    _is_safe_ip,
    _validate_scheme,
    fetch_link_preview,
    fetch_url,
    fetch_youtube_preview,
)


class TestIsSafeIP:
    def test_public_ipv4_is_safe(self):
        assert _is_safe_ip("8.8.8.8") is True

    def test_loopback_is_unsafe(self):
        assert _is_safe_ip("127.0.0.1") is False

    def test_ipv6_loopback_is_unsafe(self):
        assert _is_safe_ip("::1") is False

    def test_private_10_range_is_unsafe(self):
        assert _is_safe_ip("10.0.0.5") is False

    def test_private_192_168_range_is_unsafe(self):
        assert _is_safe_ip("192.168.1.1") is False

    def test_private_172_16_range_is_unsafe(self):
        assert _is_safe_ip("172.16.0.1") is False

    def test_link_local_is_unsafe(self):
        assert _is_safe_ip("169.254.1.1") is False

    def test_multicast_is_unsafe(self):
        assert _is_safe_ip("224.0.0.1") is False

    def test_unspecified_is_unsafe(self):
        assert _is_safe_ip("0.0.0.0") is False

    def test_ipv6_unique_local_is_unsafe(self):
        assert _is_safe_ip("fc00::1") is False

    def test_malformed_ip_is_unsafe(self):
        assert _is_safe_ip("not-an-ip") is False

    def test_empty_string_is_unsafe(self):
        assert _is_safe_ip("") is False


class TestValidateScheme:
    def test_http_allowed(self):
        assert _validate_scheme("http://example.com") == "http"

    def test_https_allowed(self):
        assert _validate_scheme("https://example.com") == "https"

    def test_file_scheme_rejected(self):
        with pytest.raises(PreviewError):
            _validate_scheme("file:///etc/passwd")

    def test_ftp_scheme_rejected(self):
        with pytest.raises(PreviewError):
            _validate_scheme("ftp://example.com/file")

    def test_data_scheme_rejected(self):
        with pytest.raises(PreviewError):
            _validate_scheme("data:text/html,<script>alert(1)</script>")

    def test_javascript_scheme_rejected(self):
        with pytest.raises(PreviewError):
            _validate_scheme("javascript:alert(1)")


class TestExtractDomain:
    def test_strips_www(self):
        assert _extract_domain("https://www.example.com/page") == "example.com"

    def test_keeps_subdomain_that_is_not_www(self):
        assert _extract_domain("https://blog.example.com/post") == "blog.example.com"

    def test_no_scheme_host_fallback(self):
        # Malformed input shouldn't crash -- worst case, echoes the input.
        assert _extract_domain("not a url") == "not a url"


class TestHeadMetaParser:
    def test_extracts_og_title_and_image(self):
        html = """
        <html><head>
        <title>Fallback Title</title>
        <meta property="og:title" content="Real Title">
        <meta property="og:image" content="https://example.com/img.png">
        <meta property="og:description" content="A description">
        <link rel="icon" href="/favicon.ico">
        </head><body>ignored</body></html>
        """
        parser = _HeadMetaParser()
        parser.feed(html)
        assert parser.og["og:title"] == "Real Title"
        assert parser.og["og:image"] == "https://example.com/img.png"
        assert parser.og["og:description"] == "A description"
        assert parser.icon_href == "/favicon.ico"
        assert parser.title == "Fallback Title"

    def test_falls_back_to_title_tag_when_no_og_title(self):
        html = "<html><head><title>Plain Title</title></head></html>"
        parser = _HeadMetaParser()
        parser.feed(html)
        assert parser.title == "Plain Title"
        assert "og:title" not in parser.og

    def test_ignores_content_after_head(self):
        html = """
        <html><head><title>Head Title</title></head>
        <body><meta property="og:title" content="Should not be captured"></body>
        </html>
        """
        parser = _HeadMetaParser()
        parser.feed(html)
        assert "og:title" not in parser.og

    def test_malformed_html_does_not_raise(self):
        parser = _HeadMetaParser()
        parser.feed("<html><head><title>Unclosed")  # no closing tags at all


# -- Local server fixture for HTTP mechanics tests -----------------------

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # silence test output

    def do_GET(self):
        if self.path == "/ok.html":
            body = (
                b"<html><head><title>Test Page</title>"
                b'<meta property="og:title" content="OG Title"></head></html>'
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/redirect-once":
            self.send_response(302)
            self.send_header("Location", "/ok.html")
            self.end_headers()
        elif self.path == "/redirect-loop":
            self.send_response(302)
            self.send_header("Location", "/redirect-loop")
            self.end_headers()
        elif self.path == "/huge":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(50 * 1024 * 1024))
            self.end_headers()
            # Body deliberately not fully sent -- the Content-Length check
            # alone must reject this before any streaming happens.
        elif self.path == "/wrong-type.json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = b'{"not": "html"}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/oembed"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = json.dumps({
                "title": "A YouTube Video", "author_name": "Some Channel",
            }).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def local_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=2)


@pytest.fixture
def allow_loopback(monkeypatch):
    """See module docstring: bypasses only the IP-safety gate, for these
    tests only, so the real HTTP-handling code can be exercised against a
    local test server. _is_safe_ip itself is never touched."""
    monkeypatch.setattr(web_preview, "_resolve_safe_ip", lambda host, port: "127.0.0.1")


class TestFetchMechanics:
    def test_fetches_html_successfully(self, local_server, allow_loopback):
        url = f"http://127.0.0.1:{local_server.server_port}/ok.html"
        content_type, body = fetch_url(url)
        assert content_type == "text/html"
        assert b"OG Title" in body

    def test_follows_a_single_redirect(self, local_server, allow_loopback):
        url = f"http://127.0.0.1:{local_server.server_port}/redirect-once"
        content_type, body = fetch_url(url)
        assert b"OG Title" in body

    def test_redirect_loop_is_capped_not_infinite(self, local_server, allow_loopback):
        url = f"http://127.0.0.1:{local_server.server_port}/redirect-loop"
        with pytest.raises(PreviewError, match="redirects"):
            fetch_url(url)

    def test_oversized_content_length_rejected_before_streaming(self, local_server, allow_loopback):
        url = f"http://127.0.0.1:{local_server.server_port}/huge"
        with pytest.raises(PreviewError, match="grande"):
            fetch_url(url)

    def test_404_raises_preview_error(self, local_server, allow_loopback):
        url = f"http://127.0.0.1:{local_server.server_port}/does-not-exist"
        with pytest.raises(PreviewError):
            fetch_url(url)


class TestFetchLinkPreviewEndToEnd:
    def test_successful_preview_extracts_title(self, local_server, allow_loopback):
        url = f"http://127.0.0.1:{local_server.server_port}/ok.html"
        preview = fetch_link_preview(url)
        assert preview.title == "OG Title"
        assert preview.domain == "127.0.0.1"

    def test_wrong_content_type_still_returns_domain_fallback(self, local_server, allow_loopback):
        url = f"http://127.0.0.1:{local_server.server_port}/wrong-type.json"
        preview = fetch_link_preview(url)
        assert preview.title is None
        assert preview.domain == "127.0.0.1"

    def test_unreachable_host_degrades_to_domain_only_fallback(self):
        """The offline/network-failure case: must never raise out of
        fetch_link_preview -- callers (the background worker) always get
        a usable, if bare, LinkPreview."""
        preview = fetch_link_preview("http://127.0.0.1:1/nonexistent")  # port 1: nothing listens
        assert preview.title is None
        assert preview.og_image is None
        assert preview.domain == "127.0.0.1"


class TestFetchYoutubePreview:
    def test_oembed_success_extracts_title_and_channel(self, local_server, allow_loopback, monkeypatch):
        monkeypatch.setattr(
            web_preview, "YOUTUBE_OEMBED_ENDPOINT",
            f"http://127.0.0.1:{local_server.server_port}/oembed",
        )
        preview = fetch_youtube_preview("https://www.youtube.com/watch?v=abc123")
        assert preview.title == "A YouTube Video"
        assert preview.channel == "Some Channel"
        assert preview.youtube is True

    def test_oembed_failure_degrades_gracefully(self):
        preview = fetch_youtube_preview("https://www.youtube.com/watch?v=doesnotexist")
        # Real network call to a nonexistent oEmbed target may succeed or
        # fail depending on sandbox network policy -- either way this must
        # not raise, which is the actual property under test.
        assert preview.youtube is True
        assert preview.domain == "youtube.com"


class TestPreviewNeverRaisesOnBadInput:
    @pytest.mark.parametrize("bad_url", [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint SSRF classic
        "http://localhost/admin",
        "http://127.0.0.1:22/",
        "not-a-url-at-all",
    ])
    def test_dangerous_or_malformed_urls_degrade_to_fallback(self, bad_url):
        preview = fetch_link_preview(bad_url)
        assert preview.title is None
        assert preview.og_image is None
        assert preview.favicon is None
