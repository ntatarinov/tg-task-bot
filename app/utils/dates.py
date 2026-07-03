from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

# Weekday roots (cover Russian declensions: "среду", "пятницу", ...) -> weekday index.
_WEEKDAY_ROOTS: dict[str, int] = {
    "понедельник": 0,
    "вторник": 1,
    "сред": 2,
    "четверг": 3,
    "пятниц": 4,
    "суббот": 5,
    "воскрес": 6,
}

# Month roots (genitive forms: "июля", "марта", ...). Order matters: "март"
# must be checked before "ма" (май) so "марта" isn't read as May.
_MONTH_ROOTS: list[tuple[str, int]] = [
    ("январ", 1), ("феврал", 2), ("март", 3), ("апрел", 4), ("ма", 5), ("июн", 6),
    ("июл", 7), ("август", 8), ("сентябр", 9), ("октябр", 10), ("ноябр", 11), ("декабр", 12),
]

_DMY_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b")
_THROUGH_RE = re.compile(r"через\s+(\d+)\s+(дн|недел)")
_DAY_MONTH_RE = re.compile(r"\b(\d{1,2})\s+([а-яё]+)")
_END_WEEK_RE = re.compile(r"конц\w*\s+недел")
_END_MONTH_RE = re.compile(r"конц\w*\s+месяц")


def _next_weekday(today: date, target_wd: int) -> date:
    ahead = (target_wd - today.weekday()) % 7
    if ahead == 0:  # "до среды", сказанное в среду, обычно про следующую
        ahead = 7
    return today + timedelta(days=ahead)


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

    if "послезавтра" in t:
        return (today + timedelta(days=2)).isoformat()
    if "завтра" in t:
        return (today + timedelta(days=1)).isoformat()
    if "сегодня" in t:
        return today.isoformat()

    m = _THROUGH_RE.search(t)
    if m:
        n = int(m.group(1))
        days = n * 7 if m.group(2).startswith("недел") else n
        return (today + timedelta(days=days)).isoformat()

    if _END_WEEK_RE.search(t):
        return _next_weekday(today, 4).isoformat()  # конец недели -> пятница
    if _END_MONTH_RE.search(t):
        last_day = calendar.monthrange(today.year, today.month)[1]
        return date(today.year, today.month, last_day).isoformat()

    for root, wd in _WEEKDAY_ROOTS.items():
        if root in t:
            return _next_weekday(today, wd).isoformat()

    m = _DAY_MONTH_RE.search(t)
    if m:
        day = int(m.group(1))
        word = m.group(2)
        for root, month in _MONTH_ROOTS:
            if word.startswith(root):
                try:
                    candidate = date(today.year, month, day)
                except ValueError:
                    return None
                if candidate < today:
                    candidate = date(today.year + 1, month, day)
                return candidate.isoformat()

    m = _DMY_RE.search(t)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        raw_year = m.group(3)
        if raw_year:
            year = int(raw_year)
            year = 2000 + year if year < 100 else year
        else:
            year = today.year
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if not raw_year and candidate < today:  # bare DD.MM already passed -> next year
            candidate = date(year + 1, month, day)
        return candidate.isoformat()

    return None


def normalize_deadline(message_text: str | None, today: date | None = None) -> str | None:
    """
    The deadline is taken ONLY from what is explicitly stated in the message.
    If there is no date phrase in the text, there is no deadline — the model is
    prone to inventing due dates that were never mentioned.
    """
    return parse_russian_deadline(message_text, today)
