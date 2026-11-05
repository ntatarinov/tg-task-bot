"""Everything around the bot: model client, startup, doctor, webhook, Redis, deployment files."""
from __future__ import annotations

import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from openai import APIConnectionError
from sqlalchemy.ext.asyncio import create_async_engine

from app import doctor, startup
from app.config import settings
from app.db import redis as redis_mod
from app.llm.base import LLMResponseError, LLMUnavailableError
from app.llm.mock_client import MockLLMClient
from app.llm.openai_client import OpenAILLMClient, create_llm_client
from app.utils import time as time_utils
from app.utils.dashboard_auth import (
    chat_dashboard_url,
    dashboard_configured,
    make_chat_token,
    verify_master_token,
)

ROOT = Path(__file__).resolve().parent.parent


def _client(monkeypatch, content='{"topics": []}', finish_reason="stop") -> OpenAILLMClient:
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_base_url", "http://localhost:11434/v1")
    client = OpenAILLMClient(api_key="ollama", model="m", base_url=settings.openai_base_url)
    message = SimpleNamespace(content=content)
    response = SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish_reason, message=message)])
    monkeypatch.setattr(client.client.chat.completions, "create", AsyncMock(return_value=response))
    return client


# --- Every shape of model answer ---------------------------------------------


@pytest.mark.parametrize(
    "content, finish_reason",
    [
        ("это не json", "stop"),
        ("", "stop"),
        ('{"topics": "нет тем"}', "stop"),
        ('{"topics": [', "stop"),
        ('{"topics": []}', "length"),
    ],
)
@pytest.mark.asyncio
async def test_unusable_answers_raise_instead_of_looking_empty(monkeypatch, content, finish_reason):
    client = _client(monkeypatch, content, finish_reason)
    with pytest.raises(LLMResponseError):
        await client.analyze_conversation([], [], [])


@pytest.mark.parametrize(
    "content, topics",
    [
        ('{"topics": []}', 0),
        ('{}', 0),
        ('```json\n{"topics": [{"title": "Тема"}]}\n```', 1),
        ('<think>размышляю {"x": 1}</think>{"topics": [{"title": "Тема"}]}', 1),
        ('Вот ответ:\n{"topics": [{"title": "Тема"}]}\nГотово.', 1),
    ],
)
@pytest.mark.asyncio
async def test_usable_answers_in_every_wrapping(monkeypatch, content, topics):
    client = _client(monkeypatch, content)
    result = await client.analyze_conversation([], [], [])
    assert len(result.topics) == topics


@pytest.mark.asyncio
async def test_merge_keeps_only_requested_topics(monkeypatch):
    client = _client(monkeypatch, '{"summaries": [{"topic_id": 1, "summary": "Итог"}, {"topic_id": 99, "summary": "Чужое"}]}')
    assert await client.merge_summaries({1: ["a", "b"]}) == {1: "Итог"}


@pytest.mark.asyncio
async def test_merge_with_wrong_shape_is_an_error(monkeypatch):
    client = _client(monkeypatch, '{"summaries": "нет"}')
    with pytest.raises(LLMResponseError):
        await client.merge_summaries({1: ["a", "b"]})


@pytest.mark.parametrize(
    "models, expected", [(["m", "other"], True), (["other"], False), ([], False)]
)
@pytest.mark.asyncio
async def test_model_availability(monkeypatch, models, expected):
    client = _client(monkeypatch)
    listing = SimpleNamespace(data=[SimpleNamespace(id=m) for m in models])
    monkeypatch.setattr(client.client.models, "list", AsyncMock(return_value=listing))

    assert await client.model_available() is expected


@pytest.mark.asyncio
async def test_model_check_survives_a_dead_server(monkeypatch):
    client = _client(monkeypatch)
    error = APIConnectionError(request=httpx.Request("GET", "http://localhost"))
    monkeypatch.setattr(client.client.models, "list", AsyncMock(side_effect=error))

    assert await client.model_available() is False


@pytest.mark.asyncio
async def test_model_check_is_false_when_blocked(monkeypatch):
    client = OpenAILLMClient(api_key="key", model="m", base_url="https://api.groq.com/openai/v1")

    assert await client.model_available() is False


@pytest.mark.parametrize(
    "provider, expected", [("openai", OpenAILLMClient), ("mock", MockLLMClient), ("anything", MockLLMClient)]
)
def test_client_factory(provider, expected):
    assert isinstance(create_llm_client(provider, "key", "m"), expected)


def test_blocked_without_a_key(monkeypatch):
    from app.llm.openai_client import llm_block_reason

    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_base_url", "https://api.groq.com/openai/v1")
    monkeypatch.setattr(settings, "llm_external_allowed", True)
    assert "OPENAI_API_KEY" in llm_block_reason()


# --- Startup -----------------------------------------------------------------


