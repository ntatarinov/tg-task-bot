"""Scenarios 11-14: volume, failures, reprocessing, scheduling — plus the bugs found in the audit."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import pytz
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bot.utils import safe_send_message
from app.config import settings
from app.db.models import Base, Digest, Message, TopicItem
from app.db.repositories import DigestRepository, MessageRepository
from app.llm.base import LLMResponseError, LLMUnavailableError
from app.llm.openai_client import OpenAILLMClient
from app.services.analysis_runner import flush_pending
from app.services.conversation_analyzer import ConversationAnalyzer
from app.services.digest_service import DigestService
from app.services.message_ingestor import MessageIngestor
from app.services.scheduler import due_slot, run_scheduled_digest, weekly_key
from tests.helpers import BASE_TIME, FakeBot, ScriptedLLM, analyze, feed, make_chat
from tests.scenarios import load_outcome

ALMATY = pytz.timezone("Asia/Almaty")


def _echo_topic(messages, topics):
    return {"topics": [{
        "topic_id": topics[0].id if topics else None,
        "title": "Большая тема",
        "summary": f"Обсудили {len(messages)} сообщений.",
        "message_ids": [m.id for m in messages],
    }]}


async def _pending(session, chat) -> int:
    return (await MessageRepository(session).pending_stats(chat.id))[0]


# --- Scenario 11: large conversations ---------------------------------------


@pytest.mark.parametrize("count", [300, 3000])
@pytest.mark.asyncio
async def test_large_conversation_is_fully_processed_in_bounded_batches(session, count):
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", f"Рабочее сообщение номер {i} про API") for i in range(count)])
    llm = ScriptedLLM(_echo_topic)

    started = time.monotonic()
    await analyze(session, chat, llm)
    elapsed = time.monotonic() - started

    assert await _pending(session, chat) == 0
    assert sum(len(c["messages"]) for c in llm.calls) == count
    # The prompt must not grow with the history: this is the point of incremental analysis.
    biggest = max(sum(len(m.text) for m in c["messages"]) for c in llm.calls)
    assert biggest <= settings.analysis_batch_max_chars
    assert len(llm.calls) >= count / settings.analysis_batch_max_messages
    assert len(llm.calls[-1]["topics"]) == 1
    assert elapsed < 60


@pytest.mark.asyncio
async def test_one_huge_message_still_gets_analyzed(session):
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", "x" * (settings.analysis_batch_max_chars * 2))])
    llm = ScriptedLLM({"topics": []})
    await analyze(session, chat, llm)

    assert await _pending(session, chat) == 0


@pytest.mark.asyncio
async def test_topic_memory_sent_to_the_model_is_capped(session):
    chat = await make_chat(session)
    for i in range(60):
        await feed(session, chat, [("Аня", f"Тема {i}")], start_id=i + 1)
        await analyze(session, chat, ScriptedLLM({"topics": [{
            "title": f"Тема {i}", "summary": "s", "message_ids": [1],
        }]}))

    await feed(session, chat, [("Аня", "Ещё сообщение")], start_id=1000)
    llm = ScriptedLLM({"topics": []})
    await analyze(session, chat, llm)

    assert len(llm.calls[0]["topics"]) <= 8
    assert len(llm.calls[0]["older_topics"]) <= 32


# --- Scenario 12: LLM and Telegram failures ---------------------------------


@pytest.mark.asyncio
async def test_llm_outage_keeps_messages_queued_and_costs_no_attempts(session):
    """Regression: the old client turned every failure into 'noise' and lost the message."""
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", "Нужно настроить бэкапы.")])

    with pytest.raises(LLMUnavailableError):
        await ConversationAnalyzer(session, ScriptedLLM(LLMUnavailableError("down"))).analyze_next_batch(chat)

    message = (await session.execute(select(Message))).scalar_one()
    assert message.pending_analysis is True
    assert message.analysis_attempts == 0

    await analyze(session, chat, ScriptedLLM({"topics": [{
        "title": "Бэкапы", "summary": "s", "message_ids": [1],
        "items": [{"kind": "task", "text": "Настроить бэкапы", "source_ids": [1]}],
    }]}))
    assert await _pending(session, chat) == 0
    assert len((await load_outcome(session, chat.id)).items) == 1


@pytest.mark.asyncio
async def test_real_client_raises_on_connection_failure(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_base_url", "http://127.0.0.1:9/v1")
    client = OpenAILLMClient(api_key="x", model="m", base_url=settings.openai_base_url)
    client.client = client.client.with_options(max_retries=0, timeout=2)

    with pytest.raises(LLMUnavailableError, match="APIConnectionError"):
        await client.analyze_conversation([], [], [])


@pytest.mark.asyncio
async def test_retired_model_surfaces_as_unavailable(monkeypatch):
    """The state the audit found: the configured model answers 404."""
    import httpx
    from openai import NotFoundError

    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_base_url", "http://127.0.0.1:9/v1")
    client = OpenAILLMClient(api_key="x", model="gone", base_url=settings.openai_base_url)

    async def not_found(**kwargs):
        request = httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions")
        raise NotFoundError("no", response=httpx.Response(404, request=request), body=None)

    monkeypatch.setattr(client.client.chat.completions, "create", not_found)
    with pytest.raises(LLMUnavailableError, match="404"):
        await client.analyze_conversation([], [], [])


@pytest.mark.asyncio
async def test_bad_answers_shrink_the_batch_until_the_culprit_is_isolated(session, monkeypatch):
    monkeypatch.setattr(settings, "analysis_max_attempts", 3)
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", f"сообщение {i}") for i in range(40)])

    def poisoned(messages, topics):
        if any(m.text == "сообщение 0" for m in messages):
            raise LLMResponseError("garbage")
        return {"topics": []}

    llm = ScriptedLLM(poisoned)
    await analyze(session, chat, llm)

    assert await _pending(session, chat) == 0
    sizes = [len(c["messages"]) for c in llm.calls]
    assert sizes[0] == settings.analysis_batch_max_messages
    assert sizes[1] < sizes[0] and sizes[2] < sizes[1]
    failed = (await session.execute(
        select(func.count()).select_from(Message).where(Message.analysis_failed.is_(True))
    )).scalar()
    # Set aside and reported, not silently dropped.
    assert 0 < failed < 40


@pytest.mark.asyncio
async def test_failed_messages_are_reported_in_the_digest(session, monkeypatch):
    monkeypatch.setattr(settings, "analysis_max_attempts", 1)
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", "текст")])
    await analyze(session, chat, ScriptedLLM(LLMResponseError("garbage")))

    digest = await DigestService(session, ScriptedLLM({"topics": []})).build_digest(
        chat, BASE_TIME - timedelta(hours=1), BASE_TIME + timedelta(hours=1)
    )
    assert "Messages that could not be analyzed: 1" in digest.text


@pytest.mark.asyncio
async def test_crash_while_applying_rolls_back_and_counts_the_attempt(session, monkeypatch):
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", "Сообщение.")])
    llm = ScriptedLLM({"topics": [{
        "title": "Тема", "summary": "s", "message_ids": [1],
        "items": [{"kind": "task", "text": "Сделать", "source_ids": [1]}],
    }]})
    analyzer = ConversationAnalyzer(session, llm)

    async def boom(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(analyzer.clusterer, "find_or_create_task", boom)
    result = await analyzer.analyze_next_batch(chat)

    assert result.processed == 0
    assert chat.telegram_chat_id  # still usable after the rollback, callers rely on it
    message = (await session.execute(select(Message))).scalar_one()
    assert message.pending_analysis is True
    assert message.analysis_attempts == 1
    assert (await session.execute(select(func.count()).select_from(TopicItem))).scalar() == 0


@pytest.mark.asyncio
async def test_flush_stops_at_outage_and_digest_says_what_is_missing(session):
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", "Сообщение.")])
    llm = ScriptedLLM(LLMUnavailableError("down"))

    await flush_pending(FakeBot(), session, llm, chat)
    digest = await DigestService(session, llm).build_digest(
        chat, BASE_TIME - timedelta(hours=1), BASE_TIME + timedelta(hours=1)
    )
    assert "Messages not analyzed yet: 1" in digest.text


@pytest.mark.asyncio
async def test_send_retries_flood_limit_and_network_errors(monkeypatch):
    waits: list[float] = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    method = SimpleNamespace()
    bot = FakeBot([
        TelegramRetryAfter(method=method, message="flood", retry_after=7),
        TelegramNetworkError(method=method, message="net"),
    ])

    sent = await safe_send_message(bot, 1, "текст")

    assert sent is not None
    assert waits[0] == 7


@pytest.mark.asyncio
async def test_send_gives_up_without_raising(monkeypatch):
    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    method = SimpleNamespace()
    bot = FakeBot([TelegramNetworkError(method=method, message="net") for _ in range(10)])

    assert await safe_send_message(bot, 1, "текст") is None


@pytest.mark.asyncio
async def test_failed_digest_delivery_is_not_recorded_as_sent_and_resumes(session, monkeypatch):
    """Regression: sent_at used to be set before the first send attempt."""
    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    chat = await make_chat(session)
    llm = ScriptedLLM({"topics": []})
    service = DigestService(session, llm)
    digest = await DigestRepository(session).create(
        chat.id, BASE_TIME, BASE_TIME, "\n\n".join(f"блок {i} " + "я" * 3000 for i in range(3))
    )
    await session.commit()

    method = SimpleNamespace()
    # First chunk goes through, then the network dies for all of its retries.
    bot = FakeBot([None] + [TelegramNetworkError(method=method, message="net") for _ in range(4)])
    assert await service.deliver(bot, digest, chat.telegram_chat_id) is False
    assert digest.sent_at is None
    assert digest.sent_chunks == 1
    assert len(bot.sent) == 1

    assert await service.deliver(bot, digest, chat.telegram_chat_id) is True
    assert digest.sent_at is not None
    assert [m["text"][:6] for m in bot.sent] == ["блок 0", "блок 1", "блок 2"]


# --- Scenario 13: processing the same message twice --------------------------


@pytest.mark.asyncio
async def test_redelivered_update_is_stored_once(session):
    """Regression: bot.db really contains duplicates from this."""
    chat = await make_chat(session)
    ingestor = MessageIngestor(session)
    args = dict(
        telegram_chat_id=chat.telegram_chat_id, chat_title="Team", telegram_user_id=1, username="a",
        display_name="A", telegram_message_id=5, text="Сообщение", raw_update_json={},
    )
    first, created_first = await ingestor.ingest(**args)
    second, created_second = await ingestor.ingest(**args)
    await session.commit()

    assert (created_first, created_second) == (True, False)
    assert first.id == second.id
    assert (await session.execute(select(func.count()).select_from(Message))).scalar() == 1
    assert await MessageRepository(session).get_by_telegram_id(chat.id, 5) is not None


@pytest.mark.asyncio
async def test_analyzed_message_redelivered_is_not_analyzed_again(session):
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", "Сообщение")])
    llm = ScriptedLLM({"topics": []})
    await analyze(session, chat, llm)
    await feed(session, chat, [("Аня", "Сообщение")])
    await analyze(session, chat, llm)

    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_same_id_in_another_chat_is_a_different_message(session):
    first = await make_chat(session, -1001)
    second = await make_chat(session, -1002)
    await feed(session, first, [("Аня", "один")])
    await feed(session, second, [("Аня", "два")])

    assert (await session.execute(select(func.count()).select_from(Message))).scalar() == 2


@pytest.mark.asyncio
async def test_edit_updates_text_and_requeues(session):
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", "Релиз в пятницу")])
    await analyze(session, chat, ScriptedLLM({"topics": []}))

    edited_at = BASE_TIME + timedelta(hours=1)
    updated = await MessageIngestor(session).apply_edit(chat.telegram_chat_id, 1, "Релиз в понедельник", edited_at)
    await session.commit()

    assert updated.text == "Релиз в понедельник"
    assert updated.pending_analysis is True
    assert updated.edited_at is not None


@pytest.mark.asyncio
async def test_edit_of_unknown_message_reports_it(session):
    chat = await make_chat(session)
    assert await MessageIngestor(session).apply_edit(chat.telegram_chat_id, 404, "x", BASE_TIME) is None


@pytest.mark.asyncio
async def test_media_without_caption_is_stored_but_not_queued(session):
    chat = await make_chat(session)
    message, created = await MessageIngestor(session).ingest(
        telegram_chat_id=chat.telegram_chat_id, chat_title="Team", telegram_user_id=1, username=None,
        display_name="A", telegram_message_id=7, text=None, raw_update_json={}, content_type="photo",
    )
    await session.commit()

    assert created is True
    assert message.content_type == "photo"
    assert message.pending_analysis is False


@pytest.mark.asyncio
async def test_telegram_send_time_is_preserved(session):
    chat = await make_chat(session)
    sent_at = BASE_TIME - timedelta(days=3)
    await feed(session, chat, [("Аня", "Старое")], start=sent_at)

    stored = (await session.execute(select(Message))).scalar_one()
    assert stored.sent_at.replace(tzinfo=pytz.utc) == sent_at


@pytest.mark.asyncio
async def test_concurrent_messages_survive_on_file_sqlite(tmp_path):
    """Regression: 2 of 3 parallel messages were lost while the LLM held the write lock."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path.as_posix()}/t.db", connect_args={"timeout": 30}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as s:
        chat = await make_chat(s)

    async def save(message_id: int) -> None:
        async with factory() as s:
            await MessageIngestor(s).ingest(
                telegram_chat_id=chat.telegram_chat_id, chat_title="Team", telegram_user_id=1,
                username="a", display_name="A", telegram_message_id=message_id,
                text=f"сообщение {message_id}", raw_update_json={},
            )
            await s.commit()

    await asyncio.gather(*(save(i) for i in range(1, 31)))

    async with factory() as s:
        assert (await s.execute(select(func.count()).select_from(Message))).scalar() == 30
    await engine.dispose()


