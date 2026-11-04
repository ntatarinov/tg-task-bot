from __future__ import annotations

import redis.asyncio as aioredis

from app.config import settings
from app.utils.logging import get_logger

logger = get_logger(__name__)

_client: aioredis.Redis | None = None
_unavailable_logged = False


def get_redis() -> aioredis.Redis | None:
    """Lazily build an async Redis client. Returns None only if construction fails."""
    global _client
    if _client is None:
        try:
            _client = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        except Exception as e:  # pragma: no cover - construction rarely fails
            logger.error("redis.init_failed", error=str(e))
            return None
    return _client


def _note_unavailable(error: str) -> None:
    global _unavailable_logged
    if not _unavailable_logged:
        logger.warning("redis.unavailable_fallback", error=error)
        _unavailable_logged = True


async def cache_exists(key: str) -> bool | None:
    """True/False if reachable, None if Redis is unavailable (caller should fall back)."""
    client = get_redis()
    if client is None:
        return None
    try:
        return bool(await client.exists(key))
    except Exception as e:
        _note_unavailable(str(e))
        return None


async def cache_set(key: str, value: str, ttl_seconds: int) -> bool:
    """Set key with TTL. Returns False if Redis is unavailable."""
    client = get_redis()
    if client is None:
        return False
    try:
        await client.set(key, value, ex=ttl_seconds)
        return True
    except Exception as e:
        _note_unavailable(str(e))
        return False


async def close_redis() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:  # pragma: no cover
            pass
        _client = None
