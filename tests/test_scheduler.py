from __future__ import annotations

from datetime import datetime

import pytest
import pytz

from app.config import settings
from app.db.repositories import ChatRepository
from app.services import scheduler
from app.services.scheduler import due_slot
from tests.conftest import ALLOWED_CHAT


def test_due_slot_matches_local_time():
    tz = pytz.timezone("Asia/Almaty")
    now = tz.localize(datetime(2026, 6, 25, 18, 0))
    assert due_slot("18:00", now) == now.date()
    assert due_slot("17:00", now) is None
    assert due_slot("18:01", now) is None


def test_get_scheduler_registers_all_jobs(monkeypatch):
    monkeypatch.setattr(scheduler, "_scheduler", None)
    jobs = {job.id for job in scheduler.get_scheduler().get_jobs()}
    assert jobs == {"digest_tick", "analysis_tick", "retention_tick"}
    monkeypatch.setattr(scheduler, "_scheduler", None)


@pytest.mark.asyncio
async def test_only_allowed_chats_are_scheduled(session):
    repo = ChatRepository(session)
    allowed = await repo.get_or_create(ALLOWED_CHAT)
    await repo.get_or_create(-555)
    await session.commit()

    assert await scheduler._allowed_chat_ids(session) == [allowed.id]


def test_external_consent_is_off_by_default():
    assert type(settings).model_fields["llm_external_allowed"].default is False
