"""Regression tests for the 2026-11 audit fixes ported from the Windows build."""
import sys

import pytest

from nexus_linux import styles, thumbnails, web_preview
from nexus_linux.paste_service import paste_failure_message


# -- link previews: SSRF and one-time links ---------------------------------

@pytest.mark.parametrize("ip, safe", [
    ("8.8.8.8", True), ("100.64.1.1", False), ("10.0.0.1", False), ("127.0.0.1", False),
    ("::ffff:127.0.0.1", False), ("169.254.169.254", False), ("2606:4700::1111", True),
])
def test_only_global_addresses_are_fetched(ip, safe):
    assert web_preview._is_safe_ip(ip) is safe


@pytest.mark.parametrize("url, sensitive", [
    ("https://example.com/article?id=3", False),
    ("https://app.example.com/reset-password?token=abc", True),
    ("https://example.com/download?X-Amz-Signature=1", True),
    ("https://user:pw@example.com/", True),
    ("https://www.youtube.com/watch?v=abc", False),
])
def test_sensitive_links_are_detected(url, sensitive):
    assert web_preview.is_sensitive_url(url) is sensitive


def test_sensitive_link_preview_makes_no_request(monkeypatch):
    monkeypatch.setattr(web_preview, "fetch_url", lambda url: pytest.fail("network used"))
    preview = web_preview.fetch_preview("https://example.com/login?code=123", is_youtube=False)
    assert preview.domain == "example.com" and preview.title is None


def test_network_errors_and_downgrades_become_preview_errors(monkeypatch):
    def boom(url):
        raise TimeoutError("timed out")
    monkeypatch.setattr(web_preview, "_fetch_once", boom)
    with pytest.raises(web_preview.PreviewError):
        web_preview.fetch_url("https://example.com/")

    def redirect(url):
        raise web_preview._Redirect("http://example.com/")
    monkeypatch.setattr(web_preview, "_fetch_once", redirect)
    with pytest.raises(web_preview.PreviewError):
        web_preview.fetch_url("https://example.com/")


def test_previews_setting_off_means_no_request(storage):
    from nexus_linux.preview_service import PreviewService
    service = PreviewService(storage)
    service.network_enabled = False
    assert service.request(1, "https://example.com/", False) is None and not service._inflight


# -- storage ----------------------------------------------------------------

def test_fts_search_is_literal_and_tracks_updates_and_deletes(storage):
    assert storage._fts
    a = storage.capture(kind="text", text_content="reunión con el equipo de diseño")
    b = storage.capture(kind="text", text_content="ore_code helper")
    storage.capture(kind="text", text_content="oreXcode distractor")
    ids = lambda q, **kw: {i.id for i in storage.recent(query=q, **kw)}
    assert ids("reunion") == {a.id}            # accents folded, as before
    assert ids("ore_c") == {b.id}              # '_' is a character, not a LIKE wildcard
    assert ids("helper", kind="url") == set()  # the kind filter still applies
    storage.enrich_search_text(a.id, "Planeación Q4")
    assert ids("planeacion") == {a.id}
    storage.delete(a.id)
    assert ids("reunion") == set()
    assert b.id in ids("o")                    # < 3 chars: LIKE path


def test_fts_unparsable_query_falls_back_to_like(storage):
    item = storage.capture(kind="text", text_content='comillas "raras" y \x01control')
    assert item.id in {i.id for i in storage.recent(query='"raras" y \x01')}


def test_listing_ties_are_ordered_newest_insert_first(storage, monkeypatch):
    import nexus_linux.storage as module
    monkeypatch.setattr(module.time, "time", lambda: 1000.0)   # same timestamp for all (coarse clocks)
    first = storage.capture(kind="text", text_content="primero")
    second = storage.capture(kind="text", text_content="segundo")
    assert [i.id for i in storage.recent()][:2] == [second.id, first.id]


def test_listing_does_not_load_image_blobs_until_needed(storage):
    image = storage.capture(kind="image", image_data=b"\x89PNG-fake-bytes")
    listed = next(i for i in storage.recent() if i.id == image.id)
    assert listed.__dict__["_image_data"] is not None and not isinstance(listed.__dict__["_image_data"], bytes)
    assert listed.image_data == b"\x89PNG-fake-bytes"      # loaded on first access


