from __future__ import annotations

import pytest

from app.bot.handlers import TASK_ACTIONS
from app.db.models import TaskStatus
from app.db.repositories import ChatRepository, TaskRepository
from app.utils.time import is_valid_digest_time, is_valid_timezone


def test_timezone_validator():
    assert is_valid_timezone("Asia/Almaty")
    assert is_valid_timezone("Europe/Moscow")
    assert is_valid_timezone("UTC")
    assert not is_valid_timezone("Mars/Phobos")
    assert not is_valid_timezone("")


def test_digest_time_validator():
    assert is_valid_digest_time("18:00")
    assert is_valid_digest_time("00:00")
    assert is_valid_digest_time("23:59")
    assert not is_valid_digest_time("24:00")
    assert not is_valid_digest_time("9:5")
    assert not is_valid_digest_time("18-00")
    assert not is_valid_digest_time("abc")


@pytest.mark.asyncio
async def test_update_chat_settings(session):
    chat_repo = ChatRepository(session)
    chat = await chat_repo.get_or_create(telegram_chat_id=-100)
    assert chat.timezone == "Asia/Almaty"  # default

    await chat_repo.update_settings(chat, timezone="Europe/Moscow", digest_time="09:30")
    await session.commit()

    reloaded = await chat_repo.get_by_id(chat.id)
    assert reloaded.timezone == "Europe/Moscow"
    assert reloaded.digest_time == "09:30"


@pytest.mark.asyncio
async def test_digest_enabled_toggle(session):
    chat_repo = ChatRepository(session)
    chat = await chat_repo.get_or_create(telegram_chat_id=-101)
    assert chat.digest_enabled is True  # on by default

    await chat_repo.update_settings(chat, digest_enabled=False)
    await session.commit()

    reloaded = await chat_repo.get_by_id(chat.id)
    assert reloaded.digest_enabled is False


def test_task_action_map_covers_transitions():
    assert TASK_ACTIONS["progress"] == TaskStatus.in_progress
    assert TASK_ACTIONS["done"] == TaskStatus.done
    assert TASK_ACTIONS["cancel"] == TaskStatus.cancelled
    assert TASK_ACTIONS["reopen"] == TaskStatus.new


@pytest.mark.asyncio
async def test_set_status_removes_task_from_active(session):
    chat_repo = ChatRepository(session)
    task_repo = TaskRepository(session)
    chat = await chat_repo.get_or_create(telegram_chat_id=-400)
    task = await task_repo.create(chat_id=chat.id, title="Задача", status=TaskStatus.new)
    await session.commit()

    assert len(await task_repo.get_active(chat.id)) == 1

    await task_repo.set_status(task, TaskStatus.done)
    await session.commit()

    assert await task_repo.get_active(chat.id) == []
