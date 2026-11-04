from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app import seen_chats
from app.config import settings
from app.utils.logging import get_logger

logger = get_logger(__name__)


def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in settings.admins


class AccessMiddleware(BaseMiddleware):
    """
    Fail-closed gate in front of every handler: anything from a chat outside
    ALLOWED_CHAT_IDS is dropped before it can be stored or answered.

    The one exception is /chatid from an admin. A denied group is noted by id and title
    so the settings panel can offer to allow it; its messages are not kept.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            chat = event.message.chat if event.message else None
        elif isinstance(event, Message):
            chat = event.chat
        else:
            chat = None
        user = getattr(event, "from_user", None)
        user_id = user.id if user else None

        if chat is not None and chat.id in settings.allowed_chats:
            return await handler(event, data)
        if (
            isinstance(event, Message)
            and is_admin(user_id)
            and (event.text or "").split("@")[0].strip() == "/chatid"
        ):
            return await handler(event, data)

        if chat is not None:
            seen_chats.remember(chat.id, chat.title, chat.type)
        logger.info(
            "access.denied",
            chat_id=chat.id if chat else None,
            chat_type=chat.type if chat else None,
            user_id=user_id,
        )
        if isinstance(event, CallbackQuery):
            await event.answer()
        return None
