"""English by default, Russian on request, and the two catalogs kept in step."""
from __future__ import annotations

import json
import re
import string
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import i18n
from app.admin_panel import FIELDS
from app.api.routes import chat_dashboard, dashboard
from app.bot.commands import COMMANDS, set_bot_commands
from app.config import settings
from app.db.repositories import ChatRepository, TaskRepository
from app.i18n import EN, RU, chat_language, normalize, t
from app.llm.mock_client import MockLLMClient
from app.llm.openai_client import (
    ANALYZE_SYSTEM_PROMPT,
    MERGE_SYSTEM_PROMPT,
    OpenAILLMClient,
    analyze_prompt,
    merge_prompt,
)
from app.llm.schemas import BatchMessage
from app.services.digest_service import DigestService
from app.services.sandbox import run_sandbox
from app.utils.dashboard_auth import make_chat_token
from app.utils.task_labels import format_clarification_question, status_labels
from tests.helpers import BASE_TIME, ScriptedLLM, analyze, feed, make_chat

STATIC = Path("app/panel_static")


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def _panel_catalogs() -> dict[str, dict[str, str]]:
    source = (STATIC / "i18n.js").read_text(encoding="utf-8")
    body = source[source.index("const I18N = {") : source.index("// Storage can be blocked")]
    blocks = re.split(r"^  (en|ru): \{$", body, flags=re.MULTILINE)
    entries = r'^    ("[^"]+"): ("(?:[^"\\]|\\.)*"),$'
    return {
        language: {
            json.loads(key): json.loads(value)
            for key, value in re.findall(entries, block, flags=re.MULTILINE)
        }
        for language, block in zip(blocks[1::2], blocks[2::2], strict=True)
    }


# --- Catalogs ----------------------------------------------------------------


def test_both_languages_have_the_same_messages():
    assert set(EN) == set(RU)


@pytest.mark.parametrize("key", sorted(EN))
def test_translations_fill_in_the_same_values(key):
    assert _placeholders(EN[key]) == _placeholders(RU[key])


def test_russian_catalog_is_actually_translated():
    same = {k for k in EN if EN[k] == RU[k]}
    # Names and addresses that read the same in both languages.
    assert same == {"group.telegram", "field.OPENAI_BASE_URL.help"}


def test_every_panel_field_has_a_label_in_every_language():
    for field in FIELDS:
        for language in i18n.LANGUAGES:
            assert t(language, f"field.{field.key}")
            assert t(language, f"group.{field.group_key}")


def test_panel_page_catalogs_match():
    catalogs = _panel_catalogs()
    assert len(catalogs["en"]) > 100
    assert set(catalogs["en"]) == set(catalogs["ru"])
    for key, text in catalogs["en"].items():
        assert _placeholders(text) == _placeholders(catalogs["ru"][key]), key


def test_panel_page_uses_only_known_keys():
    known = set(_panel_catalogs()["en"])
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    script = (STATIC / "panel.js").read_text(encoding="utf-8")
    used = set(re.findall(r'data-i18n(?:-placeholder|-label)?="([^"]+)"', page))
    used |= set(re.findall(r'\bt\("([^"]+)"', script))
    assert used and used <= known


def test_panel_markup_has_no_untranslated_russian():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"[А-Яа-яЁё]", page)
    assert not re.search(r"[А-Яа-яЁё]", (STATIC / "panel.js").read_text(encoding="utf-8"))


# --- Choosing the language ---------------------------------------------------


def test_english_is_the_default():
    assert type(settings).model_fields["default_language"].default == "en"
    assert normalize(None) == "en"


@pytest.mark.parametrize(
    "language, default, expected",
    [
        ("ru", "en", "ru"),
        ("en", "ru", "en"),
        (None, "ru", "ru"),
        ("", "ru", "ru"),
        ("de", "ru", "ru"),
        (None, "de", "en"),  # a typo in .env must not break every message
    ],
)
def test_language_resolution(monkeypatch, language, default, expected):
    monkeypatch.setattr(settings, "default_language", default)
    assert chat_language(SimpleNamespace(language=language)) == expected


def test_user_text_with_braces_is_not_treated_as_a_template():
    assert t("en", "bot.task_created", title="{id} {oops}", id=5) == "✅ Task created:\n*{id} {oops}*\nID: `5`"


def test_a_value_may_be_called_language():
    assert "Русский" in t("ru", "bot.language_set", language="Русский")


def test_status_labels_cover_every_status_in_both_languages():
    assert status_labels("en")["done"] == "✔️ Done"
    assert status_labels("ru")["done"] == "✔️ Выполнена"


