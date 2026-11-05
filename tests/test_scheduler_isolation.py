"""One chat failing must not take the others down with it."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import dispatcher
from app.config import settings
from app.db.models import Digest, Message
from app.services import scheduler
from tests.helpers import FakeBot, ScriptedLLM, feed, make_chat

FIRST, SECOND = -1001, -1002


def _crash_on_first_chat(messages, topics):
    if any("первый" in m.text for m in messages):
        raise RuntimeError("bug in one chat")
    return {"topics": []}


@pytest.fixture
def tick_env(monkeypatch, engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(scheduler, "async_session_factory", factory)
    monkeypatch.setattr(settings, "allowed_chat_ids", f"{FIRST},{SECOND}")
    return factory


@pytest.mark.asyncio
async def test_analysis_tick_survives_one_chat_failing(tick_env, monkeypatch):
    monkeypatch.setattr(scheduler, "_make_llm", lambda: ScriptedLLM(_crash_on_first_chat))
    async with tick_env() as session:
        await feed(session, await make_chat(session, FIRST), [("Аня", "первый чат")])
        await feed(session, await make_chat(session, SECOND), [("Аня", "второй чат")])

    await scheduler.analysis_tick()

    async with tick_env() as session:
        rows = (await session.execute(select(Message.text, Message.pending_analysis))).all()
    assert dict(rows) == {"первый чат": True, "второй чат": False}


@pytest.mark.asyncio
async def test_digest_tick_survives_one_chat_failing(tick_env, monkeypatch):
    bot = FakeBot()
    monkeypatch.setattr(dispatcher, "_bot", bot)
    monkeypatch.setattr(scheduler, "_make_llm", lambda: ScriptedLLM({"topics": []}))
    monkeypatch.setattr(scheduler, "due_slot", lambda *args: date(2026, 11, 2))

    original = scheduler.run_scheduled_digest

    async def crash_first(bot_, session, llm, chat, kind, key):
        if chat.telegram_chat_id == FIRST:
            await session.execute(select(1))
            raise RuntimeError("bug in one chat")
        await original(bot_, session, llm, chat, kind, key)

    monkeypatch.setattr(scheduler, "run_scheduled_digest", crash_first)
    async with tick_env() as session:
        await make_chat(session, FIRST)
        await make_chat(session, SECOND)

    await scheduler.digest_tick()

    assert [m["chat_id"] for m in bot.sent] == [SECOND]
    async with tick_env() as session:
        assert len((await session.execute(select(Digest))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_disabled_digest_and_weekly_day_are_respected(tick_env, monkeypatch):
    bot = FakeBot()
    monkeypatch.setattr(dispatcher, "_bot", bot)
    monkeypatch.setattr(scheduler, "_make_llm", lambda: ScriptedLLM({"topics": []}))
    friday = date(2026, 11, 6)
    monkeypatch.setattr(scheduler, "due_slot", lambda *args: friday)

    async with tick_env() as session:
        enabled = await make_chat(session, FIRST)
        disabled = await make_chat(session, SECOND)
        disabled.digest_enabled = False
        assert enabled.weekly_digest_day == friday.weekday()
        await session.commit()

    await scheduler.digest_tick()

    titles = [m["text"].split(" — ")[0] for m in bot.sent]
    assert titles == ["<b>Daily digest", "<b>Weekly digest"]
    assert {m["chat_id"] for m in bot.sent} == {FIRST}
