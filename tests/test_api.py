from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routes import (
    chat_dashboard,
    dashboard,
    list_chat_tasks,
    list_chats,
    update_task_status,
)
from app.api.schemas import TaskStatusUpdate
from app.db.models import TaskStatus
from app.db.repositories import ChatRepository, TaskRepository
from app.utils.dashboard_auth import make_chat_token


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


@pytest.mark.asyncio
async def test_endpoints_open_when_secret_unset(session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "dashboard_secret", "")
    chat_repo = ChatRepository(session)
    chat = await chat_repo.get_or_create(telegram_chat_id=-400)
    await session.commit()

    # No token supplied, but DASHBOARD_SECRET is empty -> allowed (dev default).
    await list_chats(session=session)
    await list_chat_tasks(chat.id, status=None, session=session)


@pytest.mark.asyncio
async def test_endpoints_require_token_when_secret_set(session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "dashboard_secret", "s3cret")
    chat_repo = ChatRepository(session)
    chat = await chat_repo.get_or_create(telegram_chat_id=-500)
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await chat_dashboard(chat.id, token="", session=session)
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException) as exc:
        await chat_dashboard(chat.id, token="wrong", session=session)
    assert exc.value.status_code == 403

    resp = await chat_dashboard(chat.id, token=make_chat_token(chat.id), session=session)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_update_task_status_changes_task(session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "dashboard_secret", "s3cret")
    chat_repo = ChatRepository(session)
    task_repo = TaskRepository(session)
    chat = await chat_repo.get_or_create(telegram_chat_id=-600)
    task = await task_repo.create(chat_id=chat.id, title="Deploy", status=TaskStatus.new)
    await session.commit()

    token = make_chat_token(chat.id)
    out = await update_task_status(
        chat.id, task.id, TaskStatusUpdate(status="done"), token=token, session=session
    )
    assert out.status == "done"


@pytest.mark.asyncio
async def test_update_task_status_rejects_wrong_chat_token(session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "dashboard_secret", "s3cret")
    chat_repo = ChatRepository(session)
    task_repo = TaskRepository(session)
    chat_a = await chat_repo.get_or_create(telegram_chat_id=-700)
    chat_b = await chat_repo.get_or_create(telegram_chat_id=-701)
    task = await task_repo.create(chat_id=chat_a.id, title="Deploy", status=TaskStatus.new)
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await update_task_status(
            chat_a.id,
            task.id,
            TaskStatusUpdate(status="done"),
            token=make_chat_token(chat_b.id),
            session=session,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_update_task_status_rejects_invalid_status(session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "dashboard_secret", "")
    chat_repo = ChatRepository(session)
    task_repo = TaskRepository(session)
    chat = await chat_repo.get_or_create(telegram_chat_id=-800)
    task = await task_repo.create(chat_id=chat.id, title="Deploy", status=TaskStatus.new)
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await update_task_status(
            chat.id, task.id, TaskStatusUpdate(status="not_a_status"), token="", session=session
        )
    assert exc.value.status_code == 400
