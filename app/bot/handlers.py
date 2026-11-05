from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.enums import ContentType
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.exc import IntegrityError, OperationalError

from app import seen_chats
from app.bot.middleware import AccessMiddleware, is_admin
from app.bot.utils import safe_answer, safe_send_message
from app.config import settings
from app.db.models import Task, TaskPriority, TaskStatus
from app.db.repositories import ChatRepository, MessageRepository, TaskRepository
from app.db.session import async_session_factory
from app.i18n import LANGUAGE_NAMES, LANGUAGES, chat_language, normalize, t
from app.llm.openai_client import create_llm_client, is_local_endpoint, llm_block_reason
from app.services.analysis_runner import flush_pending
from app.services.clarification_service import ClarificationService
from app.services.digest_service import DigestService
from app.services.message_ingestor import MessageIngestor
from app.services.retention import RetentionService
from app.utils import metrics
from app.utils.dashboard_auth import chat_dashboard_url, dashboard_configured
from app.utils.logging import get_logger
from app.utils.periods import parse_period
from app.utils.task_labels import (
    PRIORITY_EMOJI,
    TASK_ACTIONS,
    status_labels,
)
from app.utils.time import is_valid_digest_time, is_valid_timezone, now_utc

logger = get_logger(__name__)
router = Router()

_access = AccessMiddleware()
router.message.outer_middleware(_access)
router.edited_message.outer_middleware(_access)
router.callback_query.outer_middleware(_access)

DIGEST_TIME_PRESETS = ("18:00", "20:00", "21:00", "23:00")
# Both spellings work whatever language the chat is set to.
WEEKDAYS = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6,
}

# Attachments worth recording even without a caption. Service events
# (joins, pins, title changes) are not conversation and are not stored.
MEDIA_TYPES = {
    ContentType.PHOTO, ContentType.VIDEO, ContentType.DOCUMENT, ContentType.AUDIO,
    ContentType.VOICE, ContentType.VIDEO_NOTE, ContentType.ANIMATION, ContentType.STICKER,
}
SAVE_ATTEMPTS = 3

# How many task cards to send inline before pointing to the web dashboard instead
# of flooding the chat with one message per task.
INLINE_TASK_LIMIT = 8


def _make_llm():
    return create_llm_client(
        provider=settings.llm_provider,
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
    )


def _task_block_text(task: Task, lang: str) -> str:
    prio = PRIORITY_EMOJI.get(task.priority.value, "")
    status = status_labels(lang).get(task.status.value, task.status.value)
    lines = [f"{prio} *{task.title}*", status]
    if task.reporter_text:
        lines.append(t(lang, "task.reporter", name=task.reporter_text))
    if task.assignee_text:
        lines.append(t(lang, "task.assignee", name=task.assignee_text))
    if task.deadline:
        lines.append(f"📅 {task.deadline}")
    return "\n".join(lines)


