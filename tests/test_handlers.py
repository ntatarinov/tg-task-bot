"""Every command, button and message type, sent through the real aiogram dispatcher."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
import pytz
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, GetMe, PinChatMessage, SendMessage
from aiogram.types import Chat, Message, Update, User
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import handlers
from app.config import settings
from app.db.models import Chat as ChatRow
from app.db.models import Clarification, Digest, Task, TaskStatus
from app.db.models import Message as MessageRow
from app.db.repositories import ChatRepository, ClarificationRepository, TaskRepository
from tests.conftest import ADMIN_ID, ALLOWED_CHAT
from tests.helpers import ScriptedLLM, feed

MEMBER = 2000
FOREIGN_CHAT = -555
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=pytz.utc)
EDIT_TIME = int(NOW.timestamp()) + 60  # the Bot API sends edit_date as a Unix timestamp

_dispatcher: Dispatcher | None = None


def get_dispatcher() -> Dispatcher:
    # A router can only be attached once per process.
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = Dispatcher()
        _dispatcher.include_router(handlers.router)
    return _dispatcher


class RecordingSession(BaseSession):
    """Stands in for the network: records every Bot API call and answers it."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []
        self.fail_pin = False

    async def close(self) -> None:
        return None

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, GetMe):
            return User(id=1, is_bot=True, first_name="Digest", username="digest_work_bot")
        self.calls.append(method)
        if isinstance(method, PinChatMessage) and self.fail_pin:
            raise TelegramBadRequest(method=method, message="not enough rights")
        if isinstance(method, SendMessage | EditMessageText):
            return Message(
                message_id=7000 + len(self.calls),
                date=NOW,
                chat=Chat(id=method.chat_id or ALLOWED_CHAT, type="supergroup"),
                text=method.text,
            )
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""


class Harness:
    def __init__(self, factory) -> None:
        self.factory = factory
        self.session = RecordingSession()
        self.bot = Bot("123456:TEST", session=self.session)
        self.next_id = 100

    @property
    def replies(self) -> list[str]:
        return [c.text for c in self.session.calls if isinstance(c, SendMessage)]

    @property
    def reply(self) -> str:
        return "\n".join(self.replies)

    @property
    def toasts(self) -> list[str | None]:
        return [c.text for c in self.session.calls if isinstance(c, AnswerCallbackQuery)]

    def message(self, text=None, chat_id=ALLOWED_CHAT, user_id=MEMBER, **extra) -> dict:
        self.next_id += 1
        payload = {
            "message_id": self.next_id,
            "date": NOW,
            "chat": Chat(id=chat_id, type="supergroup", title="Team"),
            "from_user": User(id=user_id, is_bot=False, first_name="Аня", username=f"user{user_id}"),
        }
        if text is not None:
            payload["text"] = text
        return {**payload, **extra}

    async def feed(self, **update) -> None:
        self.next_id += 1
        await get_dispatcher().feed_update(self.bot, Update(update_id=self.next_id, **update))

    async def send(self, text=None, chat_id=ALLOWED_CHAT, user_id=MEMBER, **extra) -> None:
        await self.feed(message=Message(**self.message(text, chat_id, user_id, **extra)))

    async def admin(self, text: str) -> None:
        await self.send(text, user_id=ADMIN_ID)

    async def press(self, data: str, chat_id=ALLOWED_CHAT, user_id=MEMBER) -> None:
        from aiogram.types import CallbackQuery

        await self.feed(
            callback_query=CallbackQuery(
                id="1",
                from_user=User(id=user_id, is_bot=False, first_name="Аня"),
                chat_instance="x",
                message=Message(**self.message("кнопка", chat_id, 1)),
                data=data,
            )
        )

    async def query(self, statement):
        async with self.factory() as session:
            return (await session.execute(statement)).scalars().all()

    async def chat(self, telegram_chat_id=ALLOWED_CHAT) -> ChatRow:
        async with self.factory() as session:
            chat = await ChatRepository(session).get_or_create(telegram_chat_id, "Team")
            await session.commit()
            return chat

    async def add_task(self, telegram_chat_id=ALLOWED_CHAT, **fields) -> Task:
        chat = await self.chat(telegram_chat_id)
        async with self.factory() as session:
            fields.setdefault("title", "Задача")
            task = await TaskRepository(session).create(chat.id, **fields)
            await session.commit()
            return task


