from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytz

from app.config import settings
from app.db.repositories import TaskRepository
from app.llm.base import LLMUnavailableError
from app.llm.mock_client import MockLLMClient
from app.llm.schemas import BatchMessage
from app.services.digest_service import DigestService, message_link, split_for_telegram
from app.utils.periods import parse_period
from tests.helpers import BASE_TIME, ScriptedLLM, analyze, feed, make_chat

PERIOD = (BASE_TIME - timedelta(hours=1), BASE_TIME + timedelta(hours=2))


def _topics(*topics) -> dict:
    return {"topics": list(topics)}


def _topic(title, summary, *items, ids=(1,), **extra) -> dict:
    return {"title": title, "summary": summary, "message_ids": list(ids), "items": list(items), **extra}


async def _digest_of(session, chat, llm, kind="daily"):
    digest = await DigestService(session, llm).build_digest(chat, *PERIOD, kind=kind)
    return digest.text


@pytest.mark.asyncio
async def test_digest_follows_the_requested_structure(session):
    chat = await make_chat(session)
    await feed(session, chat, [
        ("Аня", "Решили: минимальная длина пароля 14 символов."),
        ("Аня", "Виктор, подготовь GPO до пятницы."),
        ("Борис", "Кто согласует с ИБ?"),
    ])
    llm = ScriptedLLM(_topics(_topic(
        "Security Baseline", "Обсудили политику паролей.",
        {"kind": "decision", "text": "Длина пароля 14 символов", "source_ids": [1],
         "quote": "Решили: минимальная длина пароля 14 символов"},
        {"kind": "task", "text": "Подготовить GPO", "source_ids": [2], "owner": "Виктор",
         "deadline": "до пятницы"},
        {"kind": "question", "text": "Кто согласует с ИБ?", "source_ids": [3]},
        ids=(1, 2, 3),
    )))
    await analyze(session, chat, llm)
    text = await _digest_of(session, chat, llm)

    assert "<b>Daily digest — 04.11.2026</b>" in text
    assert "<b>Topic 1. Security Baseline</b>" in text
    assert "Discussed: Обсудили политику паролей." in text
    assert "Decisions:" in text and "Длина пароля 14 символов" in text
    assert "Подготовить GPO" in text and "deadline: 2026-11-06" in text
    assert "Owners: Виктор" in text
    assert "Open questions:" in text and "Кто согласует с ИБ?" in text
    assert "<b>Summary of the day</b>" in text
    assert "What was decided:" in text
    assert "What needs to be done:" in text
    assert "Who owns what:" in text
    assert "What is still unresolved:" in text


@pytest.mark.asyncio
async def test_unknowns_are_stated_not_invented(session):
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", "Нужно обновить сертификаты.")])
    llm = ScriptedLLM(_topics(_topic(
        "Сертификаты", "Нужно обновить.", {"kind": "task", "text": "Обновить сертификаты", "source_ids": [1]},
    )))
    await analyze(session, chat, llm)
    text = await _digest_of(session, chat, llm)

    assert "deadline: Not defined" in text
    assert "Owners: Not defined" in text
    assert "Decisions: no confirmed decisions" in text


@pytest.mark.asyncio
async def test_proposal_is_never_listed_as_a_decision(session):
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", "Предлагаю PostgreSQL 16.")])
    llm = ScriptedLLM(_topics(_topic(
        "БД", "Обсудили.", {"kind": "proposal", "text": "Перейти на PostgreSQL 16", "source_ids": [1]},
    )))
    await analyze(session, chat, llm)
    text = await _digest_of(session, chat, llm)

    assert "Proposals (not accepted):" in text
    assert "Decisions: no confirmed decisions" in text


@pytest.mark.asyncio
async def test_changed_decision_shows_only_the_new_one_and_what_it_replaces(session):
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", "Решили: релиз 10 ноября.")])
    llm = ScriptedLLM(
        _topics(_topic("Релиз", "Назначили.", {
            "kind": "decision", "text": "Релиз 10 ноября", "source_ids": [1], "quote": "Решили: релиз 10 ноября",
        })),
        lambda messages, topics: _topics(_topic("Релиз", "Перенесли.", {
            "kind": "decision", "text": "Релиз 17 ноября", "source_ids": [1],
            "quote": "Договорились: релиз 17 ноября", "supersedes_item_id": topics[0].open_items[0].id,
        }, topic_id=topics[0].id)),
    )
    await analyze(session, chat, llm)
    await feed(session, chat, [("Аня", "Договорились: релиз 17 ноября.")], start_id=2)
    await analyze(session, chat, llm)
    text = await _digest_of(session, chat, llm)

    assert "Релиз 17 ноября" in text
    assert "(replaces: Релиз 10 ноября)" in text
    assert text.count("Релиз 10 ноября") == 1