def _task_keyboard(task_id: int, status: TaskStatus, lang: str) -> InlineKeyboardMarkup:
    if status in (TaskStatus.done, TaskStatus.cancelled):
        rows = [[InlineKeyboardButton(text=t(lang, "button.reopen"), callback_data=f"task:{task_id}:reopen")]]
    else:
        rows = [
            [
                InlineKeyboardButton(text=t(lang, "button.progress"), callback_data=f"task:{task_id}:progress"),
                InlineKeyboardButton(text=t(lang, "button.done"), callback_data=f"task:{task_id}:done"),
                InlineKeyboardButton(text=t(lang, "button.cancel"), callback_data=f"task:{task_id}:cancel"),
            ]
        ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _main_menu_keyboard(lang: str, internal_chat_id: int | None = None) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=t(lang, "menu.tasks"), callback_data="menu:tasks"),
            InlineKeyboardButton(text=t(lang, "menu.digest"), callback_data="menu:digest"),
        ],
        [
            InlineKeyboardButton(text=t(lang, "menu.clarify"), callback_data="menu:clarify"),
            InlineKeyboardButton(text=t(lang, "menu.settings"), callback_data="menu:settings"),
        ],
    ]
    if internal_chat_id is not None and dashboard_configured():
        rows.append(
            [InlineKeyboardButton(text=t(lang, "menu.site"), url=chat_dashboard_url(internal_chat_id))]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _command_arg(message: Message) -> str:
    """Text after the command, also for the /command@botname form groups produce."""
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


async def _language_of(telegram_chat_id: int) -> str:
    """Reads only: a chat the bot has not stored yet simply gets the default language."""
    async with async_session_factory() as session:
        return normalize(await ChatRepository(session).get_language(telegram_chat_id))


async def _require_admin(message: Message) -> bool:
    if is_admin(message.from_user.id if message.from_user else None):
        return True
    await message.answer(t(await _language_of(message.chat.id), "bot.admin_only"))
    return False


async def _chat_ref(telegram_chat_id: int, chat_title: str | None) -> tuple[int, str]:
    """The chat's internal id and language."""
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(telegram_chat_id, chat_title)
        await session.commit()
        return chat.id, chat_language(chat)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    internal_chat_id, lang = await _chat_ref(message.chat.id, message.chat.title)
    await safe_answer(
        message,
        t(lang, "bot.start"),
        parse_mode="Markdown",
        reply_markup=_main_menu_keyboard(lang, internal_chat_id),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    # Plain text: command names contain "_", which Telegram Markdown would
    # misread as italics. Actions are also available via the buttons below.
    internal_chat_id, lang = await _chat_ref(message.chat.id, message.chat.title)
    await safe_answer(message, t(lang, "bot.help"), reply_markup=_main_menu_keyboard(lang, internal_chat_id))


async def _send_tasks(bot: Bot, chat_id: int, chat_title: str | None = None) -> None:
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        task_repo = TaskRepository(session)
        chat = await chat_repo.get_or_create(chat_id, chat_title)
        tasks = await task_repo.get_active(chat.id)
        lang = chat_language(chat)
        payloads = [(_task_block_text(task, lang), task.id, task.status) for task in tasks]
        internal_chat_id = chat.id

    web_link_markup: InlineKeyboardMarkup | None = None
    if dashboard_configured():
        web_link_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(lang, "menu.open_all"),
                        url=chat_dashboard_url(internal_chat_id),
                    )
                ]
            ]
        )

    if not payloads:
        await safe_send_message(bot, chat_id, t(lang, "bot.no_tasks"), reply_markup=web_link_markup)
        return

    await safe_send_message(
        bot,
        chat_id,
        t(lang, "bot.tasks_header", count=len(payloads)),
        parse_mode="Markdown",
        reply_markup=web_link_markup,
    )
    for text, task_id, status in payloads[:INLINE_TASK_LIMIT]:
        await safe_send_message(
            bot, chat_id, text, parse_mode="Markdown", reply_markup=_task_keyboard(task_id, status, lang)
        )
    remaining = len(payloads) - INLINE_TASK_LIMIT
    if remaining > 0:
        if web_link_markup is not None:
            await safe_send_message(
                bot,
                chat_id,
                t(lang, "bot.more_on_site", count=remaining),
                reply_markup=web_link_markup,
            )
        else:
            await safe_send_message(bot, chat_id, t(lang, "bot.more", count=remaining))


async def _send_digest(
    bot: Bot, chat_id: int, chat_title: str | None = None, period_arg: str = ""
) -> None:
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(chat_id, chat_title)
        lang = chat_language(chat)
        period = parse_period(period_arg, chat.timezone, now_utc())
        if period is None:
            await safe_send_message(bot, chat_id, t(lang, "bot.bad_period"))
            return

        await safe_send_message(bot, chat_id, t(lang, "bot.digest_building"))
        llm = _make_llm()
        digest_svc = DigestService(session, llm)
        try:
            await flush_pending(bot, session, llm, chat)
            digest = await digest_svc.build_digest(chat, period.start, period.end)
            await session.commit()
        except Exception as e:
            logger.error("send_digest.error", error=type(e).__name__)
            await session.rollback()
            await safe_send_message(bot, chat_id, t(lang, "bot.digest_failed"))
            return
        if await digest_svc.deliver(bot, digest, chat_id):
            metrics.digests_sent_total.inc()


async def _send_clarify(bot: Bot, chat_id: int, chat_title: str | None = None) -> None:
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(chat_id, chat_title)
        pending = await ClarificationService(session).get_pending_for_chat(chat.id)
        lang = chat_language(chat)

    if not pending:
        await safe_send_message(bot, chat_id, t(lang, "bot.no_clarifications"))
        return
    lines = [t(lang, "bot.clarifications_header")]
    lines.extend(f"❓ {c.question}" for c in pending)
    await safe_send_message(bot, chat_id, "\n".join(lines), parse_mode="Markdown")


