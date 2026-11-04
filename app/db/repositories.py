from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    Chat,
    Clarification,
    ClarificationStatus,
    Digest,
    Message,
    Task,
    TaskMessage,
    TaskPriority,
    TaskStatus,
    Topic,
    TopicItem,
    TopicItemKind,
    TopicItemSource,
    TopicMessage,
    TopicUpdate,
    User,
)
from app.utils.time import now_utc


def _insert_ignore(session: AsyncSession, model: type):
    """INSERT ... ON CONFLICT DO NOTHING for the dialect in use."""
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    return insert(model).on_conflict_do_nothing()


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _find(self, telegram_chat_id: int) -> Chat | None:
        result = await self.session.execute(
            select(Chat).where(Chat.telegram_chat_id == telegram_chat_id)
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, telegram_chat_id: int, title: str | None = None) -> Chat:
        chat = await self._find(telegram_chat_id)
        if chat is None:
            # Handlers run concurrently, so two of them can meet a new chat at once.
            await self.session.execute(
                _insert_ignore(self.session, Chat).values(
                    telegram_chat_id=telegram_chat_id, title=title
                )
            )
            chat = await self._find(telegram_chat_id)
            assert chat is not None
        elif title and chat.title != title:
            chat.title = title
        return chat

    async def get_all_active(self) -> list[Chat]:
        result = await self.session.execute(select(Chat).where(Chat.is_active.is_(True)))
        return list(result.scalars().all())

    async def get_by_id(self, chat_id: int) -> Chat | None:
        result = await self.session.execute(select(Chat).where(Chat.id == chat_id))
        return result.scalar_one_or_none()

    async def update_settings(
        self,
        chat: Chat,
        timezone: str | None = None,
        digest_time: str | None = None,
        digest_enabled: bool | None = None,
        weekly_digest_enabled: bool | None = None,
        weekly_digest_day: int | None = None,
    ) -> Chat:
        if timezone is not None:
            chat.timezone = timezone
        if digest_time is not None:
            chat.digest_time = digest_time
        if digest_enabled is not None:
            chat.digest_enabled = digest_enabled
        if weekly_digest_enabled is not None:
            chat.weekly_digest_enabled = weekly_digest_enabled
        if weekly_digest_day is not None:
            chat.weekly_digest_day = weekly_digest_day
        await self.session.flush()
        return chat

    async def set_digest_target(self, chat: Chat, target_chat_id: int | None) -> Chat:
        chat.digest_chat_id = target_chat_id
        await self.session.flush()
        return chat


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _find(self, telegram_user_id: int) -> User | None:
        result = await self.session.execute(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        telegram_user_id: int,
        username: str | None = None,
        display_name: str | None = None,
    ) -> User:
        user = await self._find(telegram_user_id)
        if user is None:
            # A new member's first messages can arrive in one burst.
            await self.session.execute(
                _insert_ignore(self.session, User).values(
                    telegram_user_id=telegram_user_id,
                    username=username,
                    display_name=display_name,
                )
            )
            user = await self._find(telegram_user_id)
            assert user is not None
        else:
            if username:
                user.username = username
            if display_name:
                user.display_name = display_name
        return user

    async def get_by_username(self, username: str) -> User | None:
        clean = username.lstrip("@")
        result = await self.session.execute(
            select(User).where(func.lower(User.username) == clean.lower())
        )
        return result.scalar_one_or_none()


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        chat_id: int,
        user_id: int | None,
        telegram_message_id: int,
        text: str | None,
        raw_update_json: dict[str, Any] | None = None,
        telegram_thread_id: int | None = None,
        reply_to_message_id: int | None = None,
        pending_analysis: bool = False,
        sent_at: datetime | None = None,
    ) -> Message:
        message = Message(
            chat_id=chat_id,
            user_id=user_id,
            telegram_message_id=telegram_message_id,
            telegram_thread_id=telegram_thread_id,
            reply_to_message_id=reply_to_message_id,
            text=text,
            raw_update_json=raw_update_json,
            pending_analysis=pending_analysis,
            sent_at=sent_at or now_utc(),
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def insert_if_new(self, **values: Any) -> tuple[Message, bool]:
        """Save a message unless this (chat, Telegram id) already exists.
        Telegram redelivers updates, so "already there" is an expected outcome."""
        values["sent_at"] = values.get("sent_at") or now_utc()
        result = await self.session.execute(_insert_ignore(self.session, Message).values(**values))
        message = await self.get_by_telegram_id(values["chat_id"], values["telegram_message_id"])
        assert message is not None
        return message, result.rowcount > 0

    async def get_recent(self, chat_id: int, limit: int = 20) -> list[Message]:
        result = await self.session.execute(
            select(Message)
            .where(Message.chat_id == chat_id, Message.text.isnot(None))
            .order_by(Message.created_at.desc())
            .limit(limit)
            .options(selectinload(Message.user))
        )
        return list(reversed(result.scalars().all()))

    async def get_by_telegram_id(self, chat_id: int, telegram_message_id: int) -> Message | None:
        result = await self.session.execute(
            select(Message).where(
                Message.chat_id == chat_id,
                Message.telegram_message_id == telegram_message_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_pending_batch(self, chat_id: int, limit: int) -> list[Message]:
        result = await self.session.execute(
            select(Message)
            .where(Message.chat_id == chat_id, Message.pending_analysis.is_(True))
            .order_by(Message.sent_at.asc(), Message.id.asc())
            .limit(limit)
            .options(selectinload(Message.user))
        )
        return list(result.scalars().all())

    async def pending_stats(self, chat_id: int) -> tuple[int, datetime | None]:
        """How many messages are waiting, and since when."""
        result = await self.session.execute(
            select(func.count(), func.min(Message.sent_at)).where(
                Message.chat_id == chat_id, Message.pending_analysis.is_(True)
            )
        )
        count, oldest = result.one()
        return count, oldest

    async def count_in_period(self, chat_id: int, start: datetime, end: datetime) -> int:
        result = await self.session.execute(
            select(func.count()).where(
                Message.chat_id == chat_id, Message.sent_at >= start, Message.sent_at < end
            )
        )
        return result.scalar_one()

    async def count_failed(self, chat_id: int, start: datetime, end: datetime) -> int:
        result = await self.session.execute(
            select(func.count()).where(
                Message.chat_id == chat_id,
                Message.analysis_failed.is_(True),
                Message.sent_at >= start,
                Message.sent_at < end,
            )
        )
        return result.scalar_one()

    async def mark_analysis_done(self, message_ids: list[int]) -> None:
        await self.session.execute(
            update(Message).where(Message.id.in_(message_ids)).values(pending_analysis=False)
        )

    async def record_bad_answer(self, message_ids: list[int], max_attempts: int) -> int:
        """Count an unusable model answer; messages out of attempts leave the queue
        and are reported in the digest rather than blocking it forever."""
        await self.session.execute(
            update(Message)
            .where(Message.id.in_(message_ids))
            .values(analysis_attempts=Message.analysis_attempts + 1)
        )
        result = await self.session.execute(
            update(Message)
            .where(Message.id.in_(message_ids), Message.analysis_attempts >= max_attempts)
            .values(pending_analysis=False, analysis_failed=True)
        )
        return result.rowcount

    async def requeue_since(self, chat_id: int, since: datetime) -> int:
        result = await self.session.execute(
            update(Message)
            .where(Message.chat_id == chat_id, Message.sent_at >= since, Message.text.isnot(None))
            .values(pending_analysis=True, analysis_attempts=0, analysis_failed=False)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        chat_id: int,
        title: str,
        description: str | None = None,
        assignee_text: str | None = None,
        reporter_text: str | None = None,
        status: TaskStatus = TaskStatus.new,
        priority: TaskPriority = TaskPriority.medium,
        deadline: str | None = None,
        confidence: float | None = None,
        source: str | None = None,
        requirements: str | None = None,
        open_questions: str | None = None,
    ) -> Task:
        task = Task(
            chat_id=chat_id,
            title=title,
            description=description,
            assignee_text=assignee_text,
            reporter_text=reporter_text,
            status=status,
            priority=priority,
            deadline=deadline,
            confidence=confidence,
            source=source,
            requirements=requirements,
            open_questions=open_questions,
        )
        self.session.add(task)
        await self.session.flush()
        return task

    async def get_active(self, chat_id: int) -> list[Task]:
        result = await self.session.execute(
            select(Task)
            .where(
                Task.chat_id == chat_id,
                Task.status.not_in([TaskStatus.done, TaskStatus.cancelled]),
            )
            .order_by(Task.created_at.desc())
            .options(selectinload(Task.clarifications))
        )
        return list(result.scalars().all())

    async def get_by_id(self, task_id: int) -> Task | None:
        result = await self.session.execute(
            select(Task)
            .where(Task.id == task_id)
            .options(selectinload(Task.clarifications))
        )
        return result.scalar_one_or_none()

    async def get_all_for_chat(self, chat_id: int, limit: int = 200) -> list[Task]:
        result = await self.session.execute(
            select(Task)
            .where(Task.chat_id == chat_id)
            .order_by(Task.created_at.desc())
            .limit(limit)
            .options(selectinload(Task.clarifications))
        )
        return list(result.scalars().all())

    async def set_status(self, task: Task, status: TaskStatus) -> Task:
        task.status = status
        await self.session.flush()
        return task

    async def get_since(self, chat_id: int, since: datetime) -> list[Task]:
        result = await self.session.execute(
            select(Task)
            .where(Task.chat_id == chat_id, Task.created_at >= since)
            .order_by(Task.created_at.asc())
            .options(selectinload(Task.clarifications))
        )
        return list(result.scalars().all())

    async def update(self, task: Task, **kwargs: Any) -> Task:
        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)
        return task

    async def needs_clarification(self, chat_id: int) -> list[Task]:
        result = await self.session.execute(
            select(Task)
            .where(
                Task.chat_id == chat_id,
                Task.status == TaskStatus.needs_clarification,
            )
            .options(selectinload(Task.clarifications))
        )
        return list(result.scalars().all())


class TaskMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def link(self, task_id: int, message_id: int) -> TaskMessage:
        existing = await self.session.execute(
            select(TaskMessage).where(
                TaskMessage.task_id == task_id, TaskMessage.message_id == message_id
            )
        )
        found = existing.scalar_one_or_none()
        if found is not None:
            return found
        tm = TaskMessage(task_id=task_id, message_id=message_id)
        self.session.add(tm)
        await self.session.flush()
        return tm

    async def get_task_ids_for_message(self, message_id: int) -> list[int]:
        result = await self.session.execute(
            select(TaskMessage.task_id).where(TaskMessage.message_id == message_id)
        )
        return list(result.scalars().all())

    async def get_task_for_reply(self, chat_id: int, reply_to_telegram_id: int) -> Task | None:
        """Find task linked to the message being replied to."""
        msg_result = await self.session.execute(
            select(Message).where(
                Message.chat_id == chat_id,
                Message.telegram_message_id == reply_to_telegram_id,
            )
        )
        original = msg_result.scalar_one_or_none()
        if original is None:
            return None
        task_ids = await self.get_task_ids_for_message(original.id)
        if not task_ids:
            return None
        task_result = await self.session.execute(
            select(Task).where(Task.id == task_ids[0])
        )
        return task_result.scalar_one_or_none()


class ClarificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        task_id: int,
        question: str,
        asked_to_user_id: int | None = None,
    ) -> Clarification:
        c = Clarification(
            task_id=task_id,
            question=question,
            asked_to_user_id=asked_to_user_id,
        )
        self.session.add(c)
        await self.session.flush()
        return c

    async def get_pending_for_chat(self, chat_id: int) -> list[Clarification]:
        result = await self.session.execute(
            select(Clarification)
            .join(Task, Clarification.task_id == Task.id)
            .where(Task.chat_id == chat_id, Clarification.status == ClarificationStatus.pending)
            .order_by(Clarification.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_pending_for_task(self, task_id: int) -> list[Clarification]:
        result = await self.session.execute(
            select(Clarification)
            .where(
                Clarification.task_id == task_id,
                Clarification.status == ClarificationStatus.pending,
            )
            .order_by(Clarification.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_pending_by_question_message_id(
        self, chat_id: int, question_message_id: int
    ) -> Clarification | None:
        """Find the pending clarification whose question was sent as the given Telegram message."""
        result = await self.session.execute(
            select(Clarification)
            .join(Task, Clarification.task_id == Task.id)
            .where(
                Task.chat_id == chat_id,
                Clarification.question_message_id == question_message_id,
                Clarification.status == ClarificationStatus.pending,
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def set_question_message_id(
        self, clarification_id: int, question_message_id: int
    ) -> None:
        await self.session.execute(
            update(Clarification)
            .where(Clarification.id == clarification_id)
            .values(question_message_id=question_message_id)
        )

    async def get_last_asked_at(self, chat_id: int) -> datetime | None:
        result = await self.session.execute(
            select(Clarification.created_at)
            .join(Task, Clarification.task_id == Task.id)
            .where(Task.chat_id == chat_id)
            .order_by(Clarification.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def answer(self, clarification_id: int, answer_text: str, message_id: int) -> None:
        await self.session.execute(
            update(Clarification)
            .where(Clarification.id == clarification_id)
            .values(
                status=ClarificationStatus.answered,
                answer_text=answer_text,
                answer_message_id=message_id,
            )
        )


class DigestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        chat_id: int,
        period_start: datetime,
        period_end: datetime,
        text: str,
        kind: str = "manual",
        period_key: str | None = None,
    ) -> Digest:
        digest = Digest(
            chat_id=chat_id,
            kind=kind,
            period_key=period_key,
            period_start=period_start,
            period_end=period_end,
            text=text,
        )
        self.session.add(digest)
        await self.session.flush()
        return digest

    async def get_slot(self, chat_id: int, kind: str, period_key: str) -> Digest | None:
        result = await self.session.execute(
            select(Digest).where(
                Digest.chat_id == chat_id, Digest.kind == kind, Digest.period_key == period_key
            )
        )
        return result.scalar_one_or_none()

    async def last_period_end(self, chat_id: int, kind: str) -> datetime | None:
        """Where the previous digest of this kind stopped, so the next one leaves no gap."""
        result = await self.session.execute(
            select(func.max(Digest.period_end)).where(
                Digest.chat_id == chat_id, Digest.kind == kind, Digest.sent_at.isnot(None)
            )
        )
        return result.scalar_one_or_none()


class TopicRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_recent(self, chat_id: int, limit: int) -> list[Topic]:
        result = await self.session.execute(
            select(Topic)
            .where(Topic.chat_id == chat_id)
            .order_by(Topic.last_message_at.desc(), Topic.id.desc())
            .limit(limit)
            .options(selectinload(Topic.items).selectinload(TopicItem.sources))
            # Sources are written with a bulk insert the identity map cannot see.
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())

    async def create(self, chat_id: int, title: str, project: str | None) -> Topic:
        topic = Topic(chat_id=chat_id, title=title[:255], project=project, items=[])
        self.session.add(topic)
        await self.session.flush()
        return topic

    async def add_update(self, topic_id: int, summary: str, occurred_at: datetime) -> None:
        self.session.add(TopicUpdate(topic_id=topic_id, summary=summary, occurred_at=occurred_at))
        await self.session.flush()

    async def link_messages(self, topic_id: int, message_ids: list[int]) -> None:
        if not message_ids:
            return
        await self.session.execute(
            _insert_ignore(self.session, TopicMessage),
            [{"topic_id": topic_id, "message_id": m} for m in message_ids],
        )

    async def add_item(self, topic_id: int, kind: TopicItemKind, text: str, **fields: Any) -> TopicItem:
        item = TopicItem(topic_id=topic_id, kind=kind, text=text, sources=[], **fields)
        self.session.add(item)
        await self.session.flush()
        return item

    async def add_sources(self, item_id: int, telegram_message_ids: list[int]) -> None:
        if not telegram_message_ids:
            return
        await self.session.execute(
            _insert_ignore(self.session, TopicItemSource),
            [{"item_id": item_id, "telegram_message_id": m} for m in telegram_message_ids],
        )

    async def get_items(self, item_ids: list[int]) -> list[TopicItem]:
        if not item_ids:
            return []
        result = await self.session.execute(
            select(TopicItem)
            .where(TopicItem.id.in_(item_ids))
            .options(selectinload(TopicItem.topic))
        )
        return list(result.scalars().all())

    async def items_in_period(self, chat_id: int, start: datetime, end: datetime) -> list[TopicItem]:
        result = await self.session.execute(
            select(TopicItem)
            .join(Topic, TopicItem.topic_id == Topic.id)
            .where(Topic.chat_id == chat_id, TopicItem.occurred_at >= start, TopicItem.occurred_at < end)
            .order_by(TopicItem.occurred_at.asc(), TopicItem.id.asc())
            .options(selectinload(TopicItem.topic), selectinload(TopicItem.sources))
            # Sources are written with a bulk insert the identity map cannot see.
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())

    async def updates_in_period(self, chat_id: int, start: datetime, end: datetime) -> list[TopicUpdate]:
        result = await self.session.execute(
            select(TopicUpdate)
            .join(Topic, TopicUpdate.topic_id == Topic.id)
            .where(Topic.chat_id == chat_id, TopicUpdate.occurred_at >= start, TopicUpdate.occurred_at < end)
            .order_by(TopicUpdate.occurred_at.asc(), TopicUpdate.id.asc())
            .options(selectinload(TopicUpdate.topic))
        )
        return list(result.scalars().all())

    async def superseded_by(self, new_item_ids: list[int]) -> dict[int, TopicItem]:
        """Map each replacement item id to the item it replaced."""
        if not new_item_ids:
            return {}
        result = await self.session.execute(
            select(TopicItem).where(TopicItem.superseded_by_id.in_(new_item_ids))
        )
        return {item.superseded_by_id: item for item in result.scalars().all()}  # type: ignore[misc]

    async def delete_for_chat(self, chat_id: int) -> None:
        topic_ids = select(Topic.id).where(Topic.chat_id == chat_id)
        item_ids = select(TopicItem.id).where(TopicItem.topic_id.in_(topic_ids))
        await self.session.execute(delete(TopicItemSource).where(TopicItemSource.item_id.in_(item_ids)))
        await self.session.execute(
            update(TopicItem).where(TopicItem.topic_id.in_(topic_ids)).values(superseded_by_id=None)
        )
        await self.session.execute(delete(TopicItem).where(TopicItem.topic_id.in_(topic_ids)))
        await self.session.execute(delete(TopicUpdate).where(TopicUpdate.topic_id.in_(topic_ids)))
        await self.session.execute(delete(TopicMessage).where(TopicMessage.topic_id.in_(topic_ids)))
        await self.session.execute(delete(Topic).where(Topic.chat_id == chat_id))
