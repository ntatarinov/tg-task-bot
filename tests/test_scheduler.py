from __future__ import annotations

from datetime import datetime

import pytest
import pytz

from app.services.scheduler import _already_sent_today, chat_is_due


def test_chat_is_due_matches_local_time():
    tz = pytz.timezone("Asia/Almaty")
    now = tz.localize(datetime(2026, 6, 25, 18, 0))
    assert chat_is_due("18:00", now) is True
    assert chat_is_due("17:00", now) is False
    assert chat_is_due("18:01", now) is False


class _StubDigestRepo:
    def __init__(self, last_sent: datetime | None) -> None:
        self._last_sent = last_sent

    async def get_last_sent_at(self, chat_id: int) -> datetime | None:
        return self._last_sent


@pytest.mark.asyncio
async def test_already_sent_today_false_when_never_sent():
    tz = pytz.timezone("Asia/Almaty")
    now_local = datetime.now(tz)
    repo = _StubDigestRepo(None)
    # Redis is patched off by the autouse fixture → DB fallback.
    assert await _already_sent_today(repo, 1, now_local, tz) is False


@pytest.mark.asyncio
async def test_already_sent_today_true_when_sent_today():
    tz = pytz.timezone("Asia/Almaty")
    now_local = datetime.now(tz)
    repo = _StubDigestRepo(now_local)
    assert await _already_sent_today(repo, 1, now_local, tz) is True