def _language_buttons() -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text=LANGUAGE_NAMES[code], callback_data=f"language:{code}")
        for code in LANGUAGES
    ]


async def _send_settings(bot: Bot, chat_id: int, chat_title: str | None = None) -> None:
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(chat_id, chat_title)
    lang = chat_language(chat)
    weekly = (
        t(lang, "bot.weekly_on", day=t(lang, f"weekday.{chat.weekly_digest_day}"))
        if chat.weekly_digest_enabled
        else t(lang, "bot.off")
    )
    text = t(
        lang,
        "bot.settings",
        timezone=chat.timezone,
        digest_time=chat.digest_time,
        enabled=t(lang, "bot.on" if chat.digest_enabled else "bot.off"),
        weekly=weekly,
        target=chat.digest_chat_id or t(lang, "bot.this_chat"),
        language=LANGUAGE_NAMES[lang],
        chat_id=chat.telegram_chat_id,
    )
    presets = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"🕕 {preset}", callback_data=f"digest_time:{preset}")
                for preset in DIGEST_TIME_PRESETS
            ],
            _language_buttons(),
        ]
    )
    await safe_send_message(bot, chat_id, text, parse_mode="Markdown", reply_markup=presets)


@router.callback_query(F.data.startswith("menu:"))
async def on_menu(callback: CallbackQuery, bot: Bot) -> None:
    action = (callback.data or "").split(":", 1)[1]
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    chat_id = callback.message.chat.id
    chat_title = callback.message.chat.title
    await callback.answer()
    if action == "tasks":
        await _send_tasks(bot, chat_id, chat_title)
    elif action == "digest":
        await _send_digest(bot, chat_id, chat_title)
    elif action == "clarify":
        await _send_clarify(bot, chat_id, chat_title)
    elif action == "settings":
        await _send_settings(bot, chat_id, chat_title)


@router.message(Command("tasks"))
async def cmd_tasks(message: Message, bot: Bot) -> None:
    if message.chat:
        await _send_tasks(bot, message.chat.id, message.chat.title)


@router.message(Command("digest"))
async def cmd_digest(message: Message, bot: Bot) -> None:
    if message.chat:
        await _send_digest(bot, message.chat.id, message.chat.title, _command_arg(message))


@router.message(Command("task"))
async def cmd_task(message: Message) -> None:
    if not message.chat or not message.text:
        return

    task_text = _command_arg(message)
    if not task_text:
        await message.answer(t(await _language_of(message.chat.id), "bot.task_text_needed"))
        return

    fu = message.from_user
    reporter = (f"@{fu.username}" if fu and fu.username else fu.full_name) if fu else None

    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        task_repo = TaskRepository(session)
        chat = await chat_repo.get_or_create(message.chat.id, message.chat.title)
        task = await task_repo.create(
            chat_id=chat.id,
            title=task_text[:200],
            description=task_text,
            reporter_text=reporter,
            source="manual",
            status=TaskStatus.new,
            priority=TaskPriority.medium,
        )
        await session.commit()
        lang = chat_language(chat)

    await safe_answer(
        message,
        t(lang, "bot.task_created", title=task.title, id=task.id),
        parse_mode="Markdown",
    )


@router.message(Command("clarify"))
async def cmd_clarify(message: Message, bot: Bot) -> None:
    if message.chat:
        await _send_clarify(bot, message.chat.id, message.chat.title)


@router.message(Command("settings"))
async def cmd_settings(message: Message, bot: Bot) -> None:
    if message.chat:
        await _send_settings(bot, message.chat.id, message.chat.title)


@router.message(Command("set_timezone"))
async def cmd_set_timezone(message: Message) -> None:
    if not message.chat or not message.text or not await _require_admin(message):
        return
    arg = _command_arg(message)
    lang = await _language_of(message.chat.id)
    if not arg:
        await message.answer(t(lang, "bot.timezone_needed"))
        return
    if not is_valid_timezone(arg):
        await message.answer(t(lang, "bot.timezone_unknown", value=arg))
        return
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(message.chat.id, message.chat.title)
        await chat_repo.update_settings(chat, timezone=arg)
        await session.commit()
    await safe_answer(message, t(lang, "bot.timezone_set", value=arg), parse_mode="Markdown")


