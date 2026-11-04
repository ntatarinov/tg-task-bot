from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.bot.middleware import AccessMiddleware, is_admin
from app.config import _parse_id_list, settings
from app.db.models import Clarification, Digest, Topic, TopicItem, TopicItemSource
from app.db.models import Message as MessageRow
from app.db.repositories import ClarificationRepository, TaskMessageRepository, TaskRepository
from app.llm.base import LLMUnavailableError
from app.llm.openai_client import OpenAILLMClient, is_local_endpoint, llm_block_reason
from app.services.retention import RetentionService
from app.utils.logging import redact
from tests.conftest import ADMIN_ID, ALLOWED_CHAT
from tests.helpers import BASE_TIME, ScriptedLLM, analyze, feed, make_chat


def _message(chat_id: int, user_id: int = 5, text: str = "привет") -> Message:
    return Message(
        message_id=1,
        date=BASE_TIME,
        chat=Chat(id=chat_id, type="supergroup"),
        from_user=User(id=user_id, is_bot=False, first_name="U"),
        text=text,
    )


async def _passes(event) -> bool:
    handler = AsyncMock(return_value="handled")
    result = await AccessMiddleware()(handler, event, {})
    return result == "handled"


@pytest.mark.asyncio
async def test_allowed_chat_passes():
    assert await _passes(_message(ALLOWED_CHAT))


@pytest.mark.asyncio
async def test_foreign_chat_is_dropped_before_any_handler():
    assert not await _passes(_message(-555))


@pytest.mark.asyncio
async def test_empty_allowlist_denies_everything(monkeypatch):
    monkeypatch.setattr(settings, "allowed_chat_ids", "")
    assert not await _passes(_message(ALLOWED_CHAT))


@pytest.mark.asyncio
async def test_admin_can_only_ask_for_the_id_in_a_foreign_chat():
    assert await _passes(_message(-555, ADMIN_ID, "/chatid"))
    assert await _passes(_message(-555, ADMIN_ID, "/chatid@digest_work_bot"))
    assert not await _passes(_message(-555, ADMIN_ID, "/digest"))
    assert not await _passes(_message(-555, ADMIN_ID, "обычное сообщение"))
    assert not await _passes(_message(-555, 5, "/chatid"))


@pytest.mark.asyncio
async def test_callback_from_foreign_chat_is_dropped():
    callback = CallbackQuery(
        id="1",
        from_user=User(id=5, is_bot=False, first_name="U"),
        chat_instance="x",
        message=_message(-555),
        data="task:1:done",
    )
    with_answer = callback.model_copy()
    object.__setattr__(with_answer, "answer", AsyncMock())
    assert not await _passes(with_answer)


def test_admin_check_and_id_parsing():
    assert is_admin(ADMIN_ID)
    assert not is_admin(5)
    assert not is_admin(None)
    assert _parse_id_list("-100123, 456;789") == {-100123, 456, 789}
    assert _parse_id_list("") == set()


@pytest.mark.parametrize(
    "command",
    ["cmd_set_timezone", "cmd_set_digest_time", "cmd_set_weekly", "cmd_set_digest_chat",
     "cmd_digest_on", "cmd_digest_off", "cmd_reanalyze", "cmd_purge_history"],
)
@pytest.mark.asyncio
async def test_settings_commands_refuse_ordinary_members(command):
    from app.bot import handlers

    message = SimpleNamespace(
        chat=SimpleNamespace(id=ALLOWED_CHAT, title="Team"),
        from_user=SimpleNamespace(id=5),
        text="/x 1 confirm",
        answer=AsyncMock(),
    )
    await getattr(handlers, command)(message)

    assert "только администраторам" in message.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_task_button_cannot_reach_another_chats_task(monkeypatch, session):
    """Regression: the button handler trusted the task id from the client."""
    from contextlib import asynccontextmanager

    from app.bot import handlers

    other = await make_chat(session, -777, "Other")
    task = await TaskRepository(session).create(other.id, "Чужая задача")
    await make_chat(session, ALLOWED_CHAT, "Team")
    await session.commit()

    @asynccontextmanager
    async def shared_session():
        yield session

    monkeypatch.setattr(handlers, "async_session_factory", shared_session)
    message = _message(ALLOWED_CHAT)
    callback = SimpleNamespace(data=f"task:{task.id}:done", message=message, answer=AsyncMock())

    await handlers.on_task_action(callback)

    await session.refresh(task)
    assert task.status.value == "new"
    callback.answer.assert_called_once_with("Задача не найдена")


# --- Sending data to third parties -------------------------------------------


