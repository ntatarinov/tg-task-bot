from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TaskOut(BaseModel):
    id: int
    title: str
    description: str | None = None
    assignee_text: str | None = None
    reporter_text: str | None = None
    status: str
    priority: str
    deadline: str | None = None
    confidence: float | None = None
    created_at: datetime


class ChatOut(BaseModel):
    id: int
    telegram_chat_id: int
    title: str | None = None
    timezone: str
    digest_time: str
    is_active: bool
    active_tasks: int


class TaskStatusUpdate(BaseModel):
    status: str
