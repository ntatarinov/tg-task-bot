from __future__ import annotations

import asyncio
import io
import re
import sqlite3
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import admin_panel as panel
from app import polling, seen_chats
from app.config import Settings, reload_settings, settings
from app.db.models import Message as MessageRow
from app.db.repositories import ChatRepository, MessageRepository
from app.db.session import get_session
from app.env_file import read_env, update_env
from tests.conftest import ALLOWED_CHAT
from tests.helpers import feed

PASSWORD = "correct horse"
ENV_TEXT = """# Telegram
BOT_TOKEN=old-token
ALLOWED_CHAT_IDS=-100

# Model settings
OPENAI_MODEL=old-model
LLM_EXTERNAL_ALLOWED=false
UNRELATED_SETTING=keep me
"""


@pytest.fixture
def env_path(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(ENV_TEXT, encoding="utf-8")
    monkeypatch.setattr(panel, "ENV_PATH", path)
    monkeypatch.setattr(panel, "reload_settings", lambda: None)
    for field in panel.FIELDS:
        monkeypatch.delenv(field.key, raising=False)
    return path


@pytest.fixture
def client(env_path, engine, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "admin_panel_password", PASSWORD)
    monkeypatch.setattr(settings, "restart_flag_path", str(tmp_path / "restart.flag"))
    panel.throttle.reset()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def session_override():
        async with factory() as session:
            yield session
            await session.commit()

    panel.app.dependency_overrides[get_session] = session_override
    test_client = TestClient(panel.app, base_url="http://panel.test")
    test_client.factory = factory
    yield test_client
    panel.app.dependency_overrides.clear()


@pytest.fixture
def logged_in(client):
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    return client


# --- Logging in --------------------------------------------------------------


def test_panel_will_not_start_without_a_password(monkeypatch):
    monkeypatch.setattr(settings, "admin_panel_password", "")
    with pytest.raises(SystemExit, match="ADMIN_PANEL_PASSWORD"):
        panel.main()


def test_default_address_is_loopback_only():
    assert Settings.model_fields["panel_host"].default == "127.0.0.1"


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/api/settings"), ("post", "/api/settings"), ("get", "/api/overview"), ("post", "/api/check"),
        ("get", "/api/models"), ("get", "/api/chats"), ("patch", "/api/chats/1"), ("post", "/api/restart"),
        ("get", "/api/stacks"), ("post", "/api/stacks"), ("delete", "/api/stacks/x"),
        ("post", "/api/stacks/x/apply"), ("post", "/api/sandbox"), ("post", "/api/models/pull"),
        ("get", "/api/models/pull"), ("post", "/api/chats/access"), ("post", "/api/chats/1/purge"),
        ("get", "/api/migration"), ("post", "/api/export"),
    ],
)
def test_every_endpoint_requires_a_session(client, method, path):
    kwargs = {"json": {}} if method in ("post", "patch") else {}
    assert getattr(client, method)(path, **kwargs).status_code == 401


def test_login_and_logout(client):
    assert client.get("/api/session").json() == {"authenticated": False}
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401

    response = client.post("/api/login", json={"password": PASSWORD})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/session").json() == {"authenticated": True}

    client.post("/api/logout")
    assert client.get("/api/session").json() == {"authenticated": False}


