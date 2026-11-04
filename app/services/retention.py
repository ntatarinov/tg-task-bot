from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Clarification, Digest, Message, TaskMessage, TopicMessage
from app.db.repositories import TopicRepository
from app.utils.logging import get_logger
from app.utils.time import now_utc

logger = get_logger(__name__)

# The ORM would otherwise re-evaluate these date filters in Python against loaded
# objects, where SQLite's naive datetimes cannot be compared with an aware cutoff.
_NO_SYNC = {"synchronize_session": False}


class RetentionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def delete_messages(self, cutoff: datetime, chat_id: int | None = None) -> int:
        """Delete analyzed messages older than cutoff. Conclusions keep their Telegram
        message references, so topics and digests stay meaningful afterwards."""
        condition = [Message.sent_at < cutoff, Message.pending_analysis.is_(False)]
        if chat_id is not None:
            condition.append(Message.chat_id == chat_id)
        ids = select(Message.id).where(*condition)

        await self.session.execute(
            update(Clarification)
            .where(Clarification.answer_message_id.in_(ids))
            .values(answer_message_id=None)
            .execution_options(**_NO_SYNC)
        )
        await self.session.execute(
            delete(TaskMessage).where(TaskMessage.message_id.in_(ids)).execution_options(**_NO_SYNC)
        )
        await self.session.execute(
            delete(TopicMessage).where(TopicMessage.message_id.in_(ids)).execution_options(**_NO_SYNC)
        )
        result = await self.session.execute(
            delete(Message).where(*condition).execution_options(**_NO_SYNC)
        )
        return result.rowcount

    async def clear_raw_updates(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            update(Message)
            .where(Message.sent_at < cutoff, Message.raw_update_json.isnot(None))
            .values(raw_update_json=None)
            .execution_options(**_NO_SYNC)
        )
        return result.rowcount

    async def purge_chat(self, chat_id: int) -> int:
        """Remove everything derived from a chat's history. Tasks stay: they are managed by people."""
        await self.session.execute(
            update(Message)
            .where(Message.chat_id == chat_id)
            .values(pending_analysis=False)
            .execution_options(**_NO_SYNC)
        )
        removed = await self.delete_messages(now_utc() + timedelta(days=1), chat_id)
        await TopicRepository(self.session).delete_for_chat(chat_id)
        await self.session.execute(
            delete(Digest).where(Digest.chat_id == chat_id).execution_options(**_NO_SYNC)
        )
        return removed

    async def apply_policy(self) -> None:
        now = now_utc()
        removed = cleared = 0
        if settings.message_retention_days > 0:
            removed = await self.delete_messages(now - timedelta(days=settings.message_retention_days))
        if settings.raw_update_retention_days > 0:
            cleared = await self.clear_raw_updates(
                now - timedelta(days=settings.raw_update_retention_days)
            )
        await self.session.commit()
        if removed or cleared:
            logger.info("retention.applied", messages_deleted=removed, raw_cleared=cleared)
