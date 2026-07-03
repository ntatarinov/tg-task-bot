from __future__ import annotations

from datetime import datetime, timedelta

import pytz
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chat, Task, TaskStatus
from app.db.repositories import DigestRepository, MessageRepository, TaskRepository
from app.llm.base import BaseLLMClient
from app.utils.logging import get_logger
from app.utils.time import now_utc

logger = get_logger(__name__)

STATUS_LABELS: dict[str, str] = {
    TaskStatus.new.value: "Новая",
    TaskStatus.needs_clarification.value: "Нужно уточнение",
    TaskStatus.ready.value: "Готова к выполнению",
    TaskStatus.in_progress.value: "В работе",
    TaskStatus.done.value: "Выполнена",
    TaskStatus.cancelled.value: "Отменена",
}

PRIORITY_LABELS: dict[str, str] = {
    "low": "низкий",
    "medium": "средний",
    "high": "высокий",
    "critical": "критический",
}


def _format_task_block(task: Task) -> str:
    lines = [f"*{task.title}*"]
    if task.reporter_text:
        lines.append(f"  От кого: {task.reporter_text}")
    lines.append(f"  Исполнитель: {task.assignee_text or 'не указан'}")
    lines.append(f"  Статус: {STATUS_LABELS.get(task.status.value, task.status.value)}")
    lines.append(f"  Приоритет: {PRIORITY_LABELS.get(task.priority.value, task.priority.value)}")
    if task.description:
        lines.append(f"  Описание: {task.description[:300]}")
    if task.deadline:
        lines.append(f"  Дедлайн: {task.deadline}")
    if task.requirements:
        lines.append("  Требования:")
        for req_line in task.requirements.splitlines():
            lines.append(f"    {req_line}")
    if task.open_questions:
        lines.append("  Открытые вопросы:")
        for q_line in task.open_questions.splitlines():
            lines.append(f"    {q_line}")
    return "\n".join(lines)


class DigestService:
    def __init__(self, session: AsyncSession, llm_client: BaseLLMClient) -> None:
        self.session = session
        self.llm = llm_client
        self.task_repo = TaskRepository(session)
        self.message_repo = MessageRepository(session)
        self.digest_repo = DigestRepository(session)

    async def build_digest(self, chat: Chat, period_hours: int = 24) -> str:
        tz = pytz.timezone(chat.timezone)
        now = now_utc()
        period_start = now - timedelta(hours=period_hours)

        tasks = await self.task_repo.get_since(chat.id, period_start)
        recent_messages = await self.message_repo.get_recent(chat.id, 100)

        date_label = datetime.now(tz).strftime("%d.%m.%Y")
        lines = [f"📋 *Дайджест задач за {date_label}*\n"]

        new_tasks = [t for t in tasks if t.status not in (TaskStatus.done, TaskStatus.cancelled)]
        clarification_tasks = [t for t in tasks if t.status == TaskStatus.needs_clarification]

        if new_tasks:
            lines.append("*Задачи:*")
            for i, task in enumerate(new_tasks, 1):
                lines.append(f"\n{i}. {_format_task_block(task)}")
        else:
            lines.append("Новых задач за период не выявлено.")

        if clarification_tasks:
            lines.append("\n\n*Требуют уточнения:*")
            for i, task in enumerate(clarification_tasks, 1):
                lines.append(f"\n{i}. *{task.title}*")
                pending_qs = [c.question for c in task.clarifications if c.status.value == "pending"]
                for q in pending_qs:
                    lines.append(f"   ❓ {q}")

        # LLM summary
        try:
            msg_dicts = [
                {"username": m.user.username if m.user else "?", "text": m.text or ""}
                for m in recent_messages
            ]
            task_dicts = [
                {"title": t.title, "status": t.status.value, "assignee_text": t.assignee_text}
                for t in tasks
            ]
            summary = await self.llm.summarize_for_digest(msg_dicts, task_dicts)
            if summary.key_points:
                lines.append("\n\n*Краткая выжимка обсуждений:*")
                for point in summary.key_points:
                    lines.append(f"• {point}")
        except Exception as e:
            logger.error("digest.summary_failed", error=str(e))

        text = "\n".join(lines)
        await self.digest_repo.create(
            chat_id=chat.id,
            period_start=period_start,
            period_end=now,
            text=text,
            sent_at=now,
        )
        return text
