from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routes import dashboard, list_chat_tasks, list_chats
from app.db.models import TaskStatus
from app.db.repositories import ChatRepository, TaskRepository


@pytest.mark.asyncio
async def test_list_chats_reports_active_task_count(session):
    chat_repo = ChatRepository(session)
    task_repo = TaskRepository(session)
    chat = await chat_repo.get_or_create(telegram_chat_id=-100, title="Team")
    await task_repo.create(chat_id=chat.id, title="A", status=TaskStatus.new)
    await task_repo.create(chat_id=chat.id, title="B", status=TaskStatus.done)
    await session.commit()

    chats = await list_chats(session=session)
    assert len(chats) == 1
    assert chats[0].title == "Team"
    assert chats[0].active_tasks == 1  # done is excluded


@pytest.mark.asyncio
async def test_list_chat_tasks_filters_by_status(session):
    chat_repo = ChatRepository(session)
    task_repo = TaskRepository(session)
    chat = await chat_repo.get_or_create(telegram_chat_id=-200)
    await task_repo.create(chat_id=chat.id, title="open", status=TaskStatus.new)
    await task_repo.create(chat_id=chat.id, title="closed", status=TaskStatus.done)
    await session.commit()

    all_tasks = await list_chat_tasks(chat.id, status=None, session=session)
    assert len(all_tasks) == 2

    done_only = await list_chat_tasks(chat.id, status="done", session=session)
    assert [t.title for t in done_only] == ["closed"]


@pytest.mark.asyncio
async def test_list_chat_tasks_unknown_chat_404(session):
    with pytest.raises(HTTPException) as exc:
        await list_chat_tasks(99999, status=None, session=session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_renders_task_title(session):
    chat_repo = ChatRepository(session)
    task_repo = TaskRepository(session)
    chat = await chat_repo.get_or_create(telegram_chat_id=-300, title="Squad")
    await task_repo.create(chat_id=chat.id, title="Починить деплой", status=TaskStatus.new)
    await session.commit()

    resp = await dashboard(session=session)
    html = resp.body.decode("utf-8")
    assert "Squad" in html
    assert "Починить деплой" in html