@pytest.mark.asyncio
async def test_sources_link_to_messages_in_supergroups(session):
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", "Нужно сделать.")])
    llm = ScriptedLLM(_topics(_topic("Т", "С.", {"kind": "task", "text": "Сделать", "source_ids": [1]})))
    await analyze(session, chat, llm)

    assert 'href="https://t.me/c/1234567890/1"' in await _digest_of(session, chat, llm)


def test_message_link_only_for_supergroups():
    assert message_link(-1001234567890, 5) == "https://t.me/c/1234567890/5"
    assert message_link(-12345, 5) is None


@pytest.mark.asyncio
async def test_basic_group_falls_back_to_message_ids(session):
    chat = await make_chat(session, -12345)
    await feed(session, chat, [("Аня", "Нужно сделать.")])
    llm = ScriptedLLM(_topics(_topic("Т", "С.", {"kind": "task", "text": "Сделать", "source_ids": [1]})))
    await analyze(session, chat, llm)

    assert "[#1]" in await _digest_of(session, chat, llm)


@pytest.mark.asyncio
async def test_digest_contains_only_the_requested_period(session):
    """Regression: a 30-day-old message used to reach the 24-hour digest."""
    chat = await make_chat(session)
    old = BASE_TIME - timedelta(days=30)
    await feed(session, chat, [("Аня", "Старая задача.")], start=old)
    await feed(session, chat, [("Аня", "Новая задача.")], start_id=2)
    llm = ScriptedLLM(
        _topics(_topic("Старое", "Старое.", {"kind": "task", "text": "Старая работа", "source_ids": [1]})),
        _topics(_topic("Новое", "Новое.", {"kind": "task", "text": "Новая работа", "source_ids": [2]}, ids=(2,))),
    )
    await analyze(session, chat, llm)
    text = await _digest_of(session, chat, llm)

    assert "Старая работа" not in text


@pytest.mark.asyncio
async def test_several_batches_are_merged_without_repetition(session):
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", "Раз")])
    llm = ScriptedLLM(
        _topics(_topic("Тема", "Первая часть.")),
        lambda messages, topics: _topics(_topic("Тема", "Вторая часть.", topic_id=topics[0].id)),
    )
    await analyze(session, chat, llm)
    await feed(session, chat, [("Аня", "Два")], start_id=2)
    await analyze(session, chat, llm)
    await _digest_of(session, chat, llm)

    assert list(llm.merge_calls[0].values()) == [["Первая часть.", "Вторая часть."]]


@pytest.mark.asyncio
async def test_digest_survives_model_outage_while_merging(session):
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", "Раз")])
    llm = ScriptedLLM(
        _topics(_topic("Тема", "Первая часть.")),
        lambda messages, topics: _topics(_topic("Тема", "Вторая часть.", topic_id=topics[0].id)),
    )
    await analyze(session, chat, llm)
    await feed(session, chat, [("Аня", "Два")], start_id=2)
    await analyze(session, chat, llm)

    class Down(ScriptedLLM):
        async def merge_summaries(self, fragments, language="en"):
            raise LLMUnavailableError("down")

    text = await _digest_of(session, chat, Down({"topics": []}))
    assert "Первая часть. Вторая часть." in text


@pytest.mark.asyncio
async def test_user_text_cannot_inject_html(session):
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", "Нужно <b>сделать</b>")])
    llm = ScriptedLLM(_topics(_topic(
        "A <script>", "x < y", {"kind": "task", "text": "Сделать <b>", "source_ids": [1]},
    )))
    await analyze(session, chat, llm)
    text = await _digest_of(session, chat, llm)

    assert "<script>" not in text
    assert "&lt;b&gt;" in text


@pytest.mark.asyncio
async def test_no_topics_is_said_plainly(session):
    chat = await make_chat(session)
    assert "No work topics found for this period." in await _digest_of(session, chat, ScriptedLLM({"topics": []}))


@pytest.mark.asyncio
async def test_manual_tasks_are_included(session):
    chat = await make_chat(session)
    await TaskRepository(session).create(chat.id, "Ручная задача", source="manual")
    await session.commit()

    digest = await DigestService(session, ScriptedLLM({"topics": []})).build_digest(
        chat, datetime.now(pytz.utc) - timedelta(hours=1), datetime.now(pytz.utc) + timedelta(hours=1)
    )
    assert "Ручная задача" in digest.text