def test_clarification_hint_follows_the_chat():
    assert "Reply to this message" in format_clarification_question("Q?", "en")
    assert "Ответьте на это сообщение" in format_clarification_question("Q?", "ru")


# --- Telegram's menu and profile ---------------------------------------------


@pytest.mark.asyncio
async def test_menu_and_profile_are_english_with_a_russian_variant():
    bot = SimpleNamespace(
        set_my_commands=AsyncMock(), set_my_description=AsyncMock(), set_my_short_description=AsyncMock()
    )
    await set_bot_commands(bot)

    calls = {c.kwargs["language_code"]: c.args[0] for c in bot.set_my_commands.call_args_list}
    assert set(calls) == {None, "ru"}
    assert {c.command for c in calls[None]} == set(COMMANDS)
    assert next(c for c in calls[None] if c.command == "tasks").description == "Show active tasks"
    assert next(c for c in calls["ru"] if c.command == "tasks").description == "Показать активные задачи"
    bot.set_my_description.assert_any_await(EN["bot.description"], language_code=None)
    bot.set_my_description.assert_any_await(RU["bot.description"], language_code="ru")


@pytest.mark.asyncio
async def test_a_refused_profile_update_does_not_stop_the_bot():
    from aiogram.exceptions import TelegramRetryAfter

    error = TelegramRetryAfter(method=None, message="flood", retry_after=5)
    bot = SimpleNamespace(
        set_my_commands=AsyncMock(), set_my_description=AsyncMock(side_effect=error), set_my_short_description=AsyncMock()
    )
    await set_bot_commands(bot)
    assert bot.set_my_commands.await_count == 2


@pytest.mark.parametrize("language", i18n.LANGUAGES)
def test_texts_fit_telegram_limits(language):
    for name in COMMANDS:
        assert 1 <= len(t(language, f"cmd.{name}")) <= 256
    assert len(t(language, "bot.description")) <= 512
    assert len(t(language, "bot.short_description")) <= 120


# --- What the model is told --------------------------------------------------


def test_russian_prompt_is_the_one_the_models_were_measured_with():
    prompt = analyze_prompt("ru")
    assert "ЯЗЫК: title, summary и text пиши по-русски" in prompt
    assert "{language_rule}" not in prompt
    assert merge_prompt("ru") == MERGE_SYSTEM_PROMPT


def test_english_output_differs_only_in_the_language_rule():
    prompt = analyze_prompt("en")
    assert "пиши по-английски" in prompt
    assert "по-русски" not in prompt
    assert "не транслитерируй" in prompt  # the analyzer checks owners against the source text
    before, after = ANALYZE_SYSTEM_PROMPT.split("{language_rule}")
    assert prompt.startswith(before) and prompt.endswith(after)
    assert "по-английски" in merge_prompt("en")


@pytest.mark.parametrize("language, phrase", [("en", "по-английски"), ("ru", "по-русски")])
@pytest.mark.asyncio
async def test_client_sends_the_chats_language(monkeypatch, language, phrase):
    client = OpenAILLMClient(api_key="k", base_url="http://localhost:11434/v1")
    sent = AsyncMock(return_value={"topics": []})
    monkeypatch.setattr(client, "_complete_json", sent)
    message = BatchMessage(id=1, author="A", sent_at="2026-01-01 10:00", text="x")

    await client.analyze_conversation([message], [], [], language=language)

    assert phrase in sent.call_args.args[0]


@pytest.mark.parametrize("language, title", [("en", "Work tasks"), ("ru", "Рабочие задачи")])
@pytest.mark.asyncio
async def test_mock_writes_in_the_chats_language(language, title):
    message = BatchMessage(id=1, author="A", sent_at="2026-01-01 10:00", text="Нужно обновить сервер")
    analysis = await MockLLMClient().analyze_conversation([message], [], [], language=language)
    assert analysis.topics[0].title == title


# --- The pipeline ------------------------------------------------------------

ANSWER = {
    "topics": [
        {
            "title": "Сертификаты",
            "summary": "Обсудили сертификаты.",
            "message_ids": [1],
            "items": [{"kind": "task", "text": "Обновить сертификаты", "source_ids": [1]}],
        }
    ]
}


async def _chat_with_a_topic(session, language: str | None):
    chat = await make_chat(session)
    await ChatRepository(session).set_language(chat, language)
    await session.commit()
    await feed(session, chat, [("Аня", "Надо обновить сертификаты")])
    llm = ScriptedLLM(ANSWER)
    results = await analyze(session, chat, llm)
    return chat, llm, results


