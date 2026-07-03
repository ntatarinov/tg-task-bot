from __future__ import annotations

from difflib import SequenceMatcher

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chat, Message, Task, TaskPriority, TaskStatus
from app.db.repositories import TaskMessageRepository, TaskRepository
from app.llm.schemas import MessageAnalysis
from app.utils import metrics
from app.utils.logging import get_logger

logger = get_logger(__name__)

SIMILARITY_THRESHOLD = 0.6
PRIORITY_MAP: dict[str, TaskPriority] = {
    "low": TaskPriority.low,
    "medium": TaskPriority.medium,
    "high": TaskPriority.high,
    "critical": TaskPriority.critical,
}


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


class TaskClusterer:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.task_repo = TaskRepository(session)
        self.task_msg_repo = TaskMessageRepository(session)

    async def find_or_create_task(
        self,
        message: Message,
        chat: Chat,
        analysis: MessageAnalysis,
        active_tasks: list[Task],
        reporter_text: str | None = None,
    ) -> Task | None:
        task: Task | None = None

        # 1. Check if reply links to an existing task
        if message.reply_to_message_id:
            task = await self.task_msg_repo.get_task_for_reply(chat.id, message.reply_to_message_id)
            if task:
                logger.info("clusterer.linked_via_reply", task_id=task.id)

        # 2. LLM-provided related_task_id
        if not task and analysis.related_task_id:
            task = await self.task_repo.get_by_id(analysis.related_task_id)
            if task and task.chat_id != chat.id:
                task = None

        # 3. Title similarity with active tasks
        if not task and analysis.task_title and active_tasks:
            for active in active_tasks:
                score = _similarity(analysis.task_title, active.title)
                if score >= SIMILARITY_THRESHOLD:
                    task = active
                    logger.info("clusterer.linked_via_similarity", task_id=task.id, score=score)
                    break

        if task:
            # Update existing task with new info
            updates: dict = {}
            if analysis.summary and not task.description:
                updates["description"] = analysis.summary
            if analysis.assignee and not task.assignee_text:
                updates["assignee_text"] = analysis.assignee
            if analysis.deadline and not task.deadline:
                updates["deadline"] = analysis.deadline
            if updates:
                await self.task_repo.update(task, **updates)
            await self.task_msg_repo.link(task.id, message.id)
            return task

        # 4. Create new task
        if not analysis.task_title:
            return None

        priority = PRIORITY_MAP.get(analysis.priority, TaskPriority.medium)
        requirements_text = "\n".join(f"- {r}" for r in analysis.requirements) if analysis.requirements else None
        open_questions_text = "\n".join(f"- {q}" for q in analysis.open_questions) if analysis.open_questions else None

        task = await self.task_repo.create(
            chat_id=chat.id,
            title=analysis.task_title,
            description=analysis.summary,
            assignee_text=analysis.assignee,
            reporter_text=reporter_text,
            status=TaskStatus.new,
            priority=priority,
            deadline=analysis.deadline,
            confidence=analysis.confidence,
            source="llm",
            requirements=requirements_text,
            open_questions=open_questions_text,
        )
        await self.task_msg_repo.link(task.id, message.id)
        metrics.tasks_created_total.inc()
        logger.info("clusterer.task_created", task_id=task.id, title=task.title)
        return task