@pytest.mark.asyncio
async def test_large_digest_splits_into_valid_telegram_messages(session, monkeypatch):
    """Regression: a 27,000-character digest failed to send at all."""
    monkeypatch.setattr(settings, "analysis_batch_max_messages", 60)  # one scripted answer
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", f"Сообщение {i}") for i in range(60)])
    llm = ScriptedLLM(_topics(*[
        _topic(f"Тема {i}", "Обсудили " + "много " * 40,
               *[{"kind": "task", "text": f"Задача {i}-{j} " + "я" * 200, "source_ids": [i + 1]} for j in range(3)],
               ids=(i + 1,))
        for i in range(60)
    ]))
    await analyze(session, chat, llm)
    text = await _digest_of(session, chat, llm)
    chunks = split_for_telegram(text)

    assert len(text) > 4096
    assert all(len(c) <= 4096 for c in chunks)
    assert all(c.count("<b>") == c.count("</b>") for c in chunks)
    assert all(c.count("<a ") == c.count("</a>") for c in chunks)
    assert "".join(chunks).count("Тема 59") >= 1


def test_split_is_deterministic_and_handles_oversized_blocks():
    text = "\n\n".join(["короткий", "\n".join(["строка " * 50] * 40), "хвост"])
    assert split_for_telegram(text) == split_for_telegram(text)
    assert all(len(c) <= 3800 for c in split_for_telegram(text))


NOW = pytz.utc.localize(datetime(2026, 11, 4, 12, 0))  # 17:00 in Almaty


def test_period_default_is_last_24_hours():
    period = parse_period("", "Asia/Almaty", NOW)
    assert period.end - period.start == timedelta(hours=24)


def test_period_today_starts_at_local_midnight_in_utc():
    """Regression guard: SQLite drops offsets, so a local-time bound would be 5 hours off."""
    period = parse_period("today", "Asia/Almaty", NOW)
    assert period.start == pytz.utc.localize(datetime(2026, 11, 3, 19, 0))
    assert period.start.utcoffset() == timedelta(0)
    assert period.label == "04.11.2026"


def test_period_yesterday_and_week():
    assert parse_period("yesterday", "Asia/Almaty", NOW).label == "03.11.2026"
    assert parse_period("вчера", "Asia/Almaty", NOW).label == "03.11.2026"
    assert parse_period("week", "Asia/Almaty", NOW).end - parse_period("week", "Asia/Almaty", NOW).start == timedelta(days=7)
    assert parse_period("3d", "Asia/Almaty", NOW).end - parse_period("3d", "Asia/Almaty", NOW).start == timedelta(days=3)


def test_period_explicit_dates():
    period = parse_period("2026-11-01 2026-11-03", "Asia/Almaty", NOW)
    assert period.label == "01.11–03.11.2026"
    assert parse_period("01.11.2026", "Asia/Almaty", NOW).label == "01.11.2026"


@pytest.mark.parametrize("arg", ["завтра-ка", "2026-13-01", "2026-11-03 2026-11-01", "0d", "1 2 3"])
def test_period_rejects_garbage(arg):
    assert parse_period(arg, "Asia/Almaty", NOW) is None


@pytest.mark.asyncio
async def test_item_on_local_day_boundary_lands_in_the_right_day(session):
    chat = await make_chat(session)
    # 23:30 on Nov 3 in Almaty is 18:30 UTC.
    await feed(session, chat, [("Аня", "Поздняя задача.")], start=pytz.utc.localize(datetime(2026, 11, 3, 18, 30)))
    llm = ScriptedLLM(_topics(_topic("Т", "С.", {"kind": "task", "text": "Поздняя работа", "source_ids": [1]})))
    await analyze(session, chat, llm)

    service = DigestService(session, llm)
    yesterday = parse_period("yesterday", "Asia/Almaty", NOW)
    today = parse_period("today", "Asia/Almaty", NOW)
    assert "Поздняя работа" in (await service.build_digest(chat, yesterday.start, yesterday.end)).text
    assert "Поздняя работа" not in (await service.build_digest(chat, today.start, today.end)).text


@pytest.mark.asyncio
async def test_mock_llm_extracts_tasks_and_ignores_noise():
    client = MockLLMClient()
    result = await client.analyze_conversation(
        [
            BatchMessage(id=1, author="a", sent_at="", text="Нужно сделать авторизацию через JWT"),
            BatchMessage(id=2, author="b", sent_at="", text="😂"),
        ],
        [],
        [],
    )
    assert len(result.topics) == 1
    assert [i.source_ids for i in result.topics[0].items] == [[1]]
