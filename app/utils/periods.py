from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pytz

from app.utils.time import chat_timezone

_DAYS_RE = re.compile(r"^(\d{1,3})\s*[dд]$")


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime
    label: str


def _parse_date(raw: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _midnight(day: date, tz: pytz.BaseTzInfo) -> datetime:
    return tz.localize(datetime.combine(day, time.min))


def format_label(start: datetime, end: datetime, tz: pytz.BaseTzInfo) -> str:
    first = start.astimezone(tz).date()
    # The end bound is exclusive, so a period ending exactly at midnight belongs to the day before.
    last = (end.astimezone(tz) - timedelta(seconds=1)).date()
    if first >= last:
        return last.strftime("%d.%m.%Y")
    return f"{first:%d.%m}–{last:%d.%m.%Y}"


def parse_period(arg: str, timezone: str, now: datetime) -> Period | None:
    """
    Parse the /digest argument in the chat's timezone: nothing (last 24 hours),
    today, yesterday, week, 3d, one date, or two dates. Returns None if unreadable.
    """
    tz = chat_timezone(timezone)
    local_now = now.astimezone(tz)
    today = local_now.date()
    raw = arg.strip().lower()

    if raw == "":
        start, end = now - timedelta(hours=24), now
    elif raw in ("today", "сегодня"):
        start, end = _midnight(today, tz), now
    elif raw in ("yesterday", "вчера"):
        start, end = _midnight(today - timedelta(days=1), tz), _midnight(today, tz)
    elif raw in ("week", "неделя"):
        start, end = now - timedelta(days=7), now
    elif match := _DAYS_RE.match(raw):
        days = int(match.group(1))
        if days == 0:
            return None
        start, end = now - timedelta(days=days), now
    else:
        parts = raw.split()
        dates = [_parse_date(p) for p in parts]
        if not 1 <= len(parts) <= 2 or any(d is None for d in dates):
            return None
        first, last = dates[0], dates[-1]
        assert first is not None and last is not None
        if first > last:
            return None
        start, end = _midnight(first, tz), _midnight(last + timedelta(days=1), tz)

    # Always UTC past this point: SQLite stores wall-clock values and drops the offset,
    # so a local midnight would be compared against UTC columns hours off.
    return Period(
        start=start.astimezone(pytz.utc),
        end=end.astimezone(pytz.utc),
        label=format_label(start, end, tz),
    )
