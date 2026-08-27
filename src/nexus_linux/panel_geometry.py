"""Pure geometry calculation for the bottom-anchored Timeline/Stack panel.
No Qt imports, no display needed -- takes plain logical-pixel rectangles in,
returns a plain PanelGeometry out, so the responsive/multi-monitor logic is
fully unit-testable (see tests/test_panel_geometry.py) independent of
whatever screen is actually attached to Erwin right now.

Deliberately works in LOGICAL pixels throughout: QScreen.availableGeometry()
already returns logical (device-independent) coordinates in Qt -- scaling
(100%/125%/150%/175%/200%, fractional or not) is resolved by Qt before this
code ever sees a number, so nothing here needs to know the scale factor or
touch a physical-pixel value. Mixing logical and physical pixels here would
be the bug; not doing that is the whole safety property this module gives.

Monitor selection policy (see resolve_target_screen in timeline_window.py):
cursor position, not "the active window's monitor". A plain Wayland client
has no portable way to ask the compositor which OTHER application's window
is currently active without a compositor-privileged mechanism (a KWin
script loaded over D-Bus) -- and round-tripping through one on every
Ctrl+Shift+V would reintroduce tens of milliseconds into a path that was
just brought down to ~3-4ms (see PERFORMANCE_BASELINE.md §2.1). Cursor
position is the same heuristic KRunner/Albert/Ulauncher-style launchers use
for exactly this reason, and costs a single QCursor.pos() call."""

from dataclasses import dataclass
from enum import Enum


class ResponsiveMode(Enum):
    COMPACT = "compact"      # portrait, any width
    STANDARD = "standard"    # typical single desktop monitor (e.g. 1920x1080)
    WIDE = "wide"            # e.g. 2560x1440
    ULTRAWIDE = "ultrawide"  # e.g. 3440x1440, 3840x1600


# Width breakpoints in logical pixels. Width-based, not aspect-ratio-based:
# what actually determines how many ~232px cards fit side by side is
# available width, not whether the monitor happens to be 16:9 or 21:9 --
# two monitors of the same width should show the same number of cards
# regardless of aspect ratio. Portrait is the one aspect-driven exception,
# handled separately (a tall-but-narrow screen needs a fundamentally
# different layout, not just "fewer cards").
_WIDE_MIN_WIDTH = 2200      # covers 2560x1440
_ULTRAWIDE_MIN_WIDTH = 3200  # covers 3440x1440, 3840x1600

BASE_CARD_HEIGHT = 148   # must match timeline_delegate.CARD_HEIGHT
COMPACT_CARD_HEIGHT = 128  # slightly shorter cards in portrait/compact mode
CARD_SLOT_WIDTH = 232    # CARD_WIDTH + 2*CARD_MARGIN from timeline_delegate.py

_CHROME_HEIGHT = 96  # header + pinboard chips + search field + margins/spacing, roughly constant


@dataclass(frozen=True)
class PanelGeometry:
    x: int
    y: int
    width: int
    height: int
    mode: ResponsiveMode
    card_height: int
    visible_cards_hint: int


def classify_mode(logical_width: int, logical_height: int) -> ResponsiveMode:
    if logical_height <= 0 or logical_width <= 0:
        return ResponsiveMode.STANDARD  # degenerate input; don't crash on it
    if logical_width < logical_height:
        return ResponsiveMode.COMPACT
    if logical_width >= _ULTRAWIDE_MIN_WIDTH:
        return ResponsiveMode.ULTRAWIDE
    if logical_width >= _WIDE_MIN_WIDTH:
        return ResponsiveMode.WIDE
    return ResponsiveMode.STANDARD


def _margin_for(mode: ResponsiveMode, available_width: int) -> int:
    if mode is ResponsiveMode.COMPACT:
        return max(8, round(available_width * 0.03))
    if mode is ResponsiveMode.ULTRAWIDE:
        return max(32, round(available_width * 0.05))
    if mode is ResponsiveMode.WIDE:
        return max(20, round(available_width * 0.035))
    return max(12, round(available_width * 0.02))


def calculate_panel_geometry(
    available_x: int, available_y: int, available_width: int, available_height: int,
) -> PanelGeometry:
    """`available_*` is QScreen.availableGeometry() for the target screen --
    already excludes docked Plasma panels/docks, so anchoring to its bottom
    edge naturally sits above them rather than underneath. Recomputed fresh
    on every call (every Ctrl+Shift+V); nothing here is persisted as "last
    known position" by design -- see the module docstring."""
    mode = classify_mode(available_width, available_height)
    card_height = COMPACT_CARD_HEIGHT if mode is ResponsiveMode.COMPACT else BASE_CARD_HEIGHT
    margin = _margin_for(mode, available_width)

    panel_height = card_height + _CHROME_HEIGHT
    # Clamp: never so tall it eats an unreasonable share of a short screen,
    # never so short the chrome above the cards has nowhere to go.
    panel_height = max(150, min(panel_height, round(available_height * 0.4)))

    width = max(1, available_width - 2 * margin)
    x = available_x + margin
    y = available_y + available_height - panel_height

    visible_cards_hint = max(1, width // CARD_SLOT_WIDTH)

    return PanelGeometry(
        x=x, y=y, width=width, height=panel_height,
        mode=mode, card_height=card_height, visible_cards_hint=visible_cards_hint,
    )
