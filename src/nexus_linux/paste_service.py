"""Mirrors Nexus's PasteService: writes the chosen clip back to the system
clipboard, then optionally synthesizes Ctrl+V into the previously focused
window.

Two Wayland-specific findings from live testing on Erwin (2026-08-23), both
documented in MIGRATION_LINUX.md:

1. Writing: QClipboard.setMimeData()/setText() on this Qt6.11/PySide6
   wayland platform plugin does not propagate to the compositor at all --
   verified with an isolated QApplication, and again with a real visible,
   activated QWidget, neither took effect (wl-paste kept returning the prior
   clipboard owner's data). This looks like a genuine gap in Qt's Wayland
   QPA clipboard-write path, not a serial/focus issue in our code. Fix:
   shell out to wl-copy (wl-clipboard), the same tool that already proved
   reliable for reading in ClipboardMonitor.

2. Pasting: apps cannot synthesize input into other windows directly on
   Wayland (a deliberate compositor security boundary) -- the sanctioned way
   is a uinput-backed daemon. We use ydotool, talking to /dev/uinput via the
   ydotoold user service (already enabled, no sudo required).

3. Ordering (Phase 2.6, 2026-08-26): every caller that selects an item from
   a Nexus panel and synthesizes a paste MUST write to the clipboard, THEN
   hide the panel, THEN synthesize -- never synthesize while the panel is
   still the active window, or the Ctrl+V lands back inside Nexus itself
   (its own search field, typically) instead of the app the user actually
   meant to paste into. This was a real, live bug: the original call sites
   did write+synthesize+hide, in that order.

   POST_HIDE_PASTE_DELAY_MS is not a guess: measured live on Erwin by
   instrumenting TimelineWindow.fade_hide() with QTimer.singleShot probes
   at 0/5/10/20/30/50/80/120ms after hide() and logging
   self.isActiveWindow() at each -- the window's own deactivation
   completed between 1ms and 6.6ms after hide() was called (isVisible()
   flips synchronously, but isActiveWindow() lags behind it since the
   compositor round-trip is async). 40ms gives roughly 6x margin over that
   measurement while still being well under the ~100ms "feels instant"
   threshold -- not an arbitrary large sleep, and not blocking anything
   (delivered via QTimer.singleShot from the caller, never a thread sleep
   here or in this module)."""

import json
import os
import re
import shutil
import subprocess
import time
from typing import Optional

from .storage import ClipItem

YDOTOOL_BIN = shutil.which("ydotool")
WL_COPY_BIN = shutil.which("wl-copy")

POST_HIDE_PASTE_DELAY_MS = 40
WL_PASTE_BIN = shutil.which("wl-paste")
HYPRCTL_BIN = shutil.which("hyprctl")


def paste_failure_message(reason: str) -> str:
    """Tray text for a failed synthetic paste: what happened and what to do (H09).

    Technical details (exception text) stay in the log, never in the notice."""
    reason = reason or ""
    if "no está instalado" in reason:
        cause = "Falta ydotool, la herramienta que Nexus usa para pegar por ti. Instálala para pegar automáticamente."
    elif "ventana destino" in reason or "ya no está disponible" in reason:
        cause = "La ventana donde ibas a pegar ya no está disponible."
    elif "Hyprland" in reason:
        cause = "Hyprland no aceptó el pegado automático."
    else:
        cause = "No se pudo escribir automáticamente en la aplicación."
    return f"{cause} El elemento ya está copiado: pégalo con Ctrl+V."


