from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

# Every pattern is word-bounded: bare substring matching read "завтрак" as tomorrow,
# "среди" as Wednesday and "5 машин" as May 5th.
_RU_WEEKDAYS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"\bпонедельник(?:а|у|ом)?\b"), 0),
    (re.compile(r"\bвторник(?:а|у|ом)?\b"), 1),
    (re.compile(r"\bсред[ауые]\b"), 2),
    (re.compile(r"\bчетверг(?:а|у|ом)?\b"), 3),
    (re.compile(r"\bпятниц[ауые]\b"), 4),
    (re.compile(r"\bсуббот[ауые]\b"), 5),
    (re.compile(r"\bвоскресень[еяю]\b"), 6),
]

_RU_MONTHS: dict[str, int] = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
_RU_DAY_MONTH_RE = re.compile(
    r"\b(\d{1,2})\s+(январ|феврал|март|апрел|ма|июн|июл|август|сентябр|октябр|ноябр|декабр)"
    r"(?:ь|я|а|й|)\b"
)

_EN_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_EN_MONTHS = [
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
]
_EN_MONTH_ALT = "|".join(m for m in _EN_MONTHS) + "|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
_EN_DAY_MONTH_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_EN_MONTH_ALT})\b")
_EN_MONTH_DAY_RE = re.compile(rf"\b({_EN_MONTH_ALT})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b")
_EN_WEEKDAY_RE = re.compile(rf"\b({'|'.join(_EN_WEEKDAYS)})\b")
_EN_IN_RE = re.compile(r"\bin\s+(\d+)\s+(day|week)s?\b")

_DMY_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b")
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_THROUGH_RE = re.compile(r"\bчерез\s+(\d+)\s+(дн|ден|недел)")
_THROUGH_WEEK_RE = re.compile(r"\bчерез\s+неделю\b")
_END_DAY_RE = re.compile(r"\bконц\w*\s+дня\b")
_END_WEEK_RE = re.compile(r"\bконц\w*\s+недел")
_END_MONTH_RE = re.compile(r"\bконц\w*\s+месяц")


def _next_weekday(today: date, target_wd: int) -> date:
    ahead = (target_wd - today.weekday()) % 7
    if ahead == 0:  # "до среды", сказанное в среду, обычно про следующую
        ahead = 7
    return today + timedelta(days=ahead)


def _end_of_month(today: date) -> date:
    return date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])


def _roll_forward(today: date, month: int, day: int) -> str | None:
    """A date without a year: this year, or the next one if it has already passed."""
    try:
        candidate = date(today.year, month, day)
        if candidate < today:
            candidate = date(today.year + 1, month, day)
    except ValueError:
        return None
    return candidate.isoformat()


def parse_russian_deadline(text: str | None, today: date | None = None) -> str | None:
    """
    Deterministically resolve a deadline that is EXPLICITLY present in the text,
    anchored on the real "today". Returns "YYYY-MM-DD" or None if the text
    contains no deadline (we never invent one).
    """
    if not text:
        return None
    today = today or date.today()
    t = text.lower()

    if re.search(r"\bпослезавтра\b", t):
        return (today + timedelta(days=2)).isoformat()
    if re.search(r"\bзавтра\b", t):
        return (today + timedelta(days=1)).isoformat()
    if re.search(r"\bсегодня\b", t) or _END_DAY_RE.search(t):
        return today.isoformat()

    m = _THROUGH_RE.search(t)
    if m:
        n = int(m.group(1))
        days = n * 7 if m.group(2).startswith("недел") else n
        return (today + timedelta(days=days)).isoformat()
    if _THROUGH_WEEK_RE.search(t):
        return (today + timedelta(days=7)).isoformat()

    if _END_WEEK_RE.search(t):
        return _next_weekday(today, 4).isoformat()  # конец недели -> пятница
    if _END_MONTH_RE.search(t):
        return _end_of_month(today).isoformat()

    for pattern, wd in _RU_WEEKDAYS:
        if pattern.search(t):
            return _next_weekday(today, wd).isoformat()

    m = _RU_DAY_MONTH_RE.search(t)
    if m:
        return _roll_forward(today, _RU_MONTHS[m.group(2)], int(m.group(1)))

    return _parse_numeric(t, today)


def _parse_numeric(t: str, today: date) -> str | None:
    m = _ISO_RE.search(t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None

    m = _DMY_RE.search(t)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        raw_year = m.group(3)
        if not raw_year:  # bare DD.MM already passed -> next year
            return _roll_forward(today, month, day)
        year = int(raw_year)
        year = 2000 + year if year < 100 else year
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    return None


def _month_index(name: str) -> int:
    return next(i for i, month in enumerate(_EN_MONTHS, 1) if month.startswith(name[:3]))


def parse_english_deadline(text: str | None, today: date | None = None) -> str | None:
    """English counterpart of parse_russian_deadline, with the same no-invention contract."""
    if not text:
        return None
    today = today or date.today()
    t = text.lower()

    if "day after tomorrow" in t:
        return (today + timedelta(days=2)).isoformat()
    if re.search(r"\btomorrow\b", t):
        return (today + timedelta(days=1)).isoformat()
    if re.search(r"\b(?:today|tonight|eod)\b|\bend of (?:the )?day\b", t):
        return today.isoformat()

    m = _EN_IN_RE.search(t)
    if m:
        n = int(m.group(1))
        return (today + timedelta(days=n * 7 if m.group(2) == "week" else n)).isoformat()

    if re.search(r"\bend of (?:the |this )?week\b", t):
        return _next_weekday(today, 4).isoformat()
    if re.search(r"\bend of (?:the |this )?month\b", t):
        return _end_of_month(today).isoformat()

    m = _EN_WEEKDAY_RE.search(t)
    if m:
        return _next_weekday(today, _EN_WEEKDAYS.index(m.group(1))).isoformat()

    m = _EN_DAY_MONTH_RE.search(t)
    if m:
        return _roll_forward(today, _month_index(m.group(2)), int(m.group(1)))
    m = _EN_MONTH_DAY_RE.search(t)
    if m:
        return _roll_forward(today, _month_index(m.group(1)), int(m.group(2)))
    return None


def parse_deadline(text: str | None, today: date | None = None) -> str | None:
    return parse_russian_deadline(text, today) or parse_english_deadline(text, today)


def normalize_deadline(message_text: str | None, today: date | None = None) -> str | None:
    """
    The deadline is taken ONLY from what is explicitly stated in the message.
    If there is no date phrase in the text, there is no deadline — the model is
    prone to inventing due dates that were never mentioned.
    """
    return parse_deadline(message_text, today)
