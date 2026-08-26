import time
from datetime import datetime, timedelta

from nexus_linux.relative_time import format_relative


def _ts(dt: datetime) -> float:
    return dt.timestamp()


class TestRelativeTime:
    def test_just_now(self):
        now = datetime(2026, 8, 26, 17, 0, 0)
        assert format_relative(_ts(now - timedelta(seconds=10)), _ts(now)) == "Ahora"

    def test_minutes_ago(self):
        now = datetime(2026, 8, 26, 17, 0, 0)
        assert format_relative(_ts(now - timedelta(minutes=5)), _ts(now)) == "5 min"

    def test_boundary_59_seconds_is_still_ahora(self):
        now = datetime(2026, 8, 26, 17, 0, 0)
        assert format_relative(_ts(now - timedelta(seconds=59)), _ts(now)) == "Ahora"

    def test_boundary_60_seconds_is_1_min(self):
        now = datetime(2026, 8, 26, 17, 0, 0)
        assert format_relative(_ts(now - timedelta(seconds=60)), _ts(now)) == "1 min"

    def test_today_shows_hoy_with_time(self):
        now = datetime(2026, 8, 26, 17, 0, 0)
        then = datetime(2026, 8, 26, 9, 30, 0)
        assert format_relative(_ts(then), _ts(now)) == "Hoy, 09:30"

    def test_yesterday_shows_ayer_with_time(self):
        now = datetime(2026, 8, 26, 9, 0, 0)
        then = datetime(2026, 8, 25, 22, 15, 0)
        assert format_relative(_ts(then), _ts(now)) == "Ayer, 22:15"

    def test_same_year_older_shows_day_and_month(self):
        now = datetime(2026, 8, 26, 9, 0, 0)
        then = datetime(2026, 8, 12, 10, 0, 0)
        assert format_relative(_ts(then), _ts(now)) == "12 ago"

    def test_previous_year_shows_full_date(self):
        now = datetime(2026, 8, 26, 9, 0, 0)
        then = datetime(2025, 12, 24, 10, 0, 0)
        assert format_relative(_ts(then), _ts(now)) == "24 dic 2025"

    def test_future_timestamp_does_not_crash_or_go_negative(self):
        now = datetime(2026, 8, 26, 9, 0, 0)
        then = datetime(2026, 8, 26, 9, 0, 5)  # 5s in the "future" (clock skew)
        assert format_relative(_ts(then), _ts(now)) == "Ahora"

    def test_defaults_to_real_now_when_now_not_passed(self):
        # Smoke test: must not raise, and a timestamp from a second ago is "Ahora".
        assert format_relative(time.time() - 1) == "Ahora"
