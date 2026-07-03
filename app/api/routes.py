from __future__ import annotations

import html

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ChatOut, TaskOut
from app.db.models import Task
from app.db.repositories import ChatRepository, TaskRepository
from app.db.session import get_session

router = APIRouter()


def _task_to_out(task: Task) -> TaskOut:
    return TaskOut(
        id=task.id,
        title=task.title,
        description=task.description,
        assignee_text=task.assignee_text,
        reporter_text=task.reporter_text,
        status=task.status.value,
        priority=task.priority.value,
        deadline=task.deadline,
        confidence=task.confidence,
        created_at=task.created_at,
    )


@router.get("/api/chats", response_model=list[ChatOut])
async def list_chats(session: AsyncSession = Depends(get_session)) -> list[ChatOut]:
    chat_repo = ChatRepository(session)
    task_repo = TaskRepository(session)
    chats = await chat_repo.get_all_active()
    result: list[ChatOut] = []
    for chat in chats:
        active = await task_repo.get_active(chat.id)
        result.append(
            ChatOut(
                id=chat.id,
                telegram_chat_id=chat.telegram_chat_id,
                title=chat.title,
                timezone=chat.timezone,
                digest_time=chat.digest_time,
                is_active=chat.is_active,
                active_tasks=len(active),
            )
        )
    return result


@router.get("/api/chats/{chat_id}/tasks", response_model=list[TaskOut])
async def list_chat_tasks(
    chat_id: int,
    status: str | None = Query(default=None, description="Filter by task status"),
    session: AsyncSession = Depends(get_session),
) -> list[TaskOut]:
    chat_repo = ChatRepository(session)
    chat = await chat_repo.get_by_id(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    task_repo = TaskRepository(session)
    tasks = await task_repo.get_all_for_chat(chat_id)
    if status:
        tasks = [t for t in tasks if t.status.value == status]
    return [_task_to_out(t) for t in tasks]


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    """Minimal read-only web panel: active chats and their tasks."""
    chat_repo = ChatRepository(session)
    task_repo = TaskRepository(session)
    chats = await chat_repo.get_all_active()

    blocks: list[str] = []
    for chat in chats:
        tasks = await task_repo.get_all_for_chat(chat.id)
        rows = "".join(
            f"<tr>"
            f"<td>{t.id}</td>"
            f"<td>{html.escape(t.title)}</td>"
            f"<td>{html.escape(t.status.value)}</td>"
            f"<td>{html.escape(t.priority.value)}</td>"
            f"<td>{html.escape(t.assignee_text or '—')}</td>"
            f"</tr>"
            for t in tasks
        )
        title = html.escape(chat.title or f"chat {chat.telegram_chat_id}")
        blocks.append(
            f"<section><h2>{title} "
            f"<small>({html.escape(chat.timezone)}, дайджест {html.escape(chat.digest_time)})</small></h2>"
            f"<table><thead><tr><th>ID</th><th>Задача</th><th>Статус</th>"
            f"<th>Приоритет</th><th>Исполнитель</th></tr></thead>"
            f"<tbody>{rows or '<tr><td colspan=5>Задач нет</td></tr>'}</tbody></table></section>"
        )

    body = "".join(blocks) or "<p>Активных чатов нет.</p>"
    page = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Тудушка — Dashboard</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #222; }}
 h1 {{ margin-bottom: 1rem; }}
 section {{ margin-bottom: 2rem; }}
 table {{ border-collapse: collapse; width: 100%; }}
 th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; font-size: 14px; }}
 th {{ background: #f4f4f4; }}
 small {{ color: #777; font-weight: normal; }}
</style></head>
<body><h1>📋 Тудушка — задачи по чатам</h1>{body}</body></html>"""
    return HTMLResponse(content=page)
