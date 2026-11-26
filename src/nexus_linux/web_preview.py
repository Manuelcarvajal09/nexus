"""Fetches link previews (title, domain, favicon, Open Graph image, short
description) and YouTube oEmbed metadata. Everything in this module is
synchronous, blocking, network-touching code -- it exists to be called from
a background thread (see preview_service.py), NEVER from the Qt main
thread or from clipboard_monitor.py's capture path. Capture must stay
instant regardless of network conditions; this module is where "instant"
would break if it ran anywhere else.

Security posture (all of this is load-bearing, not decorative):
- http/https only. No file://, no ftp://, no data:, nothing else.
- DNS resolved and validated BEFORE connecting; the connection is then
  pinned to that specific validated IP (via _PinnedHTTPConnection /
  _PinnedHTTPSConnection below) instead of letting the stdlib re-resolve
  the hostname at connect time -- closes the classic SSRF-via-DNS-rebinding
  TOCTOU gap where a hostname resolves to a safe IP during the check and a
  private one a moment later.
- Every hop of a redirect chain is validated the same way (redirects are
  followed manually, never automatically), capped at MAX_REDIRECTS.
- Private/loopback/link-local/multicast/reserved/unspecified IPs are
  rejected -- this is what stops "copy a URL pointing at localhost or an
  internal service" from turning Nexus into an SSRF proxy.
- Strict connect + total timeouts, a hard response-size cap enforced while
  streaming (not just trusting Content-Length), and Content-Type
  allowlisting (text/html for the page itself, image/* for favicon/OG
  images).
- No JavaScript execution of any kind -- this only ever parses HTML tags
  with the stdlib HTMLParser, never renders or evaluates anything."""

import ipaddress
import json
import logging
import socket
import ssl
import time
from http.client import HTTPException
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import parse_qsl, quote, urljoin, urlparse

log = logging.getLogger("nexus.preview")

CONNECT_TIMEOUT_S = 3.0
TOTAL_TIMEOUT_S = 6.0
MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2MB -- generous for HTML/favicon/OG image, not for "download a video"
MAX_HTML_SCAN_BYTES = 300_000  # only need the <head>; never buffer a whole large page just to find OG tags
USER_AGENT = "Mozilla/5.0 (compatible; NexusLinuxPreview/1.0; +local-clipboard-manager)"


class PreviewError(Exception):
    pass


@dataclass
class LinkPreview:
    url: str
    domain: str
    title: str | None = None
    description: str | None = None
    favicon: bytes | None = None
    og_image: bytes | None = None
    youtube: bool = False
    channel: str | None = None


# -- SSRF-safe fetching --------------------------------------------------

def _validate_scheme(url: str) -> str:
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise PreviewError(f"esquema no permitido: {scheme!r}")
    return scheme


def _is_safe_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # is_global also rejects shared/CGNAT space (100.64.0.0/10, e.g. Tailscale)
    # and 6to4/ORCHID ranges that the is_private test let through.
    return ip.is_global and not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def _resolve_safe_ip(hostname: str, port: int) -> str:
    """Resolves hostname and returns ONE validated, safe IP to connect to.
    Rejects the hostname entirely if every resolved address is unsafe (a
    hostname that resolves to both a public and a private IP is treated as
    unsafe -- picking "whichever looked safe" is exactly the kind of
    resolver-dependent behavior DNS-rebinding attacks exploit)."""
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise PreviewError(f"DNS no resuelto para {hostname!r}: {exc}") from exc
    candidates = {info[4][0] for info in infos}
    if not candidates:
        raise PreviewError(f"sin direcciones para {hostname!r}")
    unsafe = {ip for ip in candidates if not _is_safe_ip(ip)}
    if unsafe:
        raise PreviewError(f"{hostname!r} resuelve a una IP no permitida ({unsafe}); rechazado")
    return next(iter(candidates))


class _PinnedHTTPConnection(HTTPConnection):
    """Connects to a pre-validated IP instead of re-resolving self.host --
    the actual fix for DNS-rebinding TOCTOU (see module docstring)."""

    def __init__(self, host, pinned_ip, port=80, timeout=CONNECT_TIMEOUT_S):
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self):
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)