def test_empty_password_never_logs_in(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_panel_password", "")
    assert not panel.session_is_valid(panel.make_session_cookie())


def test_repeated_failures_lock_the_login(client):
    for _ in range(panel.MAX_FAILED_LOGINS):
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401

    locked = client.post("/api/login", json={"password": PASSWORD})

    assert locked.status_code == 429
    assert "Подождите" in locked.json()["detail"]


@pytest.mark.parametrize(
    "cookie", ["", "garbage", "abc.def", "9999999999.forged", f"{int(time.time()) + 999}.0000"]
)
def test_forged_sessions_are_rejected(client, cookie):
    assert panel.session_is_valid(cookie) is False


def test_session_expires(client):
    cookie = panel.make_session_cookie(now=time.time() - panel.SESSION_SECONDS - 10)
    assert panel.session_is_valid(cookie) is False


def test_changing_the_password_ends_sessions(client, monkeypatch):
    cookie = panel.make_session_cookie()
    monkeypatch.setattr(settings, "admin_panel_password", "new password")
    assert panel.session_is_valid(cookie) is False


def test_writes_from_another_site_are_refused(logged_in):
    response = logged_in.post("/api/restart", headers={"origin": "https://evil.example"})
    assert response.status_code == 403
    assert logged_in.post("/api/restart", headers={"origin": "http://panel.test"}).status_code == 200


def test_page_is_served_with_strict_headers(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/static/panel.js").status_code == 200
    assert client.get("/static/panel.css").status_code == 200


def test_api_docs_are_not_exposed(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_page_loads_nothing_from_other_sites():
    page = (panel.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"""(?:src|href)=["']?(?:https?:)?//""", page)
    assert "url(" not in (panel.STATIC_DIR / "panel.css").read_text(encoding="utf-8")
    assert "fetch(path" in (panel.STATIC_DIR / "panel.js").read_text(encoding="utf-8")


def test_page_never_builds_html_from_strings():
    assert "innerHTML" not in (panel.STATIC_DIR / "panel.js").read_text(encoding="utf-8")


# --- Reading and saving settings ---------------------------------------------


def _fields(client) -> dict:
    return {f["key"]: f for f in client.get("/api/settings").json()["fields"]}


def _save(client, confirm=False, **values):
    return client.post("/api/settings", json={"values": values, "confirm_external": confirm})


def test_secrets_never_reach_the_browser(logged_in, env_path):
    response = logged_in.get("/api/settings")

    assert "old-token" not in response.text
    fields = _fields(logged_in)
    assert fields["BOT_TOKEN"]["is_set"] is True and "value" not in fields["BOT_TOKEN"]
    assert fields["OPENAI_API_KEY"]["is_set"] is False


def test_ordinary_values_are_shown(logged_in):
    assert _fields(logged_in)["OPENAI_MODEL"]["value"] == "old-model"


def test_saving_keeps_comments_order_and_other_settings(logged_in, env_path):
    response = _save(logged_in, OPENAI_MODEL="gemma4:26b")

    assert response.json() == {"changed": ["OPENAI_MODEL"], "restart_needed": True}
    text = env_path.read_text(encoding="utf-8")
    assert "# Telegram" in text and "# Model settings" in text
    assert "UNRELATED_SETTING=keep me" in text
    assert text.index("BOT_TOKEN") < text.index("OPENAI_MODEL=gemma4:26b")
    assert read_env(env_path)["BOT_TOKEN"] == "old-token"


def test_saving_makes_a_backup(logged_in, env_path):
    _save(logged_in, OPENAI_MODEL="new")
    assert "OPENAI_MODEL=old-model" in env_path.with_name(".env.bak").read_text(encoding="utf-8")


def test_blank_secret_keeps_the_stored_one(logged_in, env_path):
    assert _save(logged_in, BOT_TOKEN="").json()["changed"] == []
    assert read_env(env_path)["BOT_TOKEN"] == "old-token"


def test_new_secret_replaces_the_old_one(logged_in, env_path):
    _save(logged_in, BOT_TOKEN="new-token")
    assert read_env(env_path)["BOT_TOKEN"] == "new-token"


def test_unchanged_values_cause_no_write_and_no_restart(logged_in, env_path):
    before = env_path.stat().st_mtime_ns
    response = _save(logged_in, OPENAI_MODEL="old-model")

    assert response.json() == {"changed": [], "restart_needed": False}
    assert env_path.stat().st_mtime_ns == before


def test_new_keys_are_appended(logged_in, env_path):
    _save(logged_in, ADMIN_USER_IDS="123,456")
    assert read_env(env_path)["ADMIN_USER_IDS"] == "123,456"


def test_settings_outside_the_list_cannot_be_written(logged_in, env_path):
    for key in ("ADMIN_PANEL_PASSWORD", "RESTART_FLAG_PATH", "STACKS_PATH", "PATH"):
        response = _save(logged_in, **{key: "x"})
        assert response.status_code == 422
        assert key in response.json()["detail"]["errors"]
    assert env_path.read_text(encoding="utf-8") == ENV_TEXT


def test_a_value_cannot_smuggle_in_another_setting(logged_in, env_path):
    response = _save(logged_in, OPENAI_MODEL="x\nADMIN_PANEL_PASSWORD=hacked")

    assert response.status_code == 422
    assert "hacked" not in env_path.read_text(encoding="utf-8")


def test_one_bad_value_saves_nothing(logged_in, env_path):
    response = _save(logged_in, OPENAI_MODEL="new", LLM_TIMEOUT_SECONDS="soon")

    assert response.status_code == 422
    assert read_env(env_path)["OPENAI_MODEL"] == "old-model"


@pytest.mark.parametrize(
    "key, value",
    [
        ("LLM_TIMEOUT_SECONDS", "soon"), ("LLM_TIMEOUT_SECONDS", "1"), ("LLM_TIMEOUT_SECONDS", "-5"),
        ("LLM_TIMEOUT_SECONDS", ""), ("ANALYSIS_BATCH_MAX_MESSAGES", "0"), ("ALLOWED_CHAT_IDS", "abc"),
        ("ALLOWED_CHAT_IDS", "1,,2"), ("ADMIN_USER_IDS", "1 2"), ("OPENAI_BASE_URL", "ftp://x"),
        ("OPENAI_BASE_URL", "localhost"), ("DEFAULT_DIGEST_TIME", "25:00"), ("DEFAULT_TIMEZONE", "Mars/Phobos"),
        ("LLM_PROVIDER", "other"), ("LLM_REASONING_EFFORT", "maximum"), ("CLARIFICATIONS_ENABLED", "yes"),
    ],
)
def test_bad_values_are_refused_with_a_reason(logged_in, key, value):
    response = _save(logged_in, **{key: value})

    assert response.status_code == 422
    assert response.json()["detail"]["errors"][key]


@pytest.mark.parametrize(
    "key, value",
    [
        ("LLM_TIMEOUT_SECONDS", "900"), ("ALLOWED_CHAT_IDS", "-1001, -1002"), ("ALLOWED_CHAT_IDS", ""),
        ("OPENAI_BASE_URL", "http://localhost:11434/v1"), ("OPENAI_BASE_URL", ""),
        ("DEFAULT_DIGEST_TIME", "23:00"), ("DEFAULT_TIMEZONE", "Europe/Moscow"), ("LLM_PROVIDER", "mock"),
        ("LLM_REASONING_EFFORT", "none"), ("LLM_REASONING_EFFORT", ""), ("CLARIFICATIONS_ENABLED", "false"),
        ("MESSAGE_RETENTION_DAYS", "0"),
    ],
)
def test_good_values_are_accepted(logged_in, env_path, key, value):
    assert _save(logged_in, **{key: value}).status_code == 200
    assert read_env(env_path).get(key, "") == value


def test_every_field_has_a_valid_definition():
    known = set(Settings.model_fields)
    for field in panel.FIELDS:
        assert field.key.lower() in known
        assert field.kind in ("text", "secret", "bool", "int", "ids", "url", "time", "timezone", "select")


def test_external_service_needs_explicit_consent(logged_in, env_path):
    refused = _save(logged_in, LLM_EXTERNAL_ALLOWED="true")
    assert refused.status_code == 422
    assert read_env(env_path)["LLM_EXTERNAL_ALLOWED"] == "false"

    assert _save(logged_in, confirm=True, LLM_EXTERNAL_ALLOWED="true").status_code == 200
    assert read_env(env_path)["LLM_EXTERNAL_ALLOWED"] == "true"


def test_turning_the_external_service_off_needs_no_consent(logged_in, env_path):
    _save(logged_in, confirm=True, LLM_EXTERNAL_ALLOWED="true")
    assert _save(logged_in, LLM_EXTERNAL_ALLOWED="false").status_code == 200


def test_values_fixed_by_the_server_are_read_only(logged_in, env_path, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://ollama:11434/v1")

    field = _fields(logged_in)["OPENAI_BASE_URL"]
    assert field["locked"] is True and field["value"] == "http://ollama:11434/v1"
    assert _save(logged_in, OPENAI_BASE_URL="https://evil.example/v1").status_code == 422


def test_secret_fixed_by_the_server_is_not_shown_either(logged_in, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "server-key")
    response = logged_in.get("/api/settings")

    assert "server-key" not in response.text


def test_saved_values_reach_the_running_panel(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("OPENAI_MODEL=fresh-model\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.chdir(tmp_path)
    original = settings.openai_model
    try:
        reload_settings()
        assert settings.openai_model == "fresh-model"
    finally:
        settings.openai_model = original


# --- The .env file itself ----------------------------------------------------


@pytest.mark.parametrize(
    "value", ["simple", "with space", "has#hash", 'has"quote', "back\\slash", "$dollar", "кириллица", ""]
)
def test_values_survive_a_round_trip(tmp_path, value):
    path = tmp_path / ".env"
    update_env(path, {"KEY": value})
    assert read_env(path)["KEY"] == value


def test_update_refuses_multiline_values(tmp_path):
    with pytest.raises(ValueError):
        update_env(tmp_path / ".env", {"KEY": "a\nb"})


def test_duplicate_keys_are_all_updated(tmp_path):
    path = tmp_path / ".env"
    path.write_text("KEY=one\nKEY=two\n", encoding="utf-8")
    update_env(path, {"KEY": "three"})
    assert path.read_text(encoding="utf-8") == "KEY=three\nKEY=three\n"


def test_commented_out_lines_are_left_alone(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# KEY=example\n", encoding="utf-8")
    update_env(path, {"KEY": "real"})
    assert path.read_text(encoding="utf-8") == "# KEY=example\nKEY=real\n"


def test_missing_file_reads_as_empty(tmp_path):
    assert read_env(tmp_path / "absent") == {}


# --- Overview, checks, models ------------------------------------------------


@pytest.mark.parametrize(
    "provider, base_url, consent, location, blocked",
    [
        ("mock", "", False, "mock", False),
        ("openai", "http://localhost:11434/v1", False, "local", False),
        ("openai", "https://api.groq.com/openai/v1", True, "external", False),
        ("openai", "https://api.groq.com/openai/v1", False, "external", True),
    ],
)
def test_overview_for_every_model_setup(logged_in, monkeypatch, provider, base_url, consent, location, blocked):
    monkeypatch.setattr(settings, "llm_provider", provider)
    monkeypatch.setattr(settings, "openai_base_url", base_url)
    monkeypatch.setattr(settings, "openai_api_key", "key")
    monkeypatch.setattr(settings, "llm_external_allowed", consent)

    data = logged_in.get("/api/overview").json()

    assert data["model_location"] == location
    assert bool(data["analysis_blocked"]) is blocked


def test_overview_counts_the_queue(logged_in):
    async def seed():
        async with logged_in.factory() as session:
            chat = await ChatRepository(session).get_or_create(ALLOWED_CHAT, "Team")
            await session.commit()
            await feed(session, chat, [("Аня", "один"), ("Аня", "два")])

    asyncio.run(seed())
    assert logged_in.get("/api/overview").json()["pending"] == 2


def test_checks_are_reported_for_the_page(logged_in, monkeypatch):
    monkeypatch.setattr(panel.doctor, "check_telegram", AsyncMock())
    monkeypatch.setattr(panel.doctor, "check_database", AsyncMock())
    monkeypatch.setattr(settings, "allowed_chat_ids", "")

    data = logged_in.post("/api/check").json()

    assert data["failed"] >= 1
    assert any(i["state"] == "fail" and "ALLOWED_CHAT_IDS" in i["text"] for i in data["items"])
    assert any(i["state"] == "warn" for i in data["items"])  # the mock provider


def test_check_results_contain_no_token(logged_in, monkeypatch):
    monkeypatch.setattr(settings, "bot_token", "123456:SECRETSECRETSECRETSECRETSECRET12345")

    class Client(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": False, "error_code": 401})))

    monkeypatch.setattr(panel.doctor.httpx, "AsyncClient", Client)

    assert "SECRETSECRET" not in logged_in.post("/api/check").text


@pytest.mark.parametrize(
    "reply, expected",
    [
        ({"models": [{"name": "gemma4:26b"}, {"name": "gemma4:12b"}]}, ["gemma4:12b", "gemma4:26b"]),
        ({"unexpected": True}, []),
    ],
)
def test_installed_models(logged_in, monkeypatch, reply, expected):
    class Client(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=reply)))

    monkeypatch.setattr(panel.httpx, "AsyncClient", Client)

    assert logged_in.get("/api/models").json()["models"] == expected


def test_models_when_ollama_is_down(logged_in, monkeypatch):
    monkeypatch.setattr(settings, "local_llm_url", "http://127.0.0.1:9/v1")
    assert logged_in.get("/api/models").json() == {"models": []}


# --- Chats -------------------------------------------------------------------


def _chat(client, telegram_chat_id=ALLOWED_CHAT):
    async def seed():
        async with client.factory() as session:
            chat = await ChatRepository(session).get_or_create(telegram_chat_id, "<b>Team</b>")
            await session.commit()
            return chat.id

    return asyncio.run(seed())


def test_chats_are_listed(logged_in):
    _chat(logged_in)
    _chat(logged_in, -999)

    chats = {c["telegram_chat_id"]: c for c in logged_in.get("/api/chats").json()["chats"]}
    assert isinstance(logged_in.get("/api/chats").json()["waiting"], list)

    assert chats[ALLOWED_CHAT]["allowed"] is True
    assert chats[-999]["allowed"] is False
    assert chats[ALLOWED_CHAT]["last_digest_at"] is None


@pytest.mark.parametrize(
    "body, column, expected",
    [
        ({"timezone": "Europe/Moscow"}, "timezone", "Europe/Moscow"),
        ({"digest_time": "23:00"}, "digest_time", "23:00"),
        ({"digest_enabled": False}, "digest_enabled", False),
        ({"weekly_digest_enabled": False}, "weekly_digest_enabled", False),
        ({"weekly_digest_day": 0}, "weekly_digest_day", 0),
        ({"digest_chat_id": ALLOWED_CHAT}, "digest_chat_id", ALLOWED_CHAT),
    ],
)
def test_chat_settings_change(logged_in, body, column, expected):
    chat_id = _chat(logged_in)
    assert logged_in.patch(f"/api/chats/{chat_id}", json=body).json()[column] == expected
    stored = next(c for c in logged_in.get("/api/chats").json()["chats"] if c["id"] == chat_id)
    assert stored[column] == expected


def test_digest_target_can_be_cleared(logged_in):
    chat_id = _chat(logged_in)
    logged_in.patch(f"/api/chats/{chat_id}", json={"digest_chat_id": ALLOWED_CHAT})
    assert logged_in.patch(f"/api/chats/{chat_id}", json={"clear_digest_chat": True}).json()["digest_chat_id"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"timezone": "Mars/Phobos"}, {"digest_time": "25:00"}, {"weekly_digest_day": 7},
        {"weekly_digest_day": -1}, {"digest_chat_id": -999},
    ],
)
def test_bad_chat_settings_are_refused(logged_in, body):
    assert logged_in.patch(f"/api/chats/{_chat(logged_in)}", json=body).status_code == 422


def test_missing_chat(logged_in):
    assert logged_in.patch("/api/chats/999", json={}).status_code == 404


# --- One chat for testing, another for real ----------------------------------


def _access(client, telegram_chat_id, allowed):
    return client.post("/api/chats/access", json={"telegram_chat_id": telegram_chat_id, "allowed": allowed})


def test_foreign_group_appears_waiting_with_its_title(logged_in):
    seen_chats.remember(-555, "Тестовая группа", "supergroup")

    data = logged_in.get("/api/chats").json()

    assert data["waiting"] == [{"telegram_chat_id": -555, "title": "Тестовая группа"}]


@pytest.mark.parametrize("chat_type", ["private", "channel"])
def test_only_groups_are_noted(logged_in, chat_type):
    seen_chats.remember(777, "Кто-то", chat_type)
    assert logged_in.get("/api/chats").json()["waiting"] == []


def test_a_title_change_updates_the_note():
    seen_chats.remember(-555, "Старое", "group")
    seen_chats.remember(-555, "Новое", "group")
    assert seen_chats.load()["-555"]["title"] == "Новое"


def test_broken_notes_file_reads_as_empty(tmp_path, monkeypatch):
    path = tmp_path / "broken.json"
    path.write_text("{oops", encoding="utf-8")
    monkeypatch.setattr(settings, "seen_chats_path", str(path))
    assert seen_chats.load() == {}


def test_notes_failure_does_not_stop_the_bot(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "seen_chats_path", str(tmp_path / "no" / "such" / "dir.json"))
    seen_chats.remember(-555, "Группа", "group")


@pytest.mark.asyncio
async def test_denied_message_notes_the_group_but_not_the_text():
    from datetime import datetime

    import pytz
    from aiogram.types import Chat as TelegramChat
    from aiogram.types import Message as TelegramMessage

    from app.bot.middleware import AccessMiddleware

    event = TelegramMessage(
        message_id=1, date=datetime(2026, 11, 4, tzinfo=pytz.utc),
        chat=TelegramChat(id=-555, type="supergroup", title="Тест"), text="Секретный текст",
    )
    await AccessMiddleware()(AsyncMock(), event, {})

    stored = Path(settings.seen_chats_path).read_text(encoding="utf-8")
    assert "Тест" in stored
    assert "Секретный" not in stored


def test_moving_from_the_test_chat_to_the_real_one(logged_in, env_path):
    """The whole flow: allow the test chat, then swap it for the working chat."""
    assert _access(logged_in, -111, True).json() == {"restart_needed": True}
    assert read_env(env_path)["ALLOWED_CHAT_IDS"] == "-111,-100"

    _access(logged_in, -222, True)
    _access(logged_in, -111, False)

    assert read_env(env_path)["ALLOWED_CHAT_IDS"] == "-222,-100"


def test_allowing_twice_changes_nothing(logged_in, env_path):
    _access(logged_in, -111, True)
    _access(logged_in, -111, True)
    assert read_env(env_path)["ALLOWED_CHAT_IDS"].count("-111") == 1


def test_access_list_fixed_by_the_server_cannot_be_changed(logged_in, monkeypatch):
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "-1")
    assert _access(logged_in, -111, True).status_code == 422


def test_allowed_chat_without_messages_is_still_listed(logged_in, monkeypatch):
    monkeypatch.setattr(settings, "allowed_chat_ids", "-333")
    assert logged_in.get("/api/chats").json()["allowed_without_messages"] == [{"telegram_chat_id": -333}]


def test_clearing_a_test_chat_keeps_the_real_one(logged_in):
    async def seed():
        async with logged_in.factory() as session:
            repo = ChatRepository(session)
            test_chat = await repo.get_or_create(-111, "Тест")
            real_chat = await repo.get_or_create(-222, "Рабочий")
            await session.commit()
            await feed(session, test_chat, [("Аня", "тестовое")])
            await feed(session, real_chat, [("Аня", "рабочее")])
            for chat in (test_chat, real_chat):
                await MessageRepository(session).mark_analysis_done([1, 2])
            await session.commit()
            return test_chat.id

    test_chat_id = asyncio.run(seed())

    refused = logged_in.post(f"/api/chats/{test_chat_id}/purge", json={})
    assert refused.status_code == 422

    assert logged_in.post(f"/api/chats/{test_chat_id}/purge", json={"confirm": True}).json() == {"removed": 1}

    async def texts():
        async with logged_in.factory() as session:
            return (await session.execute(select(MessageRow.text))).scalars().all()

    assert asyncio.run(texts()) == ["рабочее"]


def test_clearing_a_missing_chat(logged_in):
    assert logged_in.post("/api/chats/999/purge", json={"confirm": True}).status_code == 404


# --- Moving to another hosting -----------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text("x = 1", encoding="utf-8")
    (root / "install.sh").write_text("#!/bin/sh", encoding="utf-8")
    (root / ".env").write_text("BOT_TOKEN=secret", encoding="utf-8")
    with sqlite3.connect(root / "bot.db") as conn:
        conn.execute("CREATE TABLE messages (text TEXT)")
    monkeypatch.setattr(panel, "PROJECT_ROOT", root)
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{(root / 'bot.db').as_posix()}")
    return root


def test_export_gives_everything_a_new_server_needs(logged_in, project):
    response = logged_in.post("/api/export", json={"password": PASSWORD})

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["content-disposition"].count("digest-bot-") == 1
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert {"app/main.py", "install.sh", ".env", "data/bot.db"} <= set(names)


def test_export_needs_the_password_even_with_a_session(logged_in, project):
    response = logged_in.post("/api/export", json={"password": "wrong"})

    assert response.status_code == 401
    assert b"PK" not in response.content[:2]


def test_export_password_guessing_is_throttled(logged_in, project):
    for _ in range(panel.MAX_FAILED_LOGINS):
        logged_in.post("/api/export", json={"password": "wrong"})

    assert logged_in.post("/api/export", json={"password": PASSWORD}).status_code == 429


def test_migration_summary(logged_in, project):
    data = logged_in.get("/api/migration").json()

    assert data["database_included"] is True
    assert data["messages"] == 0
    assert data["custom_stacks"] == 0


def test_migration_warns_when_the_database_cannot_be_packed(logged_in, monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u:p@db/x")
    assert logged_in.get("/api/migration").json()["database_included"] is False


def test_server_settings_can_be_changed(logged_in, env_path):
    response = _save(
        logged_in,
        APP_ENV="production",
        REDIS_URL="redis://cache:6379/0",
        WEBHOOK_URL="https://bot.example.com/telegram/webhook",
        WEBHOOK_SECRET="hook-secret",
        DATABASE_URL="postgresql+asyncpg://u:p@db/x",
    )

    assert response.status_code == 200
    env = read_env(env_path)
    assert env["APP_ENV"] == "production"
    assert env["DATABASE_URL"] == "postgresql+asyncpg://u:p@db/x"


def test_database_password_is_not_shown(logged_in, env_path):
    _save(logged_in, DATABASE_URL="postgresql+asyncpg://user:db-password@db/x")
    assert "db-password" not in logged_in.get("/api/settings").text


# --- Restart -----------------------------------------------------------------


def test_restart_request_creates_the_flag(logged_in):
    assert logged_in.post("/api/restart").json() == {"ok": True}
    assert logged_in.get("/api/overview").json()["restart_pending"] is True


@pytest.mark.asyncio
async def test_bot_stops_when_the_flag_appears(tmp_path, monkeypatch):
    flag = tmp_path / "restart.flag"
    monkeypatch.setattr(settings, "restart_flag_path", str(flag))
    dp = SimpleNamespace(stop_polling=AsyncMock())
    state = {"restart": False}

    await polling.watch_restart_flag(dp, state)
    dp.stop_polling.assert_not_called()

    flag.touch()
    await polling.watch_restart_flag(dp, state)

    dp.stop_polling.assert_awaited_once()
    assert state["restart"] is True
    assert not flag.exists()


@pytest.mark.asyncio
async def test_bot_exits_with_the_restart_code(tmp_path, monkeypatch):
    flag = tmp_path / "restart.flag"
    monkeypatch.setattr(settings, "restart_flag_path", str(flag))
    jobs: list = []
    scheduler = SimpleNamespace(
        start=lambda: None,
        shutdown=lambda wait: None,
        add_job=lambda func, trigger, args, **kwargs: jobs.append((func, args)),
    )
    dp = SimpleNamespace(
        resolve_used_update_types=lambda: [], stop_polling=AsyncMock(), start_polling=AsyncMock()
    )

    async def polling_until_flag(*args, **kwargs):
        flag.touch()
        func, job_args = jobs[0]
        await func(*job_args)

    dp.start_polling.side_effect = polling_until_flag
    bot = SimpleNamespace(delete_webhook=AsyncMock(), session=SimpleNamespace(close=AsyncMock()))
    monkeypatch.setattr(polling, "run_startup_checks", AsyncMock())
    monkeypatch.setattr(polling, "setup_bot", AsyncMock(return_value=(bot, dp)))
    monkeypatch.setattr(polling, "get_scheduler", lambda: scheduler)
    monkeypatch.setattr(polling, "close_redis", AsyncMock())
    monkeypatch.setattr(polling, "engine", SimpleNamespace(dispose=AsyncMock()))

    with pytest.raises(SystemExit) as exit_info:
        await polling.main()

    assert exit_info.value.code == polling.RESTART_EXIT_CODE


@pytest.mark.asyncio
async def test_stale_restart_request_is_cleared_on_start(tmp_path, monkeypatch):
    flag = tmp_path / "restart.flag"
    flag.touch()
    monkeypatch.setattr(settings, "restart_flag_path", str(flag))
    scheduler = SimpleNamespace(start=lambda: None, shutdown=lambda wait: None, add_job=lambda *a, **k: None)
    dp = SimpleNamespace(resolve_used_update_types=lambda: [], start_polling=AsyncMock())
    bot = SimpleNamespace(delete_webhook=AsyncMock(), session=SimpleNamespace(close=AsyncMock()))
    monkeypatch.setattr(polling, "run_startup_checks", AsyncMock())
    monkeypatch.setattr(polling, "setup_bot", AsyncMock(return_value=(bot, dp)))
    monkeypatch.setattr(polling, "get_scheduler", lambda: scheduler)
    monkeypatch.setattr(polling, "close_redis", AsyncMock())
    monkeypatch.setattr(polling, "engine", SimpleNamespace(dispose=AsyncMock()))

    await polling.main()

    assert not flag.exists()
