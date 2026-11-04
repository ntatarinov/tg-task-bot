from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

# NOTE: SQLAlchemy evaluates these ``Mapped[...]`` annotations at runtime, so the
# columns deliberately use ``Optional``/``List`` rather than PEP 604 ``X | None``,
# which would break on the Python 3.9 dev interpreter. Ruff is told to leave this
# file alone (see [tool.ruff.lint.per-file-ignores] in pyproject.toml).


class Base(DeclarativeBase):
    pass


class TaskStatus(str, enum.Enum):
    new = "new"
    needs_clarification = "needs_clarification"
    ready = "ready"
    in_progress = "in_progress"
    done = "done"
    cancelled = "cancelled"


class TaskPriority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class ClarificationStatus(str, enum.Enum):
    pending = "pending"
    answered = "answered"
    ignored = "ignored"


class TopicItemKind(str, enum.Enum):
    proposal = "proposal"
    decision = "decision"
    task = "task"
    question = "question"
    blocker = "blocker"
    next_action = "next_action"


class TopicItemStatus(str, enum.Enum):
    open = "open"
    superseded = "superseded"
    resolved = "resolved"


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Almaty")
    digest_time: Mapped[str] = mapped_column(String(5), default="18:00")
    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    weekly_digest_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 0 = Monday ... 6 = Sunday
    weekly_digest_day: Mapped[int] = mapped_column(Integer, default=4)
    # Where digests go; NULL means this chat itself.
    digest_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[List[Message]] = relationship("Message", back_populates="chat")
    tasks: Mapped[List[Task]] = relationship("Task", back_populates="chat")
    digests: Mapped[List[Digest]] = relationship("Digest", back_populates="chat")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255))
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[List[Message]] = relationship("Message", back_populates="user")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("uq_messages_chat_telegram_id", "chat_id", "telegram_message_id", unique=True),
        Index("ix_messages_chat_pending", "chat_id", "pending_analysis"),
        Index("ix_messages_chat_sent_at", "chat_id", "sent_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(Integer, ForeignKey("chats.id"), nullable=False, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_thread_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    reply_to_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    text: Mapped[Optional[str]] = mapped_column(Text)
    raw_update_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    pending_analysis: Mapped[bool] = mapped_column(Boolean, default=False)
    # Unparseable model answers only; an LLM outage never counts against a message.
    analysis_attempts: Mapped[int] = mapped_column(Integer, default=0)
    analysis_failed: Mapped[bool] = mapped_column(Boolean, default=False)
    # When the author sent it; created_at is only when this row was written.
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    edited_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    content_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    forward_from: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="telegram")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    chat: Mapped[Chat] = relationship("Chat", back_populates="messages")
    user: Mapped[Optional[User]] = relationship("User", back_populates="messages")
    task_messages: Mapped[List[TaskMessage]] = relationship("TaskMessage", back_populates="message")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(Integer, ForeignKey("chats.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    assignee_text: Mapped[Optional[str]] = mapped_column(String(255))
    reporter_text: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), default=TaskStatus.new, nullable=False
    )
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(TaskPriority), default=TaskPriority.medium, nullable=False
    )
    deadline: Mapped[Optional[str]] = mapped_column(String(100))
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    source: Mapped[Optional[str]] = mapped_column(String(50))
    requirements: Mapped[Optional[str]] = mapped_column(Text)
    open_questions: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    chat: Mapped[Chat] = relationship("Chat", back_populates="tasks")
    task_messages: Mapped[List[TaskMessage]] = relationship("TaskMessage", back_populates="task")
    clarifications: Mapped[List[Clarification]] = relationship(
        "Clarification", back_populates="task"
    )


class TaskMessage(Base):
    __tablename__ = "task_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.id"), nullable=False, index=True)
    message_id: Mapped[int] = mapped_column(Integer, ForeignKey("messages.id"), nullable=False)

    task: Mapped[Task] = relationship("Task", back_populates="task_messages")
    message: Mapped[Message] = relationship("Message", back_populates="task_messages")


class Clarification(Base):
    __tablename__ = "clarifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.id"), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    asked_to_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    status: Mapped[ClarificationStatus] = mapped_column(
        Enum(ClarificationStatus), default=ClarificationStatus.pending, nullable=False
    )
    answer_text: Mapped[Optional[str]] = mapped_column(Text)
    answer_message_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("messages.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    task: Mapped[Task] = relationship("Task", back_populates="clarifications")


class Digest(Base):
    __tablename__ = "digests"
    # period_key is NULL for manual digests, and NULLs never collide in a unique index.
    __table_args__ = (Index("uq_digests_slot", "chat_id", "kind", "period_key", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(Integer, ForeignKey("chats.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="manual")
    period_key: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Lets a retry resume after a partial send instead of repeating earlier parts.
    sent_chunks: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    chat: Mapped[Chat] = relationship("Chat", back_populates="digests")


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(Integer, ForeignKey("chats.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    project: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    items: Mapped[List[TopicItem]] = relationship("TopicItem", back_populates="topic")
    updates: Mapped[List[TopicUpdate]] = relationship("TopicUpdate", back_populates="topic")


class TopicUpdate(Base):
    """What one analysis batch said about a topic — the raw material for "Обсудили"."""

    __tablename__ = "topic_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(Integer, ForeignKey("topics.id"), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    topic: Mapped[Topic] = relationship("Topic", back_populates="updates")


class TopicItem(Base):
    __tablename__ = "topic_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(Integer, ForeignKey("topics.id"), nullable=False, index=True)
    kind: Mapped[TopicItemKind] = mapped_column(
        Enum(TopicItemKind, native_enum=False, length=20), nullable=False
    )
    status: Mapped[TopicItemStatus] = mapped_column(
        Enum(TopicItemStatus, native_enum=False, length=20),
        default=TopicItemStatus.open,
        nullable=False,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    deadline: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    superseded_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("topic_items.id"), nullable=True
    )
    task_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("tasks.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    topic: Mapped[Topic] = relationship("Topic", back_populates="items")
    sources: Mapped[List[TopicItemSource]] = relationship(
        "TopicItemSource", back_populates="item", cascade="all, delete-orphan"
    )


class TopicItemSource(Base):
    """Evidence for an item. Stores the Telegram id rather than a foreign key so the
    reference survives retention deleting the stored message."""

    __tablename__ = "topic_item_sources"
    __table_args__ = (
        Index("uq_topic_item_sources", "item_id", "telegram_message_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(Integer, ForeignKey("topic_items.id"), nullable=False)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    item: Mapped[TopicItem] = relationship("TopicItem", back_populates="sources")


class TopicMessage(Base):
    __tablename__ = "topic_messages"
    __table_args__ = (Index("uq_topic_messages", "topic_id", "message_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(Integer, ForeignKey("topics.id"), nullable=False)
    message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("messages.id"), nullable=False, index=True
    )