class PasteService:
    def __init__(self, on_write=None, on_synthetic_paste=None, on_paste_failed=None):
        """on_write: optional callback invoked right before writing to the
        clipboard, so callers can suppress the monitor from re-capturing it.
        on_synthetic_paste: optional callback invoked right before sending a
        synthetic Ctrl+V via ydotool, so EvdevStackWatcher can ignore the
        kernel-level keypress this itself generates (it's indistinguishable
        from a real one at that layer).
        on_paste_failed: optional callback invoked when a synthetic paste
        could not be sent (ydotoold not running, etc). Without this, the item
        still lands on the clipboard correctly but nothing tells the user why
        it didn't also get typed -- a silent failure a polished app avoids."""
        self._on_write = on_write
        self._on_synthetic_paste = on_synthetic_paste
        self._on_paste_failed = on_paste_failed
        self._target_address = None

    def remember_target(self) -> None:
        """Remember an exact window before opening a Nexus panel, never its title."""
        if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or not HYPRCTL_BIN:
            return
        self._target_address = None
        try:
            result = subprocess.run([HYPRCTL_BIN, "-j", "activewindow"], capture_output=True, timeout=2)
            window = json.loads(result.stdout) if result.returncode == 0 else {}
            address = window.get("address", "")
            if window.get("pid") != os.getpid() and re.fullmatch(r"0x[0-9a-fA-F]+", address):
                self._target_address = address
        except (ValueError, OSError, subprocess.TimeoutExpired):
            pass

    def write_to_clipboard(self, item: ClipItem) -> bool:
        if WL_COPY_BIN is None:
            return False
        args = [WL_COPY_BIN]
        mime = "text/plain;charset=utf-8"
        data: bytes
        if item.kind in ("text", "url"):
            data = (item.text_content or "").encode("utf-8")
        elif item.kind == "image" and item.image_data:
            mime = "image/png"
            args += ["-t", mime]
            data = item.image_data
        elif item.kind == "file" and item.file_path:
            mime = "text/uri-list"
            args += ["-t", mime]
            from pathlib import Path
            try:
                data = (Path(item.file_path).as_uri() + "\r\n").encode("utf-8")
            except ValueError:
                return False
        else:
            return False

        if self._on_write:
            self._on_write(data, mime)
        try:
            result = subprocess.run(
                args, input=data, timeout=2,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

        if result.returncode != 0:
            return False
        return self._wait_until_clipboard_matches(data, mime=mime)

    @staticmethod
    def _wait_until_clipboard_matches(expected: bytes, attempts: int = 6, delay: float = 0.03, mime: str | None = None) -> bool:
        """wl-copy forks a background process to actually own the selection;
        calling write_to_clipboard twice in quick succession (exactly what
        ClipStack.paste_next_and_advance does) can otherwise race and leave
        stale content on the clipboard. Confirmed live on Erwin (2026-08-24)."""
        if WL_PASTE_BIN is None:
            return False
        for _ in range(attempts):
            try:
                result = subprocess.run(
                    [WL_PASTE_BIN, "--no-newline"] + (["-t", mime] if mime else []), capture_output=True, timeout=1
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                return False
            if result.stdout == expected:
                return True
            time.sleep(delay)
        return False

    def synthetic_paste(self) -> bool:
        """Sends Ctrl+V via ydotool. Returns False if ydotool/ydotoold is
        unavailable so callers can fall back to 'paste it yourself'."""
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            return self._hyprland_paste()
        if not YDOTOOL_BIN:
            if self._on_paste_failed:
                self._on_paste_failed("ydotool no está instalado")
            return False
        if self._on_synthetic_paste:
            self._on_synthetic_paste()
        try:
            subprocess.run(
                [YDOTOOL_BIN, "key", "29:1", "47:1", "47:0", "29:0"],
                check=True, timeout=2, capture_output=True,
            )
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
            if self._on_paste_failed:
                self._on_paste_failed(f"ydotool falló: {exc}")
            return False

    def _hyprland_paste(self) -> bool:
        # Hyprland 0.56 uses Lua dispatchers. Only a validated address can
        # enter this code; clipboard contents never become shell/Lua code.
        if not HYPRCTL_BIN or not self._target_address:
            if self._on_paste_failed:
                self._on_paste_failed("No se identificó ventana destino; usa Ctrl+V manualmente")
            return False
        selector = "address:" + self._target_address
        try:
            clients = subprocess.run([HYPRCTL_BIN, "-j", "clients"], capture_output=True, timeout=2)
            if clients.returncode != 0 or not any(w.get("address") == self._target_address for w in json.loads(clients.stdout)):
                raise ValueError("La ventana destino ya no está disponible")
            lua = (
                'hl.dispatch(hl.dsp.focus({window = "' + selector + '"})); '
                'hl.dispatch(hl.dsp.send_shortcut({mods = "CTRL", key = "V", window = "' + selector + '"}))'
            )
            result = subprocess.run([HYPRCTL_BIN, "eval", lua], capture_output=True, timeout=2)
            if result.returncode != 0 or result.stdout.strip() != b"ok":
                raise ValueError("Hyprland rechazó el pegado")
            return True
        except (OSError, ValueError, subprocess.TimeoutExpired):
            if self._on_paste_failed:
                self._on_paste_failed("Pegado Hyprland falló; el clip sigue disponible para Ctrl+V")
            return False

    def paste(self, item: ClipItem, synthesize: bool = True) -> bool:
        if not self.write_to_clipboard(item):
            return False
        if synthesize:
            return self.synthetic_paste()
        return True
