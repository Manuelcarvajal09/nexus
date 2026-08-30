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
CARD_ROW_GAP = 8         # vertical gap between wrapped rows in Expanded View

# Quick Shelf width is a CONTENT-DRIVEN target -- "how many cards do we want
# comfortably visible" -- not a raw fraction of the screen. A plain
# width = available_width * percentage is exactly what turns a 34" ultrawide
# into a floor-to-ceiling bar: 90% of 3440px is still ~3100px of cards
# nobody asked to see. 5-7 well-sized cards beats 10+ small ones crammed
# edge to edge (confirmed by choice, not just measurement -- see
# NEXUS_2_RELEASE_NOTES.md Fase 2.7).
_TARGET_VISIBLE_CARDS = {
    ResponsiveMode.COMPACT: 3,
    ResponsiveMode.STANDARD: 6,
    ResponsiveMode.WIDE: 7,
    ResponsiveMode.ULTRAWIDE: 8,
}
# Absolute width ceilings (logical px), independent of screen size -- the
# actual mechanism that stops ultrawide from producing a giant strip. WIDE
# and ULTRAWIDE get one; STANDARD/COMPACT never need it since their
# content-driven width is already well under any reasonable cap.
_ABSOLUTE_MAX_SHELF_WIDTH = {
    ResponsiveMode.WIDE: 1700,
    ResponsiveMode.ULTRAWIDE: 1950,
}
# Percentage ceiling, applied on top of the above -- belt and suspenders
# against a content-driven width that would otherwise dominate an unusually
# narrow "wide-classified" screen.
_WIDTH_CEILING_FRACTION = 0.90

# Fallback only -- real callers (TimelineWindow) measure their own chrome's
# actual rendered height (self._chrome.sizeHint()) and pass it in, since a
# hardcoded guess here is exactly what let the Quick Shelf balloon well past
# its ~190-240px target in practice: generic widget padding alone measured
# ~150px against this constant's guess of 96. Kept only so callers that
# don't (or can't) measure -- tests included -- still get a sane default.
DEFAULT_CHROME_HEIGHT = 80

# Expanded View: opt-in (Tab key / toggle button), never the default on
# open -- grows the panel toward this fraction of the screen instead of the
# Quick Shelf's compact card-height-driven target, so the card list can wrap
# into a multi-row grid (more cards visible, not bigger individual cards).
_EXPANDED_HEIGHT_FRACTION = 0.40
_EXPANDED_HEIGHT_CEILING = 0.45
# Safety ceiling for the default Quick Shelf, distinct from the Expanded
# View target above -- never so tall it eats an unreasonable share of a
# short/laptop screen even if a caller passes an unusually large chrome_height.
_QUICK_SHELF_HEIGHT_CEILING = 0.30


@dataclass(frozen=True)
class PanelGeometry:
    x: int
    y: int
    width: int
    height: int
    mode: ResponsiveMode
    card_height: int
    visible_cards_hint: int
    expanded: bool = False
    rows_hint: int = 1


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
    chrome_height: int = DEFAULT_CHROME_HEIGHT, expanded: bool = False,
) -> PanelGeometry:
    """`available_*` is QScreen.availableGeometry() for the target screen --
    already excludes docked Plasma panels/docks, so anchoring to its bottom
    edge naturally sits above them rather than underneath. Recomputed fresh
    on every call (every Ctrl+Shift+V); nothing here is persisted as "last
    known position" by design -- see the module docstring.

    `chrome_height` should be the caller's actually-measured header+search
    height (see TimelineWindow._apply_geometry) -- see DEFAULT_CHROME_HEIGHT
    above for why a guessed constant alone isn't good enough. `expanded`
    switches from the Quick Shelf's compact, card-height-driven target to
    the taller Expanded View target."""
    mode = classify_mode(available_width, available_height)
    card_height = COMPACT_CARD_HEIGHT if mode is ResponsiveMode.COMPACT else BASE_CARD_HEIGHT
    margin = _margin_for(mode, available_width)

    if expanded:
        panel_height = round(available_height * _EXPANDED_HEIGHT_FRACTION)
        panel_height = max(card_height + chrome_height, panel_height)
        panel_height = min(panel_height, round(available_height * _EXPANDED_HEIGHT_CEILING))
    else:
        # The list viewport includes a small card gutter in addition to the
        # delegate's card height; reserving it prevents Qt from growing the
        # top-level window below the physical screen edge.
        panel_height = card_height + 16 + chrome_height
        # A minimum breathable surface prevents the native Qt layout from
        # clipping the card row when Windows font scaling enlarges chrome.
        panel_height = max(340, panel_height)
        panel_height = max(1, min(panel_height, round(available_height * 0.36)))

    # Content-driven width: start from how many cards we actually want
    # visible, not from a raw fraction of the screen -- see
    # _TARGET_VISIBLE_CARDS's docstring. expanded doesn't widen the panel
    # (only Expanded View's extra ROWS change with it); a wider Quick Shelf
    # was never the ask, more visible rows was.
    target_cards = _TARGET_VISIBLE_CARDS[mode]
    content_width = target_cards * CARD_SLOT_WIDTH + 2 * margin
    if mode is ResponsiveMode.STANDARD:
        content_width = min(950, max(760, round(available_width * 0.47)))
    width = min(content_width, round(available_width * _WIDTH_CEILING_FRACTION))
    abs_max = _ABSOLUTE_MAX_SHELF_WIDTH.get(mode)
    if abs_max is not None:
        width = min(width, abs_max)
    width = max(1, min(width, available_width))
    x = available_x + (available_width - width) // 2
    y = available_y + available_height - panel_height - min(28, max(14, round(available_height * 0.02)))

    cols = max(1, width // CARD_SLOT_WIDTH)
    rows_hint = 1
    if expanded:
        rows_hint = max(1, (panel_height - chrome_height + CARD_ROW_GAP) // (card_height + CARD_ROW_GAP))
    visible_cards_hint = cols * rows_hint

    return PanelGeometry(
        x=x, y=y, width=width, height=panel_height,
        mode=mode, card_height=card_height, visible_cards_hint=visible_cards_hint,
        expanded=expanded, rows_hint=rows_hint,
    )
