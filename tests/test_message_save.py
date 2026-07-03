from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import Message
from app.db.repositories import ChatRepository, MessageRepository, UserRepository


@pytest.mark.asyncio
async def test_save_message(session):
    chat_repo = ChatRepository(session)
    user_repo = UserRepository(session)
    msg_repo = MessageRepository(session)

    chat = await chat_repo.get_or_create(telegram_chat_id=-100123456, title="Test Chat")
    user = await user_repo.get_or_create(
        telegram_user_id=111222333, username="testuser", display_name="Test User"
    )
    message = await msg_repo.create(
        chat_id=chat.id,
        user_id=user.id,
        telegram_message_id=42,
        text="Нужно сделать новую фичу",
        raw_update_json={"message_id": 42},
    )
    await session.commit()

    result = await session.execute(select(Message).where(Message.id == message.id))
    saved = result.scalar_one()

    assert saved.text == "Нужно сделать новую фичу"
    assert saved.chat_id == chat.id
    assert saved.user_id == user.id
    assert saved.telegram_message_id == 42
    assert saved.pending_analysis is False


@pytest.mark.asyncio
async def test_get_recent_messages(session):
    chat_repo = ChatRepository(session)
    msg_repo = MessageRepository(session)

    chat = await chat_repo.get_or_create(telegram_chat_id=-100999888)
    for i in range(5):
        await msg_repo.create(
            chat_id=chat.id,
            user_id=None,
            telegram_message_id=i + 1,
            text=f"Сообщение {i + 1}",
        )
    await session.commit()

    recent = await msg_repo.get_recent(chat.id, limit=3)
    assert len(recent) == 3


@pytest.mark.asyncio
async def test_get_or_create_chat_idempotent(session):
    chat_repo = ChatRepository(session)
    chat1 = await chat_repo.get_or_create(telegram_chat_id=-100777666)
    chat2 = await chat_repo.get_or_create(telegram_chat_id=-100777666, title="Updated")
    await session.commit()

    assert chat1.id == chat2.id
    assert chat2.title == "Updated"
