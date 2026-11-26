"""What the shared interface needs from a platform's paste service.

Windows (platform.windows.paste) and Linux (platform.linux.paste_service)
implement it; core and ui depend only on this protocol.
"""
from __future__ import annotations

from typing import Optional, Protocol

from nexus.core.storage import ClipItem

# Delay between hiding Nexus and synthesizing Ctrl+V, so the keystroke reaches
# the app underneath instead of the closing Nexus window (measured live).
POST_HIDE_PASTE_DELAY_MS = 40


class PasteService(Protocol):
    def remember_target(self) -> Optional[object]: ...
    def write_to_clipboard(self, item: ClipItem) -> bool: ...
    def synthetic_paste(self) -> bool: ...
