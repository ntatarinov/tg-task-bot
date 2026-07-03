from __future__ import annotations

import pytest

from app.db.models import TaskStatus
from app.db.repositories import ChatRepository, TaskRepository
from app.llm.mock_client import MockLLMClient
from app.services.clarification_service import ClarificationToAsk
from app.services.message_ingestor import MessageIngestor
from app.services.task_analyzer import TaskAnalyzer


def _ingestor(session):
    analyzer = TaskAnalyzer(session, MockLLMClient())
    return MessageIngestor(session, analyzer)


@pytest.mark.asyncio
async def test_ingest_creates_task_and_asks_clarification(session):
    ingestor = _ingestor(session)
    msg, to_ask = await ingestor.ingest(
        telegram_chat_id=-100200,
        chat_title="Team",
        telegram_user_id=1,
        username="lead",
        display_name="Lead",
        telegram_message_id=1,
        text="Нужно реализовать оплату картой",
        raw_update_json={"message_id": 1},
    )
    await session.commit()

    assert msg is not None
    assert isinstance(to_ask, ClarificationToAsk)  # no assignee → mock asks who

    chat = await ChatRepository(session).get_or_create(-100200)
    tasks = await TaskRepository(session).get_active(chat.id)
    assert len(tasks) == 1
    assert "оплат" in tasks[0].title.lower()
    assert tasks[0].status == TaskStatus.needs_clarification
    # The task records who reported it (author of the source message).
    assert tasks[0].reporter_text == "@lead"


@pytest.mark.asyncio
async def test_ingest_empty_message_is_ignored(session):
    ingestor = _ingestor(session)
    msg, to_ask = await ingestor.ingest(
        telegram_chat_id=-100201,
        chat_title=None,
        telegram_user_id=None,
        username=None,
        display_name=None,
        telegram_message_id=2,
        text="   ",
        raw_update_json={},
    )
    assert msg is None
    assert to_ask is None


@pytest.mark.asyncio
async def test_ingest_noise_saves_message_without_task(session):
    ingestor = _ingestor(session)
    msg, to_ask = await ingestor.ingest(
        telegram_chat_id=-100202,
        chat_title=None,
        telegram_user_id=5,
        username="joker",
        display_name="Joker",
        telegram_message_id=3,
        text="😂",
        raw_update_json={},
    )
    await session.commit()

    assert msg is not None  # message is still persisted
    assert to_ask is None

    chat = await ChatRepository(session).get_or_create(-100202)
    tasks = await TaskRepository(session).get_active(chat.id)
    assert tasks == []
