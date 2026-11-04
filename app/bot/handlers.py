from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.enums import ContentType
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.exc import IntegrityError, OperationalError

from app.bot.middleware import AccessMiddleware, is_admin
from app.bot.utils import safe_answer, safe_send_message
from app.config import settings
from app.db.models import Task, TaskPriority, TaskStatus
from app.db.repositories import ChatRepository, MessageRepository, TaskRepository
from app.db.session import async_session_factory
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
    STATUS_LABELS,
    TASK_ACTIONS,
)
from app.utils.time import is_valid_digest_time, is_valid_timezone, now_utc

logger = get_logger(__name__)
router = Router()

_access = AccessMiddleware()
router.message.outer_middleware(_access)
router.edited_message.outer_middleware(_access)
router.callback_query.outer_middleware(_access)

DIGEST_TIME_PRESETS = ("18:00", "20:00", "21:00", "23:00")
WEEKDAYS = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}
WEEKDAY_NAMES = {index: name for name, index in WEEKDAYS.items()}

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


def _task_block_text(task: Task) -> str:
    prio = PRIORITY_EMOJI.get(task.priority.value, "")
    status = STATUS_LABELS.get(task.status.value, task.status.value)
    lines = [f"{prio} *{task.title}*", status]
    if task.reporter_text:
        lines.append(f"🙋 От: {task.reporter_text}")
    if task.assignee_text:
        lines.append(f"👤 Исполнитель: {task.assignee_text}")
    if task.deadline:
        lines.append(f"📅 {task.deadline}")
    return "\n".join(lines)


def _task_keyboard(task_id: int, status: TaskStatus) -> InlineKeyboardMarkup:
    if status in (TaskStatus.done, TaskStatus.cancelled):
        rows = [[InlineKeyboardButton(text="🔄 Возобновить", callback_data=f"task:{task_id}:reopen")]]
    else:
        rows = [
            [
                InlineKeyboardButton(text="🔄 В работу", callback_data=f"task:{task_id}:progress"),
                InlineKeyboardButton(text="✔️ Готово", callback_data=f"task:{task_id}:done"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"task:{task_id}:cancel"),
            ]
        ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _main_menu_keyboard(internal_chat_id: int | None = None) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="📋 Задачи", callback_data="menu:tasks"),
            InlineKeyboardButton(text="📊 Дайджест", callback_data="menu:digest"),
        ],
        [
            InlineKeyboardButton(text="❓ Уточнения", callback_data="menu:clarify"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:settings"),
        ],
    ]
    if internal_chat_id is not None and dashboard_configured():
        rows.append(
            [InlineKeyboardButton(text="🌐 Сайт с задачами", url=chat_dashboard_url(internal_chat_id))]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _command_arg(message: Message) -> str:
    """Text after the command, also for the /command@botname form groups produce."""
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


async def _require_admin(message: Message) -> bool:
    if is_admin(message.from_user.id if message.from_user else None):
        return True
    await message.answer("Эта команда доступна только администраторам бота.")
    return False


async def _get_internal_chat_id(telegram_chat_id: int, chat_title: str | None) -> int:
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(telegram_chat_id, chat_title)
        await session.commit()
        return chat.id


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    internal_chat_id = None
    if message.chat:
        internal_chat_id = await _get_internal_chat_id(message.chat.id, message.chat.title)
    await safe_answer(
        message,
        "👋 Привет! Я *Digest* — анализирую рабочий чат и собираю итоги обсуждений.\n\n"
        "Я слежу за обсуждениями, разбираю их по темам, выделяю решения и задачи "
        "и присылаю ежедневный и еженедельный дайджест.\n\n"
        "Выбери действие на кнопках ниже 👇",
        parse_mode="Markdown",
        reply_markup=_main_menu_keyboard(internal_chat_id),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    # Plain text: command names contain "_", which Telegram Markdown would
    # misread as italics. Actions are also available via the buttons below.
    text = (
        "Digest — что я умею:\n\n"
        "Я сам читаю все сообщения в чате и выделяю из них задачи — команды для "
        "этого не нужны. Кнопки ниже — чтобы посмотреть результат:\n\n"
        "📋 Задачи — активные задачи (с кнопками статусов)\n"
        "📊 Дайджест — сводка за сегодня\n"
        "❓ Уточнения — задачи, где не хватает данных\n"
        "⚙️ Настройки — часовой пояс и время дайджеста\n\n"
        "Отчёт за период: /digest, /digest today, /digest yesterday, /digest week, /digest 3d, "
        "/digest 2026-11-01 2026-11-03\n\n"
        "Другие команды: /task <текст>, /status, /chatid\n\n"
        "Для администраторов: /set_timezone <TZ>, /set_digest_time <HH:MM>, "
        "/set_weekly <пн..вс|off>, /set_digest_chat <id|here>, /digest_on, /digest_off, "
        "/reanalyze <дней>, /purge_history <дней> confirm, /pin_dashboard"
    )
    internal_chat_id = None
    if message.chat:
        internal_chat_id = await _get_internal_chat_id(message.chat.id, message.chat.title)
    await safe_answer(message, text, reply_markup=_main_menu_keyboard(internal_chat_id))


async def _send_tasks(bot: Bot, chat_id: int, chat_title: str | None = None) -> None:
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        task_repo = TaskRepository(session)
        chat = await chat_repo.get_or_create(chat_id, chat_title)
        tasks = await task_repo.get_active(chat.id)
        payloads = [(_task_block_text(t), t.id, t.status) for t in tasks]
        internal_chat_id = chat.id

    web_link_markup: InlineKeyboardMarkup | None = None
    if dashboard_configured():
        web_link_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🌐 Открыть все на сайте",
                        url=chat_dashboard_url(internal_chat_id),
                    )
                ]
            ]
        )

    if not payloads:
        await safe_send_message(bot, chat_id, "Активных задач нет.", reply_markup=web_link_markup)
        return

    await safe_send_message(
        bot,
        chat_id,
        f"*Активные задачи* ({len(payloads)}):",
        parse_mode="Markdown",
        reply_markup=web_link_markup,
    )
    for text, task_id, status in payloads[:INLINE_TASK_LIMIT]:
        await safe_send_message(
            bot, chat_id, text, parse_mode="Markdown", reply_markup=_task_keyboard(task_id, status)
        )
    remaining = len(payloads) - INLINE_TASK_LIMIT
    if remaining > 0:
        if web_link_markup is not None:
            await safe_send_message(
                bot,
                chat_id,
                f"…и ещё {remaining}. Полный список — на сайте 👇",
                reply_markup=web_link_markup,
            )
        else:
            await safe_send_message(bot, chat_id, f"…и ещё {remaining}.")


