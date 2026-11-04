from __future__ import annotations

import re
from difflib import SequenceMatcher

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chat, Task, TaskPriority, TaskStatus
from app.db.repositories import TaskMessageRepository, TaskRepository
from app.utils import metrics
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Only near-identical wording counts as the same task. At the old 0.6, "Интеграция
# API оплаты" and "Интеграция API доставки" (0.77) collapsed into one.
SIMILARITY_THRESHOLD = 0.9


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def same_wording(a: str, b: str, threshold: float) -> bool:
    """Near-identical text with the same numbers. The numbers are usually the whole
    point: "релиз 10 ноября" and "релиз 17 ноября" differ by one character."""
    return re.findall(r"\d+", a) == re.findall(r"\d+", b) and similarity(a, b) >= threshold


class TaskClusterer:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.task_repo = TaskRepository(session)
        self.task_msg_repo = TaskMessageRepository(session)

    async def find_or_create_task(
        self,
        chat: Chat,
        title: str,
        message_ids: list[int],
        assignee: str | None = None,
        deadline: str | None = None,
        reporter_text: str | None = None,
    ) -> tuple[Task, bool]:
        """Returns (task, created) — `created` is True only when a new row was inserted,
        so callers can tell a fresh task apart from one that was merely updated."""
        active_tasks = await self.task_repo.get_active(chat.id)
        task = next(
            (t for t in active_tasks if same_wording(title, t.title, SIMILARITY_THRESHOLD)), None
        )

        if task:
            updates: dict = {}
            if assignee and not task.assignee_text:
                updates["assignee_text"] = assignee
            if deadline and not task.deadline:
                updates["deadline"] = deadline
            if updates:
                await self.task_repo.update(task, **updates)
            logger.info("clusterer.linked_via_similarity", task_id=task.id)
            created = False
        else:
            task = await self.task_repo.create(
                chat_id=chat.id,
                title=title[:500],
                description=title,
                assignee_text=assignee,
                reporter_text=reporter_text,
                status=TaskStatus.new,
                priority=TaskPriority.medium,
                deadline=deadline,
                source="llm",
            )
            metrics.tasks_created_total.inc()
            logger.info("clusterer.task_created", task_id=task.id)
            created = True

        for message_id in message_ids:
            await self.task_msg_repo.link(task.id, message_id)
        return task, created
