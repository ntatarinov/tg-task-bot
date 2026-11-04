from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import Message

from app.utils.logging import get_logger

logger = get_logger(__name__)

SEND_ATTEMPTS = 4


async def _send_with_backoff(bot: Bot, chat_id: int, text: str, **kwargs) -> Message:
    """Ride out flood limits and transient network or server failures."""
    for attempt in range(SEND_ATTEMPTS):
        try:
            return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except TelegramRetryAfter as e:
            if attempt == SEND_ATTEMPTS - 1:
                raise
            logger.warning("send.flood_wait", retry_after=e.retry_after)
            await asyncio.sleep(e.retry_after)
        except (TelegramNetworkError, TelegramServerError) as e:
            if attempt == SEND_ATTEMPTS - 1:
                raise
            logger.warning("send.transient_error", error=type(e).__name__, attempt=attempt)
            await asyncio.sleep(2**attempt)
    raise AssertionError("unreachable")


async def safe_send_message(bot: Bot, chat_id: int, text: str, **kwargs) -> Message | None:
    """
    Send a message, retrying once without parse_mode if Telegram rejects the
    entities (e.g. an unbalanced '*' or '_' in a user-derived task title).
    """
    try:
        return await _send_with_backoff(bot, chat_id, text, **kwargs)
    except TelegramBadRequest as e:
        logger.warning("send.parse_failed_retry_plain", error=str(e))
        kwargs["parse_mode"] = None  # override the bot-level default to send as plain text
        try:
            return await _send_with_backoff(bot, chat_id, text, **kwargs)
        except Exception as e2:
            logger.error("send.failed", error=str(e2))
            return None
    except Exception as e:
        logger.error("send.failed", error=str(e))
        return None


async def safe_answer(message: Message, text: str, **kwargs) -> Message | None:
    """Same fallback strategy as safe_send_message, for Message.answer."""
    try:
        return await message.answer(text, **kwargs)
    except TelegramBadRequest as e:
        logger.warning("answer.parse_failed_retry_plain", error=str(e))
        kwargs["parse_mode"] = None  # override the bot-level default to send as plain text
        try:
            return await message.answer(text, **kwargs)
        except Exception as e2:
            logger.error("answer.failed", error=str(e2))
            return None
    except Exception as e:
        logger.error("answer.failed", error=str(e))
        return None