# --- Scenario 14: scheduled digests ------------------------------------------


def _local(hour: int, minute: int = 0, day: int = 4) -> datetime:
    return ALMATY.localize(datetime(2026, 11, day, hour, minute))


@pytest.mark.parametrize("digest_time", ["18:00", "20:00", "21:00", "23:00", "09:37"])
def test_due_exactly_at_any_configured_time(digest_time):
    hour, minute = map(int, digest_time.split(":"))
    assert due_slot(digest_time, _local(hour, minute)) == _local(hour, minute).date()


def test_not_due_before_time():
    assert due_slot("18:00", _local(17, 59), catchup_hours=6) is None


def test_missed_slot_is_caught_up_within_window():
    """Regression: a restart at exactly 18:00 used to cost the day's digest."""
    assert due_slot("18:00", _local(18, 1)) is None
    assert due_slot("18:00", _local(18, 1), catchup_hours=6) == _local(18).date()
    assert due_slot("18:00", _local(23, 59), catchup_hours=6) == _local(18).date()


def test_catch_up_window_ends():
    assert due_slot("12:00", _local(19), catchup_hours=6) is None


def test_late_slot_is_caught_up_after_midnight():
    assert due_slot("23:00", _local(1, day=5), catchup_hours=6) == _local(23).date()


