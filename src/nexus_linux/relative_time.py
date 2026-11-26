"""Human-friendly relative timestamps for Timeline cards ("Ahora", "2 min",
"Hoy, 14:32", "Ayer", "12 ago"). Nexus had no timestamp display at all
before Phase 2 -- every row showed type/app/pin status but never when
something was copied, a gap relative to both the original spec and to
Paste's own cards (which always show a timestamp)."""

from datetime import datetime
from .i18n import plural, tr

_MONTHS_ES = (
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic",
)


def format_relative(timestamp: float, now: float | None = None) -> str:
    now_dt = datetime.fromtimestamp(now) if now is not None else datetime.now()
    then_dt = datetime.fromtimestamp(timestamp)
    delta = now_dt - then_dt
    seconds = delta.total_seconds()

    if seconds < 0:
        seconds = 0  # clock skew / a captured item timestamped slightly in the future

    if seconds < 60:
        return tr("Ahora")
    if seconds < 3600:
        minutes = int(seconds // 60)
        return tr("{n} min", n=minutes)

    same_day = now_dt.date() == then_dt.date()
    if same_day:
        return tr("Hoy, {time}", time=then_dt.strftime('%H:%M'))

    yesterday = (now_dt.date() - then_dt.date()).days == 1
    if yesterday:
        return tr("Ayer, {time}", time=then_dt.strftime('%H:%M'))

    month = _MONTHS_ES[then_dt.month - 1]
    if then_dt.year == now_dt.year:
        return f"{then_dt.day} {month}"
    return f"{then_dt.day} {month} {then_dt.year}"