@router.message(Command("set_digest_time"))
async def cmd_set_digest_time(message: Message) -> None:
    if not message.chat or not message.text or not await _require_admin(message):
        return
    arg = _command_arg(message)
    lang = await _language_of(message.chat.id)
    if not arg:
        await message.answer(t(lang, "bot.time_needed"))
        return
    if not is_valid_digest_time(arg):
        await message.answer(t(lang, "bot.time_invalid", value=arg))
        return
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(message.chat.id, message.chat.title)
        await chat_repo.update_settings(chat, digest_time=arg)
        await session.commit()
    await safe_answer(message, t(lang, "bot.time_set", value=arg), parse_mode="Markdown")


@router.callback_query(F.data.startswith("digest_time:"))
async def on_digest_time(callback: CallbackQuery) -> None:
    value = (callback.data or "").split(":", 1)[1]
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    lang = await _language_of(callback.message.chat.id)
    if not is_admin(callback.from_user.id):
        await callback.answer(t(lang, "bot.admin_only_short"), show_alert=True)
        return
    if not is_valid_digest_time(value):
        await callback.answer()
        return
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(callback.message.chat.id, callback.message.chat.title)
        await chat_repo.update_settings(chat, digest_time=value)
        await session.commit()
    await callback.answer(t(lang, "bot.time_set_short", value=value))


async def _save_language(telegram_chat_id: int, chat_title: str | None, language: str) -> None:
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(telegram_chat_id, chat_title)
        await chat_repo.set_language(chat, language)
        await session.commit()


@router.message(Command("language"))
async def cmd_language(message: Message) -> None:
    if not message.chat or not await _require_admin(message):
        return
    arg = _command_arg(message).lower()
    lang = await _language_of(message.chat.id)
    if not arg:
        await message.answer(
            t(lang, "bot.language_current", language=LANGUAGE_NAMES[lang]),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[_language_buttons()]),
        )
        return
    if arg not in LANGUAGES:
        await message.answer(t(lang, "bot.language_unknown", value=arg))
        return
    await _save_language(message.chat.id, message.chat.title, arg)
    await message.answer(t(arg, "bot.language_set", language=LANGUAGE_NAMES[arg]))


@router.callback_query(F.data.startswith("language:"))
async def on_language(callback: CallbackQuery) -> None:
    value = (callback.data or "").split(":", 1)[1]
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    chat = callback.message.chat
    if not is_admin(callback.from_user.id):
        await callback.answer(t(await _language_of(chat.id), "bot.admin_only_short"), show_alert=True)
        return
    if value not in LANGUAGES:
        await callback.answer()
        return
    await _save_language(chat.id, chat.title, value)
    await callback.answer(t(value, "bot.language_set", language=LANGUAGE_NAMES[value]))


@router.message(Command("set_weekly"))
async def cmd_set_weekly(message: Message) -> None:
    if not message.chat or not await _require_admin(message):
        return
    arg = _command_arg(message).lower()
    lang = await _language_of(message.chat.id)
    if arg != "off" and arg not in WEEKDAYS:
        await message.answer(t(lang, "bot.weekly_needed"))
        return
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(message.chat.id, message.chat.title)
        if arg == "off":
            await chat_repo.update_settings(chat, weekly_digest_enabled=False)
        else:
            await chat_repo.update_settings(
                chat, weekly_digest_enabled=True, weekly_digest_day=WEEKDAYS[arg]
            )
        await session.commit()
    await message.answer(
        t(lang, "bot.weekly_off")
        if arg == "off"
        else t(lang, "bot.weekly_set", day=t(lang, f"weekday.{WEEKDAYS[arg]}"))
    )


@router.message(Command("set_digest_chat"))
async def cmd_set_digest_chat(message: Message) -> None:
    if not message.chat or not await _require_admin(message):
        return
    arg = _command_arg(message).lower()
    lang = await _language_of(message.chat.id)
    target: int | None = None
    if arg != "here":
        try:
            target = int(arg)
        except ValueError:
            await message.answer(t(lang, "bot.target_needed"))
            return
        # A digest is a summary of this chat: it may only go somewhere already trusted.
        if target not in settings.allowed_chats:
            await message.answer(t(lang, "bot.target_not_allowed"))
            return
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(message.chat.id, message.chat.title)
        await chat_repo.set_digest_target(chat, target)
        await session.commit()
    await message.answer(t(lang, "bot.target_set", target=target or t(lang, "bot.this_chat")))


