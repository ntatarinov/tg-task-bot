from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.bot.utils import safe_answer, safe_send_message
from app.config import settings
from app.db.models import Task, TaskPriority, TaskStatus
from app.db.repositories import ChatRepository, TaskRepository
from app.db.session import async_session_factory
from app.llm.openai_client import create_llm_client
from app.services.clarification_service import ClarificationService, ClarificationToAsk
from app.services.digest_service import DigestService
from app.services.message_ingestor import MessageIngestor
from app.services.task_analyzer import TaskAnalyzer
from app.utils import metrics
from app.utils.logging import get_logger
from app.utils.time import is_valid_digest_time, is_valid_timezone

logger = get_logger(__name__)
router = Router()

STATUS_LABELS: dict[str, str] = {
    TaskStatus.new.value: "🆕 Новая",
    TaskStatus.needs_clarification.value: "❓ Нужно уточнение",
    TaskStatus.ready.value: "✅ Готова",
    TaskStatus.in_progress.value: "🔄 В работе",
    TaskStatus.done.value: "✔️ Выполнена",
    TaskStatus.cancelled.value: "❌ Отменена",
}

PRIORITY_EMOJI: dict[str, str] = {
    "low": "🟢",
    "medium": "🟡",
    "high": "🟠",
    "critical": "🔴",
}

