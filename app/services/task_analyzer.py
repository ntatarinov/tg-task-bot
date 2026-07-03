from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Chat, Message, TaskPriority, TaskStatus, User
from app.db.repositories import (
    MessageRepository,
    TaskRepository,
)
from app.llm.base import BaseLLMClient
from app.services.clarification_service import ClarificationService, ClarificationToAsk
from app.services.task_clusterer import TaskClusterer
from app.utils.dates import normalize_deadline
from app.utils.logging import get_logger

logger = get_logger(__name__)

PRIORITY_MAP: dict[str, TaskPriority] = {
    "low": TaskPriority.low,
    "medium": TaskPriority.medium,
    "high": TaskPriority.high,
    "critical": TaskPriority.critical,
}


def _user_label(user: User | None) -> str | None:
    """A human-readable label for who reported the task."""
    if user is None:
        return None
    if user.username:
        return f"@{user.username}"
    return user.display_name


class TaskAnalyzer:
    def __init__(self, session: AsyncSession, llm_client: BaseLLMClient) -> None:
        self.session = session
        self.llm = llm_client
        self.message_repo = MessageRepository(session)
        self.task_repo = TaskRepository(session)
        self.clusterer = TaskClusterer(session)
        self.clarification_svc = ClarificationService(session)

    async def analyze_and_process(
        self,
        message: Message,
        chat: Chat,
        user: User | None,
    ) -> ClarificationToAsk | None:
        """
        Analyze a message and create/update tasks.
        Returns a clarification to ask if one should be sent, else None.
        """
        # Step 8: a reply to a clarification question answers it and updates the task.
        if await self.clarification_svc.handle_possible_answer(message, chat):
            return None

        context_messages = await self.message_repo.get_recent(chat.id, settings.context_messages_limit)
        active_tasks = await self.task_repo.get_active(chat.id)

        reply_to_text: str | None = None
        if message.reply_to_message_id:
            reply_msg = await self.message_repo.get_by_telegram_id(chat.id, message.reply_to_message_id)
            if reply_msg:
                reply_to_text = reply_msg.text

        context_dicts = [
            {
                "username": m.user.username or m.user.display_name if m.user else "unknown",
                "text": m.text or "",
            }
            for m in context_messages
            if m.id != message.id
        ]
        task_dicts = [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status.value,
                "assignee_text": t.assignee_text,
            }
            for t in active_tasks
        ]

        analysis = await self.llm.analyze_message(
            message_text=message.text or "",
            context_messages=context_dicts,
            active_tasks=task_dicts,
            reply_to_text=reply_to_text,
        )

        logger.info(
            "task_analyzer.result",
            message_id=message.id,
            message_type=analysis.message_type,
            contains_task=analysis.contains_task,
            confidence=analysis.confidence,
        )

        if not analysis.contains_task:
            return None

        # Deadline comes ONLY from an explicit date phrase in the message — the
        # model invents due dates otherwise. No phrase -> no deadline.
        analysis.deadline = normalize_deadline(message.text)

        task = await self.clusterer.find_or_create_task(
            message=message,
            chat=chat,
            analysis=analysis,
            active_tasks=active_tasks,
            reporter_text=_user_label(user),
        )

        if not task:
            return None

        to_ask: ClarificationToAsk | None = None
        if (
            analysis.should_ask_clarification
            and analysis.clarification_question
            and analysis.confidence >= settings.clarification_min_confidence
        ):
            can_ask = await self.clarification_svc.can_ask(chat.id)
            if can_ask:
                clarification = await self.clarification_svc.create(
                    task=task,
                    question=analysis.clarification_question,
                    user=user,
                )
                if task.status != TaskStatus.needs_clarification:
                    await self.task_repo.update(task, status=TaskStatus.needs_clarification)
                to_ask = ClarificationToAsk(
                    clarification_id=clarification.id,
                    question=analysis.clarification_question,
                )

        return to_ask