def test_cached_thumbnail_never_reads_the_blob(qapp, storage, monkeypatch):
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage
    picture = QImage(8, 8, QImage.Format.Format_ARGB32); picture.fill(0xFF336699)
    buffer = QBuffer(); buffer.open(QIODevice.OpenModeFlag.WriteOnly); picture.save(buffer, "PNG")
    stored = storage.capture(kind="image", image_data=bytes(buffer.data()))
    first = next(i for i in storage.recent() if i.id == stored.id)
    assert thumbnails.rounded_rect_for(first, 20, 20, 4) is not None
    again = next(i for i in storage.recent() if i.id == stored.id)
    monkeypatch.setattr(storage, "_load_image_data", lambda item_id: pytest.fail("blob read for a cached thumbnail"))
    again._loader = storage._load_image_data
    assert thumbnails.rounded_rect_for(again, 20, 20, 4) is not None


def test_secure_delete_is_enabled_and_bloated_file_is_compacted(file_storage, monkeypatch):
    storage = file_storage
    assert storage._conn.execute("PRAGMA secure_delete").fetchone()[0] == 1
    for n in range(40):
        storage.capture(kind="text", text_content=("x" * 50_000) + str(n))
    storage._conn.execute("DELETE FROM clip_items")
    storage._conn.commit()
    monkeypatch.setattr(type(storage), "COMPACT_MIN_FREE_BYTES", 1)
    assert storage.compact_if_bloated()
    assert storage._conn.execute("PRAGMA freelist_count").fetchone()[0] == 0


# -- UI text and colour -------------------------------------------------------

def _luminance(value):
    from PySide6.QtGui import QColor
    color = styles.colour(value) if hasattr(styles, "colour") else QColor(value)
    channels = [x / 255 for x in (color.red(), color.green(), color.blue())]
    r, g, b = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in channels]
    return .2126 * r + .7152 * g + .0722 * b, color.alphaF()


def _over(ink, background):
    """Composite a translucent ink over an opaque background, then measure."""
    from PySide6.QtGui import QColor
    fg = styles.colour(ink) if hasattr(styles, "colour") else QColor(ink)
    bg = styles.colour(background) if hasattr(styles, "colour") else QColor(background)
    a = fg.alphaF()
    mixed = QColor(round(fg.red() * a + bg.red() * (1 - a)), round(fg.green() * a + bg.green() * (1 - a)),
                   round(fg.blue() * a + bg.blue() * (1 - a)))
    return mixed.name(), bg.name()


@pytest.mark.parametrize("key", sorted(styles.PRESETS))
def test_secondary_text_colours_are_readable_in_every_theme(qapp, key):
    if key == "liquid_glass" and sys.platform == "win32":
        pytest.skip("Windows Liquid Glass is painted from windows_design tokens over Acrylic, not these")
    theme = styles.PRESETS[key]
    for ink in (theme.text_tertiary, theme.danger):
        for background in (theme.bg, theme.card_bg):
            fg, bg = _over(ink, background)
            (lf, _), (lb, _) = _luminance(fg), _luminance(bg)
            ratio = (max(lf, lb) + .05) / (min(lf, lb) + .05)
            assert ratio >= 4.5, (key, ink, background, round(ratio, 2))


def test_stack_order_uses_plain_words(qapp, storage, paste_service):
    from nexus_linux.stack import ClipStack
    from nexus_linux.stack_window_linux import StackWindow
    window = StackWindow(ClipStack(storage, paste_service))
    assert window._order_btn.text() == "En orden"
    window._toggle_order()
    assert window._order_btn.text() == "Inverso" and "LIFO" not in window._order_btn.toolTip()
    window.close()


@pytest.mark.parametrize("reason, expected", [
    ("ydotool no está instalado", "Falta ydotool"),
    ("No se identificó ventana destino; usa Ctrl+V manualmente", "ya no está disponible"),
    ("ydotool falló: Command '['ydotool']' returned non-zero exit status 1.", "No se pudo escribir"),
])
def test_paste_failure_notice_says_what_to_do_without_technical_text(reason, expected):
    message = paste_failure_message(reason)
    assert expected in message and "Ctrl+V" in message and "exit status" not in message
