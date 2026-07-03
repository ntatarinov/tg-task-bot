from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.db.models import TaskPriority, TaskStatus
from app.db.repositories import ChatRepository, MessageRepository, TaskRepository, UserRepository
from app.llm.schemas import DigestSummary
from app.services.digest_service import DigestService


@pytest.mark.asyncio
async def test_digest_with_tasks(session):
    chat_repo = ChatRepository(session)
    task_repo = TaskRepository(session)
    msg_repo = MessageRepository(session)
    user_repo = UserRepository(session)

    chat = await chat_repo.get_or_create(telegram_chat_id=-100888999)
    user = await user_repo.get_or_create(telegram_user_id=42, username="dev")

    # Message in last 24h
    await msg_repo.create(
        chat_id=chat.id,
        user_id=user.id,
        telegram_message_id=1,
        text="Нужно добавить кэширование",
    )

    await task_repo.create(
        chat_id=chat.id,
        title="Добавить кэширование Redis",
        description="Реализовать кэш для API",
        assignee_text="@dev",
        status=TaskStatus.new,
        priority=TaskPriority.high,
    )
    await session.commit()

    mock_llm = AsyncMock()
    mock_llm.summarize_for_digest = AsyncMock(
        return_value=DigestSummary(
            key_points=["Обсуждали кэширование"],
            summary="Запланировали кэш",
        )
    )

    svc = DigestService(session, mock_llm)
    text = await svc.build_digest(chat, period_hours=24)

    assert "Дайджест" in text
    assert "Добавить кэширование Redis" in text
    assert "Обсуждали кэширование" in text


@pytest.mark.asyncio
async def test_digest_no_tasks(session):
    chat_repo = ChatRepository(session)
    chat = await chat_repo.get_or_create(telegram_chat_id=-100000001)
    await session.commit()

    mock_llm = AsyncMock()
    mock_llm.summarize_for_digest = AsyncMock(return_value=DigestSummary())

    svc = DigestService(session, mock_llm)
    text = await svc.build_digest(chat)

    assert "Новых задач за период не выявлено" in text


@pytest.mark.asyncio
async def test_mock_llm_analyze_task_signal(session):
    from app.llm.mock_client import MockLLMClient

    client = MockLLMClient()
    result = await client.analyze_message(
        message_text="Нужно сделать авторизацию через JWT",
        context_messages=[],
        active_tasks=[],
    )

    assert result.contains_task is True
    assert result.message_type == "task_signal"
    assert result.confidence > 0


@pytest.mark.asyncio
async def test_mock_llm_noise_detection(session):
    from app.llm.mock_client import MockLLMClient

    client = MockLLMClient()
    result = await client.analyze_message(
        message_text="😂",
        context_messages=[],
        active_tasks=[],
    )
    assert result.contains_task is False
    assert result.message_type == "noise"
