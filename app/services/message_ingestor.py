from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message
from app.db.repositories import (
    ChatRepository,
    MessageRepository,
    UserRepository,
)
from app.services.clarification_service import ClarificationService
from app.utils import metrics
from app.utils.logging import get_logger

logger = get_logger(__name__)


class MessageIngestor:
    """Saves incoming messages and nothing else. No model call happens here, so a
    slow or broken LLM can neither hold a database lock nor lose a message; analysis
    picks the rows up from the queue afterwards (see ConversationAnalyzer)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.chat_repo = ChatRepository(session)
        self.user_repo = UserRepository(session)
        self.message_repo = MessageRepository(session)
        self.clarification_svc = ClarificationService(session)

    async def ingest(
        self,
        telegram_chat_id: int,
        chat_title: str | None,
        telegram_user_id: int | None,
        username: str | None,
        display_name: str | None,
        telegram_message_id: int,
        text: str | None,
        raw_update_json: dict[str, Any] | None,
        telegram_thread_id: int | None = None,
        reply_to_message_id: int | None = None,
        sent_at: datetime | None = None,
        content_type: str | None = None,
        forward_from: str | None = None,
        source: str = "telegram",
    ) -> tuple[Message, bool]:
        """
        Save a message idempotently. Returns (message, created); a redelivered
        update comes back with created=False and changes nothing.
        """
        chat = await self.chat_repo.get_or_create(telegram_chat_id, chat_title)

        user = None
        if telegram_user_id:
            user = await self.user_repo.get_or_create(telegram_user_id, username, display_name)

        has_text = bool(text and text.strip())
        message, created = await self.message_repo.insert_if_new(
            chat_id=chat.id,
            user_id=user.id if user else None,
            telegram_message_id=telegram_message_id,
            telegram_thread_id=telegram_thread_id,
            reply_to_message_id=reply_to_message_id,
            text=text if has_text else None,
            raw_update_json=raw_update_json,
            # A photo without a caption is kept for the record but has nothing to analyze.
            pending_analysis=has_text,
            sent_at=sent_at,
            content_type=content_type,
            forward_from=forward_from,
            source=source,
        )
        if not created:
            logger.info("message.duplicate_ignored", message_id=message.id, chat_id=chat.id)
            return message, False

        logger.info("message.saved", message_id=message.id, chat_id=chat.id)
        metrics.messages_ingested_total.inc()

        # A reply to a clarification question closes it; the text still goes through
        # analysis, so whatever the answer says reaches the topic as well.
        await self.clarification_svc.handle_possible_answer(message, chat)
        return message, True

    async def apply_edit(
        self,
        telegram_chat_id: int,
        telegram_message_id: int,
        text: str | None,
        edited_at: datetime,
    ) -> Message | None:
        """Update a stored message and queue it again. Returns None if it was never seen."""
        chat = await self.chat_repo.get_or_create(telegram_chat_id)
        message = await self.message_repo.get_by_telegram_id(chat.id, telegram_message_id)
        if message is None:
            return None
        message.edited_at = edited_at
        if text != message.text:
            message.text = text
            message.pending_analysis = bool(text and text.strip())
            message.analysis_attempts = 0
            message.analysis_failed = False
        await self.session.flush()
        return message