async def _send_digest(
    bot: Bot, chat_id: int, chat_title: str | None = None, period_arg: str = ""
) -> None:
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(chat_id, chat_title)
        period = parse_period(period_arg, chat.timezone, now_utc())
        if period is None:
            await safe_send_message(
                bot,
                chat_id,
                "Не понял период. Примеры: /digest, /digest today, /digest yesterday, "
                "/digest week, /digest 3d, /digest 2026-11-01 2026-11-03",
            )
            return

        await safe_send_message(bot, chat_id, "⏳ Формирую дайджест...")
        llm = _make_llm()
        digest_svc = DigestService(session, llm)
        try:
            await flush_pending(bot, session, llm, chat)
            digest = await digest_svc.build_digest(chat, period.start, period.end)
            await session.commit()
        except Exception as e:
            logger.error("send_digest.error", error=type(e).__name__)
            await session.rollback()
            await safe_send_message(bot, chat_id, "Не удалось сформировать дайджест. Попробуйте позже.")
            return
        if await digest_svc.deliver(bot, digest, chat_id):
            metrics.digests_sent_total.inc()


async def _send_clarify(bot: Bot, chat_id: int, chat_title: str | None = None) -> None:
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(chat_id, chat_title)
        pending = await ClarificationService(session).get_pending_for_chat(chat.id)

    if not pending:
        await safe_send_message(bot, chat_id, "Нет задач, требующих уточнения.")
        return
    lines = ["*Задачи, требующие уточнения:*\n"]
    lines.extend(f"❓ {c.question}" for c in pending)
    await safe_send_message(bot, chat_id, "\n".join(lines), parse_mode="Markdown")


