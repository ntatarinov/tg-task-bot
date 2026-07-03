from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Chat, Clarification, Message, Task, TaskStatus, User
from app.db.redis import cache_exists, cache_set
from app.db.repositories import (
    ClarificationRepository,
    MessageRepository,
    TaskMessageRepository,
    TaskRepository,
)
from app.utils import metrics
from app.utils.logging import get_logger
from app.utils.time import ensure_aware_utc, now_utc

logger = get_logger(__name__)


def _cooldown_key(chat_id: int) -> str:
    return f"clarify:cooldown:{chat_id}"


@dataclass
class ClarificationToAsk:
    """Lightweight carrier so handlers can send the question and back-link the message id."""

    clarification_id: int
    question: str


class ClarificationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ClarificationRepository(session)
        self.message_repo = MessageRepository(session)
        self.task_repo = TaskRepository(session)
        self.task_msg_repo = TaskMessageRepository(session)

    async def can_ask(self, chat_id: int) -> bool:
        """Anti-spam: don't ask clarifications too often. Redis fast-path, DB fallback."""
        on_cooldown = await cache_exists(_cooldown_key(chat_id))
        if on_cooldown is True:
            return False
        if on_cooldown is None:
            # Redis unavailable — fall back to the database timestamp.
            last_asked = await self.repo.get_last_asked_at(chat_id)
            if last_asked is not None:
                cooldown = timedelta(hours=settings.clarification_cooldown_hours)
                if (now_utc() - ensure_aware_utc(last_asked)) < cooldown:
                    return False
        return True

    async def create(
        self,
        task: Task,
        question: str,
        user: User | None = None,
    ) -> Clarification:
        clarification = await self.repo.create(
            task_id=task.id,
            question=question,
            asked_to_user_id=user.id if user else None,
        )
        await cache_set(
            _cooldown_key(task.chat_id),
            "1",
            ttl_seconds=settings.clarification_cooldown_hours * 3600,
        )
        metrics.clarifications_asked_total.inc()
        logger.info("clarification.created", clarification_id=clarification.id, task_id=task.id)
        return clarification

    async def set_question_message_id(
        self, clarification_id: int, question_message_id: int
    ) -> None:
        await self.repo.set_question_message_id(clarification_id, question_message_id)

    async def handle_possible_answer(self, message: Message, chat: Chat) -> bool:
        """
        If this message replies to a clarification question, store the answer and
        move the task back to `new` so it gets re-evaluated. Returns True if handled.
        """
        if not message.reply_to_message_id or not message.text:
            return False

        clarification = await self.repo.get_pending_by_question_message_id(
            chat.id, message.reply_to_message_id
        )
        if clarification is None:
            return False

        await self.repo.answer(clarification.id, message.text, message.id)

        task = await self.task_repo.get_by_id(clarification.task_id)
        if task is not None and task.status == TaskStatus.needs_clarification:
            await self.task_repo.update(task, status=TaskStatus.new)
        logger.info(
            "clarification.answered",
            clarification_id=clarification.id,
            task_id=clarification.task_id,
        )
        return True

    async def get_pending_for_chat(self, chat_id: int) -> list[Clarification]:
        return await self.repo.get_pending_for_chat(chat_id)
