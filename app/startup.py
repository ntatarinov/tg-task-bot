from __future__ import annotations

from app.config import settings
from app.db.schema import schema_is_current
from app.db.session import engine
from app.llm.openai_client import OpenAILLMClient, llm_block_reason
from app.services.scheduler import _make_llm
from app.utils.logging import get_logger

logger = get_logger(__name__)


async def run_startup_checks() -> None:
    """Fail on what cannot work, and warn loudly about what would otherwise fail silently."""
    async with engine.connect() as conn:
        if not await conn.run_sync(schema_is_current):
            raise SystemExit("The database schema is out of date. Run: python -m app.init_db")

    if not settings.allowed_chats:
        logger.warning("startup.no_allowed_chats", hint="Set ALLOWED_CHAT_IDS; every chat is ignored")
    if not settings.admins:
        logger.warning("startup.no_admins", hint="Set ADMIN_USER_IDS; nobody can change settings")

    blocked = llm_block_reason()
    if blocked:
        logger.warning("startup.analysis_paused", reason=blocked)
        return
    llm = _make_llm()
    if isinstance(llm, OpenAILLMClient) and not await llm.model_available():
        logger.error("startup.model_unavailable", model=settings.openai_model)