def test_weekly_key_is_iso_week():
    assert weekly_key(_local(12).date()) == "2026-W45"


def _one_task(messages, topics):
    return {"topics": [{
        "title": "Тема", "summary": "Обсудили.", "message_ids": [m.id for m in messages],
        "items": [{"kind": "task", "text": "Сделать работу", "source_ids": [messages[0].id]}],
    }]}


@pytest.mark.asyncio
async def test_scheduled_digest_is_sent_once(session):
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", "Сообщение")], start=datetime.now(pytz.utc) - timedelta(hours=1))
    bot = FakeBot()
    llm = ScriptedLLM(_one_task)

    await run_scheduled_digest(bot, session, llm, chat, "daily", "2026-11-04")
    await run_scheduled_digest(bot, session, llm, chat, "daily", "2026-11-04")

    digests = [m for m in bot.sent if "digest" in m["text"]]
    assert len(digests) == 1
    assert "Сделать работу" in digests[0]["text"]
    assert digests[0]["chat_id"] == chat.telegram_chat_id


@pytest.mark.asyncio
async def test_scheduled_digest_retries_until_delivered(session, monkeypatch):
    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    chat = await make_chat(session)
    method = SimpleNamespace()
    bot = FakeBot([TelegramNetworkError(method=method, message="net") for _ in range(4)])
    llm = ScriptedLLM({"topics": []})

    await run_scheduled_digest(bot, session, llm, chat, "daily", "2026-11-04")
    digest = await DigestRepository(session).get_slot(chat.id, "daily", "2026-11-04")
    assert digest.sent_at is None

    await run_scheduled_digest(bot, session, llm, chat, "daily", "2026-11-04")
    await session.refresh(digest)
    assert digest.sent_at is not None
    assert (await session.execute(select(func.count()).select_from(Digest))).scalar() == 1


