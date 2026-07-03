from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import Task, TaskPriority, TaskStatus
from app.db.repositories import ChatRepository, TaskRepository


@pytest.mark.asyncio
async def test_create_task(session):
    chat_repo = ChatRepository(session)
    task_repo = TaskRepository(session)

    chat = await chat_repo.get_or_create(telegram_chat_id=-100111222)
    task = await task_repo.create(
        chat_id=chat.id,
        title="Реализовать авторизацию",
        description="Добавить JWT-авторизацию",
        assignee_text="@developer",
        priority=TaskPriority.high,
        source="llm",
        confidence=0.85,
    )
    await session.commit()

    result = await session.execute(select(Task).where(Task.id == task.id))
    saved = result.scalar_one()

    assert saved.title == "Реализовать авторизацию"
    assert saved.priority == TaskPriority.high
    assert saved.status == TaskStatus.new
    assert saved.confidence == pytest.approx(0.85)
    assert saved.source == "llm"


@pytest.mark.asyncio
async def test_get_active_tasks(session):
    chat_repo = ChatRepository(session)
    task_repo = TaskRepository(session)

    chat = await chat_repo.get_or_create(telegram_chat_id=-100333444)
    await task_repo.create(chat_id=chat.id, title="Активная задача", status=TaskStatus.new)
    await task_repo.create(chat_id=chat.id, title="Выполненная задача", status=TaskStatus.done)
    await task_repo.create(chat_id=chat.id, title="Отменённая задача", status=TaskStatus.cancelled)
    await session.commit()

    active = await task_repo.get_active(chat.id)
    assert len(active) == 1
    assert active[0].title == "Активная задача"


@pytest.mark.asyncio
async def test_update_task(session):
    chat_repo = ChatRepository(session)
    task_repo = TaskRepository(session)

    chat = await chat_repo.get_or_create(telegram_chat_id=-100555666)
    task = await task_repo.create(chat_id=chat.id, title="Задача без исполнителя")
    await task_repo.update(task, assignee_text="@kolya", status=TaskStatus.in_progress)
    await session.commit()

    result = await session.execute(select(Task).where(Task.id == task.id))
    updated = result.scalar_one()

    assert updated.assignee_text == "@kolya"
    assert updated.status == TaskStatus.in_progress