async def _digest(session, chat, llm) -> str:
    end = BASE_TIME.replace(year=BASE_TIME.year + 1)
    return (await DigestService(session, llm).build_digest(chat, BASE_TIME.replace(year=2000), end)).text


@pytest.mark.asyncio
async def test_chat_without_a_choice_gets_an_english_digest_of_a_russian_chat(session):
    chat, llm, _ = await _chat_with_a_topic(session, None)

    text = await _digest(session, chat, llm)

    assert llm.calls[0]["language"] == "en"
    assert "Topic 1." in text and "Discussed:" in text and "Тема 1." not in text


@pytest.mark.asyncio
async def test_russian_chat_gets_a_russian_digest(session):
    chat, llm, _ = await _chat_with_a_topic(session, "ru")

    text = await _digest(session, chat, llm)

    assert llm.calls[0]["language"] == "ru"
    assert "<b>Дайджест — " in text and "Тема 1." in text and "Обсудили:" in text
    assert "Ответственные: Не определено" in text and "Open questions" not in text


@pytest.mark.asyncio
async def test_default_language_reaches_chats_without_a_choice(session, monkeypatch):
    monkeypatch.setattr(settings, "default_language", "ru")
    chat, llm, _ = await _chat_with_a_topic(session, None)
    assert "Тема 1." in await _digest(session, chat, llm)


@pytest.mark.asyncio
async def test_a_chats_choice_beats_the_default(session, monkeypatch):
    monkeypatch.setattr(settings, "default_language", "ru")
    chat, llm, _ = await _chat_with_a_topic(session, "en")
    assert "Topic 1." in await _digest(session, chat, llm)


@pytest.mark.parametrize(
    "language, question",
    [(None, 'Who will take the task "Обновить сертификаты"?'), ("ru", "Кто возьмёт задачу «Обновить сертификаты»?")],
)
@pytest.mark.asyncio
async def test_clarification_question_follows_the_chat(session, language, question):
    _, _, results = await _chat_with_a_topic(session, language)
    assert results[0].clarification.question == question


@pytest.mark.parametrize("language, label", [("en", "Discussed:"), ("ru", "Обсудили:")])
@pytest.mark.asyncio
async def test_sandbox_report_is_in_the_panels_language(language, label):
    result = await run_sandbox(ScriptedLLM(ANSWER), "Аня: Надо обновить сертификаты", language)
    assert label in result["digest"]


# --- The task site -----------------------------------------------------------


async def _site_chat(session, language: str | None):
    chat = await ChatRepository(session).get_or_create(-300, "Team")
    await ChatRepository(session).set_language(chat, language)
    await TaskRepository(session).create(chat_id=chat.id, title="Задача")
    await session.commit()
    return chat


async def _site_page(session, chat, lang: str = "") -> str:
    token = make_chat_token(chat.id)
    response = await chat_dashboard(chat.id, token=token, lang=lang, session=session)
    return response.body.decode("utf-8")


@pytest.fixture
def site_secret(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_secret", "s3cret")


@pytest.mark.asyncio
async def test_task_site_is_english_by_default(session, site_secret):
    page = await _site_page(session, await _site_chat(session, None))

    assert '<html lang="en">' in page and "Nothing found" in page and '"🆕 New"' in page
    assert "Ничего не найдено" not in page


@pytest.mark.asyncio
async def test_task_site_follows_the_chat_language(session, site_secret):
    page = await _site_page(session, await _site_chat(session, "ru"))
    assert '<html lang="ru">' in page and "Ничего не найдено" in page


@pytest.mark.asyncio
async def test_task_site_switch_overrides_the_chat_and_keeps_the_token(session, site_secret):
    chat = await _site_chat(session, None)

    page = await _site_page(session, chat, "ru")

    assert '<html lang="ru">' in page
    assert f'href="?token={make_chat_token(chat.id)}&amp;lang=en"' in page


@pytest.mark.asyncio
async def test_task_site_ignores_an_unknown_language(session, site_secret):
    page = await _site_page(session, await _site_chat(session, None), '"><script>')
    assert '<html lang="en">' in page and "<script>\"" not in page


@pytest.mark.parametrize("lang, heading", [("", "tasks by chat"), ("ru", "задачи по чатам")])
@pytest.mark.asyncio
async def test_overview_page_language(session, lang, heading):
    response = await dashboard(token="", lang=lang, session=session)
    assert heading in response.body.decode("utf-8")
