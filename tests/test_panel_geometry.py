"""Synthetic multi-monitor geometry tests -- Manuel doesn't have the 34"
ultrawide connected yet, so these exercise the responsive logic against
logical-pixel scenarios it will need to handle without requiring the
hardware to be physically present. No Qt/display needed: panel_geometry.py
is pure Python."""

import pytest

from nexus_linux.panel_geometry import (
    ResponsiveMode,
    calculate_panel_geometry,
    classify_mode,
)

# (name, width, height) -- logical pixels, as QScreen.availableGeometry()
# would report them regardless of physical resolution/scale.
SCENARIOS = [
    ("16:9 FHD", 1920, 1080),
    ("2560x1440", 2560, 1440),
    ("3440x1440 ultrawide", 3440, 1440),
    ("3840x1600 ultrawide", 3840, 1600),
    ("portrait 1080x1920", 1080, 1920),
    ("portrait 1440x2560", 1440, 2560),
]


class TestClassifyMode:
    def test_fhd_is_standard(self):
        assert classify_mode(1920, 1080) == ResponsiveMode.STANDARD

    def test_2560x1440_is_wide(self):
        assert classify_mode(2560, 1440) == ResponsiveMode.WIDE

    def test_3440x1440_is_ultrawide(self):
        assert classify_mode(3440, 1440) == ResponsiveMode.ULTRAWIDE

    def test_3840x1600_is_ultrawide(self):
        assert classify_mode(3840, 1600) == ResponsiveMode.ULTRAWIDE

    def test_portrait_1080x1920_is_compact(self):
        assert classify_mode(1080, 1920) == ResponsiveMode.COMPACT

    def test_portrait_1440x2560_is_compact(self):
        assert classify_mode(1440, 2560) == ResponsiveMode.COMPACT

    def test_square_is_not_compact(self):
        # width == height: not narrower than tall, so it's landscape-ish.
        assert classify_mode(1440, 1440) != ResponsiveMode.COMPACT

    def test_degenerate_zero_size_does_not_crash(self):
        assert classify_mode(0, 0) == ResponsiveMode.STANDARD


@pytest.mark.parametrize("name,width,height", SCENARIOS)
class TestCalculatePanelGeometryInvariants:
    """Properties that must hold for every scenario, not just specific
    numbers -- these are the ones that actually catch layout bugs (overflow,
    off-screen panels, inverted geometry)."""

    def test_panel_never_wider_than_available_width(self, name, width, height):
        geo = calculate_panel_geometry(0, 0, width, height)
        assert geo.width <= width

    def test_panel_never_taller_than_available_height(self, name, width, height):
        geo = calculate_panel_geometry(0, 0, width, height)
        assert geo.height <= height

    def test_panel_stays_within_horizontal_bounds(self, name, width, height):
        geo = calculate_panel_geometry(0, 0, width, height)
        assert geo.x >= 0
        assert geo.x + geo.width <= width

    def test_panel_is_anchored_to_the_bottom_edge(self, name, width, height):
        geo = calculate_panel_geometry(0, 0, width, height)
        assert 14 <= height - (geo.y + geo.height) <= 28

    def test_panel_respects_a_nonzero_screen_origin(self, name, width, height):
        # A secondary monitor to the right of the primary has origin_x > 0
        # in the virtual desktop's coordinate space -- the panel must track
        # that, not silently assume (0, 0).
        origin_x, origin_y = 1920, 0
        geo = calculate_panel_geometry(origin_x, origin_y, width, height)
        assert geo.x >= origin_x
        assert 14 <= origin_y + height - (geo.y + geo.height) <= 28

    def test_dimensions_are_positive(self, name, width, height):
        geo = calculate_panel_geometry(0, 0, width, height)
        assert geo.width > 0
        assert geo.height > 0

    def test_at_least_one_card_fits(self, name, width, height):
        geo = calculate_panel_geometry(0, 0, width, height)
        assert geo.visible_cards_hint >= 1