@pytest_asyncio.fixture
async def harness(engine, monkeypatch):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(handlers, "async_session_factory", factory)
    monkeypatch.setattr(handlers, "_make_llm", lambda: ScriptedLLM({"topics": []}))
    monkeypatch.setattr(settings, "public_base_url", "")
    return Harness(factory)


# --- Basic commands ----------------------------------------------------------


@pytest.mark.parametrize("command", ["/start", "/help", "/start@digest_work_bot"])
@pytest.mark.asyncio
async def test_greeting_commands_answer(harness, command):
    await harness.send(command)
    assert "Digest" in harness.reply


@pytest.mark.asyncio
async def test_chatid_shows_the_chat(harness):
    await harness.send("/chatid")
    assert str(ALLOWED_CHAT) in harness.reply


@pytest.mark.parametrize(
    "provider, base_url, consent, expected",
    [
        ("mock", "", False, "mock"),
        ("openai", "http://localhost:11434/v1", False, "local model"),
        ("openai", "https://api.groq.com/openai/v1", True, "external"),
        ("openai", "https://api.groq.com/openai/v1", False, "paused"),
    ],
)
@pytest.mark.asyncio
async def test_status_for_each_model_setup(harness, monkeypatch, provider, base_url, consent, expected):
    monkeypatch.setattr(settings, "llm_provider", provider)
    monkeypatch.setattr(settings, "openai_base_url", base_url)
    monkeypatch.setattr(settings, "openai_api_key", "key")
    monkeypatch.setattr(settings, "llm_external_allowed", consent)

    await harness.send("/status")

    assert expected in harness.reply
    assert "Messages in the queue: 0" in harness.reply


# --- Tasks -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tasks_when_empty(harness):
    await harness.send("/tasks")
    assert "No active tasks" in harness.reply


@pytest.mark.asyncio
async def test_tasks_lists_cards_with_buttons(harness):
    await harness.add_task(title="Настроить бэкапы", assignee_text="@dev", reporter_text="@lead", deadline="2026-11-06")
    await harness.send("/tasks")

    assert "Настроить бэкапы" in harness.reply
    assert "@dev" in harness.reply and "@lead" in harness.reply and "2026-11-06" in harness.reply


@pytest.mark.asyncio
async def test_long_task_list_is_cut_short(harness):
    for i in range(handlers.INLINE_TASK_LIMIT + 3):
        await harness.add_task(title=f"Задача {i}")
    await harness.send("/tasks")

    assert "…and 3 more" in harness.reply


@pytest.mark.parametrize(
    "command, title",
    [("/task Обновить документацию", "Обновить документацию"), ("/task@digest_work_bot Сделать X", "Сделать X")],
)
@pytest.mark.asyncio
async def test_manual_task_is_created(harness, command, title):
    await harness.send(command)

    tasks = await harness.query(select(Task))
    assert [t.title for t in tasks] == [title]
    assert tasks[0].source == "manual"
    assert tasks[0].reporter_text == f"@user{MEMBER}"


@pytest.mark.asyncio
async def test_manual_task_needs_text(harness):
    await harness.send("/task")
    assert "Give the task text" in harness.reply
    assert await harness.query(select(Task)) == []


