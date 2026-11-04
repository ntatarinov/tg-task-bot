from __future__ import annotations

import re
from datetime import datetime, time

import pytz

_DIGEST_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def now_utc() -> datetime:
    return datetime.now(tz=pytz.utc)


def is_valid_timezone(timezone: str) -> bool:
    return timezone in pytz.all_timezones_set


def chat_timezone(name: str) -> pytz.BaseTzInfo:
    """A bad stored value must not stop a chat's digests, so it falls back to the default."""
    from app.config import settings

    try:
        return pytz.timezone(name)
    except pytz.UnknownTimeZoneError:
        return pytz.timezone(settings.default_timezone)


def is_valid_digest_time(time_str: str) -> bool:
    return bool(_DIGEST_TIME_RE.match(time_str))


def ensure_aware_utc(dt: datetime) -> datetime:
    """Treat naive datetimes (e.g. from SQLite) as UTC so arithmetic stays consistent."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=pytz.utc)
    return dt


def now_in_tz(timezone: str) -> datetime:
    tz = pytz.timezone(timezone)
    return datetime.now(tz=tz)


def parse_digest_time(time_str: str) -> time:
    """Parse HH:MM string into time object."""
    hour, minute = map(int, time_str.split(":"))
    return time(hour=hour, minute=minute)


def localize_datetime(dt: datetime, timezone: str) -> datetime:
    tz = pytz.timezone(timezone)
    if dt.tzinfo is None:
        return tz.localize(dt)
    return dt.astimezone(tz)