@pytest.fixture
def startup_db(monkeypatch, engine):
    monkeypatch.setattr(startup, "engine", engine)
    return engine


@pytest.mark.asyncio
async def test_startup_refuses_an_outdated_schema(monkeypatch, tmp_path):
    empty = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/empty.db")
    monkeypatch.setattr(startup, "engine", empty)

    with pytest.raises(SystemExit, match="app.init_db"):
        await startup.run_startup_checks()
    await empty.dispose()


@pytest.mark.asyncio
async def test_startup_passes_on_a_good_setup(startup_db):
    await startup.run_startup_checks()


@pytest.mark.asyncio
async def test_startup_tolerates_empty_access_lists(startup_db, monkeypatch):
    monkeypatch.setattr(settings, "allowed_chat_ids", "")
    monkeypatch.setattr(settings, "admin_user_ids", "")
    await startup.run_startup_checks()


@pytest.mark.asyncio
async def test_startup_continues_when_analysis_is_blocked(startup_db, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_base_url", "https://api.groq.com/openai/v1")
    await startup.run_startup_checks()


@pytest.mark.asyncio
async def test_startup_checks_the_model(startup_db, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_base_url", "http://localhost:11434/v1")
    check = AsyncMock(return_value=False)
    monkeypatch.setattr(OpenAILLMClient, "model_available", check)

    await startup.run_startup_checks()

    check.assert_awaited_once()


# --- Doctor ------------------------------------------------------------------


@pytest.fixture
def doctor_db(monkeypatch, engine):
    monkeypatch.setattr(doctor, "engine", engine)


@pytest.mark.asyncio
async def test_doctor_on_a_healthy_setup(doctor_db, monkeypatch, capsys):
    payload = {"ok": True, "result": {"username": "b", "can_read_all_group_messages": True}}

    class Client(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))

    monkeypatch.setattr(doctor.httpx, "AsyncClient", Client)

    assert await doctor.main() == 0
    assert "Everything is ready" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_doctor_reports_unreachable_telegram(monkeypatch):
    class Client(httpx.AsyncClient):
        def __init__(self, **kwargs):
            def fail(request):
                raise httpx.ConnectError("no route")

            super().__init__(transport=httpx.MockTransport(fail))

    monkeypatch.setattr(doctor.httpx, "AsyncClient", Client)
    report = doctor.Report()

    await doctor.check_telegram(report)

    assert report.failed == 1


@pytest.mark.parametrize(
    "base_url, available, answer, failures, hint",
    [
        ("http://localhost:11434/v1", True, None, 0, "local"),
        ("http://localhost:11434/v1", False, None, 1, "ollama pull"),
        ("http://localhost:11434/v1", True, LLMUnavailableError("down"), 1, "did not answer"),
    ],
)
@pytest.mark.asyncio
async def test_doctor_model_states(monkeypatch, capsys, base_url, available, answer, failures, hint):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_base_url", base_url)
    monkeypatch.setattr(OpenAILLMClient, "model_available", AsyncMock(return_value=available))
    monkeypatch.setattr(OpenAILLMClient, "analyze_conversation", AsyncMock(side_effect=answer))
    report = doctor.Report()

    await doctor.check_llm(report)

    assert report.failed == failures
    assert hint in capsys.readouterr().out


@pytest.mark.asyncio
async def test_doctor_warns_about_mock_without_failing(capsys):
    report = doctor.Report()
    await doctor.check_llm(report)

    assert report.failed == 0
    assert "mock" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_doctor_reports_outdated_schema(monkeypatch, tmp_path):
    empty = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/empty.db")
    monkeypatch.setattr(doctor, "engine", empty)
    report = doctor.Report()

    await doctor.check_database(report)

    assert report.failed == 1
    await empty.dispose()


# --- Web endpoints -----------------------------------------------------------


def test_health_and_metrics():
    from app.main import app

    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/metrics").status_code == 200


def test_webhook_before_startup_is_unavailable(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "_bot", None)
    assert TestClient(main.app).post("/telegram/webhook", json={}).status_code == 503


def test_readiness_reports_database_state(monkeypatch, engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    import app.db.session as session_mod
    from app.main import app

    factory = async_sessionmaker(engine, class_=AsyncSession)
    monkeypatch.setattr(session_mod, "async_session_factory", factory)
    assert TestClient(app).get("/ready").json() == {"status": "ready"}

    @asynccontextmanager
    async def broken():
        raise RuntimeError("db down")
        yield

    monkeypatch.setattr(session_mod, "async_session_factory", broken)
    assert TestClient(app).get("/ready").status_code == 503


def test_dashboard_tokens(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_secret", "s3cret")
    monkeypatch.setattr(settings, "public_base_url", "https://example.com/")

    assert dashboard_configured()
    assert verify_master_token("s3cret") and not verify_master_token("wrong")
    assert chat_dashboard_url(5) == f"https://example.com/dashboard/5?token={make_chat_token(5)}"
    assert make_chat_token(5) != make_chat_token(6)


# --- Both ways of receiving updates ------------------------------------------


def _fake_bot() -> SimpleNamespace:
    return SimpleNamespace(
        set_webhook=AsyncMock(),
        delete_webhook=AsyncMock(),
        session=SimpleNamespace(close=AsyncMock()),
    )


def _fake_dispatcher() -> SimpleNamespace:
    return SimpleNamespace(
        resolve_used_update_types=lambda: ["message", "edited_message", "callback_query"],
        start_polling=AsyncMock(),
    )


def _patch_process(monkeypatch, module, bot, dp) -> SimpleNamespace:
    scheduler = SimpleNamespace(
        start=lambda: None, shutdown=lambda wait: None, add_job=lambda *args, **kwargs: None
    )
    monkeypatch.setattr(module, "run_startup_checks", AsyncMock())
    monkeypatch.setattr(module, "setup_bot", AsyncMock(return_value=(bot, dp)))
    monkeypatch.setattr(module, "get_scheduler", lambda: scheduler)
    monkeypatch.setattr(module, "engine", SimpleNamespace(dispose=AsyncMock()))
    return scheduler


@pytest.mark.asyncio
async def test_polling_keeps_updates_sent_while_the_bot_was_down(monkeypatch):
    """Regression: drop_pending_updates=True threw away everything from the downtime."""
    from app import polling

    bot, dp = _fake_bot(), _fake_dispatcher()
    _patch_process(monkeypatch, polling, bot, dp)
    monkeypatch.setattr(polling, "close_redis", AsyncMock())

    await polling.main()

    bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)
    assert "edited_message" in dp.start_polling.call_args.kwargs["allowed_updates"]
    polling.run_startup_checks.assert_awaited_once()


@pytest.mark.parametrize(
    "webhook_url, secret, scheduler_on",
    [("https://example.com/hook", "s3cret", True), ("https://example.com/hook", "", False), ("", "", True)],
)
def test_web_process_in_every_mode(monkeypatch, webhook_url, secret, scheduler_on):
    from app import main
    from app.db import redis as redis_module

    bot, dp = _fake_bot(), _fake_dispatcher()
    _patch_process(monkeypatch, main, bot, dp)
    monkeypatch.setattr(redis_module, "close_redis", AsyncMock())
    monkeypatch.setattr(settings, "webhook_url", webhook_url)
    monkeypatch.setattr(settings, "webhook_secret", secret)
    monkeypatch.setattr(settings, "enable_scheduler", scheduler_on)

    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200

    if webhook_url:
        kwargs = bot.set_webhook.call_args.kwargs
        assert kwargs["drop_pending_updates"] is False
        assert kwargs["secret_token"] == (secret or None)
    else:
        bot.set_webhook.assert_not_called()
    bot.session.close.assert_awaited_once()


@pytest.mark.parametrize("api_base", ["", "http://127.0.0.1:8081"])
def test_bot_can_use_a_custom_api_server(monkeypatch, api_base):
    from app.bot import dispatcher

    monkeypatch.setattr(settings, "bot_token", "123456:TEST")
    monkeypatch.setattr(settings, "telegram_api_base", api_base)

    bot = dispatcher.create_bot()

    assert bot.session.api.base.startswith(api_base or "https://api.telegram.org")
    assert dispatcher.get_bot() is bot


@pytest.mark.asyncio
async def test_bot_commands_are_registered():
    from app.bot.commands import set_bot_commands

    bot = SimpleNamespace(
        set_my_commands=AsyncMock(), set_my_description=AsyncMock(), set_my_short_description=AsyncMock()
    )
    await set_bot_commands(bot)

    names = {c.command for c in bot.set_my_commands.call_args.args[0]}
    assert {"digest", "status", "chatid", "set_weekly", "purge_history"} <= names


# --- Redis -------------------------------------------------------------------


class FakeRedis:
    def __init__(self, fail=False) -> None:
        self.data: dict = {}
        self.fail = fail

    async def set(self, key, value, ex=None, nx=False):
        if self.fail:
            raise ConnectionError("down")
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def exists(self, key):
        if self.fail:
            raise ConnectionError("down")
        return int(key in self.data)


@pytest.mark.asyncio
async def test_redis_helpers_with_a_working_server(monkeypatch):
    server = FakeRedis()
    monkeypatch.setattr(redis_mod, "get_redis", lambda: server)

    assert await redis_mod.cache_exists("k") is False
    assert await redis_mod.cache_set("k", "v", ttl_seconds=1) is True
    assert await redis_mod.cache_exists("k") is True


@pytest.mark.asyncio
async def test_redis_helpers_degrade_when_it_fails(monkeypatch):
    monkeypatch.setattr(redis_mod, "get_redis", lambda: FakeRedis(fail=True))

    assert await redis_mod.cache_exists("k") is None
    assert await redis_mod.cache_set("k", "v", ttl_seconds=1) is False


@pytest.mark.asyncio
async def test_redis_helpers_without_a_client():
    # conftest makes get_redis return None, the state after a failed construction.
    assert await redis_mod.cache_exists("k") is None
    assert await redis_mod.cache_set("k", "v", ttl_seconds=1) is False


# --- Time helpers ------------------------------------------------------------


def test_time_helpers():
    from datetime import datetime

    assert time_utils.now_in_tz("Asia/Almaty").utcoffset() is not None
    assert time_utils.parse_digest_time("18:30").hour == 18
    assert time_utils.localize_datetime(datetime(2026, 1, 1), "UTC").tzinfo is not None
    aware = time_utils.now_utc()
    assert time_utils.localize_datetime(aware, "Asia/Almaty") == aware


# --- Deployment files: the three ways to run on a server ---------------------


def _compose(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def test_selfhost_keeps_everything_on_the_server():
    services = _compose("docker-compose.selfhost.yml")["services"]
    env = services["bot"]["environment"]

    assert env["LOCAL_LLM_URL"] == "http://ollama:11434/v1"
    # Which model runs must stay in .env, or the panel could not swap it.
    assert not {"OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_API_KEY", "LLM_PROVIDER"} & set(env)
    assert int(services["ollama"]["environment"]["OLLAMA_CONTEXT_LENGTH"]) >= 8192
    # The model must not be reachable from outside, and the bot needs no inbound traffic.
    assert "ports" not in services["ollama"] and "ports" not in services["bot"]


def test_settings_panel_is_never_published_to_the_network():
    panel = _compose("docker-compose.selfhost.yml")["services"]["panel"]
    assert all(port.startswith("127.0.0.1:") for port in panel["ports"])
    assert "./.env:/app/.env" in panel["volumes"]


@pytest.mark.parametrize("name", ["docker-compose.selfhost.yml", "docker-compose.cloud.yml"])
def test_saved_settings_reach_the_bot_after_a_restart(name):
    """env_file is read only when a container is created, so it would ignore the panel."""
    bot = _compose(name)["services"]["bot"]
    assert "env_file" not in bot
    assert "./.env:/app/.env" in bot["volumes"]
    assert bot["environment"]["RESTART_FLAG_PATH"] == "/data/restart.flag"


@pytest.mark.parametrize("name", ["docker-compose.selfhost.yml", "docker-compose.cloud.yml"])
def test_server_variants_keep_data_outside_the_container(name):
    bot = _compose(name)["services"]["bot"]

    assert "./data:/data" in bot["volumes"]
    assert bot["environment"]["DATABASE_URL"].endswith("/data/bot.db")
    assert bot["restart"] == "unless-stopped"
    assert "app.init_db" in bot["command"]


def test_gpu_override_only_adds_the_device():
    override = _compose("docker-compose.gpu.yml")["services"]
    assert list(override) == ["ollama"]
    assert override["ollama"]["deploy"]["resources"]["reservations"]["devices"][0]["driver"] == "nvidia"


def test_image_cannot_pick_up_secrets_or_chat_data():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert {".env", "*.db", "data/", "digest-bot-*.zip"} <= set(ignored)


def test_dockerfile_copies_the_package_before_installing_it():
    lines = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
    assert lines.index("COPY app ./app") < next(i for i, line in enumerate(lines) if "pip install" in line)


def test_install_script_has_unix_line_endings():
    assert b"\r" not in (ROOT / "install.sh").read_bytes()


def test_line_endings_are_pinned_for_git():
    assert "eol=lf" in (ROOT / ".gitattributes").read_text(encoding="utf-8")


def test_install_script_repairs_a_windows_env_file():
    assert "sed -i 's/\\r$//' .env" in (ROOT / "install.sh").read_text(encoding="utf-8")


def _working_bash() -> bool:
    # On Windows `bash` may be a WSL stub that exists but cannot run anything.
    if shutil.which("bash") is None:
        return False
    return subprocess.run(["bash", "-c", "exit 0"], check=False, capture_output=True).returncode == 0


@pytest.mark.skipif(not _working_bash(), reason="no working bash here")
def test_install_script_parses():
    script = (ROOT / "install.sh").read_bytes()
    assert subprocess.run(["bash", "-n"], input=script, check=False).returncode == 0


def test_env_example_works_without_a_database_server():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "\nDATABASE_URL=sqlite+aiosqlite:///./bot.db\n" in text
    assert "\nLLM_EXTERNAL_ALLOWED=false\n" in text