async def _send_settings(bot: Bot, chat_id: int, chat_title: str | None = None) -> None:
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(chat_id, chat_title)
    weekly = (
        f"по дням «{WEEKDAY_NAMES[chat.weekly_digest_day]}»"
        if chat.weekly_digest_enabled
        else "выключен"
    )
    text = (
        f"*Настройки чата:*\n\n"
        f"🌐 Часовой пояс: `{chat.timezone}`\n"
        f"🕕 Время дайджеста: `{chat.digest_time}`\n"
        f"📨 Дайджест в этом чате: {'включён' if chat.digest_enabled else 'выключен'}\n"
        f"🗓 Еженедельный: {weekly}\n"
        f"📤 Куда отправлять: `{chat.digest_chat_id or 'этот чат'}`\n"
        f"🆔 Chat ID: `{chat.telegram_chat_id}`\n\n"
        f"Изменить: `/set_timezone <TZ>`, `/set_digest_time <HH:MM>`, `/set_weekly <пн..вс|off>`, "
        f"`/set_digest_chat <id|here>`, `/digest_on`, `/digest_off`"
    )
    presets = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"🕕 {t}", callback_data=f"digest_time:{t}")
                for t in DIGEST_TIME_PRESETS
            ]
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
        await message.answer("Укажите текст задачи: /task <описание задачи>")
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

    await safe_answer(
        message,
        f"✅ Задача создана:\n*{task.title}*\nID: `{task.id}`",
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
    if not arg:
        await message.answer("Укажите часовой пояс, например: /set_timezone Europe/Moscow")
        return
    if not is_valid_timezone(arg):
        await message.answer(
            f"Неизвестный часовой пояс: {arg}\nПримеры: Asia/Almaty, Europe/Moscow, UTC"
        )
        return
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(message.chat.id, message.chat.title)
        await chat_repo.update_settings(chat, timezone=arg)
        await session.commit()
    await safe_answer(message, f"✅ Часовой пояс обновлён: `{arg}`", parse_mode="Markdown")


@router.message(Command("set_digest_time"))
async def cmd_set_digest_time(message: Message) -> None:
    if not message.chat or not message.text or not await _require_admin(message):
        return
    arg = _command_arg(message)
    if not arg:
        await message.answer("Укажите время в формате HH:MM, например: /set_digest_time 18:00")
        return
    if not is_valid_digest_time(arg):
        await message.answer(f"Неверный формат времени: {arg}\nОжидается HH:MM, например 18:00")
        return
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(message.chat.id, message.chat.title)
        await chat_repo.update_settings(chat, digest_time=arg)
        await session.commit()
    await safe_answer(message, f"✅ Время дайджеста обновлено: `{arg}`", parse_mode="Markdown")


@router.callback_query(F.data.startswith("digest_time:"))
async def on_digest_time(callback: CallbackQuery) -> None:
    value = (callback.data or "").split(":", 1)[1]
    if not is_admin(callback.from_user.id):
        await callback.answer("Только для администраторов", show_alert=True)
        return
    if not is_valid_digest_time(value) or not isinstance(callback.message, Message):
        await callback.answer()
        return
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(callback.message.chat.id, callback.message.chat.title)
        await chat_repo.update_settings(chat, digest_time=value)
        await session.commit()
    await callback.answer(f"Время дайджеста: {value}")


@router.message(Command("set_weekly"))
async def cmd_set_weekly(message: Message) -> None:
    if not message.chat or not await _require_admin(message):
        return
    arg = _command_arg(message).lower()
    if arg != "off" and arg not in WEEKDAYS:
        await message.answer("Укажите день или off, например: /set_weekly пт")
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
        "🔕 Еженедельный дайджест выключен." if arg == "off" else f"✅ Еженедельный дайджест: {arg}"
    )


@router.message(Command("set_digest_chat"))
async def cmd_set_digest_chat(message: Message) -> None:
    if not message.chat or not await _require_admin(message):
        return
    arg = _command_arg(message).lower()
    target: int | None = None
    if arg != "here":
        try:
            target = int(arg)
        except ValueError:
            await message.answer("Укажите ID чата или here, например: /set_digest_chat -1001234567890")
            return
        # A digest is a summary of this chat: it may only go somewhere already trusted.
        if target not in settings.allowed_chats:
            await message.answer("Этого чата нет в ALLOWED_CHAT_IDS.")
            return
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(message.chat.id, message.chat.title)
        await chat_repo.set_digest_target(chat, target)
        await session.commit()
    await message.answer(f"✅ Отчёты будут приходить в: {target or 'этот чат'}")


