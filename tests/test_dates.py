from __future__ import annotations

from datetime import date

from app.utils.dates import normalize_deadline, parse_russian_deadline

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