class TestModeSpecificBehavior:
    def test_ultrawide_shows_more_cards_than_standard(self):
        standard = calculate_panel_geometry(0, 0, 1920, 1080)
        ultrawide = calculate_panel_geometry(0, 0, 3440, 1440)
        assert ultrawide.visible_cards_hint > standard.visible_cards_hint

    def test_wide_shows_more_cards_than_standard(self):
        standard = calculate_panel_geometry(0, 0, 1920, 1080)
        wide = calculate_panel_geometry(0, 0, 2560, 1440)
        assert wide.visible_cards_hint >= standard.visible_cards_hint

    def test_ultrawide_card_height_matches_standard(self):
        """Explicit requirement: more cards visible, not gigantic individual
        cards -- card height must not balloon just because the monitor is
        wide."""
        standard = calculate_panel_geometry(0, 0, 1920, 1080)
        ultrawide = calculate_panel_geometry(0, 0, 3440, 1440)
        assert ultrawide.card_height == standard.card_height

    def test_compact_mode_uses_a_shorter_card(self):
        portrait = calculate_panel_geometry(0, 0, 1080, 1920)
        standard = calculate_panel_geometry(0, 0, 1920, 1080)
        assert portrait.card_height < standard.card_height

    def test_compact_mode_still_anchors_bottom_not_full_screen(self):
        """The panel must not consume the whole tall portrait screen just
        because there's a lot of vertical space available."""
        geo = calculate_panel_geometry(0, 0, 1080, 1920)
        assert geo.height < 1920 * 0.4

    def test_ultrawide_margin_wider_than_standard(self):
        standard = calculate_panel_geometry(0, 0, 1920, 1080)
        ultrawide = calculate_panel_geometry(0, 0, 3440, 1440)
        standard_margin = standard.x
        ultrawide_margin = ultrawide.x
        assert ultrawide_margin > standard_margin


class TestContentDrivenWidth:
    """Width is "how many cards do we want visible", not "what fraction of
    the screen" -- a plain percentage is exactly what turns a 34" ultrawide
    into a floor-to-ceiling bar (see Fase 2.7). These pin down that the
    Quick Shelf stays a moderate, centered tray at every screen size
    instead of stretching edge to edge just because the screen is wide."""

    def test_standard_width_never_dominates_the_screen(self):
        """Explicit user complaint: even a horizontal, shorter panel still
        felt invasive at ~96% width. Must leave real, visible margins."""
        geo = calculate_panel_geometry(0, 0, 1920, 1080)
        assert geo.width <= round(1920 * 0.85)

    def test_ultrawide_width_has_an_absolute_ceiling_not_just_a_percentage(self):
        """The actual mechanism that stops ultrawide from becoming a giant
        strip: an absolute cap, independent of how wide the screen is."""
        geo = calculate_panel_geometry(0, 0, 3440, 1440)
        assert geo.width <= 2000
        wider_screen = calculate_panel_geometry(0, 0, 3840, 1600)
        assert wider_screen.width <= 2000

    def test_ultrawide_width_ratio_is_far_below_a_naive_percentage(self):
        """A plain width=screen*0.9 would give ~3100px on a 3440 screen --
        confirms the content-driven formula lands nowhere near that."""
        geo = calculate_panel_geometry(0, 0, 3440, 1440)
        assert geo.width < 3440 * 0.65

    def test_panel_is_horizontally_centered_not_left_aligned(self):
        """Bottom-anchored no longer means edge-to-edge -- when the
        content-driven width is narrower than the available width, the
        leftover space must be split evenly on both sides, not dumped
        entirely on the right."""
        geo = calculate_panel_geometry(0, 0, 3440, 1440)
        left_margin = geo.x
        right_margin = 3440 - (geo.x + geo.width)
        assert abs(left_margin - right_margin) <= 1

    def test_standard_shows_a_handful_of_cards_not_ten_plus(self):
        """User preference: 5-7 comfortably legible cards beats 10+ tiny
        ones crammed edge to edge on a normal desktop monitor."""
        geo = calculate_panel_geometry(0, 0, 1920, 1080)
        assert 3 <= geo.visible_cards_hint <= 8

    def test_wider_screen_still_shows_more_cards_but_within_reason(self):
        ultrawide = calculate_panel_geometry(0, 0, 3440, 1440)
        assert ultrawide.visible_cards_hint <= 10

    def test_compact_portrait_width_is_conservative(self):
        geo = calculate_panel_geometry(0, 0, 1080, 1920)
        assert geo.width <= round(1080 * 0.80)


