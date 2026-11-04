from __future__ import annotations

import html
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ChatOut, TaskOut, TaskStatusUpdate
from app.config import settings
from app.db.models import Chat, Task, TaskStatus
from app.db.repositories import ChatRepository, TaskRepository
from app.db.session import get_session
from app.utils.dashboard_auth import verify_chat_token, verify_master_token
from app.utils.task_labels import PRIORITY_EMOJI, STATUS_LABELS

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


def _open_access_allowed() -> bool:
    """No secret means no protection, which is acceptable only on a developer machine."""
    return not settings.dashboard_secret and settings.app_env == "local"


def _require_master_token(token: str) -> None:
    if not _open_access_allowed() and not verify_master_token(token):
        raise HTTPException(status_code=403, detail="Invalid token")


def _require_chat_token(chat_id: int, token: str) -> None:
    if not _open_access_allowed() and not verify_chat_token(chat_id, token):
        raise HTTPException(status_code=403, detail="Invalid token")


@router.get("/api/chats", response_model=list[ChatOut])
async def list_chats(
    token: str = "",
    session: AsyncSession = Depends(get_session),
) -> list[ChatOut]:
    _require_master_token(token)
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
    token: str = "",
    session: AsyncSession = Depends(get_session),
) -> list[TaskOut]:
    _require_chat_token(chat_id, token)
    chat_repo = ChatRepository(session)
    chat = await chat_repo.get_by_id(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    task_repo = TaskRepository(session)
    tasks = await task_repo.get_all_for_chat(chat_id)
    if status:
        tasks = [t for t in tasks if t.status.value == status]
    return [_task_to_out(t) for t in tasks]


@router.patch("/api/chats/{chat_id}/tasks/{task_id}/status", response_model=TaskOut)
async def update_task_status(
    chat_id: int,
    task_id: int,
    payload: TaskStatusUpdate,
    token: str = "",
    session: AsyncSession = Depends(get_session),
) -> TaskOut:
    _require_chat_token(chat_id, token)
    try:
        new_status = TaskStatus(payload.status)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid status") from None

    task_repo = TaskRepository(session)
    task = await task_repo.get_by_id(task_id)
    if task is None or task.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="Task not found")

    await task_repo.set_status(task, new_status)
    return _task_to_out(task)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    token: str = "",
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Read-only overview of all chats. Requires the master token once DASHBOARD_SECRET is set."""
    _require_master_token(token)
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
        link = f'<a href="/dashboard/{chat.id}?token={token}">открыть</a>' if token else ""
        blocks.append(
            f"<section><h2>{title} "
            f"<small>({html.escape(chat.timezone)}, дайджест {html.escape(chat.digest_time)})</small> "
            f"{link}</h2>"
            f"<table><thead><tr><th>ID</th><th>Задача</th><th>Статус</th>"
            f"<th>Приоритет</th><th>Исполнитель</th></tr></thead>"
            f"<tbody>{rows or '<tr><td colspan=5>Задач нет</td></tr>'}</tbody></table></section>"
        )

    body = "".join(blocks) or "<p>Активных чатов нет.</p>"
    page = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Digest — Dashboard</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #222; }}
 h1 {{ margin-bottom: 1rem; }}
 section {{ margin-bottom: 2rem; }}
 table {{ border-collapse: collapse; width: 100%; }}
 th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; font-size: 14px; }}
 th {{ background: #f4f4f4; }}
 small {{ color: #777; font-weight: normal; }}
</style></head>
<body><h1>📋 Digest — задачи по чатам</h1>{body}</body></html>"""
    return HTMLResponse(content=page)


def _safe_script_json(obj: object) -> str:
    """json.dumps, but safe to embed inside a <script> tag: task titles are
    user-controlled text and could otherwise contain a literal '</script>'."""
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")


