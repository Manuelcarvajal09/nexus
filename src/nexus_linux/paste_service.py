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
   ydotoold user service (already enabled, no sudo required)."""

import shutil
import subprocess
import time
from typing import Optional

from .storage import ClipItem

YDOTOOL_BIN = shutil.which("ydotool")
WL_COPY_BIN = shutil.which("wl-copy")
WL_PASTE_BIN = shutil.which("wl-paste")


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

    def write_to_clipboard(self, item: ClipItem) -> bool:
        if WL_COPY_BIN is None:
            return False
        if self._on_write:
            self._on_write()

        args = [WL_COPY_BIN]
        data: bytes
        if item.kind in ("text", "url"):
            data = (item.text_content or "").encode("utf-8")
        elif item.kind == "image" and item.image_data:
            args += ["-t", "image/png"]
            data = item.image_data
        elif item.kind == "file" and item.file_path:
            args += ["-t", "text/uri-list"]
            data = f"file://{item.file_path}".encode("utf-8")
        else:
            return False

        try:
            subprocess.run(
                args, input=data, timeout=2,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

        if item.kind in ("text", "url"):
            self._wait_until_clipboard_matches(data)
        else:
            time.sleep(0.05)  # wl-copy's background daemon needs a beat to take ownership
        return True

    @staticmethod
    def _wait_until_clipboard_matches(expected: bytes, attempts: int = 6, delay: float = 0.03) -> bool:
        """wl-copy forks a background process to actually own the selection;
        calling write_to_clipboard twice in quick succession (exactly what
        ClipStack.paste_next_and_advance does) can otherwise race and leave
        stale content on the clipboard. Confirmed live on Erwin (2026-08-24)."""
        if WL_PASTE_BIN is None:
            return False
        for _ in range(attempts):
            try:
                result = subprocess.run(
                    [WL_PASTE_BIN, "--no-newline"], capture_output=True, timeout=1
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
        if not YDOTOOL_BIN:
            if self._on_paste_failed:
                self._on_paste_failed("ydotool no está instalado")
            return False
        if self._on_synthetic_paste:
            self._on_synthetic_paste()
        try:
            subprocess.run(
                [YDOTOOL_BIN, "key", "29:1", "47:1", "47:0", "29:0"],
                check=True,
                timeout=2,
                capture_output=True,
            )
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
            if self._on_paste_failed:
                self._on_paste_failed(f"ydotool falló: {exc}")
            return False

    def paste(self, item: ClipItem, synthesize: bool = True) -> bool:
        self.write_to_clipboard(item)
        if synthesize:
            return self.synthetic_paste()
        return True