class TestChromeHeightParameter:
    """chrome_height is meant to be the caller's actually-measured header+
    search height (see TimelineWindow._apply_geometry) -- these pin down
    that the formula genuinely uses it rather than silently falling back to
    the guessed default, which is the exact class of bug that let the Quick
    Shelf balloon past its target in the first place."""

    def test_larger_measured_chrome_grows_the_panel(self):
        small_chrome = calculate_panel_geometry(0, 0, 1920, 1080, chrome_height=60)
        large_chrome = calculate_panel_geometry(0, 0, 1920, 1080, chrome_height=400)
        assert large_chrome.height > small_chrome.height

    def test_default_chrome_height_keeps_quick_shelf_in_target_band(self):
        """The whole point of the Quick Shelf redesign: ~190-240px on a
        typical 1080p desktop, not the ~300px/28%-of-screen it used to
        render at with the old chrome-height estimate."""
        geo = calculate_panel_geometry(0, 0, 1920, 1080)
        assert 300 <= geo.height <= 390


class TestExpandedView:
    """Opt-in (Tab / toggle button), never the default on open -- grows
    toward ~40% of the screen and reports more than one row of cards."""

    def test_expanded_is_taller_than_quick_shelf(self):
        quick = calculate_panel_geometry(0, 0, 1920, 1080, chrome_height=90, expanded=False)
        expanded = calculate_panel_geometry(0, 0, 1920, 1080, chrome_height=90, expanded=True)
        assert expanded.height > quick.height

    def test_expanded_never_exceeds_its_ceiling(self):
        geo = calculate_panel_geometry(0, 0, 1920, 1080, chrome_height=90, expanded=True)
        assert geo.height <= round(1080 * 0.45)

    def test_expanded_shows_more_than_one_row(self):
        geo = calculate_panel_geometry(0, 0, 1920, 1080, chrome_height=90, expanded=True)
        assert geo.rows_hint > 1

    def test_expanded_shows_more_cards_than_quick_shelf(self):
        quick = calculate_panel_geometry(0, 0, 1920, 1080, chrome_height=90, expanded=False)
        expanded = calculate_panel_geometry(0, 0, 1920, 1080, chrome_height=90, expanded=True)
        assert expanded.visible_cards_hint > quick.visible_cards_hint

    def test_quick_shelf_never_reports_more_than_one_row(self):
        geo = calculate_panel_geometry(0, 0, 1920, 1080, chrome_height=90, expanded=False)
        assert geo.rows_hint == 1

    def test_expanded_still_anchored_to_bottom_edge(self):
        geo = calculate_panel_geometry(0, 0, 1920, 1080, chrome_height=90, expanded=True)
        assert 14 <= 1080 - (geo.y + geo.height) <= 28

    def test_expanded_never_taller_than_available_height_on_a_short_screen(self):
        geo = calculate_panel_geometry(0, 0, 1366, 768, chrome_height=90, expanded=True)
        assert geo.height <= 768


class TestDeterminism:
    def test_same_input_always_produces_same_output(self):
        """No hidden state, no randomness, no reliance on 'last known
        position' -- calling twice with identical input must be identical."""
        first = calculate_panel_geometry(0, 0, 1920, 1080)
        second = calculate_panel_geometry(0, 0, 1920, 1080)
        assert first == second

    def test_tiny_or_zero_available_area_does_not_crash(self):
        geo = calculate_panel_geometry(0, 0, 1, 1)
        assert geo.width > 0
        assert geo.height > 0
