"""Liquid Glass lab: three comparable prototypes of the Nexus panel, real DWM on screen.

A  Native Glass   - DWM Desktop Acrylic (live blur) + Qt tint, reflection and rim lights.
B  Optical Glass  - no Acrylic. The pixels behind the panel are captured BEFORE it is
                    shown, blurred and re-painted with an edge "lens" (content bunches up
                    at the borders), thickness shading and light-driven rim highlights.
C  Hybrid         - Acrylic for the panel (live), optical lens glass for the controls.

Isolation: own synthetic backdrop window, synthetic content, no database, no clipboard,
no hotkeys, no singleton, never touches a running Nexus. Only a screen region that is
covered by our own windows is captured, and only when no foreign window overlaps it.
The captured backdrop pixels stay in memory, are never logged and are discarded.

    python tools/windows_glass_lab.py OUT_DIR [--modes A,B,C] [--variants light,dark]
        [--phases white,black,photo,app] [--scale 1.0] [--bench]
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import statistics
import sys
import time
from ctypes import wintypes
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("out")
parser.add_argument("--modes", default="A,B,C")
parser.add_argument("--variants", default="light,dark")
parser.add_argument("--phases", default="white,black,photo,app")
parser.add_argument("--scale", default="1")
parser.add_argument("--bench", action="store_true")
parser.add_argument("--src", default=str(Path(__file__).resolve().parents[1] / "src"))
args = parser.parse_args()

os.environ["QT_QPA_PLATFORM"] = "windows"
os.environ["QT_SCALE_FACTOR"] = args.scale
sys.path.insert(0, args.src)

from PySide6.QtCore import QPointF, QRect, QRectF, Qt                           # noqa: E402
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath,  # noqa: E402
                           QPen, QRadialGradient)
from PySide6.QtTest import QTest                                                 # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget                              # noqa: E402

from nexus_linux import windows_design                                           # noqa: E402
from nexus_linux.windows_backdrop import (BackdropController, NativeBackend,     # noqa: E402
                                          configure_native_host, handle_native_host_event)

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [ENUMPROC, wintypes.LPARAM]
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

PANEL_W, PANEL_H = 620, 452
MARGIN = 56            # synthetic desktop visible around the panel (also the lens source margin)

TOKENS = {
    "dark": dict(text="#F6F8FC", sub="#C9D3E1", faint="#B7C2D3", accent="#B3CFF6", dark=True),
    "light": dict(text="#111827", sub="#344054", faint="#475467", accent="#2563C9", dark=False),
}


# --------------------------------------------------------------------------- image helpers
def _saturate(image: QImage, amount: float) -> QImage:
    out = QImage(image)
    for y in range(out.height()):
        for x in range(out.width()):
            c = out.pixelColor(x, y)
            h, s, v, a = c.getHsvF()
            if h < 0:
                continue
            out.setPixelColor(x, y, QColor.fromHsvF(h, min(1.0, s * amount), v, a))
    return out


def soften(image: QImage, divisor: int, saturation: float = 1.0) -> QImage:
    """Cheap, allocation-light blur: repeated halving, optional saturation lift, repeated doubling."""
    width, height = image.width(), image.height()
    tw, th = max(2, width // divisor), max(2, height // divisor)
    current = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    while current.width() > tw * 1.5 or current.height() > th * 1.5:
        current = current.scaled(max(tw, current.width() // 2), max(th, current.height() // 2),
                                 Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    if saturation != 1.0:
        current = _saturate(current, saturation)
    while current.width() < width or current.height() < height:
        current = current.scaled(min(width, current.width() * 2), min(height, current.height() * 2),
                                 Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return current


class Snapshot:
    """Backdrop pixels (device resolution) that cover the panel plus MARGIN on every side."""

    def __init__(self, image: QImage):
        self.rect = QRectF(-MARGIN, -MARGIN, PANEL_W + 2 * MARGIN, PANEL_H + 2 * MARGIN)
        raw = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        t = time.perf_counter()
        self.panel = soften(raw, 8, 1.35)       # the big plate: soft, a little more vivid
        self.control = soften(raw, 9, 1.15)     # controls show a lighter, slightly more vivid sample
        self.card = soften(raw, 11, 1.1)        # cards are frosted: content layer, quiet
        self.build_ms = (time.perf_counter() - t) * 1000

    def sample(self, rect: QRectF, which: str = "card", worst: str = "light") -> QColor:
        """Worst-case backdrop colour under a panel-coordinate rect (5x5 grid on the blurred plate).

        worst="light": the brightest sample (what light text on dark glass must survive);
        worst="dark": the darkest one (dark text on light glass)."""
        image = getattr(self, which)
        k = image.width() / self.rect.width()
        found = None
        for i in range(5):
            for j in range(5):
                x = (rect.left() + rect.width() * (i + .5) / 5 - self.rect.left()) * k
                y = (rect.top() + rect.height() * (j + .5) / 5 - self.rect.top()) * k
                c = image.pixelColor(max(0, min(image.width() - 1, int(x))), max(0, min(image.height() - 1, int(y))))
                if found is None or (_lum(c) > _lum(found)) == (worst == "light"):
                    found = c
        return found


def _lum(c: QColor) -> float:
    def f(v):
        v /= 255
        return v / 12.92 if v <= .03928 else ((v + .055) / 1.055) ** 2.4
    return .2126 * f(c.red()) + .7152 * f(c.green()) + .0722 * f(c.blue())


def contrast(a: QColor, b: QColor) -> float:
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + .05) / (min(la, lb) + .05)


def adaptive_alpha(backdrop: QColor, veil: QColor, text: QColor, target: float, base: int) -> int:
    """Smallest veil alpha (>= base) that keeps `text` at `target`:1 over this very backdrop.

    Only possible when the pixels behind are known (B, C). Acrylic (A) hides them, so A has to
    ship one static tint that is right for the worst backdrop.
    """
    for alpha in range(base, 256, 3):
        k = alpha / 255
        mixed = QColor(round(backdrop.red() * (1 - k) + veil.red() * k), round(backdrop.green() * (1 - k) + veil.green() * k),
                       round(backdrop.blue() * (1 - k) + veil.blue() * k))
        if contrast(text, mixed) >= target:
            return alpha
    return 255


def rr(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    radius = max(0.0, min(radius, rect.width() / 2, rect.height() / 2))
    path.addRoundedRect(rect, radius, radius)
    return path


def lens(p: QPainter, rect: QRectF, radius: float, image: QImage, snap: Snapshot,
         depth: float, width: float, bands: int = 10) -> None:
    """Edge refraction approximation.

    Windows gives no per-pixel access to the compositor, so the snapshot is sampled by
    concentric bands: each band is the snapshot scaled about the shape's centre so that,
    at its outer edge, the sample comes `d` pixels from beyond the border. `d` decays
    quadratically toward the interior, which bunches the surrounding content at the rim
    the way a thick lens does. Interior is the plain (blurred) backdrop.
    """
    centre = rect.center()
    half_w, half_h = rect.width() / 2, rect.height() / 2
    # Painted outside-in, each layer clipped to the whole remaining interior: the next,
    # smaller layer simply overwrites it, so no anti-aliased seams between bands.
    for i in range(bands + 1):
        t0 = width * i / bands
        shape = rr(rect.adjusted(t0, t0, -t0, -t0), radius - t0)
        d = depth * max(0.0, 1 - i / bands) ** 2
        sx, sy = half_w / (half_w + d), half_h / (half_h + d)
        p.save()
        p.setClipPath(shape)
        p.translate(centre); p.scale(sx, sy); p.translate(-centre)
        p.drawImage(snap.rect, image)
        p.restore()


def rim(p: QPainter, rect: QRectF, radius: float, light: float, tok: dict, strength: float = 1.0,
        accent: bool = False, dispersion: bool = False) -> None:
    """Thickness and light: bright edge facing the light, a weaker one opposite, dark inner line."""
    dark = tok["dark"]
    ux, uy = math.cos(math.radians(light)), -math.sin(math.radians(light))
    centre = rect.center()
    reach = abs(ux) * rect.width() / 2 + abs(uy) * rect.height() / 2
    a = QPointF(centre.x() + ux * reach, centre.y() + uy * reach)
    b = QPointF(centre.x() - ux * reach, centre.y() - uy * reach)
    hi = (.62 if dark else .98) * strength
    lo = (.26 if dark else .55) * strength
    g = QLinearGradient(a, b)
    g.setColorAt(0, QColor(255, 255, 255, int(255 * hi)))
    g.setColorAt(.34, QColor(255, 255, 255, int(255 * .10 * strength)))
    g.setColorAt(.66, QColor(255, 255, 255, int(255 * .08 * strength)))
    g.setColorAt(1, QColor(255, 255, 255, int(255 * lo)))
    path = rr(rect.adjusted(.5, .5, -.5, -.5), radius)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if dispersion:
        # chromatic fringe of a thick lens: a faint cool line on the lit side, a warm one opposite
        fringe = QLinearGradient(a, b)
        fringe.setColorAt(0, QColor(120, 200, 255, int(70 * strength))); fringe.setColorAt(.4, QColor(0, 0, 0, 0))
        fringe.setColorAt(.6, QColor(0, 0, 0, 0)); fringe.setColorAt(1, QColor(255, 130, 190, int(60 * strength)))
        p.setPen(QPen(QBrush(fringe), 1.0))
        p.drawPath(rr(rect.adjusted(1.6, 1.6, -1.6, -1.6), radius - 1.1))
    p.setPen(QPen(QBrush(g), 1.1))
    p.drawPath(path)
    # thickness: soft light collected along the lit edge, shade along the far one, inside the shape
    p.save()
    p.setClipPath(rr(rect, radius))
    inner = QLinearGradient(a, b)
    inner.setColorAt(0, QColor(255, 255, 255, int(255 * (.20 if dark else .55) * strength)))
    inner.setColorAt(.22, QColor(255, 255, 255, 0))
    inner.setColorAt(.78, QColor(0, 0, 0, 0))
    inner.setColorAt(1, QColor(0, 0, 0, int(255 * (.20 if dark else .07) * strength)))
    p.setPen(QPen(QBrush(inner), 5))
    p.drawPath(rr(rect.adjusted(2.5, 2.5, -2.5, -2.5), radius - 2.5))
    p.restore()
    if accent:
        c = QColor(tok["accent"]); c.setAlpha(150)
        p.setPen(QPen(c, 1.4)); p.drawPath(path)


def specular(p: QPainter, rect: QRectF, radius: float, light: float, tok: dict, strength: float = 1.0) -> None:
    ux, uy = math.cos(math.radians(light)), -math.sin(math.radians(light))
    centre = rect.center()
    focus = QPointF(centre.x() + ux * rect.width() * .30, centre.y() + uy * rect.height() * .62)
    reach = max(rect.width() * .55, rect.height() * .9)
    g = QRadialGradient(focus, reach)
    g.setColorAt(0, QColor(255, 255, 255, int(255 * (.16 if tok["dark"] else .46) * strength)))
    g.setColorAt(1, QColor(255, 255, 255, 0))
    p.save(); p.setClipPath(rr(rect, radius)); p.fillRect(rect, g); p.restore()


def shadow(p: QPainter, rect: QRectF, radius: float, dark: bool, size: float = 1.0) -> None:
    p.setBrush(Qt.BrushStyle.NoBrush)
    for width, alpha in ((16, 5), (11, 8), (6, 14), (3, 18)):
        p.setPen(QPen(QColor(0, 0, 0, int(alpha * (1.5 if dark else .8) * size)), width))
        p.drawPath(rr(rect.adjusted(0, 3, 0, 3), radius))


# --------------------------------------------------------------------------- the prototype panel
class LabPanel(QWidget):
    def __init__(self, mode: str, variant: str):
        super().__init__(None)
        configure_native_host(self)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setMouseTracking(True)
        self.mode, self.variant = mode, variant
        self.snap: Snapshot | None = None
        self.native = False
        self.light = 125.0           # degrees; 90 = from above, 180 = from the left
        self.resize(PANEL_W, PANEL_H)
        self._font = windows_design.font

    @property
    def tok(self):
        return TOKENS[self.variant]

    def set_native(self, enabled: bool) -> None:
        if enabled != self.native:
            self.native = enabled
            self.update()

    def nativeEvent(self, event_type, message):
        result = handle_native_host_event(self, event_type, message)
        return result if result is not None else super().nativeEvent(event_type, message)

    def mouseMoveEvent(self, event):
        if not windows_design.motion_enabled():
            return
        c = self.rect().center()
        angle = math.degrees(math.atan2(-(event.position().y() - c.y()), event.position().x() - c.x()))
        # light sits opposite the pointer: it reads as a lamp that follows the hand
        self.light = (angle + 180) % 360
        self.update()

    # ---- layout -------------------------------------------------------
    HEADER = QRectF(24, 20, 572, 40)
    TABS = QRectF(24, 76, 296, 42)
    SEARCH = QRectF(24, 132, 572, 46)
    CARDS = [QRectF(24 + i * 195, 196, 182, 224) for i in range(3)]

    # ---- painting -----------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.native:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            p.fillRect(self.rect(), QColor(0, 0, 0, 0))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        self.paint_panel(p)
        self.paint_content(p)
        p.end()

    def paint_panel(self, p: QPainter):
        tok, dark = self.tok, self.tok["dark"]
        full = QRectF(0, 0, PANEL_W, PANEL_H)
        if self.mode == "B" or not self.native:
            # Optical plate: the captured backdrop, soft and vivid, then a veil for legibility.
            if self.snap is not None:
                p.save(); p.translate(0, 0)
                p.drawImage(self.snap.rect, self.snap.panel)
                p.restore()
                lens(p, full, 0, self.snap.panel, self.snap, depth=16, width=22)
            else:
                p.fillRect(full, QColor("#1B2027" if dark else "#F2F4F8"))
            p.fillRect(full, QColor(8, 12, 20, 92) if dark else QColor(255, 255, 255, 84))
        else:
            # Acrylic is live behind this: a light tint only.
            p.fillRect(full, QColor(10, 14, 22, 44) if dark else QColor(255, 255, 255, 64))
        # shared: soft top reflection + the lit edge
        g = QLinearGradient(0, 0, 0, 120)
        g.setColorAt(0, QColor(255, 255, 255, 34 if dark else 120))
        g.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(QRectF(0, 0, PANEL_W, 120), g)
        p.setPen(QPen(QColor(255, 255, 255, 70 if dark else 230), 1))
        p.drawLine(QPointF(0, .5), QPointF(PANEL_W, .5))

    def ctrl(self, p: QPainter, rect: QRectF, radius: float, *, selected=False, strong=False):
        """A control: the most expressive glass on the panel."""
        tok, dark = self.tok, self.tok["dark"]
        optical = self.mode in ("B", "C") and self.snap is not None
        shadow(p, rect, radius, dark, 1.0 if not selected else 1.4)
        p.save(); p.setClipPath(rr(rect, radius))
        if optical:
            lens(p, rect, radius, self.snap.control, self.snap, depth=16 if not strong else 20, width=18)
            veil = QColor(255, 255, 255, 30 if dark else 98)
            if selected:
                veil = QColor(255, 255, 255, 56 if dark else 150)
            # dark glass uses a dark veil so light text keeps its ratio over white backdrops
            veil_color = QColor(8, 12, 20) if dark else QColor(255, 255, 255)
            alpha = adaptive_alpha(self.snap.sample(rect, "control", "light" if dark else "dark"), veil_color, QColor(tok["sub"]), 5.0, 40 if dark else veil.alpha())
            veil = QColor(veil_color.red(), veil_color.green(), veil_color.blue(), alpha)
            if dark:
                veil_hi = QColor(255, 255, 255, 18 if not selected else 40)
                p.fillRect(rect, veil); p.fillRect(rect, veil_hi)
            else:
                p.fillRect(rect, veil)
            tint = QColor(tok["accent"]); tint.setAlpha(46 if selected else 0)
            p.fillRect(rect, tint)
        else:
            fill = QColor(255, 255, 255, (40 if selected else 22) if dark else (170 if selected else 120))
            p.fillRect(rect, fill)
            if selected:
                tint = QColor(tok["accent"]); tint.setAlpha(40); p.fillRect(rect, tint)
        p.restore()
        specular(p, rect, radius, self.light, tok, 1.0 if optical else .55)
        rim(p, rect, radius, self.light, tok, 1.0 if optical else .7, accent=selected, dispersion=optical)

    def card(self, p: QPainter, rect: QRectF, radius: float, selected: bool):
        """Content layer: translucent, but carries its own contrast layer (text must read)."""
        tok, dark = self.tok, self.tok["dark"]
        shadow(p, rect, radius, dark, .9 if not selected else 1.6)
        p.save(); p.setClipPath(rr(rect, radius))
        if self.mode == "B" and self.snap is not None:
            p.drawImage(self.snap.rect, self.snap.card)
        # contrast layer: a dark/white body that guarantees the text ratio over any backdrop
        base = QColor(6, 10, 18, 148) if dark else QColor(255, 255, 255, 168)
        if self.snap is not None:   # B and C know the pixels behind the card: adapt the contrast layer to them
            alpha = adaptive_alpha(self.snap.sample(rect, "card", "light" if dark else "dark"), QColor(base.red(), base.green(), base.blue()),
                                   QColor(tok["faint"]), 6.4 if selected else 5.8, base.alpha())
            base.setAlpha(alpha)
            if os.environ.get("LAB_DEBUG"):
                s = self.snap.sample(rect, "card", "light" if dark else "dark")
                print("card", self.mode, self.variant, s.name(), "alpha", alpha, file=sys.stderr)
        p.fillRect(rect, base)
        if selected:
            tint = QColor(tok["accent"]); tint.setAlpha(34 if dark else 30)
            p.fillRect(rect, tint)
        p.restore()
        specular(p, rect, radius, self.light, tok, .22 if dark else .4)
        rim(p, rect, radius, self.light, tok, .75 if not selected else 1.0, accent=selected)

    def text(self, p, rect, string, size, weight=QFont.Weight.Normal, color=None, flags=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter):
        p.setFont(self._font(size, weight))
        p.setPen(QColor(color or self.tok["text"]))
        p.drawText(rect, int(flags), string)

    def paint_content(self, p: QPainter):
        tok = self.tok
        icon = lambda name, color, size: windows_design.icon(name, color, size).pixmap(size, size)
        # header ------------------------------------------------------
        h = self.HEADER
        tile = QRectF(h.left(), h.top() + 2, 36, 36)
        self.ctrl(p, tile, 12, strong=True)
        p.drawPixmap(int(tile.left() + 6), int(tile.top() + 6), icon("brand", tok["accent"], 24))
        self.text(p, QRectF(h.left() + 48, h.top() - 2, 160, 24), "Nexus", 17, QFont.Weight.DemiBold)
        self.text(p, QRectF(h.left() + 48, h.top() + 20, 160, 18), "HISTORIAL", 10, QFont.Weight.DemiBold, tok["faint"])
        counter = QRectF(h.right() - 214, h.top() + 4, 70, 32)
        self.ctrl(p, counter, 16)
        self.text(p, counter, "128", 12, QFont.Weight.DemiBold, flags=Qt.AlignmentFlag.AlignCenter)
        style = QRectF(h.right() - 136, h.top() + 3, 90, 34)
        self.ctrl(p, style, 17)
        self.text(p, QRectF(style.left() + 14, style.top(), 52, style.height()), "Estilo", 12, QFont.Weight.Medium, tok["sub"])
        p.drawPixmap(int(style.right() - 28), int(style.top() + 10), icon("chevron_down", tok["sub"], 14))
        close = QRectF(h.right() - 36, h.top() + 3, 34, 34)
        self.ctrl(p, close, 17)
        p.drawPixmap(int(close.left() + 9), int(close.top() + 9), icon("close", tok["sub"], 16))
        # tabs --------------------------------------------------------
        t = self.TABS
        self.ctrl(p, t, 21, strong=True)
        sel = QRectF(t.left() + 4, t.top() + 4, 120, t.height() - 8)
        self.ctrl(p, sel, 17, selected=True)
        p.drawPixmap(int(sel.left() + 14), int(sel.top() + 9), icon("history", tok["accent"], 16))
        self.text(p, QRectF(sel.left() + 36, sel.top(), 80, sel.height()), "Historial", 12, QFont.Weight.DemiBold, tok["text"])
        p.drawPixmap(int(t.left() + 138), int(t.top() + 13), icon("board", tok["sub"], 16))
        self.text(p, QRectF(t.left() + 160, t.top(), 90, t.height()), "Pinboards", 12, QFont.Weight.Medium, tok["sub"])
        p.drawPixmap(int(t.right() - 36), int(t.top() + 11), icon("more", tok["sub"], 20))
        # search ------------------------------------------------------
        s = self.SEARCH
        self.ctrl(p, s, 23, strong=True)
        p.drawPixmap(int(s.left() + 18), int(s.top() + 14), icon("search", tok["sub"], 18))
        self.text(p, QRectF(s.left() + 46, s.top(), 400, s.height()), "Buscar en tu historial", 13, color=tok["faint"])
        # cards -------------------------------------------------------
        self.paint_cards(p)

    def paint_cards(self, p: QPainter):
        tok = self.tok
        icon = lambda name, color, size: windows_design.icon(name, color, size).pixmap(size, size)
        specs = [
            dict(kind="TEXTO", n="1", title="Auditoría completa de ciberseguridad",
                 body="Eres un auditor senior de ciberseguridad. Revisa todo el sistema paso a paso y entrega un informe completo.",
                 meta="Claude · hace 2 min", selected=True),
            dict(kind="ENLACE", n="2", title="", body="A calmer place for creative work\nexample.com",
                 meta="Edge · hace 8 min", selected=False, link=True),
            dict(kind="IMAGEN", n="3", title="Moodboard de la campaña", body="", meta="Recortes · hace 14 min",
                 selected=False, image=True),
        ]
        for rect, spec in zip(self.CARDS, specs):
            self.card(p, rect, 18, spec["selected"])
            pad = 14
            top = rect.top() + pad
            # type label + discreet number badge
            self.text(p, QRectF(rect.left() + pad, top, 100, 16), spec["kind"], 9.5, QFont.Weight.DemiBold, tok["faint"])
            badge = QRectF(rect.right() - pad - 22, top - 2, 22, 20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 30 if tok["dark"] else 110))
            p.drawRoundedRect(badge, 8, 8)
            self.text(p, badge, spec["n"], 10, QFont.Weight.DemiBold, tok["sub"], Qt.AlignmentFlag.AlignCenter)
            y = top + 26
            if spec["title"]:
                self.text(p, QRectF(rect.left() + pad, y, rect.width() - 2 * pad, 38), spec["title"], 13.5,
                          QFont.Weight.DemiBold, flags=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap.value)
                y += 44
            if spec.get("image"):
                art = QRectF(rect.left() + pad, y, rect.width() - 2 * pad, 96)
                g = QLinearGradient(art.topLeft(), art.bottomRight())
                g.setColorAt(0, QColor("#527CA8")); g.setColorAt(1, QColor("#C99B8D"))
                p.setBrush(g); p.setPen(Qt.PenStyle.NoPen); p.drawRoundedRect(art, 10, 10)
                p.setBrush(QColor("#F1DECD")); p.drawEllipse(QPointF(art.right() - 34, art.top() + 30), 15, 15)
                p.setBrush(QColor(41, 61, 86, 220)); p.drawRoundedRect(QRectF(art.left(), art.bottom() - 30, art.width(), 30), 10, 10)
            elif spec.get("link"):
                art = QRectF(rect.left() + pad, y, rect.width() - 2 * pad, 70)
                g = QLinearGradient(art.topLeft(), art.bottomRight())
                g.setColorAt(0, QColor("#6C5CE7")); g.setColorAt(1, QColor("#74B9FF"))
                p.setBrush(g); p.setPen(Qt.PenStyle.NoPen); p.drawRoundedRect(art, 10, 10)
                lines = spec["body"].split("\n")
                self.text(p, QRectF(rect.left() + pad, y + 78, rect.width() - 2 * pad, 38), lines[0], 12.5,
                          QFont.Weight.DemiBold, flags=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap.value)
                self.text(p, QRectF(rect.left() + pad, y + 116, rect.width() - 2 * pad, 18), lines[1], 11.5, color=tok["sub"])
            else:
                self.text(p, QRectF(rect.left() + pad, y, rect.width() - 2 * pad, 96), spec["body"], 12,
                          color=tok["sub"], flags=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap.value)
            self.text(p, QRectF(rect.left() + pad, rect.bottom() - 30, rect.width() - 2 * pad, 18), spec["meta"], 10.5, color=tok["faint"])


# --------------------------------------------------------------------------- desktop stand-in
class Backdrop(QWidget):
    """Our own 'desktop'. Phases cover the cases asked for: white, black, photograph, colourful app."""

    def __init__(self, rect: QRect):
        super().__init__(None)
        self.phase = "white"
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(rect)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if self.phase in ("white", "black"):
            white = self.phase == "white"
            p.fillRect(self.rect(), QColor("#FFFFFF" if white else "#000000"))
            ink = QColor("#1F2937" if white else "#E5E7EB")
            for x in range(0, w, 34):
                p.fillRect(x, 0, 6, h, QColor(ink.red(), ink.green(), ink.blue(), 70))
            p.setFont(QFont("Segoe UI", 14)); p.setPen(ink)
            for row in range(12):
                p.drawText(20, 34 + row * 48, "Fondo %s · texto nítido que debe verse desenfocado a través del cristal" % ("blanco" if white else "negro"))
        elif self.phase == "photo":
            g = QLinearGradient(0, 0, 0, h)
            g.setColorAt(0, QColor("#1B2A5E")); g.setColorAt(.45, QColor("#E2674A")); g.setColorAt(.62, QColor("#F6B35A")); g.setColorAt(1, QColor("#101820"))
            p.fillRect(self.rect(), g)
            sun = QRadialGradient(w * .62, h * .56, 150)
            sun.setColorAt(0, QColor(255, 244, 200, 255)); sun.setColorAt(.35, QColor(255, 200, 120, 200)); sun.setColorAt(1, QColor(255, 160, 80, 0))
            p.fillRect(self.rect(), sun)
            p.setPen(Qt.PenStyle.NoPen)
            for i, (x, hh, c) in enumerate(((0, .36, "#0E1A2B"), (.22, .28, "#16263B"), (.5, .42, "#0B1523"), (.78, .3, "#14233A"))):
                path = QPainterPath(); path.moveTo(w * x, h)
                path.lineTo(w * x + w * .12, h * (1 - hh)); path.lineTo(w * x + w * .3, h); path.closeSubpath()
                p.setBrush(QColor(c)); p.drawPath(path)
            p.setPen(QColor(255, 255, 255, 190)); p.setFont(QFont("Segoe UI", 22, QFont.Weight.Bold))
            p.drawText(30, 70, "Fotografía · atardecer"); p.drawText(30, h - 40, "Detalle fino: BORDES y TEXTO")
        else:
            p.fillRect(self.rect(), QColor("#F3F5F9"))
            palette_ = ["#E8386A", "#1F6FE0", "#FFD84A", "#12B886", "#7C4DFF", "#FF8A3D"]
            for i in range(14):
                p.setBrush(QColor(palette_[i % 6])); p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(QRectF(20 + (i % 5) * 150, 24 + (i // 5) * 130, 134, 108), 14, 14)
            p.setPen(QColor("#111827")); p.setFont(QFont("Segoe UI", 12))
            for row in range(10):
                p.drawText(24, 60 + row * 58, "Aplicación colorida · 1234567890 — texto y botones detrás")
        p.end()


def foreground(hwnd):
    if user32.SetForegroundWindow(hwnd):
        return int(user32.GetForegroundWindow() or 0) == int(hwnd)
    current = user32.GetForegroundWindow()
    thread = int(user32.GetWindowThreadProcessId(current, None)) if current else 0
    own = int(kernel32.GetCurrentThreadId())
    attached = bool(thread and thread != own and user32.AttachThreadInput(own, thread, True))
    try:
        return bool(user32.SetForegroundWindow(hwnd)) and int(user32.GetForegroundWindow() or 0) == int(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(own, thread, False)


def topmost(window):
    user32.SetWindowPos(int(window.winId()), wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)


def foreign_overlap(region: QRect):
    own = {int(w.winId()) for w in QApplication.topLevelWidgets() if w.isVisible()}
    blockers, reached = [], [False]

    @ENUMPROC
    def visit(hwnd, _):
        if int(hwnd) in own:
            reached[0] = True
            return False
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD(); user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != os.getpid():
                rect = wintypes.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(rect))
                if rect.left < region.right() and rect.right > region.left() and rect.top < region.bottom() and rect.bottom > region.top():
                    blockers.append(int(pid.value))
        return True

    user32.EnumWindows(visit, 0)
    return blockers


def rss_mb() -> float:
    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("a", ctypes.c_size_t), ("b", ctypes.c_size_t),
                    ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    counters = PMC(); counters.cb = ctypes.sizeof(PMC)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return round(counters.WorkingSetSize / 1048576, 1)


def main():
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    original = int(user32.GetForegroundWindow() or 0)
    app = QApplication([]); app.setQuitOnLastWindowClosed(False)
    app.setFont(windows_design.font())
    screen = app.primaryScreen()
    work = screen.availableGeometry()
    region = QRect(work.x(), work.y(), PANEL_W + 2 * MARGIN, PANEL_H + 2 * MARGIN)
    report = {"build": sys.getwindowsversion().build, "dpr": screen.devicePixelRatio(), "platform": app.platformName(),
              "scale": args.scale, "captures": [], "bench": {}}
    backdrop = Backdrop(region); backdrop.show(); topmost(backdrop); app.processEvents(); QTest.qWait(150)
    panels = {}
    for mode in args.modes.split(","):
        for variant in args.variants.split(","):
            panel = LabPanel(mode, variant)
            panel.move(region.x() + MARGIN, region.y() + MARGIN)
            panels[(mode, variant)] = panel
            panel.winId()

            def can_paint(window):
                return bool(sys.getwindowsversion().build >= 22621 and app.platformName() == "windows"
                            and not window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                            and window.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground))
            dark = TOKENS[variant]["dark"]
            ctrl = BackdropController(panel, backend=NativeBackend(), request="satin" if mode == "B" else "desktop_acrylic",
                                      can_native_paint=can_paint,
                                      on_state=lambda state, w=panel: w.set_native(state.native_enabled),
                                      variant=lambda d=dark: (d, 0x635346 if d else 0xD1C3B9))
            panel._backdrop_controller = ctrl
    for phase in args.phases.split(","):
        for (mode, variant), panel in panels.items():
            panel.hide(); app.processEvents()
            backdrop.phase = phase; backdrop.update(); app.processEvents(); QTest.qWait(160)
            snap = None
            if mode in ("B", "C"):
                t0 = time.perf_counter()
                shot = screen.grabWindow(0, region.x(), region.y(), region.width(), region.height())
                grab_ms = (time.perf_counter() - t0) * 1000
                snap = Snapshot(shot.toImage())
                panel.snap = snap
                report["bench"].setdefault(f"{mode}-{variant}-{phase}", {}).update(grab_ms=round(grab_ms, 1), blur_ms=round(snap.build_ms, 1))
            opened = time.perf_counter()
            panel.show(); topmost(panel); foreground(int(panel.winId()))
            app.processEvents(); QTest.qWait(200)
            # the retained caption (DWM requirement) adds frame to the first size: settle it like Nexus does
            panel.setGeometry(region.x() + MARGIN, region.y() + MARGIN, PANEL_W, PANEL_H)
            app.processEvents(); QTest.qWait(150); app.processEvents()
            report["bench"].setdefault(f"{mode}-{variant}-{phase}", {})["show_to_painted_ms"] = round((time.perf_counter() - opened) * 1000 - 300, 1)
            label = f"{mode}-{variant}-{phase}"
            blockers = foreign_overlap(region)
            if blockers:
                report["captures"].append({"name": label, "skipped": "foreign window overlaps the region", "pids": blockers[:5]})
            else:
                grabbed = screen.grabWindow(0, region.x(), region.y(), region.width(), region.height())
                grabbed.save(str(out / f"{label}.png"))
                report["captures"].append({"name": label, "native": panel.native, "pixels": [grabbed.width(), grabbed.height()],
                                           "dpr": screen.devicePixelRatio(), "screen": [screen.geometry().width(), screen.geometry().height()]})
            panel.hide()
    if args.bench:
        for (mode, variant), panel in panels.items():
            panel.show(); app.processEvents(); QTest.qWait(200)
            times = []
            for i in range(40):
                panel.light = 90 + i * 3
                t0 = time.perf_counter(); image = panel.grab(); times.append((time.perf_counter() - t0) * 1000)
            report["bench"][f"repaint-{mode}-{variant}"] = {"median_ms": round(statistics.median(times), 2), "max_ms": round(max(times), 2)}
            panel.hide()
        report["rss_mb"] = rss_mb()
    backdrop.hide()
    for panel in panels.values():
        panel._backdrop_controller.close()
    if original:
        foreground(original)
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("build", "platform", "dpr")}), len(report["captures"]), "capturas")


if __name__ == "__main__":
    main()