@router.message(Command("chatid"))
async def cmd_chatid(message: Message) -> None:
    await message.answer(f"Chat ID: {message.chat.id}")


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    async with async_session_factory() as session:
        chat = await ChatRepository(session).get_or_create(message.chat.id, message.chat.title)
        pending, _ = await MessageRepository(session).pending_stats(chat.id)
        lang = chat_language(chat)
    blocked = llm_block_reason(lang)
    if settings.llm_provider != "openai":
        where = t(lang, "bot.model_mock")
    elif is_local_endpoint(settings.openai_base_url):
        where = t(lang, "bot.model_local")
    else:
        where = t(lang, "bot.model_external")
    state = t(lang, "bot.analysis_paused", reason=blocked) if blocked else t(lang, "bot.analysis_running")
    await message.answer(
        t(lang, "bot.status", model=settings.openai_model, where=where, state=state, pending=pending)
    )


@router.message(Command("reanalyze"))
async def cmd_reanalyze(message: Message) -> None:
    if not message.chat or not await _require_admin(message):
        return
    arg = _command_arg(message)
    lang = await _language_of(message.chat.id)
    if not arg.isdigit() or int(arg) == 0:
        await message.answer(t(lang, "bot.days_needed"))
        return
    async with async_session_factory() as session:
        chat = await ChatRepository(session).get_or_create(message.chat.id, message.chat.title)
        count = await MessageRepository(session).requeue_since(
            chat.id, now_utc() - timedelta(days=int(arg))
        )
        await session.commit()
    await message.answer(t(lang, "bot.requeued", count=count))


@router.message(Command("purge_history"))
async def cmd_purge_history(message: Message) -> None:
    if not message.chat or not await _require_admin(message):
        return
    parts = _command_arg(message).split()
    lang = await _language_of(message.chat.id)
    if len(parts) != 2 or not parts[0].isdigit() or parts[1] != "confirm":
        await message.answer(t(lang, "bot.purge_help"))
        return
    days = int(parts[0])
    async with async_session_factory() as session:
        chat = await ChatRepository(session).get_or_create(message.chat.id, message.chat.title)
        retention = RetentionService(session)
        if days == 0:
            removed = await retention.purge_chat(chat.id)
        else:
            removed = await retention.delete_messages(now_utc() - timedelta(days=days), chat.id)
        await session.commit()
    logger.info("history.purged", chat_id=message.chat.id, days=days, removed=removed)
    await message.answer(t(lang, "bot.purged", count=removed))


async def _set_digest_enabled(message: Message, enabled: bool) -> None:
    if not message.chat or not await _require_admin(message):
        return
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(message.chat.id, message.chat.title)
        await chat_repo.update_settings(chat, digest_enabled=enabled)
        await session.commit()
        lang = chat_language(chat)
    await message.answer(t(lang, "bot.digest_enabled" if enabled else "bot.digest_disabled"))


@router.message(Command("pin_dashboard"))
async def cmd_pin_dashboard(message: Message, bot: Bot) -> None:
    """Send the web-dashboard link and pin it, so it stays visible above the chat."""
    if not message.chat or not await _require_admin(message):
        return
    internal_chat_id, lang = await _chat_ref(message.chat.id, message.chat.title)
    if not dashboard_configured():
        await message.answer(t(lang, "bot.site_not_configured"))
        return
    sent = await safe_send_message(
        bot,
        message.chat.id,
        t(lang, "bot.site_link"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(lang, "menu.open_site"),
                        url=chat_dashboard_url(internal_chat_id),
                    )
                ]
            ]
        ),
    )
    if sent is None:
        return
    try:
        await bot.pin_chat_message(chat_id=message.chat.id, message_id=sent.message_id)
    except Exception as e:
        logger.warning("pin_dashboard.pin_failed", error=str(e))
        await message.answer(t(lang, "bot.pin_failed"))


@router.message(Command("digest_on"))
async def cmd_digest_on(message: Message) -> None:
    await _set_digest_enabled(message, True)


@router.message(Command("digest_off"))
async def cmd_digest_off(message: Message) -> None:
    await _set_digest_enabled(message, False)


