"""Trying a stack on sample text, and swapping it in one action."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import admin_panel as panel
from app import stacks
from app.bundle import build_bundle
from app.config import settings
from app.db.models import Message
from app.db.session import get_session
from app.env_file import read_env
from app.llm.base import LLMUnavailableError
from app.llm.openai_client import OpenAILLMClient
from app.services import sandbox
from app.services.sandbox import parse_chat, run_sandbox
from tests.helpers import ScriptedLLM

PASSWORD = "correct horse"
ANSWER = {"topics": [{
    "title": "Сертификаты", "summary": "Обсудили сертификаты.", "message_ids": [1, 2],
    "items": [
        {"kind": "task", "text": "Обновить сертификаты", "source_ids": [1, 2], "owner": "Виктор", "deadline": "до пятницы"},
        {"kind": "decision", "text": "Выдумка", "source_ids": [99]},
    ],
}]}
CHAT = "Аня: Виктор, обнови сертификаты до пятницы.\nВиктор: Возьму."


@pytest.fixture
def env_path(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("OPENAI_MODEL=old\nOPENAI_BASE_URL=http://localhost:11434/v1\n", encoding="utf-8")
    monkeypatch.setattr(panel, "ENV_PATH", path)
    monkeypatch.setattr(panel, "reload_settings", lambda: None)
    for field in panel.FIELDS:
        monkeypatch.delenv(field.key, raising=False)
    return path


@pytest.fixture
def client(env_path, engine, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "admin_panel_password", PASSWORD)
    monkeypatch.setattr(settings, "stacks_path", str(tmp_path / "stacks.json"))
    monkeypatch.setattr(settings, "local_llm_url", "http://localhost:11434/v1")
    monkeypatch.setattr(settings, "openai_base_url", "http://localhost:11434/v1")
    monkeypatch.setattr(settings, "openai_model", "gemma4:26b")
    panel.throttle.reset()
    panel.pull_state.update(model=None, status="idle", percent=0, error=None)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def session_override():
        async with factory() as session:
            yield session

    panel.app.dependency_overrides[get_session] = session_override
    test_client = TestClient(panel.app, base_url="http://panel.test")
    assert test_client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    yield test_client
    panel.app.dependency_overrides.clear()


def _use_llm(monkeypatch, llm) -> list:
    """Replaces the model behind the sandbox and records how the client was built."""
    built: list = []

    def fake(stack, allow_external=False):
        built.append((stack, allow_external))
        return llm

    monkeypatch.setattr(stacks, "make_client", fake)
    return built


def _custom(client, **overrides):
    body = {
        "name": "Свой сервер", "base_url": "https://llm.example.com/v1", "model": "my-model",
        "api_key": "custom-secret", "reasoning_effort": "", "max_output_tokens": 3000, "timeout_seconds": 120,
    }
    return client.post("/api/stacks", json={**body, **overrides})


# --- Reading a pasted conversation -------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Аня: привет", [("Аня", "привет")]),
        ("Аня: первое\nБорис: второе", [("Аня", "первое"), ("Борис", "второе")]),
        ("Аня: первое\nпродолжение", [("Аня", "первое\nпродолжение")]),
        ("без автора", [("Participant", "без автора")]),
        ("\n\nАня: с пустыми строками\n\n", [("Аня", "с пустыми строками")]),
        ("Аня: время 15:00 и ссылка http://x", [("Аня", "время 15:00 и ссылка http://x")]),
        ("", []),
    ],
)
def test_pasted_chat_parsing(text, expected):
    assert parse_chat(text) == expected


# --- The sandbox --------------------------------------------------------------


@pytest.mark.asyncio
async def test_sandbox_runs_the_real_pipeline():
    result = await run_sandbox(ScriptedLLM(ANSWER), CHAT)

    assert result["messages"] == 2
    assert result["rejected"] == 1  # the same checks as production: the invented item is gone
    item = result["topics"][0]["items"][0]
    assert (item["kind"], item["owner"]) == ("task", "Виктор")
    assert item["deadline"]
    assert "Обновить сертификаты" in result["digest"]
    assert "<b>" not in result["digest"]


@pytest.mark.asyncio
async def test_sandbox_never_asks_questions_in_a_chat():
    answer = {"topics": [{"title": "Т", "message_ids": [1], "items": [{"kind": "task", "text": "Сделать", "source_ids": [1]}]}]}
    calls = AsyncMock()
    import app.services.clarification_service as clarification_module

    original = clarification_module.cache_set
    clarification_module.cache_set = calls
    try:
        await run_sandbox(ScriptedLLM(answer), "Аня: Надо сделать")
    finally:
        clarification_module.cache_set = original

    calls.assert_not_called()


@pytest.mark.asyncio
async def test_sandbox_reports_unusable_answers(monkeypatch):
    from app.llm.base import LLMResponseError

    monkeypatch.setattr(settings, "analysis_max_attempts", 1)
    result = await run_sandbox(ScriptedLLM(LLMResponseError("garbage")), CHAT)

    assert result["failed"] == 2
    assert result["topics"] == []


def test_sandbox_leaves_the_bot_database_alone(client, monkeypatch, engine):
    _use_llm(monkeypatch, ScriptedLLM(ANSWER))

    response = client.post("/api/sandbox", json={"stack_id": "current", "text": CHAT})
    assert response.status_code == 200

    async def count():
        factory = async_sessionmaker(engine, class_=AsyncSession)
        async with factory() as session:
            return (await session.execute(select(func.count()).select_from(Message))).scalar()

    import asyncio

    assert asyncio.run(count()) == 0


def test_sandbox_does_not_change_settings(client, env_path, monkeypatch):
    _use_llm(monkeypatch, ScriptedLLM(ANSWER))
    before = env_path.read_text(encoding="utf-8")

    client.post("/api/sandbox", json={"stack_id": "local-gemma4-12b", "text": CHAT})

    assert env_path.read_text(encoding="utf-8") == before
    assert settings.openai_model == "gemma4:26b"


def test_sandbox_uses_the_chosen_stack(client, monkeypatch):
    built = _use_llm(monkeypatch, ScriptedLLM(ANSWER))

    data = client.post("/api/sandbox", json={"stack_id": "local-gemma4-12b", "text": CHAT}).json()

    assert built[0][0].model == "gemma4:12b"
    assert data["model"] == "gemma4:12b"


@pytest.mark.parametrize(
    "body, status",
    [
        ({"stack_id": "nope", "text": CHAT}, 404),
        ({"stack_id": "current", "text": "   "}, 422),
        ({"stack_id": "current", "text": "x" * (sandbox.MAX_SANDBOX_CHARS + 1)}, 422),
    ],
)
def test_sandbox_refuses_bad_requests(client, body, status):
    assert client.post("/api/sandbox", json=body).status_code == status


def test_sandbox_reports_a_dead_model(client, monkeypatch):
    _use_llm(monkeypatch, ScriptedLLM(LLMUnavailableError("APIConnectionError")))

    response = client.post("/api/sandbox", json={"stack_id": "current", "text": CHAT})

    assert response.status_code == 502
    assert "did not answer" in response.json()["detail"]


def test_text_goes_to_an_external_service_only_with_confirmation(client, monkeypatch):
    built = _use_llm(monkeypatch, ScriptedLLM(ANSWER))
    monkeypatch.setattr(settings, "openai_api_key", "key")
    body = {"stack_id": "groq-gpt-oss-120b", "text": CHAT}

    refused = client.post("/api/sandbox", json=body)
    assert refused.status_code == 422
    assert built == []

    assert client.post("/api/sandbox", json={**body, "confirm_external": True}).status_code == 200
    assert built[0][1] is True


def test_external_stack_without_a_key_is_explained(client, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")
    body = {"stack_id": "groq-gpt-oss-120b", "text": CHAT, "confirm_external": True}

    assert "API key" in client.post("/api/sandbox", json=body).json()["detail"]


# --- The client built for a stack --------------------------------------------


def test_stack_client_carries_its_own_options():
    stack = stacks.Stack("x", "X", "http://localhost:11434/v1", "m", "ollama", "none", 6000, 900)
    client = stacks.make_client(stack)

    assert (client.model, client.max_output_tokens, client.reasoning_effort) == ("m", 6000, "none")
    assert client.client.timeout == 900


@pytest.mark.asyncio
async def test_stack_options_win_over_global_settings(monkeypatch):
    monkeypatch.setattr(settings, "llm_reasoning_effort", "high")
    monkeypatch.setattr(settings, "llm_max_output_tokens", 100)
    client = stacks.make_client(stacks.Stack("x", "X", "http://localhost:11434/v1", "m", "k", "", 6000, 60))
    seen: dict = {}

    async def create(**kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop here")

    monkeypatch.setattr(client.client.chat.completions, "create", create)
    with pytest.raises(RuntimeError):
        await client.analyze_conversation([], [], [])

    assert seen["max_tokens"] == 6000
    assert "extra_body" not in seen  # an empty effort means none is sent


@pytest.mark.asyncio
async def test_external_client_is_blocked_whatever_the_bot_is_configured_for(monkeypatch):
    """The bot running a local model must not open the door for another client."""
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_base_url", "http://localhost:11434/v1")
    client = OpenAILLMClient(api_key="key", model="m", base_url="https://api.groq.com/openai/v1")
    network = AsyncMock()
    monkeypatch.setattr(client.client.chat.completions, "create", network)

    with pytest.raises(LLMUnavailableError):
        await client.analyze_conversation([], [], [])

    network.assert_not_called()


# --- Listing, adding, deleting ------------------------------------------------


def test_builtin_stacks_are_offered(client):
    data = client.get("/api/stacks").json()

    assert [s["id"] for s in data["stacks"]] == ["local-gemma4-26b", "local-gemma4-12b", "groq-gpt-oss-120b"]
    assert [s["external"] for s in data["stacks"]] == [False, False, True]
    assert [s["active"] for s in data["stacks"]] == [True, False, False]


def test_local_stacks_follow_the_deployment(client, monkeypatch):
    monkeypatch.setattr(settings, "local_llm_url", "http://ollama:11434/v1")
    assert client.get("/api/stacks").json()["stacks"][0]["base_url"] == "http://ollama:11434/v1"


def test_custom_stack_is_saved_and_survives(client):
    created = _custom(client).json()

    listed = {s["id"]: s for s in client.get("/api/stacks").json()["stacks"]}
    assert listed[created["id"]]["model"] == "my-model"
    assert listed[created["id"]]["builtin"] is False


def test_stack_keys_never_reach_the_browser(client):
    response = _custom(client)
    listing = client.get("/api/stacks")

    assert "custom-secret" not in response.text and "custom-secret" not in listing.text
    assert response.json()["has_key"] is True


@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"base_url": "ftp://x"}, "base_url"), ({"base_url": ""}, "base_url"), ({"model": ""}, "model"),
        ({"model": "bad model"}, "model"), ({"model": "x;rm -rf"}, "model"), ({"name": ""}, "name"),
        ({"name": "a\nb"}, "name"), ({"api_key": "a\nb"}, "api_key"), ({"reasoning_effort": "max"}, "reasoning_effort"),
        ({"max_output_tokens": 10}, "max_output_tokens"), ({"timeout_seconds": 1}, "timeout_seconds"),
    ],
)
def test_bad_stacks_are_refused(client, overrides, field):
    response = _custom(client, **overrides)

    assert response.status_code == 422
    assert field in response.json()["detail"]["errors"]


def test_custom_stack_can_be_deleted_but_builtin_cannot(client):
    created = _custom(client).json()

    assert client.delete(f"/api/stacks/{created['id']}").status_code == 200
    assert client.delete("/api/stacks/local-gemma4-26b").status_code == 404
    assert len(client.get("/api/stacks").json()["stacks"]) == 3


# --- Swapping -----------------------------------------------------------------


def test_applying_a_local_stack_writes_every_setting(client, env_path):
    response = client.post("/api/stacks/local-gemma4-12b/apply", json={})

    assert response.json() == {"ok": True, "restart_needed": True}
    env = read_env(env_path)
    assert env["OPENAI_MODEL"] == "gemma4:12b"
    assert env["LLM_REASONING_EFFORT"] == "none"
    assert env["LLM_MAX_OUTPUT_TOKENS"] == "6000"
    assert env["LLM_TIMEOUT_SECONDS"] == "900"
    assert env["LLM_PROVIDER"] == "openai"
    assert env["LLM_EXTERNAL_ALLOWED"] == "false"


def test_cloud_stack_needs_consent_and_records_it(client, env_path, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "key")

    assert client.post("/api/stacks/groq-gpt-oss-120b/apply", json={}).status_code == 422
    assert read_env(env_path)["OPENAI_MODEL"] == "old"

    assert client.post("/api/stacks/groq-gpt-oss-120b/apply", json={"confirm_external": True}).status_code == 200
    env = read_env(env_path)
    assert env["OPENAI_MODEL"] == "openai/gpt-oss-120b"
    assert env["LLM_EXTERNAL_ALLOWED"] == "true"
    assert env["LLM_REASONING_EFFORT"] == ""


def test_going_back_to_local_withdraws_the_consent(client, env_path, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "key")
    client.post("/api/stacks/groq-gpt-oss-120b/apply", json={"confirm_external": True})

    client.post("/api/stacks/local-gemma4-26b/apply", json={})

    assert read_env(env_path)["LLM_EXTERNAL_ALLOWED"] == "false"


def test_cloud_stack_without_any_key_cannot_be_applied(client, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")
    response = client.post("/api/stacks/groq-gpt-oss-120b/apply", json={"confirm_external": True})

    assert "API key" in response.json()["detail"]


def test_custom_stack_brings_its_key(client, env_path):
    created = _custom(client).json()

    client.post(f"/api/stacks/{created['id']}/apply", json={"confirm_external": True})

    assert read_env(env_path)["OPENAI_API_KEY"] == "custom-secret"


def test_missing_stack_cannot_be_applied(client):
    assert client.post("/api/stacks/nope/apply", json={}).status_code == 404


def test_settings_fixed_by_the_server_block_a_swap(client, env_path, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "fixed")

    response = client.post("/api/stacks/local-gemma4-12b/apply", json={})

    assert response.status_code == 422
    assert "OPENAI_MODEL" in response.json()["detail"]
    assert read_env(env_path)["OPENAI_MODEL"] == "old"


# --- Downloading a model ------------------------------------------------------


class _Pull(httpx.AsyncClient):
    lines = [
        json.dumps({"status": "pulling", "total": 100, "completed": 40}),
        "",
        json.dumps({"status": "success"}),
    ]

    def __init__(self, **kwargs):
        body = "\n".join(self.lines).encode("utf-8")
        super().__init__(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body)))


@pytest.mark.asyncio
async def test_pull_finishes(monkeypatch):
    monkeypatch.setattr(panel.httpx, "AsyncClient", _Pull)
    panel.pull_state.update(status="running", percent=0, error=None)

    await panel._pull_model("http://localhost:11434", "gemma4:12b")

    assert panel.pull_state["status"] == "done"
    assert panel.pull_state["percent"] == 100


@pytest.mark.asyncio
async def test_failed_pull_is_reported(monkeypatch):
    class Failing(_Pull):
        lines = [json.dumps({"error": "model not found"})]

    monkeypatch.setattr(panel.httpx, "AsyncClient", Failing)
    panel.pull_state.update(status="running", percent=0, error=None)

    await panel._pull_model("http://localhost:11434", "nope")

    assert panel.pull_state["status"] == "failed"
    assert "Could not" in panel.pull_state["error"]


@pytest.mark.parametrize("model", ["", "bad model", "x;y", "../etc"])
def test_pull_refuses_odd_names(client, model):
    if model == "../etc":
        pytest.skip("slashes are valid in model names; Ollama rejects unknown ones itself")
    assert client.post("/api/models/pull", json={"model": model}).status_code == 422


def test_only_one_pull_at_a_time(client):
    panel.pull_state.update(status="running")
    assert client.post("/api/models/pull", json={"model": "gemma4:12b"}).status_code == 409


def test_pull_status_is_available(client):
    assert client.get("/api/models/pull").json()["status"] == "idle"


# --- Moving custom stacks to the server --------------------------------------


def test_bundle_carries_custom_stacks(tmp_path):
    Path(settings.stacks_path).write_text("[]", encoding="utf-8")
    output = tmp_path / "out.zip"

    build_bundle(output, root=tmp_path, database_url="sqlite+aiosqlite:///:memory:")

    assert "data/stacks.json" in zipfile.ZipFile(output).namelist()


def test_code_only_bundle_leaves_stack_keys_behind(tmp_path):
    Path(settings.stacks_path).write_text("[]", encoding="utf-8")
    output = tmp_path / "out.zip"

    build_bundle(output, root=tmp_path, with_data=False, database_url="sqlite+aiosqlite:///:memory:")

    assert "data/stacks.json" not in zipfile.ZipFile(output).namelist()
