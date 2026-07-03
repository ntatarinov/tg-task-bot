from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
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
    User,
)


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(self, telegram_chat_id: int, title: str | None = None) -> Chat:
        result = await self.session.execute(
            select(Chat).where(Chat.telegram_chat_id == telegram_chat_id)
        )
        chat = result.scalar_one_or_none()
        if chat is None:
            chat = Chat(telegram_chat_id=telegram_chat_id, title=title)
            self.session.add(chat)
            await self.session.flush()
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
    ) -> Chat:
        if timezone is not None:
            chat.timezone = timezone
        if digest_time is not None:
            chat.digest_time = digest_time
        if digest_enabled is not None:
            chat.digest_enabled = digest_enabled
        await self.session.flush()
        return chat


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(
        self,
        telegram_user_id: int,
        username: str | None = None,
        display_name: str | None = None,
    ) -> User:
        result = await self.session.execute(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                telegram_user_id=telegram_user_id,
                username=username,
                display_name=display_name,
            )
            self.session.add(user)
            await self.session.flush()
        else:
            if username:
                user.username = username
            if display_name:
                user.display_name = display_name
        return user


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
        )
        self.session.add(message)
        await self.session.flush()
        return message

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

    async def get_pending_analysis(self, limit: int = 50) -> list[Message]:
        result = await self.session.execute(
            select(Message)
            .where(Message.pending_analysis.is_(True))
            .order_by(Message.created_at.asc())
            .limit(limit)
            .options(selectinload(Message.chat), selectinload(Message.user))
        )
        return list(result.scalars().all())

    async def mark_analysis_done(self, message_id: int) -> None:
        await self.session.execute(
            update(Message).where(Message.id == message_id).values(pending_analysis=False)
        )


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
        sent_at: datetime | None = None,
    ) -> Digest:
        digest = Digest(
            chat_id=chat_id,
            period_start=period_start,
            period_end=period_end,
            text=text,
            sent_at=sent_at,
        )
        self.session.add(digest)
        await self.session.flush()
        return digest

    async def get_last_sent_at(self, chat_id: int) -> datetime | None:
        result = await self.session.execute(
            select(Digest.sent_at)
            .where(Digest.chat_id == chat_id, Digest.sent_at.isnot(None))
            .order_by(Digest.sent_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