@pytest.mark.parametrize(
    "url, local",
    [
        ("http://localhost:11434/v1", True),
        ("http://127.0.0.1:11434/v1", True),
        ("http://192.168.1.10:11434/v1", True),
        ("http://host.docker.internal:11434/v1", True),
        ("https://api.groq.com/openai/v1", False),
        ("", False),
    ],
)
def test_endpoint_locality(url, local):
    assert is_local_endpoint(url) is local


def test_external_llm_is_blocked_without_consent(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_base_url", "https://api.groq.com/openai/v1")
    monkeypatch.setattr(settings, "openai_api_key", "key")
    assert "LLM_EXTERNAL_ALLOWED" in llm_block_reason()

    monkeypatch.setattr(settings, "llm_external_allowed", True)
    assert llm_block_reason() is None


def test_local_llm_needs_no_consent(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_base_url", "http://localhost:11434/v1")
    assert llm_block_reason() is None


@pytest.mark.asyncio
async def test_no_chat_content_leaves_without_consent(monkeypatch):
    """Checked at the network call itself, so no caller can forget it."""
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "key")
    monkeypatch.setattr(settings, "openai_base_url", "https://api.groq.com/openai/v1")
    client = OpenAILLMClient(api_key="key", model="m", base_url=settings.openai_base_url)
    network_call = AsyncMock()
    monkeypatch.setattr(client.client.chat.completions, "create", network_call)

    with pytest.raises(LLMUnavailableError):
        await client.analyze_conversation([], [], [])
    with pytest.raises(LLMUnavailableError):
        await client.merge_summaries({1: ["a", "b"]})

    network_call.assert_not_called()


def _recording_client(monkeypatch):
    """A local-endpoint client whose network call records its arguments."""
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_base_url", "http://localhost:11434/v1")
    client = OpenAILLMClient(api_key="ollama", model="m", base_url=settings.openai_base_url)
    seen: dict = {}

    async def create(**kwargs):
        seen.update(kwargs)
        message = SimpleNamespace(content='{"topics": []}')
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=message)])

    monkeypatch.setattr(client.client.chat.completions, "create", create)
    return client, seen


@pytest.mark.asyncio
async def test_reasoning_effort_is_sent_only_when_configured(monkeypatch):
    client, seen = _recording_client(monkeypatch)

    await client.analyze_conversation([], [], [])
    assert "extra_body" not in seen

    monkeypatch.setattr(settings, "llm_reasoning_effort", "none")
    await client.analyze_conversation([], [], [])
    assert seen["extra_body"] == {"reasoning_effort": "none"}


@pytest.mark.asyncio
async def test_output_token_limit_is_configurable(monkeypatch):
    client, seen = _recording_client(monkeypatch)
    monkeypatch.setattr(settings, "llm_max_output_tokens", 6000)

    await client.analyze_conversation([], [], [])

    assert seen["max_tokens"] == 6000


@pytest.mark.asyncio
async def test_analysis_tick_does_nothing_while_blocked(monkeypatch):
    from app.services import scheduler

    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "key")
    monkeypatch.setattr(settings, "openai_base_url", "https://api.groq.com/openai/v1")
    factory = AsyncMock()
    monkeypatch.setattr(scheduler, "async_session_factory", factory)

    await scheduler.analysis_tick()

    factory.assert_not_called()


# --- Logging -----------------------------------------------------------------


def test_secrets_are_redacted():
    token = "1234567890:AAH" + "x" * 32
    key = "gsk_" + "a" * 40
    assert token not in redact(f"failed for {token}")
    assert key not in redact(f"bad key {key}")
    assert redact("ordinary text 12:30") == "ordinary text 12:30"


def test_database_errors_do_not_carry_message_text():
    from app.db.session import engine

    assert engine.sync_engine.hide_parameters is True


# --- Webhook and dashboard ---------------------------------------------------


def _webhook_client(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "_bot", object())
    monkeypatch.setattr(main, "_dp", SimpleNamespace(feed_update=AsyncMock()))
    return TestClient(main.app), main


