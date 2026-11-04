from __future__ import annotations

import sqlite3
import zipfile

import httpx
import pytest

from app import doctor
from app.bundle import build_bundle
from app.config import settings


@pytest.fixture
def project(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("x = 1", encoding="utf-8")
    (tmp_path / "app" / "__pycache__").mkdir()
    (tmp_path / "app" / "__pycache__" / "main.pyc").write_text("junk", encoding="utf-8")
    (tmp_path / "install.sh").write_text("#!/bin/sh", encoding="utf-8")
    (tmp_path / ".env").write_text("BOT_TOKEN=secret", encoding="utf-8")
    (tmp_path / "result.json").write_text("{}", encoding="utf-8")
    (tmp_path / "ollama.log").write_text("log", encoding="utf-8")
    with sqlite3.connect(tmp_path / "bot.db") as conn:
        conn.execute("CREATE TABLE messages (text TEXT)")
        conn.execute("INSERT INTO messages VALUES ('сообщение')")
    return tmp_path


def _url(project) -> str:
    return f"sqlite+aiosqlite:///{(project / 'bot.db').as_posix()}"


def test_bundle_has_what_a_server_needs_and_no_stray_files(project):
    output = project / "out.zip"
    build_bundle(output, root=project, database_url=_url(project))

    names = set(zipfile.ZipFile(output).namelist())
    assert {"app/main.py", "install.sh", ".env", "data/bot.db"} <= names
    assert "result.json" not in names
    assert "ollama.log" not in names
    assert not any("__pycache__" in n for n in names)
    assert not any("\\" in n for n in names)  # backslash paths break on Linux


def test_bundled_database_is_a_readable_snapshot(project, tmp_path_factory):
    output = project / "out.zip"
    build_bundle(output, root=project, database_url=_url(project))

    target = tmp_path_factory.mktemp("unpacked")
    zipfile.ZipFile(output).extract("data/bot.db", target)
    with sqlite3.connect(target / "data" / "bot.db") as conn:
        assert conn.execute("SELECT text FROM messages").fetchall() == [("сообщение",)]


def test_code_only_bundle_carries_no_secrets_or_chat_data(project):
    output = project / "out.zip"
    build_bundle(output, root=project, with_data=False, database_url=_url(project))

    names = set(zipfile.ZipFile(output).namelist())
    assert ".env" not in names
    assert "data/bot.db" not in names


def test_doctor_reports_empty_access_lists(monkeypatch, capsys):
    monkeypatch.setattr(settings, "allowed_chat_ids", "")
    monkeypatch.setattr(settings, "admin_user_ids", "")
    report = doctor.Report()

    doctor.check_access(report)

    assert report.failed == 2
    assert "ALLOWED_CHAT_IDS" in capsys.readouterr().out


@pytest.mark.parametrize(
    "payload, failures",
    [
        ({"ok": False, "error_code": 401}, 1),
        ({"ok": True, "result": {"username": "b", "can_read_all_group_messages": True}}, 0),
        ({"ok": True, "result": {"username": "b", "can_read_all_group_messages": False}}, 1),
    ],
)
@pytest.mark.asyncio
async def test_doctor_reads_telegram_answer(monkeypatch, payload, failures):
    class Client(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))

    monkeypatch.setattr(doctor.httpx, "AsyncClient", Client)
    report = doctor.Report()

    await doctor.check_telegram(report)

    assert report.failed == failures


@pytest.mark.asyncio
async def test_doctor_explains_blocked_analysis(monkeypatch, capsys):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_base_url", "https://api.groq.com/openai/v1")
    monkeypatch.setattr(settings, "openai_api_key", "key")
    report = doctor.Report()

    await doctor.check_llm(report)

    assert report.failed == 1
    assert "LLM_EXTERNAL_ALLOWED" in capsys.readouterr().out
