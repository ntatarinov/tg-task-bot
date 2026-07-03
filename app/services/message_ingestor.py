from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message
from app.db.repositories import (
    ChatRepository,
    MessageRepository,
    UserRepository,
)
from app.services.clarification_service import ClarificationToAsk
from app.services.task_analyzer import TaskAnalyzer
from app.utils import metrics
from app.utils.logging import get_logger

logger = get_logger(__name__)


class MessageIngestor:
    def __init__(self, session: AsyncSession, task_analyzer: TaskAnalyzer) -> None:
        self.session = session
        self.analyzer = task_analyzer
        self.chat_repo = ChatRepository(session)
        self.user_repo = UserRepository(session)
        self.message_repo = MessageRepository(session)

    async def ingest(
        self,
        telegram_chat_id: int,
        chat_title: str | None,
        telegram_user_id: int | None,
        username: str | None,
        display_name: str | None,
        telegram_message_id: int,
        text: str | None,
        raw_update_json: dict[str, Any],
        telegram_thread_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> tuple[Message | None, ClarificationToAsk | None]:
        """
        Save message and run analysis.
        Returns (message, clarification_to_ask).
        """
        if not text or not text.strip():
            return None, None

        chat = await self.chat_repo.get_or_create(telegram_chat_id, chat_title)

        user = None
        if telegram_user_id:
            user = await self.user_repo.get_or_create(telegram_user_id, username, display_name)

        message = await self.message_repo.create(
            chat_id=chat.id,
            user_id=user.id if user else None,
            telegram_message_id=telegram_message_id,
            text=text,
            raw_update_json=raw_update_json,
            telegram_thread_id=telegram_thread_id,
            reply_to_message_id=reply_to_message_id,
        )
        logger.info("message.saved", message_id=message.id, chat_id=chat.id)
        metrics.messages_ingested_total.inc()

        try:
            to_ask = await self.analyzer.analyze_and_process(
                message=message,
                chat=chat,
                user=user,
            )
            return message, to_ask
        except Exception as e:
            logger.error("message.analysis_failed", error=str(e), message_id=message.id)
            message.pending_analysis = True
            return message, None