def test_webhook_rejects_wrong_secret(monkeypatch):
    client, main = _webhook_client(monkeypatch)
    monkeypatch.setattr(settings, "webhook_secret", "s3cret")

    forged = client.post("/telegram/webhook", json={"update_id": 1})
    genuine = client.post(
        "/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )

    assert forged.status_code == 403
    assert genuine.status_code == 200
    main._dp.feed_update.assert_called_once()


def test_webhook_without_secret_is_closed_outside_local(monkeypatch):
    client, _ = _webhook_client(monkeypatch)
    monkeypatch.setattr(settings, "app_env", "production")

    assert client.post("/telegram/webhook", json={"update_id": 1}).status_code == 403


def test_webhook_failure_asks_telegram_to_redeliver(monkeypatch):
    client, main = _webhook_client(monkeypatch)
    main._dp.feed_update.side_effect = RuntimeError("db down")

    assert client.post("/telegram/webhook", json={"update_id": 1}).status_code == 500


def test_dashboard_without_secret_is_closed_outside_local(monkeypatch):
    from fastapi import HTTPException

    from app.api.routes import _require_chat_token, _require_master_token

    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(HTTPException):
        _require_master_token("")
    with pytest.raises(HTTPException):
        _require_chat_token(1, "")


# --- Retention ---------------------------------------------------------------


async def _chat_with_history(session):
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", "Старое решение: решили взять Redis.")], start=BASE_TIME - timedelta(days=100))
    await feed(session, chat, [("Аня", "Новое сообщение")], start_id=2)
    await analyze(session, chat, ScriptedLLM(lambda messages, topics: {"topics": [{
        "title": "Кэш", "summary": "Итог.", "message_ids": [m.id for m in messages],
        "items": [{"kind": "decision", "text": "Redis", "source_ids": [1], "quote": "решили взять Redis"}],
    }]}))
    return chat


@pytest.mark.asyncio
async def test_retention_is_off_by_default(session):
    await _chat_with_history(session)
    await RetentionService(session).apply_policy()

    assert (await session.execute(select(func.count()).select_from(MessageRow))).scalar() == 2


@pytest.mark.asyncio
async def test_retention_deletes_old_messages_and_keeps_conclusions(session, monkeypatch):
    await _chat_with_history(session)
    monkeypatch.setattr(settings, "message_retention_days", 30)

    await RetentionService(session).apply_policy()

    texts = (await session.execute(select(MessageRow.text))).scalars().all()
    assert texts == ["Новое сообщение"]
    assert (await session.execute(select(func.count()).select_from(TopicItem))).scalar() == 1
    # The reference to the original Telegram message outlives the stored copy.
    assert (await session.execute(select(TopicItemSource.telegram_message_id))).scalars().all() == [1]


@pytest.mark.asyncio
async def test_retention_never_deletes_unanalyzed_messages(session, monkeypatch):
    chat = await make_chat(session)
    await feed(session, chat, [("Аня", "Ещё не разобрано")], start=BASE_TIME - timedelta(days=100))
    monkeypatch.setattr(settings, "message_retention_days", 30)

    await RetentionService(session).apply_policy()

    assert (await session.execute(select(func.count()).select_from(MessageRow))).scalar() == 1


@pytest.mark.asyncio
async def test_retention_handles_linked_rows(session, monkeypatch):
    chat = await make_chat(session)
    messages = await feed(session, chat, [("Аня", "Ответ")], start=BASE_TIME - timedelta(days=100))
    messages[0].pending_analysis = False
    task = await TaskRepository(session).create(chat.id, "Задача")
    await TaskMessageRepository(session).link(task.id, messages[0].id)
    clarifications = ClarificationRepository(session)
    clarification = await clarifications.create(task.id, "Кто?")
    await clarifications.answer(clarification.id, "Я", messages[0].id)
    await session.commit()

    monkeypatch.setattr(settings, "message_retention_days", 30)
    await RetentionService(session).apply_policy()

    stored = (await session.execute(
        select(Clarification.answer_message_id, Clarification.answer_text)
    )).one()
    assert stored == (None, "Я")


@pytest.mark.asyncio
async def test_raw_updates_expire_separately(session, monkeypatch):
    chat = await make_chat(session)
    messages = await feed(session, chat, [("Аня", "Текст")], start=BASE_TIME - timedelta(days=100))
    messages[0].raw_update_json = {"from": {"first_name": "Аня"}}
    await session.commit()
    monkeypatch.setattr(settings, "raw_update_retention_days", 7)

    await RetentionService(session).apply_policy()
    await session.refresh(messages[0])

    assert messages[0].raw_update_json is None
    assert messages[0].text == "Текст"


@pytest.mark.asyncio
async def test_purge_removes_everything_derived_but_keeps_tasks(session):
    from app.db.repositories import DigestRepository

    chat = await _chat_with_history(session)
    await TaskRepository(session).create(chat.id, "Задача остаётся")
    await DigestRepository(session).create(chat.id, BASE_TIME, BASE_TIME, "текст")
    await session.commit()

    await RetentionService(session).purge_chat(chat.id)
    await session.commit()

    for model in (MessageRow, Topic, TopicItem, TopicItemSource, Digest):
        assert (await session.execute(select(func.count()).select_from(model))).scalar() == 0
    assert len(await TaskRepository(session).get_active(chat.id)) == 1