@router.message(Command("chatid"))
async def cmd_chatid(message: Message) -> None:
    await message.answer(f"Chat ID: {message.chat.id}")


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    async with async_session_factory() as session:
        chat = await ChatRepository(session).get_or_create(message.chat.id, message.chat.title)
        pending, _ = await MessageRepository(session).pending_stats(chat.id)
    blocked = llm_block_reason()
    if settings.llm_provider != "openai":
        where = "тестовый режим без модели (mock)"
    elif is_local_endpoint(settings.openai_base_url):
        where = "локальная модель, данные не покидают сервер"
    else:
        where = "внешний сервис"
    await message.answer(
        f"Модель: {settings.openai_model} ({where})\n"
        f"Анализ: {'приостановлен: ' + blocked if blocked else 'работает'}\n"
        f"Сообщений в очереди: {pending}"
    )


@router.message(Command("reanalyze"))
async def cmd_reanalyze(message: Message) -> None:
    if not message.chat or not await _require_admin(message):
        return
    arg = _command_arg(message)
    if not arg.isdigit() or int(arg) == 0:
        await message.answer("Укажите число дней: /reanalyze 7")
        return
    async with async_session_factory() as session:
        chat = await ChatRepository(session).get_or_create(message.chat.id, message.chat.title)
        count = await MessageRepository(session).requeue_since(
            chat.id, now_utc() - timedelta(days=int(arg))
        )
        await session.commit()
    await message.answer(f"Поставлено в очередь на анализ: {count}")


@router.message(Command("purge_history"))
async def cmd_purge_history(message: Message) -> None:
    if not message.chat or not await _require_admin(message):
        return
    parts = _command_arg(message).split()
    if len(parts) != 2 or not parts[0].isdigit() or parts[1] != "confirm":
        await message.answer(
            "Удаление необратимо. Формат: /purge_history <дней> confirm\n"
            "Удаляются сообщения старше указанного числа дней. "
            "0 удаляет всю историю чата вместе с темами и отчётами; задачи остаются."
        )
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
    await message.answer(f"Удалено сообщений: {removed}")


async def _set_digest_enabled(message: Message, enabled: bool) -> None:
    if not message.chat or not await _require_admin(message):
        return
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(message.chat.id, message.chat.title)
        await chat_repo.update_settings(chat, digest_enabled=enabled)
        await session.commit()
    if enabled:
        await message.answer("🔔 Дайджест в этом чате включён.")
    else:
        await message.answer("🔕 Дайджест в этом чате выключен. Задачи бот собирать продолжит.")


@router.message(Command("pin_dashboard"))
async def cmd_pin_dashboard(message: Message, bot: Bot) -> None:
    """Send the web-dashboard link and pin it, so it stays visible above the chat."""
    if not message.chat or not await _require_admin(message):
        return
    if not dashboard_configured():
        await message.answer(
            "Сайт с задачами не настроен (нет DASHBOARD_SECRET / PUBLIC_BASE_URL в .env)."
        )
        return
    internal_chat_id = await _get_internal_chat_id(message.chat.id, message.chat.title)
    sent = await safe_send_message(
        bot,
        message.chat.id,
        "🌐 Задачи этого чата на сайте:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🌐 Открыть сайт с задачами",
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
        await message.answer(
            "Ссылку отправил, но закрепить не получилось — дайте боту право "
            "«Закреплять сообщения» в настройках группы и повторите /pin_dashboard."
        )


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
    new_status = TASK_ACTIONS.get(action)
    if new_status is None or not task_id_str.isdigit():
        await callback.answer("Неизвестное действие")
        return

    if not isinstance(callback.message, Message):
        await callback.answer()
        return

    new_text: str | None = None
    markup: InlineKeyboardMarkup | None = None
    async with async_session_factory() as session:
        task_repo = TaskRepository(session)
        chat = await ChatRepository(session).get_or_create(callback.message.chat.id)
        task = await task_repo.get_by_id(int(task_id_str))
        # The id comes from the client, so a task of another chat must look like no task.
        if task is None or task.chat_id != chat.id:
            await callback.answer("Задача не найдена")
            return
        await task_repo.set_status(task, new_status)
        await session.commit()
        new_text = _task_block_text(task)
        markup = _task_keyboard(task.id, task.status)

    await callback.answer(f"Статус: {STATUS_LABELS.get(new_status.value, new_status.value)}")
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