# Callback action -> resulting status for inline task buttons.
TASK_ACTIONS: dict[str, TaskStatus] = {
    "progress": TaskStatus.in_progress,
    "done": TaskStatus.done,
    "cancel": TaskStatus.cancelled,
    "reopen": TaskStatus.new,
}


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


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 Задачи", callback_data="menu:tasks"),
                InlineKeyboardButton(text="📊 Дайджест", callback_data="menu:digest"),
            ],
            [
                InlineKeyboardButton(text="❓ Уточнения", callback_data="menu:clarify"),
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:settings"),
            ],
        ]
    )


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await safe_answer(
        message,
        "👋 Привет! Я *Тудушка* — анализирую командный чат и формирую задачи.\n\n"
        "Я слежу за обсуждениями, автоматически выделяю задачи из переписки "
        "и каждый день в 18:00 присылаю дайджест.\n\n"
        "Выбери действие на кнопках ниже 👇",
        parse_mode="Markdown",
        reply_markup=_main_menu_keyboard(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    # Plain text: command names contain "_", which Telegram Markdown would
    # misread as italics. Actions are also available via the buttons below.
    text = (
        "Тудушка — что я умею:\n\n"
        "Я сам читаю все сообщения в чате и выделяю из них задачи — команды для "
        "этого не нужны. Кнопки ниже — чтобы посмотреть результат:\n\n"
        "📋 Задачи — активные задачи (с кнопками статусов)\n"
        "📊 Дайджест — сводка за сегодня\n"
        "❓ Уточнения — задачи, где не хватает данных\n"
        "⚙️ Настройки — часовой пояс и время дайджеста\n\n"
        "Доп. команды: /task <текст>, /set_timezone <TZ>, "
        "/set_digest_time <HH:MM>, /digest_off (выкл. дайджест в этом чате), /digest_on"
    )
    await safe_answer(message, text, reply_markup=_main_menu_keyboard())


async def _send_tasks(bot: Bot, chat_id: int, chat_title: str | None = None) -> None:
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        task_repo = TaskRepository(session)
        chat = await chat_repo.get_or_create(chat_id, chat_title)
        tasks = await task_repo.get_active(chat.id)
        payloads = [(_task_block_text(t), t.id, t.status) for t in tasks]

    if not payloads:
        await safe_send_message(bot, chat_id, "Активных задач нет.")
        return

    await safe_send_message(
        bot, chat_id, f"*Активные задачи* ({len(payloads)}):", parse_mode="Markdown"
    )
    for text, task_id, status in payloads[:20]:
        await safe_send_message(
            bot, chat_id, text, parse_mode="Markdown", reply_markup=_task_keyboard(task_id, status)
        )
    if len(payloads) > 20:
        await safe_send_message(bot, chat_id, f"…и ещё {len(payloads) - 20}.")


async def _send_digest(bot: Bot, chat_id: int, chat_title: str | None = None) -> None:
    await safe_send_message(bot, chat_id, "⏳ Формирую дайджест...")
    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        chat = await chat_repo.get_or_create(chat_id, chat_title)
        digest_svc = DigestService(session, _make_llm())
        try:
            text = await digest_svc.build_digest(chat)
            await session.commit()
        except Exception as e:
            logger.error("send_digest.error", error=str(e))
            await safe_send_message(bot, chat_id, "Не удалось сформировать дайджест. Попробуйте позже.")
            return
    metrics.digests_sent_total.inc()
    await safe_send_message(bot, chat_id, text, parse_mode="Markdown")


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
    text = (
        f"*Настройки чата:*\n\n"
        f"🌐 Часовой пояс: `{chat.timezone}`\n"
        f"🕕 Время дайджеста: `{chat.digest_time}`\n"
        f"📨 Дайджест в этом чате: {'включён' if chat.digest_enabled else 'выключен'}\n"
        f"🆔 Chat ID: `{chat.telegram_chat_id}`\n\n"
        f"Изменить: `/set_timezone <TZ>`, `/set_digest_time <HH:MM>`, "
        f"`/digest_on`, `/digest_off`"
    )
    await safe_send_message(bot, chat_id, text, parse_mode="Markdown")


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
        await _send_digest(bot, message.chat.id, message.chat.title)


@router.message(Command("task"))
async def cmd_task(message: Message) -> None:
    if not message.chat or not message.text:
        return

    task_text = message.text.removeprefix("/task").strip()
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
    if not message.chat or not message.text:
        return
    arg = message.text.removeprefix("/set_timezone").strip()
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
    if not message.chat or not message.text:
        return
    arg = message.text.removeprefix("/set_digest_time").strip()
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


async def _set_digest_enabled(message: Message, enabled: bool) -> None:
    if not message.chat:
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

    new_text: str | None = None
    markup: InlineKeyboardMarkup | None = None
    async with async_session_factory() as session:
        task_repo = TaskRepository(session)
        task = await task_repo.get_by_id(int(task_id_str))
        if task is None:
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


@router.message()
async def handle_message(message: Message, bot: Bot) -> None:
    """Catch-all: ingest every text/caption message and optionally ask clarification."""
    if not message.chat:
        return
    # Also capture captions on photos/documents so media task signals aren't lost.
    content = message.text or message.caption
    if not content or content.startswith("/"):
        return

    from_user = message.from_user
    raw_update: dict = {}
    try:
        raw_update = message.model_dump(mode="json", exclude_none=True)
    except Exception:
        pass

    to_ask: ClarificationToAsk | None = None

    async with async_session_factory() as session:
        llm = _make_llm()
        analyzer = TaskAnalyzer(session, llm)
        ingestor = MessageIngestor(session, analyzer)
        try:
            _, to_ask = await ingestor.ingest(
                telegram_chat_id=message.chat.id,
                chat_title=message.chat.title,
                telegram_user_id=from_user.id if from_user else None,
                username=from_user.username if from_user else None,
                display_name=(
                    f"{from_user.first_name or ''} {from_user.last_name or ''}".strip()
                    if from_user
                    else None
                ),
                telegram_message_id=message.message_id,
                text=content,
                raw_update_json=raw_update,
                telegram_thread_id=message.message_thread_id,
                reply_to_message_id=(
                    message.reply_to_message.message_id if message.reply_to_message else None
                ),
            )
            await session.commit()
        except Exception as e:
            logger.error("handle_message.error", error=str(e))
            await session.rollback()
            return

    if to_ask:
        sent = await safe_send_message(
            bot,
            chat_id=message.chat.id,
            text=to_ask.question,
            reply_to_message_id=message.message_id,
        )
        # Remember which Telegram message carried the question so a reply to it
        # can later be matched as the answer (see ClarificationService).
        if sent is not None:
            async with async_session_factory() as session:
                try:
                    svc = ClarificationService(session)
                    await svc.set_question_message_id(to_ask.clarification_id, sent.message_id)
                    await session.commit()
                except Exception as e:
                    logger.error("handle_message.set_question_message_id_failed", error=str(e))
                    await session.rollback()
