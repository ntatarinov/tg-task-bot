from __future__ import annotations

from datetime import date

import pytest

from app.utils.dates import normalize_deadline, parse_deadline, parse_russian_deadline

# Anchor: Friday, 2026-06-26.
TODAY = date(2026, 6, 26)


def test_weekday_resolves_to_future_with_correct_year():
    # "до среды" from Friday 26 Jun -> next Wednesday 1 Jul 2026
    assert parse_russian_deadline("сделать экспорт до среды", TODAY) == "2026-07-01"


def test_weekday_all_forms():
    assert parse_russian_deadline("к пятнице", TODAY) == "2026-07-03"
    assert parse_russian_deadline("до понедельника", TODAY) == "2026-06-29"


def test_tomorrow_and_after_tomorrow():
    assert parse_russian_deadline("надо завтра", TODAY) == "2026-06-27"
    assert parse_russian_deadline("давай послезавтра", TODAY) == "2026-06-28"


def test_through_n_days_and_weeks():
    assert parse_russian_deadline("через 3 дня", TODAY) == "2026-06-29"
    assert parse_russian_deadline("через 2 недели", TODAY) == "2026-07-10"


def test_explicit_date_with_year():
    assert parse_russian_deadline("дедлайн 15.07.2026", TODAY) == "2026-07-15"


def test_bare_date_in_past_rolls_to_next_year():
    # 10.01 already passed in 2026 -> 2027
    assert parse_russian_deadline("к 10.01", TODAY) == "2027-01-10"


def test_no_deadline_returns_none():
    assert parse_russian_deadline("просто обсуждаем фичу", TODAY) is None
    assert parse_russian_deadline(None, TODAY) is None


def test_month_name_in_genitive():
    assert parse_russian_deadline("к 5 июля", TODAY) == "2026-07-05"
    assert parse_russian_deadline("сделать к 30 июня", TODAY) == "2026-06-30"


def test_end_of_week_and_month():
    assert parse_russian_deadline("до конца недели", TODAY) == "2026-07-03"  # ближайшая пятница
    assert parse_russian_deadline("к концу месяца", TODAY) == "2026-06-30"


def test_normalize_uses_only_text():
    # Message has an explicit phrase -> use it.
    assert normalize_deadline("сделать экспорт до среды", TODAY) == "2026-07-01"


def test_normalize_no_phrase_means_no_deadline():
    # No date phrase in the message -> no deadline, even if the model invented one.
    assert normalize_deadline("надо добавить отчёт, Маша сделает", TODAY) is None
    assert normalize_deadline("просто обсуждаем", TODAY) is None


@pytest.mark.parametrize(
    "text",
    ["пошли на завтрак", "среди прочего обсудили API", "купили 5 машин", "средняя нагрузка выросла",
     "пятничный созыв", "2 марки серверов"],
)
def test_words_that_only_look_like_dates_are_not_deadlines(text):
    """Regression: substring matching produced deadlines from all of these."""
    assert parse_deadline(text, TODAY) is None


def test_may_is_still_recognised():
    assert parse_russian_deadline("к 5 мая", TODAY) == "2027-05-05"
    assert parse_russian_deadline("к 3 марта", TODAY) == "2027-03-03"


def test_more_russian_phrases():
    assert parse_russian_deadline("через неделю", TODAY) == "2026-07-03"
    assert parse_russian_deadline("до конца дня", TODAY) == "2026-06-26"
    assert parse_russian_deadline("в воскресенье", TODAY) == "2026-06-28"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("by tomorrow", "2026-06-27"),
        ("the day after tomorrow", "2026-06-28"),
        ("by EOD", "2026-06-26"),
        ("by Friday", "2026-07-03"),
        ("on Monday", "2026-06-29"),
        ("in 3 days", "2026-06-29"),
        ("in 2 weeks", "2026-07-10"),
        ("by the end of the week", "2026-07-03"),
        ("end of month", "2026-06-30"),
        ("by July 5", "2026-07-05"),
        ("by 5th July", "2026-07-05"),
        ("due Jan 10", "2027-01-10"),
    ],
)
def test_english_deadlines(text, expected):
    assert parse_deadline(text, TODAY) == expected


def test_english_without_a_date():
    assert parse_deadline("we should look into this", TODAY) is None


def test_iso_date():
    assert parse_deadline("дедлайн 2026-12-01", TODAY) == "2026-12-01"
    assert parse_deadline("deadline 2026-13-01", TODAY) is None


def test_invalid_calendar_dates_are_rejected():
    assert parse_deadline("к 31 февраля", TODAY) is None
    assert parse_deadline("к 45.13", TODAY) is None
