from __future__ import annotations

import pytest

from app.db.models import TaskStatus
from app.db.repositories import ChatRepository, ClarificationRepository, TaskRepository
from app.llm.mock_client import MockLLMClient
from app.services.message_ingestor import MessageIngestor
from tests.helpers import analyze, feed, make_chat


async def _ingest(session, text, message_id=1, chat_id=-100200, **extra):
    return await MessageIngestor(session).ingest(
        telegram_chat_id=chat_id,
        chat_title="Team",
        telegram_user_id=1,
        username="lead",
        display_name="Lead",
        telegram_message_id=message_id,
        text=text,
        raw_update_json={"message_id": message_id},
        **extra,
    )


@pytest.mark.asyncio
async def test_ingest_saves_and_queues_without_calling_a_model(session):
    message, created = await _ingest(session, "Нужно реализовать оплату картой")
    await session.commit()

    assert created is True
    assert message.pending_analysis is True
    assert message.sent_at is not None
    chat = await ChatRepository(session).get_or_create(-100200)
    assert await TaskRepository(session).get_active(chat.id) == []


@pytest.mark.asyncio
async def test_blank_text_is_stored_but_not_queued(session):
    message, _ = await _ingest(session, "   ")
    assert message.text is None
    assert message.pending_analysis is False


@pytest.mark.asyncio
async def test_task_from_batch_records_reporter_and_asks_for_owner(session):
    chat = await make_chat(session)
    await _ingest(session, "Нужно реализовать оплату картой", chat_id=chat.telegram_chat_id)
    await session.commit()

    results = await analyze(session, chat, MockLLMClient())

    tasks = await TaskRepository(session).get_active(chat.id)
    assert len(tasks) == 1
    assert "оплат" in tasks[0].title.lower()
    assert tasks[0].status == TaskStatus.needs_clarification
    assert tasks[0].reporter_text == "@lead"
    assert results[0].clarification is not None


@pytest.mark.asyncio
async def test_noise_creates_nothing(session):
    chat = await make_chat(session)
    await feed(session, chat, [("Шутник", "😂")])
    await analyze(session, chat, MockLLMClient())

    assert await TaskRepository(session).get_active(chat.id) == []


@pytest.mark.asyncio
async def test_clarifications_can_be_switched_off(session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "clarifications_enabled", False)
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", "Нужно обновить документацию")])

    results = await analyze(session, chat, MockLLMClient())

    assert results[0].clarification is None


@pytest.mark.asyncio
async def test_reply_to_clarification_closes_it_and_still_gets_analyzed(session):
    chat = await make_chat(session)
    task = await TaskRepository(session).create(
        chat.id, "Оплата", status=TaskStatus.needs_clarification
    )
    clarifications = ClarificationRepository(session)
    clarification = await clarifications.create(task_id=task.id, question="Кто?")
    await clarifications.set_question_message_id(clarification.id, 555)
    await session.commit()

    message, _ = await _ingest(
        session, "Это сделает Коля", chat_id=chat.telegram_chat_id, reply_to_message_id=555
    )
    await session.commit()

    assert await clarifications.get_pending_for_task(task.id) == []
    assert message.pending_analysis is True