@pytest.mark.asyncio
async def test_manual_digest_does_not_cancel_the_scheduled_one(session):
    """Regression: any /digest during the day silently cancelled the evening digest."""
    chat = await make_chat(session)
    llm = ScriptedLLM({"topics": []})
    service = DigestService(session, llm)
    manual = await service.build_digest(chat, BASE_TIME, BASE_TIME + timedelta(hours=1))
    await session.commit()
    bot = FakeBot()
    await service.deliver(bot, manual, chat.telegram_chat_id)

    await run_scheduled_digest(bot, session, llm, chat, "daily", "2026-11-04")

    assert len(bot.sent) == 2


@pytest.mark.asyncio
async def test_daily_and_weekly_are_independent_slots(session):
    chat = await make_chat(session)
    bot = FakeBot()
    llm = ScriptedLLM({"topics": []})

    await run_scheduled_digest(bot, session, llm, chat, "daily", "2026-11-06")
    await run_scheduled_digest(bot, session, llm, chat, "weekly", "2026-W45")

    assert "Daily digest" in bot.sent[0]["text"]
    assert "Weekly digest" in bot.sent[1]["text"]


@pytest.mark.asyncio
async def test_next_daily_digest_starts_where_the_last_one_ended(session):
    chat = await make_chat(session)
    bot = FakeBot()
    llm = ScriptedLLM({"topics": []})

    await run_scheduled_digest(bot, session, llm, chat, "daily", "2026-11-04")
    await run_scheduled_digest(bot, session, llm, chat, "daily", "2026-11-05")

    rows = (await session.execute(select(Digest).order_by(Digest.id))).scalars().all()
    assert rows[1].period_start == rows[0].period_end


@pytest.mark.asyncio
async def test_digest_goes_to_the_configured_target_chat(session):
    chat = await make_chat(session)
    chat.digest_chat_id = -100777
    await session.commit()
    bot = FakeBot()

    await run_scheduled_digest(bot, session, ScriptedLLM({"topics": []}), chat, "daily", "2026-11-04")

    assert bot.sent[0]["chat_id"] == -100777