@pytest.mark.parametrize(
    "action, status",
    [("progress", TaskStatus.in_progress), ("done", TaskStatus.done),
     ("cancel", TaskStatus.cancelled), ("reopen", TaskStatus.new)],
)
@pytest.mark.asyncio
async def test_status_buttons(harness, action, status):
    task = await harness.add_task()
    await harness.press(f"task:{task.id}:{action}")

    assert (await harness.query(select(Task.status))) == [status]
    assert any(isinstance(c, EditMessageText) for c in harness.session.calls)


@pytest.mark.parametrize("data", ["task:1:explode", "task:abc:done", "task:1", "task:1:done:extra"])
@pytest.mark.asyncio
async def test_malformed_task_buttons_change_nothing(harness, data):
    task = await harness.add_task(status=TaskStatus.new)
    assert task.id == 1

    await harness.press(data)

    assert (await harness.query(select(Task.status))) == [TaskStatus.new]


@pytest.mark.asyncio
async def test_button_of_a_missing_task(harness):
    await harness.press("task:999:done")
    assert harness.toasts == ["Task not found"]


@pytest.mark.asyncio
async def test_clarify_lists_open_questions(harness):
    await harness.send("/clarify")
    assert "No tasks need clarification" in harness.reply

    task = await harness.add_task()
    async with harness.factory() as session:
        await ClarificationRepository(session).create(task.id, "Кто возьмёт задачу?")
        await session.commit()
    await harness.send("/clarify")

    assert "Кто возьмёт задачу?" in harness.reply


# --- Menu --------------------------------------------------------------------


@pytest.mark.parametrize(
    "data, expected",
    [("menu:tasks", "No active tasks"), ("menu:digest", "No work topics found"),
     ("menu:clarify", "No tasks"), ("menu:settings", "Chat settings")],
)
@pytest.mark.asyncio
async def test_menu_buttons(harness, data, expected):
    await harness.press(data)
    assert expected in harness.reply


# --- Digest periods ----------------------------------------------------------


@pytest.mark.parametrize(
    "arg",
    ["", " today", " сегодня", " yesterday", " вчера", " week", " неделя", " 3d", " 3д",
     " 2026-11-01", " 01.11.2026", " 2026-11-01 2026-11-03"],
)
@pytest.mark.asyncio
async def test_digest_for_every_period_form(harness, arg):
    await harness.send(f"/digest{arg}")

    assert "Digest —" in harness.reply
    digests = await harness.query(select(Digest))
    assert len(digests) == 1 and digests[0].sent_at is not None


@pytest.mark.parametrize("arg", ["потом", "2026-13-01", "2026-11-03 2026-11-01", "0d", "1 2 3"])
@pytest.mark.asyncio
async def test_digest_rejects_an_unreadable_period(harness, arg):
    await harness.send(f"/digest {arg}")

    assert "didn't understand the period" in harness.reply
    assert await harness.query(select(Digest)) == []


@pytest.mark.asyncio
async def test_digest_analyzes_the_queue_first(harness, monkeypatch):
    chat = await harness.chat()
    async with harness.factory() as session:
        await feed(session, chat, [("Аня", "Нужно обновить сертификаты.")], start=datetime.now(pytz.utc) - timedelta(hours=1))
    monkeypatch.setattr(handlers, "_make_llm", lambda: ScriptedLLM({"topics": [{
        "title": "Сертификаты", "summary": "Обсудили сертификаты.", "message_ids": [1],
        "items": [{"kind": "task", "text": "Обновить сертификаты", "source_ids": [1]}],
    }]}))

    await harness.send("/digest")

    assert "Обновить сертификаты" in harness.reply