def _render_chat_dashboard(chat: Chat, tasks: list[Task], token: str) -> str:
    task_payload = [
        {
            "id": t.id,
            "title": t.title,
            "status": t.status.value,
            "priority": t.priority.value,
            "assignee": t.assignee_text,
            "reporter": t.reporter_text,
            "deadline": t.deadline,
        }
        for t in tasks
    ]
    title = html.escape(chat.title or f"chat {chat.telegram_chat_id}")
    status_labels_json = _safe_script_json(STATUS_LABELS)
    priority_emoji_json = _safe_script_json(PRIORITY_EMOJI)
    tasks_json = _safe_script_json(task_payload)

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — задачи</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: system-ui, -apple-system, sans-serif;
    margin: 0; padding: 1rem 1rem 3rem;
    max-width: 720px; margin-inline: auto;
    background: #fafafa; color: #1a1a1a;
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #121212; color: #eee; }}
    .card {{ background: #1e1e1e !important; border-color: #333 !important; }}
    .filters button {{ background: #222 !important; color: #eee !important; border-color: #444 !important; }}
    .filters button.active {{ background: #3a7afe !important; color: #fff !important; }}
  }}
  h1 {{ font-size: 1.25rem; margin: 0.25rem 0 1rem; }}
  .filters {{ display: flex; gap: 0.4rem; flex-wrap: wrap; margin-bottom: 1rem; }}
  .filters button {{
    border: 1px solid #ddd; background: #fff; border-radius: 999px;
    padding: 0.35rem 0.8rem; font-size: 0.85rem; cursor: pointer;
  }}
  .filters button.active {{ background: #3a7afe; color: #fff; border-color: #3a7afe; }}
  .card {{
    border: 1px solid #e2e2e2; border-radius: 10px; padding: 0.8rem 1rem;
    margin-bottom: 0.7rem; background: #fff;
  }}
  .card h3 {{ margin: 0 0 0.3rem; font-size: 1rem; }}
  .card .status {{ font-size: 0.85rem; margin-bottom: 0.4rem; opacity: 0.85; }}
  .card .meta {{ font-size: 0.85rem; opacity: 0.75; margin: 0.15rem 0; }}
  .card .actions {{ margin-top: 0.6rem; display: flex; gap: 0.4rem; flex-wrap: wrap; }}
  .card button.action {{
    border: none; border-radius: 8px; padding: 0.4rem 0.7rem;
    font-size: 0.85rem; cursor: pointer; color: #fff;
  }}
  .btn-progress {{ background: #3a7afe; }}
  .btn-done {{ background: #2fae5b; }}
  .btn-cancel {{ background: #e05252; }}
  .btn-reopen {{ background: #8a8a8a; }}
  .empty {{ opacity: 0.7; padding: 2rem 0; text-align: center; }}
</style>
</head>
<body>
<h1>📋 {title}</h1>
<div class="filters" id="filters"></div>
<div id="list"></div>
<div class="empty" id="empty" hidden>Ничего не найдено</div>

<script>
const CHAT_ID = {chat.id};
const TOKEN = {json.dumps(token)};
const STATUS_LABELS = {status_labels_json};
const PRIORITY_EMOJI = {priority_emoji_json};
let tasks = {tasks_json};
let currentFilter = "active";

function isActive(t) {{ return t.status !== "done" && t.status !== "cancelled"; }}

function esc(s) {{
  const d = document.createElement("div");
  d.textContent = s == null ? "" : s;
  return d.innerHTML;
}}

function renderFilters() {{
  const groups = [
    ["active", "Активные"],
    ["all", "Все"],
    ["done", "Готово"],
    ["cancelled", "Отменено"],
  ];
  const el = document.getElementById("filters");
  el.innerHTML = groups.map(([key, label]) =>
    `<button data-filter="${{key}}" class="${{key === currentFilter ? 'active' : ''}}">${{label}}</button>`
  ).join("");
  el.querySelectorAll("button").forEach(btn => {{
    btn.addEventListener("click", () => {{
      currentFilter = btn.dataset.filter;
      renderFilters();
      renderList();
    }});
  }});
}}

function actionsFor(task) {{
  if (task.status === "done" || task.status === "cancelled") {{
    return [["new", "🔄 Возобновить", "btn-reopen"]];
  }}
  return [
    ["in_progress", "🔄 В работу", "btn-progress"],
    ["done", "✔️ Готово", "btn-done"],
    ["cancelled", "❌ Отмена", "btn-cancel"],
  ];
}}

function renderList() {{
  const filtered = tasks.filter(t => {{
    if (currentFilter === "active") return isActive(t);
    if (currentFilter === "all") return true;
    return t.status === currentFilter;
  }});
  const list = document.getElementById("list");
  const empty = document.getElementById("empty");
  if (filtered.length === 0) {{
    list.innerHTML = "";
    empty.hidden = false;
    return;
  }}
  empty.hidden = true;
  list.innerHTML = filtered.map(t => `
    <div class="card" id="task-${{t.id}}">
      <h3>${{PRIORITY_EMOJI[t.priority] || ""}} ${{esc(t.title)}}</h3>
      <div class="status">${{STATUS_LABELS[t.status] || esc(t.status)}}</div>
      ${{t.reporter ? `<div class="meta">🙋 От: ${{esc(t.reporter)}}</div>` : ""}}
      ${{t.assignee ? `<div class="meta">👤 Исполнитель: ${{esc(t.assignee)}}</div>` : ""}}
      ${{t.deadline ? `<div class="meta">📅 ${{esc(t.deadline)}}</div>` : ""}}
      <div class="actions">
        ${{actionsFor(t).map(([status, label, cls]) =>
          `<button class="action ${{cls}}" data-id="${{t.id}}" data-status="${{status}}">${{label}}</button>`
        ).join("")}}
      </div>
    </div>
  `).join("");
  list.querySelectorAll("button.action").forEach(btn => {{
    btn.addEventListener("click", () => updateStatus(Number(btn.dataset.id), btn.dataset.status));
  }});
}}

async function updateStatus(taskId, status) {{
  const res = await fetch(`/api/chats/${{CHAT_ID}}/tasks/${{taskId}}/status?token=${{encodeURIComponent(TOKEN)}}`, {{
    method: "PATCH",
    headers: {{ "Content-Type": "application/json" }},
    body: JSON.stringify({{ status }}),
  }});
  if (!res.ok) {{
    alert("Не удалось обновить статус");
    return;
  }}
  const updated = await res.json();
  tasks = tasks.map(t => t.id === taskId ? {{ ...t, status: updated.status }} : t);
  renderList();
}}

renderFilters();
renderList();
</script>
</body>
</html>"""


@router.get("/dashboard/{chat_id}", response_class=HTMLResponse)
async def chat_dashboard(
    chat_id: int,
    token: str = "",
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Interactive per-chat task list opened from the bot's '🌐 Открыть на сайте' button."""
    _require_chat_token(chat_id, token)
    chat_repo = ChatRepository(session)
    chat = await chat_repo.get_by_id(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    task_repo = TaskRepository(session)
    tasks = await task_repo.get_all_for_chat(chat_id)
    return HTMLResponse(content=_render_chat_dashboard(chat, tasks, token))
