"""Official Nexus icon for Windows: the "N" in a rounded square, outlined in the
light blue of the Liquid Glass theme (the same glyph as the one in the panel
header, see windows_design.icon("brand")).

It is drawn as a vector at every size, so the window, taskbar and tray icons are
always the same design. The multi-resolution .ico used by shortcuts is generated
from this module (tools/build_windows_icons.py) and kept in assets/icons.

On small sizes the stroke is thickened so the outline stays crisp, and the glyph
sits on the dark panel colour so it is legible on light and dark taskbars alike.
"""
from __future__ import annotations

import ctypes
import logging
import struct
import sys
from functools import lru_cache

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap

BRAND_ACCENT = "#B3CFF6"   # Liquid Glass accent: the light-blue outline
BRAND_TILE = "#171C25"     # Liquid Glass panel colour
BRAND_EDGE = "#3D4859"
PAUSED_DOT = "#F5B84B"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
APP_USER_MODEL_ID = "StoreM.Nexus"
SANDBOX_APP_USER_MODEL_ID = "StoreM.Nexus.Sandbox"


def render_brand(size: int, paused: bool = False) -> QImage:
    """The Nexus mark at `size` x `size` pixels (transparent corners)."""
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    tile = QRectF(0.5, 0.5, size - 1, size - 1)
    radius = size * 0.225
    painter.setBrush(QColor(BRAND_TILE))
    painter.setPen(QPen(QColor(BRAND_EDGE), max(1.0, size / 64)))
    painter.drawRoundedRect(tile, radius, radius)
    # Same geometry as windows_design.icon("brand"): a 20-unit box with the
    # rounded square (2..18) and the N inside it.
    scale = size * 0.60 / 16
    painter.translate(size / 2, size / 2)
    painter.scale(scale, scale)
    painter.translate(-10, -10)
    units = max(1.5, 1.7 / scale)  # never thinner than ~1.7 px on screen
    painter.setPen(QPen(QColor(BRAND_ACCENT), units, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(2, 2, 16, 16), 5, 5)
    painter.drawLine(QPointF(7, 13), QPointF(7, 7))
    painter.drawLine(QPointF(7, 7), QPointF(13, 13))
    painter.drawLine(QPointF(13, 13), QPointF(13, 7))
    painter.resetTransform()
    if paused:
        dot = size * 0.17
        center = QPointF(size - dot - size * 0.04, size - dot - size * 0.04)
        painter.setPen(QPen(QColor(BRAND_TILE), max(1.0, size / 24)))
        painter.setBrush(QColor(PAUSED_DOT))
        painter.drawEllipse(center, dot, dot)
    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def build_ico_bytes(sizes=SIZES) -> bytes:
    """A multi-resolution .ico whose entries are PNG images (Windows Vista+)."""
    images = [png_bytes(render_brand(size)) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, payload = b"", b""
    for size, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset + len(payload))
        payload += data
    return header + entries + payload


@lru_cache(maxsize=2)
def brand_icon(paused: bool = False) -> QIcon:
    icon = QIcon()
    for size in SIZES:
        icon.addPixmap(QPixmap.fromImage(render_brand(size, paused)))
    return icon


def app_icon() -> QIcon:
    return brand_icon(False)


def tray_icon(paused: bool = False) -> QIcon:
    return brand_icon(bool(paused))


def set_app_user_model_id(app_id: str | None = None) -> bool:
    """Give the process its own taskbar identity (and not python's), so the
    taskbar shows the Nexus icon and a pinned Nexus keeps it. Must run before
    any window exists. Harmless (returns False) off Windows or on failure."""
    if sys.platform != "win32":
        return False
    if app_id is None:
        from . import windows_sandbox
        app_id = SANDBOX_APP_USER_MODEL_ID if windows_sandbox.active() else APP_USER_MODEL_ID
    try:
        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
    except (AttributeError, OSError):
        logging.getLogger("nexus.icons").warning("AppUserModelID unavailable")
        return False
    return result == 0