@router.callback_query(F.data.startswith("task:"))
async def on_task_action(callback: CallbackQuery) -> None:
    """Handle inline status buttons under each task (the task state machine)."""
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    _, task_id_str, action = parts
    if not isinstance(callback.message, Message):
        await callback.answer()
        return

    new_status = TASK_ACTIONS.get(action)
    if new_status is None or not task_id_str.isdigit():
        await callback.answer(t(await _language_of(callback.message.chat.id), "bot.unknown_action"))
        return

    new_text: str | None = None
    markup: InlineKeyboardMarkup | None = None
    async with async_session_factory() as session:
        task_repo = TaskRepository(session)
        chat = await ChatRepository(session).get_or_create(callback.message.chat.id)
        lang = chat_language(chat)
        task = await task_repo.get_by_id(int(task_id_str))
        # The id comes from the client, so a task of another chat must look like no task.
        if task is None or task.chat_id != chat.id:
            await callback.answer(t(lang, "bot.task_not_found"))
            return
        await task_repo.set_status(task, new_status)
        await session.commit()
        new_text = _task_block_text(task, lang)
        markup = _task_keyboard(task.id, task.status, lang)

    await callback.answer(t(lang, "bot.status_changed", status=status_labels(lang)[new_status.value]))
    # callback.message may be an InaccessibleMessage (too old) — only edit a real one.
    if isinstance(callback.message, Message) and new_text is not None:
        try:
            await callback.message.edit_text(
                new_text, parse_mode="Markdown", reply_markup=markup
            )
        except Exception as e:
            logger.error("on_task_action.edit_failed", error=str(e))


def _forward_label(message: Message) -> str | None:
    origin = message.forward_origin
    if origin is None:
        return None
    for attr in ("sender_user", "sender_chat", "chat"):
        source = getattr(origin, attr, None)
        if source is not None:
            return getattr(source, "full_name", None) or getattr(source, "title", None)
    return getattr(origin, "sender_user_name", None)


async def _save(message: Message, content: str | None) -> None:
    """Telegram will not redeliver an update it considers handled, so a transient
    database error gets retried here instead of silently losing the message."""
    from_user = message.from_user
    raw_update: dict = {}
    try:
        raw_update = message.model_dump(mode="json", exclude_none=True)
    except Exception:
        pass

    for attempt in range(SAVE_ATTEMPTS):
        async with async_session_factory() as session:
            try:
                await MessageIngestor(session).ingest(
                    telegram_chat_id=message.chat.id,
                    chat_title=message.chat.title,
                    telegram_user_id=from_user.id if from_user else None,
                    username=from_user.username if from_user else None,
                    display_name=from_user.full_name if from_user else None,
                    telegram_message_id=message.message_id,
                    text=content,
                    raw_update_json=raw_update,
                    telegram_thread_id=message.message_thread_id,
                    reply_to_message_id=(
                        message.reply_to_message.message_id if message.reply_to_message else None
                    ),
                    sent_at=message.date,
                    content_type=ContentType(message.content_type).value,
                    forward_from=_forward_label(message),
                )
                await session.commit()
                return
            except (OperationalError, IntegrityError) as e:
                await session.rollback()
                logger.warning("handle_message.save_retry", attempt=attempt, error=type(e).__name__)
        await asyncio.sleep(0.5 * 2**attempt)
    logger.error(
        "handle_message.save_failed", chat_id=message.chat.id, message_id=message.message_id
    )


def _edit_time(message: Message) -> datetime:
    # The Bot API sends a Unix timestamp here, unlike `date`.
    if isinstance(message.edit_date, int):
        return datetime.fromtimestamp(message.edit_date, tz=UTC)
    return message.edit_date or now_utc()


@router.my_chat_member()
async def on_membership_change(update: ChatMemberUpdated) -> None:
    """Telegram has no call listing a bot's chats, so being added or removed is noted here:
    a new group shows up in the panel at once, before anyone writes in it."""
    present = update.new_chat_member.status in ("member", "administrator", "creator", "restricted")
    seen_chats.remember(update.chat.id, update.chat.title, update.chat.type, present)
    logger.info("bot.membership_changed", chat_id=update.chat.id, present=present)


@router.edited_message()
async def handle_edited_message(message: Message) -> None:
    content = message.text or message.caption
    if content and content.startswith("/"):
        return
    async with async_session_factory() as session:
        updated = await MessageIngestor(session).apply_edit(
            telegram_chat_id=message.chat.id,
            telegram_message_id=message.message_id,
            text=content,
            edited_at=_edit_time(message),
        )
        await session.commit()
    if updated is None:
        await _save(message, content)


@router.message()
async def handle_message(message: Message) -> None:
    """Catch-all: store every conversational message. Analysis happens later, in batches."""
    # Also capture captions on photos/documents so media task signals aren't lost.
    content = message.text or message.caption
    if content and content.startswith("/"):
        return
    if not content and message.content_type not in MEDIA_TYPES:
        return
    await _save(message, content)