class _PinnedHTTPSConnection(HTTPSConnection):
    def __init__(self, host, pinned_ip, port=443, timeout=CONNECT_TIMEOUT_S):
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self):
        raw = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        context = ssl.create_default_context()
        # server_hostname=self.host (not the pinned IP) keeps SNI/certificate
        # validation checking the real hostname, so this doesn't trade
        # SSRF-safety for silently accepting a mismatched/invalid cert.
        self.sock = context.wrap_socket(raw, server_hostname=self.host)


def _fetch_once(url: str) -> tuple[str, bytes]:
    """One hop, no redirect following. Returns (content_type, body). Raises
    PreviewError for anything unsafe or out of policy."""
    _validate_scheme(url)
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise PreviewError("URL sin host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    pinned_ip = _resolve_safe_ip(host, port)

    conn_cls = _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
    conn = conn_cls(host, pinned_ip, port, timeout=CONNECT_TIMEOUT_S)
    try:
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        conn.request("GET", path, headers={
            "User-Agent": USER_AGENT, "Accept": "text/html,image/*;q=0.8,*/*;q=0.1",
        })
        response = conn.getresponse()

        if response.status in (301, 302, 303, 307, 308):
            location = response.getheader("Location")
            response.read(0)
            if not location:
                raise PreviewError("redirect sin Location")
            raise _Redirect(urljoin(url, location))

        if response.status != 200:
            raise PreviewError(f"HTTP {response.status}")

        content_type = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
        content_length = response.getheader("Content-Length")
        if content_length and int(content_length) > MAX_RESPONSE_BYTES:
            raise PreviewError("respuesta demasiado grande (Content-Length)")

        chunks = []
        total = 0
        deadline = time.monotonic() + TOTAL_TIMEOUT_S
        while True:
            if time.monotonic() > deadline:
                raise PreviewError("respuesta demasiado lenta")
            chunk = response.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise PreviewError("respuesta demasiado grande (streaming)")
            chunks.append(chunk)
            if content_type == "text/html" and total >= MAX_HTML_SCAN_BYTES:
                break  # enough to find <head> OG tags; stop reading the rest
        return content_type, b"".join(chunks)
    finally:
        conn.close()


class _Redirect(Exception):
    def __init__(self, location: str):
        self.location = location


def fetch_url(url: str) -> tuple[str, bytes]:
    """Follows up to MAX_REDIRECTS redirects, re-validating scheme/DNS/IP at
    every hop -- a redirect to a private IP is rejected exactly like a
    direct request to one would be."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        try:
            return _fetch_once(current)
        except _Redirect as redirect:
            if urlparse(current).scheme == "https" and urlparse(redirect.location).scheme != "https":
                raise PreviewError("redirect de https a http rechazado")
            current = redirect.location
        except PreviewError:
            raise
        except (OSError, ValueError, HTTPException) as exc:
            # Timeouts, resets, TLS errors, bad headers: a normal "no preview",
            # recorded as failed instead of being retried on every refresh.
            raise PreviewError(f"error de red: {type(exc).__name__}") from exc
    raise PreviewError("demasiados redirects")


# Query keys that usually mark a one-time or secret link (password reset, magic
# login, signed download, OAuth code). Fetching such a link for a preview can
# consume it or leak it to the server's logs, so it is never requested.
SENSITIVE_QUERY_KEYS = frozenset({
    "token", "access_token", "id_token", "code", "key", "apikey", "api_key", "secret",
    "password", "pwd", "pass", "otp", "auth", "session", "sid", "signature", "sig",
    "x-amz-signature", "x-goog-signature", "reset", "magic", "invite", "ticket",
})
SENSITIVE_PATH_WORDS = ("reset", "magic", "verify", "confirm", "unsubscribe", "activate", "login", "signin", "oauth")


def is_sensitive_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        return True
    keys = {key.lower() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    if keys & SENSITIVE_QUERY_KEYS:
        return True
    path = parsed.path.lower()
    return any(word in path for word in SENSITIVE_PATH_WORDS)


# -- HTML metadata extraction ---------------------------------------------

class _HeadMetaParser(HTMLParser):
    """Stdlib-only, no JS execution, no external parser dependency. Stops
    itself once </head> is seen -- OG tags and <title> always live there."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.og: dict[str, str] = {}
        self.icon_href: str | None = None
        self._in_title = False
        self._done = False

    def handle_starttag(self, tag, attrs):
        if self._done:
            return
        attrs_d = dict(attrs)
        if tag == "head":
            return
        if tag == "title" and self.title is None:
            self._in_title = True
        elif tag == "meta":
            prop = attrs_d.get("property") or attrs_d.get("name") or ""
            if prop.startswith("og:") and attrs_d.get("content"):
                self.og[prop] = attrs_d["content"]
        elif tag == "link" and "icon" in (attrs_d.get("rel") or "").lower():
            if attrs_d.get("href"):
                self.icon_href = attrs_d["href"]

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "head":
            self._done = True

    def handle_data(self, data):
        if self._in_title and self.title is None:
            self.title = data.strip()


def _extract_domain(url: str) -> str:
    host = urlparse(url).hostname or url
    return host[4:] if host.startswith("www.") else host


def fetch_link_preview(url: str) -> LinkPreview:
    domain = _extract_domain(url)
    preview = LinkPreview(url=url, domain=domain)

    try:
        content_type, body = fetch_url(url)
    except PreviewError as exc:
        log.info("Preview no disponible para %s: %s", domain, exc)
        return preview  # fallback: domain-only, exactly what the UI shows on failure

    if not content_type.startswith("text/html"):
        return preview

    try:
        html_text = body.decode("utf-8", errors="replace")
    except Exception:
        return preview

    parser = _HeadMetaParser()
    try:
        parser.feed(html_text)
    except Exception:
        log.info("HTML de %s no se pudo parsear para preview", domain)
        return preview

    preview.title = parser.og.get("og:title") or parser.title
    preview.description = parser.og.get("og:description")

    og_image_url = parser.og.get("og:image")
    if og_image_url:
        preview.og_image = _fetch_image_safely(urljoin(url, og_image_url))

    favicon_url = urljoin(url, parser.icon_href) if parser.icon_href else urljoin(url, "/favicon.ico")
    preview.favicon = _fetch_image_safely(favicon_url)

    return preview


def _fetch_image_safely(image_url: str) -> bytes | None:
    try:
        content_type, body = fetch_url(image_url)
    except PreviewError:
        return None
    # Only the common raster formats: no SVG/TIFF/other decoders fed with remote bytes.
    if content_type not in ("image/png", "image/jpeg", "image/gif", "image/webp", "image/x-icon",
                            "image/vnd.microsoft.icon"):
        return None
    return body


# -- YouTube oEmbed (no API key required) ---------------------------------

YOUTUBE_OEMBED_ENDPOINT = "https://www.youtube.com/oembed"


def fetch_youtube_preview(url: str) -> LinkPreview:
    """oEmbed is YouTube's own public, unauthenticated metadata endpoint --
    no API key, no quota, exactly the 'robust alternative to a keyed API'
    the spec asked for. Still goes through the same SSRF-safe fetch_url()
    as everything else -- YouTube's own domain is not special-cased or
    exempted from the safety checks."""
    domain = _extract_domain(url)
    preview = LinkPreview(url=url, domain=domain, youtube=True)

    oembed_url = f"{YOUTUBE_OEMBED_ENDPOINT}?url={quote(url, safe='')}&format=json"
    try:
        content_type, body = fetch_url(oembed_url)
    except PreviewError as exc:
        log.info("oEmbed no disponible para %s: %s", url, exc)
        return preview

    if "json" not in content_type:
        return preview
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return preview

    preview.title = data.get("title")
    preview.channel = data.get("author_name")
    thumbnail_url = data.get("thumbnail_url")
    if thumbnail_url:
        preview.og_image = _fetch_image_safely(thumbnail_url)

    return preview


def fetch_preview(url: str, *, is_youtube: bool) -> LinkPreview:
    """Single entry point used by preview_service.py -- picks the right
    strategy so callers don't need to know about oEmbed vs. OG scraping."""
    if is_sensitive_url(url):
        # Domain-only card, no request at all (see SENSITIVE_QUERY_KEYS).
        return LinkPreview(url=url, domain=_extract_domain(url), youtube=is_youtube)
    if is_youtube:
        return fetch_youtube_preview(url)
    return fetch_link_preview(url)
