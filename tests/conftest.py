from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
ALLOWED_CHAT = -1001234567890
ADMIN_ID = 1000


@pytest.fixture(autouse=True)
def _redis_unavailable(monkeypatch):
    """Default to Redis-down so tests exercise the DB fallbacks fast and offline."""
    import app.db.redis as redis_mod

    monkeypatch.setattr(redis_mod, "get_redis", lambda: None)


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch, tmp_path):
    """Keep tests independent of whatever happens to be in the local .env."""
    from app.config import settings

    monkeypatch.setattr(settings, "seen_chats_path", str(tmp_path / "seen_chats.json"))
    monkeypatch.setattr(settings, "stacks_path", str(tmp_path / "stacks.json"))
    monkeypatch.setattr(settings, "restart_flag_path", str(tmp_path / "restart.flag"))
    monkeypatch.setattr(settings, "dashboard_secret", "")
    monkeypatch.setattr(settings, "webhook_secret", "")
    monkeypatch.setattr(settings, "app_env", "local")
    monkeypatch.setattr(settings, "llm_provider", "mock")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "openai_base_url", "")
    monkeypatch.setattr(settings, "llm_external_allowed", False)
    monkeypatch.setattr(settings, "llm_reasoning_effort", "")
    monkeypatch.setattr(settings, "llm_max_output_tokens", 2500)
    monkeypatch.setattr(settings, "allowed_chat_ids", str(ALLOWED_CHAT))
    monkeypatch.setattr(settings, "admin_user_ids", str(ADMIN_ID))
    monkeypatch.setattr(settings, "message_retention_days", 0)
    monkeypatch.setattr(settings, "raw_update_retention_days", 0)
    monkeypatch.setattr(settings, "clarifications_enabled", True)


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
