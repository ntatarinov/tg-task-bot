from __future__ import annotations

import pytest

from app.db.models import ClarificationStatus, TaskStatus
from app.db.repositories import (
    ChatRepository,
    ClarificationRepository,
    MessageRepository,
    TaskMessageRepository,
    TaskRepository,
)
from app.services.clarification_service import ClarificationService


@pytest.mark.asyncio
async def test_answer_reply_updates_task(session):
    chat_repo = ChatRepository(session)
    task_repo = TaskRepository(session)
    msg_repo = MessageRepository(session)
    clar_repo = ClarificationRepository(session)

    chat = await chat_repo.get_or_create(telegram_chat_id=-100123)
    task = await task_repo.create(
        chat_id=chat.id, title="Оплата картой", status=TaskStatus.needs_clarification
    )
    clarification = await clar_repo.create(task_id=task.id, question="Кто исполнитель?")
    # The bot's question was delivered as Telegram message #555.
    await clar_repo.set_question_message_id(clarification.id, 555)

    # User replies to message #555 with the answer.
    answer_msg = await msg_repo.create(
        chat_id=chat.id,
        user_id=None,
        telegram_message_id=600,
        text="Это сделает Коля",
        reply_to_message_id=555,
    )
    await session.commit()

    svc = ClarificationService(session)
    handled = await svc.handle_possible_answer(answer_msg, chat)
    await session.commit()

    assert handled is True
    refreshed = await task_repo.get_by_id(task.id)
    assert refreshed.status == TaskStatus.new
    pending = await clar_repo.get_pending_for_task(task.id)
    assert pending == []

    stored = await session.get(type(clarification), clarification.id)
    await session.refresh(stored)
    assert stored.status == ClarificationStatus.answered
    assert stored.answer_text == "Это сделает Коля"
    assert stored.answer_message_id == answer_msg.id


@pytest.mark.asyncio
async def test_unrelated_reply_is_not_an_answer(session):
    chat_repo = ChatRepository(session)
    msg_repo = MessageRepository(session)
    chat = await chat_repo.get_or_create(telegram_chat_id=-100999)
    msg = await msg_repo.create(
        chat_id=chat.id,
        user_id=None,
        telegram_message_id=10,
        text="реплай на обычное сообщение",
        reply_to_message_id=9,
    )
    await session.commit()

    svc = ClarificationService(session)
    handled = await svc.handle_possible_answer(msg, chat)
    assert handled is False


@pytest.mark.asyncio
async def test_cooldown_blocks_repeated_questions_db_fallback(session):
    """With Redis down, the DB timestamp must enforce the anti-spam cooldown."""
    chat_repo = ChatRepository(session)
    task_repo = TaskRepository(session)
    chat = await chat_repo.get_or_create(telegram_chat_id=-100777)
    task = await task_repo.create(chat_id=chat.id, title="Задача")
    await session.commit()

    svc = ClarificationService(session)
    assert await svc.can_ask(chat.id) is True

    await svc.create(task=task, question="Уточни сроки?")
    await session.commit()

    # Just asked → cooldown active → cannot ask again.
    assert await svc.can_ask(chat.id) is False


@pytest.mark.asyncio
async def test_task_message_link_is_idempotent(session):
    chat_repo = ChatRepository(session)
    task_repo = TaskRepository(session)
    msg_repo = MessageRepository(session)
    link_repo = TaskMessageRepository(session)

    chat = await chat_repo.get_or_create(telegram_chat_id=-100555)
    task = await task_repo.create(chat_id=chat.id, title="Задача")
    msg = await msg_repo.create(
        chat_id=chat.id, user_id=None, telegram_message_id=1, text="текст"
    )
    await session.commit()

    first = await link_repo.link(task.id, msg.id)
    second = await link_repo.link(task.id, msg.id)
    assert first.id == second.id
