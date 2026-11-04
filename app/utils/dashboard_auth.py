from __future__ import annotations

import hashlib
import hmac

from app.config import settings


def make_chat_token(chat_id: int) -> str:
    """Derive a per-chat dashboard token from DASHBOARD_SECRET — no extra DB column needed."""
    digest = hmac.new(
        settings.dashboard_secret.encode("utf-8"),
        str(chat_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:20]


def verify_chat_token(chat_id: int, token: str) -> bool:
    if not settings.dashboard_secret or not token:
        return False
    return hmac.compare_digest(make_chat_token(chat_id), token)


def verify_master_token(token: str) -> bool:
    if not settings.dashboard_secret or not token:
        return False
    return hmac.compare_digest(settings.dashboard_secret, token)


def dashboard_configured() -> bool:
    return bool(settings.dashboard_secret and settings.public_base_url)


def chat_dashboard_url(chat_id: int) -> str:
    base = settings.public_base_url.rstrip("/")
    return f"{base}/dashboard/{chat_id}?token={make_chat_token(chat_id)}"