@pytest.mark.asyncio
async def test_digest_failure_is_reported_not_swallowed(harness, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(handlers.DigestService, "build_digest", boom)

    await harness.send("/digest")

    assert "Could not build the digest" in harness.reply


# --- Settings: who may change what -------------------------------------------


@pytest.mark.parametrize(
    "command, column, expected",
    [
        ("/set_timezone Europe/Moscow", "timezone", "Europe/Moscow"),
        ("/set_digest_time 19:30", "digest_time", "19:30"),
        ("/set_weekly пн", "weekly_digest_day", 0),
        ("/set_weekly вс", "weekly_digest_day", 6),
        ("/set_weekly off", "weekly_digest_enabled", False),
        ("/digest_off", "digest_enabled", False),
    ],
)
@pytest.mark.asyncio
async def test_admin_changes_settings(harness, command, column, expected):
    await harness.admin(command)
    assert getattr(await harness.chat(), column) == expected


@pytest.mark.asyncio
async def test_digest_on_after_off(harness):
    await harness.admin("/digest_off")
    await harness.admin("/digest_on")
    assert (await harness.chat()).digest_enabled is True


@pytest.mark.parametrize(
    "command, expected",
    [
        ("/set_timezone", "Give a time zone"),
        ("/set_timezone Mars/Phobos", "Unknown time zone"),
        ("/set_digest_time", "Give the time"),
        ("/set_digest_time 25:00", "Wrong time format"),
        ("/set_weekly", "Give a day"),
        ("/set_weekly пятница", "Give a day"),
        ("/set_digest_chat abc", "Give a chat ID"),
        ("/set_digest_chat -999", "ALLOWED_CHAT_IDS"),
        ("/reanalyze", "number of days"),
        ("/reanalyze 0", "number of days"),
        ("/reanalyze много", "number of days"),
        ("/purge_history", "cannot be undone"),
        ("/purge_history 30", "cannot be undone"),
        ("/purge_history abc confirm", "cannot be undone"),
    ],
)
@pytest.mark.asyncio
async def test_admin_commands_explain_bad_input(harness, command, expected):
    await harness.admin(command)
    assert expected in harness.reply


@pytest.mark.parametrize(
    "command",
    ["/set_timezone UTC", "/set_digest_time 10:00", "/set_weekly пн", "/set_digest_chat here",
     "/digest_on", "/digest_off", "/reanalyze 7", "/purge_history 0 confirm", "/pin_dashboard"],
)
@pytest.mark.asyncio
async def test_members_are_refused_every_admin_command(harness, command):
    chat_before = await harness.chat()
    await harness.send(command)

    assert "for bot admins only" in harness.reply
    assert (await harness.chat()).digest_time == chat_before.digest_time


@pytest.mark.asyncio
async def test_digest_target_can_be_set_and_reset(harness, monkeypatch):
    monkeypatch.setattr(settings, "allowed_chat_ids", f"{ALLOWED_CHAT},-777")

    await harness.admin("/set_digest_chat -777")
    assert (await harness.chat()).digest_chat_id == -777

    await harness.admin("/set_digest_chat here")
    assert (await harness.chat()).digest_chat_id is None


@pytest.mark.asyncio
async def test_settings_show_presets_and_current_values(harness):
    await harness.send("/settings")

    assert "Asia/Almaty" in harness.reply
    keyboard = next(c for c in harness.session.calls if isinstance(c, SendMessage)).reply_markup
    labels = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert labels == [
        *(f"digest_time:{t}" for t in ("18:00", "20:00", "21:00", "23:00")),
        "language:en",
        "language:ru",
    ]


@pytest.mark.parametrize("value", ["18:00", "20:00", "21:00", "23:00"])
@pytest.mark.asyncio
async def test_time_preset_buttons(harness, value):
    await harness.press(f"digest_time:{value}", user_id=ADMIN_ID)
    assert (await harness.chat()).digest_time == value


@pytest.mark.asyncio
async def test_member_cannot_use_time_presets(harness):
    await harness.press("digest_time:23:00")

    assert (await harness.chat()).digest_time == "18:00"
    assert harness.toasts == ["Admins only"]


@pytest.mark.asyncio
async def test_reanalyze_requeues_recent_messages(harness):
    chat = await harness.chat()
    async with harness.factory() as session:
        messages = await feed(session, chat, [("Аня", "Сообщение")], start=datetime.now(pytz.utc) - timedelta(hours=1))
        messages[0].pending_analysis = False
        await session.commit()

    await harness.admin("/reanalyze 1")

    assert "Queued for analysis: 1" in harness.reply
    assert (await harness.query(select(MessageRow.pending_analysis))) == [True]


@pytest.mark.parametrize("days, left", [(30, 1), (0, 0)])
@pytest.mark.asyncio
async def test_purge_history(harness, days, left):
    chat = await harness.chat()
    async with harness.factory() as session:
        messages = await feed(session, chat, [("Аня", "Новое")], start=datetime.now(pytz.utc) - timedelta(hours=1))
        old = await feed(session, chat, [("Аня", "Старое")], start_id=2, start=datetime.now(pytz.utc) - timedelta(days=90))
        for message in messages + old:
            message.pending_analysis = False
        await session.commit()

    await harness.admin(f"/purge_history {days} confirm")

    assert len(await harness.query(select(MessageRow))) == left


@pytest.mark.parametrize(
    "secret, url, fail_pin, expected",
    [
        ("", "", False, "not set up"),
        ("s3cret", "https://example.com", False, "tasks on the site"),
        ("s3cret", "https://example.com", True, "could not pin it"),
    ],
)
@pytest.mark.asyncio
async def test_pin_dashboard_variants(harness, monkeypatch, secret, url, fail_pin, expected):
    monkeypatch.setattr(settings, "dashboard_secret", secret)
    monkeypatch.setattr(settings, "public_base_url", url)
    harness.session.fail_pin = fail_pin

    await harness.admin("/pin_dashboard")

    assert expected in harness.reply


@pytest.mark.asyncio
async def test_dashboard_link_appears_when_configured(harness, monkeypatch):
    monkeypatch.setattr(settings, "dashboard_secret", "s3cret")
    monkeypatch.setattr(settings, "public_base_url", "https://example.com")

    await harness.send("/tasks")

    markup = next(c for c in harness.session.calls if isinstance(c, SendMessage)).reply_markup
    assert "https://example.com/dashboard/" in markup.inline_keyboard[0][0].url


# --- Incoming messages -------------------------------------------------------


async def _stored(harness) -> MessageRow:
    return (await harness.query(select(MessageRow)))[0]


@pytest.mark.asyncio
async def test_text_is_stored_and_queued_without_replying(harness):
    await harness.send("Нужно обновить сертификаты")

    stored = await _stored(harness)
    assert stored.text == "Нужно обновить сертификаты"
    assert stored.pending_analysis is True
    assert stored.content_type == "text"
    assert harness.session.calls == []


@pytest.mark.asyncio
async def test_message_metadata_is_stored(harness):
    original = Message(**harness.message("Вопрос"))
    await harness.send("Ответ", reply_to_message=original, message_thread_id=55)

    stored = await _stored(harness)
    assert stored.reply_to_message_id == original.message_id
    assert stored.telegram_thread_id == 55
    assert stored.sent_at.replace(tzinfo=pytz.utc) == NOW


@pytest.mark.asyncio
async def test_caption_counts_as_text(harness):
    photo = [{"file_id": "a", "file_unique_id": "b", "width": 1, "height": 1}]
    await harness.send(caption="Скриншот ошибки", photo=photo)

    stored = await _stored(harness)
    assert stored.text == "Скриншот ошибки"
    assert stored.content_type == "photo"


@pytest.mark.parametrize(
    "extra, content_type",
    [
        ({"photo": [{"file_id": "a", "file_unique_id": "b", "width": 1, "height": 1}]}, "photo"),
        ({"document": {"file_id": "a", "file_unique_id": "b"}}, "document"),
        ({"voice": {"file_id": "a", "file_unique_id": "b", "duration": 1}}, "voice"),
        ({"sticker": {"file_id": "a", "file_unique_id": "b", "type": "regular", "width": 1, "height": 1,
                      "is_animated": False, "is_video": False}}, "sticker"),
    ],
)
@pytest.mark.asyncio
async def test_media_without_caption_is_recorded_not_queued(harness, extra, content_type):
    await harness.send(**extra)

    stored = await _stored(harness)
    assert stored.text is None
    assert stored.content_type == content_type
    assert stored.pending_analysis is False


@pytest.mark.asyncio
async def test_service_events_are_not_stored(harness):
    await harness.send(new_chat_title="Новое название")
    assert await harness.query(select(MessageRow)) == []


@pytest.mark.asyncio
async def test_commands_are_not_stored_as_conversation(harness):
    await harness.send("/unknown_command привет")
    assert await harness.query(select(MessageRow)) == []


@pytest.mark.parametrize(
    "origin, expected",
    [
        ({"type": "user", "date": NOW, "sender_user": {"id": 5, "is_bot": False, "first_name": "Ольга"}}, "Ольга"),
        ({"type": "hidden_user", "date": NOW, "sender_user_name": "Скрытый"}, "Скрытый"),
        ({"type": "chat", "date": NOW, "sender_chat": {"id": -1, "type": "group", "title": "Группа"}}, "Группа"),
        ({"type": "channel", "date": NOW, "message_id": 1, "chat": {"id": -2, "type": "channel", "title": "Канал"}}, "Канал"),
    ],
)
@pytest.mark.asyncio
async def test_every_forward_origin_is_recorded(harness, origin, expected):
    await harness.send("Пересланное", forward_origin=origin)
    assert (await _stored(harness)).forward_from == expected


@pytest.mark.asyncio
async def test_anonymous_sender_is_stored(harness):
    fields = harness.message("Сообщение от имени группы")
    fields.pop("from_user")
    await harness.feed(message=Message(**fields))

    stored = await _stored(harness)
    assert stored.text == "Сообщение от имени группы"
    assert stored.user_id is None


@pytest.mark.asyncio
async def test_edit_of_a_known_message(harness):
    fields = harness.message("Релиз в пятницу")
    await harness.feed(message=Message(**fields))

    await harness.feed(edited_message=Message(**{**fields, "text": "Релиз в понедельник", "edit_date": EDIT_TIME}))

    stored = await _stored(harness)
    assert stored.text == "Релиз в понедельник"
    assert stored.edited_at.replace(tzinfo=pytz.utc) == NOW + timedelta(seconds=60)


@pytest.mark.asyncio
async def test_edit_of_an_unseen_message_is_stored(harness):
    await harness.feed(edited_message=Message(**harness.message("Правка", edit_date=EDIT_TIME)))
    assert (await _stored(harness)).text == "Правка"


@pytest.mark.asyncio
async def test_edit_into_a_command_is_ignored(harness):
    await harness.feed(edited_message=Message(**harness.message("/digest", edit_date=EDIT_TIME)))
    assert await harness.query(select(MessageRow)) == []


@pytest.mark.asyncio
async def test_reply_to_bot_question_closes_the_clarification(harness):
    task = await harness.add_task()
    async with harness.factory() as session:
        repo = ClarificationRepository(session)
        clarification = await repo.create(task.id, "Кто возьмёт?")
        await repo.set_question_message_id(clarification.id, 555)
        await session.commit()

    question = Message(message_id=555, date=NOW, chat=Chat(id=ALLOWED_CHAT, type="supergroup"), text="Кто возьмёт?")
    await harness.send("Возьму я", reply_to_message=question)

    stored = (await harness.query(select(Clarification)))[0]
    assert stored.answer_text == "Возьму я"


@pytest.mark.asyncio
async def test_transient_database_error_is_retried(harness, monkeypatch):
    async def no_wait(seconds):
        return None

    monkeypatch.setattr(handlers.asyncio, "sleep", no_wait)
    original = handlers.MessageIngestor.ingest
    attempts = {"count": 0}

    async def flaky(self, **kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise OperationalError("locked", {}, Exception("database is locked"))
        return await original(self, **kwargs)

    monkeypatch.setattr(handlers.MessageIngestor, "ingest", flaky)

    await harness.send("Важное сообщение")

    assert attempts["count"] == 3
    assert (await _stored(harness)).text == "Важное сообщение"


@pytest.mark.asyncio
async def test_persistent_database_error_does_not_crash_the_bot(harness, monkeypatch):
    async def no_wait(seconds):
        return None

    async def broken(self, **kwargs):
        raise OperationalError("locked", {}, Exception("database is locked"))

    monkeypatch.setattr(handlers.asyncio, "sleep", no_wait)
    monkeypatch.setattr(handlers.MessageIngestor, "ingest", broken)

    await harness.send("Сообщение")

    assert await harness.query(select(MessageRow)) == []


# --- Being added to and removed from groups ----------------------------------


def _membership(harness, status: str, chat_id=FOREIGN_CHAT) -> dict:
    bot = User(id=1, is_bot=True, first_name="Digest")
    actor = User(id=MEMBER, is_bot=False, first_name="Аня")
    old = "left" if status != "left" else "member"
    return {
        "chat": Chat(id=chat_id, type="supergroup", title="Новая группа"),
        "from": actor,
        "date": NOW,
        "old_chat_member": {"status": old, "user": bot},
        "new_chat_member": {"status": status, "user": bot, **({"until_date": 0} if status == "kicked" else {})},
    }


@pytest.mark.parametrize(
    "status, present",
    [("member", True), ("administrator", True), ("left", False), ("kicked", False)],
)
@pytest.mark.asyncio
async def test_membership_changes_are_noted_at_once(harness, status, present):
    from aiogram.types import ChatMemberUpdated

    from app import seen_chats

    new_member = _membership(harness, status)
    if status == "administrator":
        new_member["new_chat_member"].update(
            can_be_edited=False, is_anonymous=False, can_manage_chat=True, can_delete_messages=True,
            can_manage_video_chats=True, can_restrict_members=True, can_promote_members=False,
            can_change_info=True, can_invite_users=True, can_post_stories=False, can_edit_stories=False,
            can_delete_stories=False,
        )
    await harness.feed(my_chat_member=ChatMemberUpdated(**new_member))

    entry = seen_chats.load()[str(FOREIGN_CHAT)]
    assert entry["title"] == "Новая группа"
    assert entry["present"] is present
    assert harness.session.calls == []


def test_membership_updates_are_requested_from_telegram():
    assert "my_chat_member" in get_dispatcher().resolve_used_update_types()


# --- Foreign chats -----------------------------------------------------------


@pytest.mark.parametrize(
    "text", ["Секретная переписка", "/digest", "/tasks", "/settings", "/status", "/task Сделать"]
)
@pytest.mark.asyncio
async def test_foreign_chat_gets_nothing_and_leaves_no_trace(harness, text):
    await harness.send(text, chat_id=FOREIGN_CHAT)

    assert harness.session.calls == []
    assert (await harness.query(select(func.count()).select_from(MessageRow))) == [0]
    assert (await harness.query(select(func.count()).select_from(ChatRow))) == [0]


@pytest.mark.asyncio
async def test_foreign_button_press_is_only_acknowledged(harness):
    await harness.press("menu:tasks", chat_id=FOREIGN_CHAT)

    assert harness.replies == []
    assert len(harness.toasts) == 1


def test_forward_label_without_forward():
    assert handlers._forward_label(SimpleNamespace(forward_origin=None)) is None


# --- Language ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_bot_answers_in_english_by_default(harness):
    await harness.send("/tasks")
    assert harness.reply == "No active tasks."


@pytest.mark.parametrize("command", ["/language ru", "/language RU"])
@pytest.mark.asyncio
async def test_language_command_switches_the_chat(harness, command):
    await harness.admin(command)

    assert (await harness.chat()).language == "ru"
    assert harness.reply == "✅ Язык этого чата: Русский"

    await harness.send("/tasks")
    assert "Активных задач нет." in harness.reply


@pytest.mark.asyncio
async def test_language_stays_per_chat(harness, monkeypatch):
    other = -1009999
    monkeypatch.setattr(settings, "allowed_chat_ids", f"{ALLOWED_CHAT},{other}")
    await harness.admin("/language ru")

    await harness.send("/tasks", chat_id=other)

    assert harness.replies[-1] == "No active tasks."


@pytest.mark.asyncio
async def test_language_without_an_argument_offers_buttons(harness):
    await harness.admin("/language")

    sent = next(c for c in harness.session.calls if isinstance(c, SendMessage))
    assert sent.text.startswith("Language of this chat: English")
    assert [b.callback_data for b in sent.reply_markup.inline_keyboard[0]] == ["language:en", "language:ru"]


@pytest.mark.asyncio
async def test_unknown_language_is_explained(harness):
    await harness.admin("/language de")

    assert "Unknown language: de" in harness.reply
    assert (await harness.chat()).language is None


@pytest.mark.asyncio
async def test_language_is_for_admins(harness):
    await harness.send("/language ru")

    assert "for bot admins only" in harness.reply
    assert (await harness.chat()).language is None


@pytest.mark.asyncio
async def test_language_button_switches_the_chat(harness):
    await harness.press("language:ru", user_id=ADMIN_ID)

    assert (await harness.chat()).language == "ru"
    assert harness.toasts == ["✅ Язык этого чата: Русский"]


@pytest.mark.asyncio
async def test_language_button_refuses_members_and_forged_values(harness):
    await harness.press("language:ru")
    await harness.press("language:de", user_id=ADMIN_ID)

    assert (await harness.chat()).language is None
    assert harness.toasts[0] == "Admins only"


@pytest.mark.parametrize(
    "command, text",
    [
        ("/settings", "Настройки чата"),
        ("/start", "Привет"),
        ("/help", "что я умею"),
        ("/status", "Сообщений в очереди"),
        ("/digest потом", "Не понял период"),
        ("/task", "Укажите текст задачи"),
    ],
)
@pytest.mark.asyncio
async def test_russian_chat_gets_russian_replies(harness, command, text):
    await harness.admin("/language ru")
    await harness.send(command)
    assert text in harness.reply


@pytest.mark.asyncio
async def test_refusal_comes_in_the_chats_language(harness):
    await harness.admin("/language ru")
    await harness.send("/reanalyze 7")
    assert "только администраторам" in harness.reply


@pytest.mark.asyncio
async def test_settings_show_the_language(harness):
    await harness.send("/settings")
    assert "Language: English" in harness.reply


@pytest.mark.parametrize(
    "command, day, reply",
    [
        ("/set_weekly fri", 4, "Friday"),
        ("/set_weekly FRI", 4, "Friday"),
        ("/set_weekly пт", 4, "Friday"),
        ("/set_weekly mon", 0, "Monday"),
    ],
)
@pytest.mark.asyncio
async def test_weekdays_work_in_both_spellings(harness, command, day, reply):
    await harness.admin(command)

    assert (await harness.chat()).weekly_digest_day == day
    assert reply in harness.reply


@pytest.mark.asyncio
async def test_task_buttons_follow_the_chat_language(harness):
    await harness.admin("/language ru")
    await harness.add_task(title="Задача")

    await harness.send("/tasks")

    cards = [c for c in harness.session.calls if isinstance(c, SendMessage) and "Задача" in c.text]
    assert cards[-1].reply_markup.inline_keyboard[0][1].text == "✔️ Готово"
