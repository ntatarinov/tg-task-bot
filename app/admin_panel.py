from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app import doctor, seen_chats, stacks
from app.bundle import ROOT as PROJECT_ROOT
from app.bundle import build_bundle
from app.config import _parse_id_list, reload_settings, settings
from app.db.models import (
    Chat,
    Digest,
    Message,
    TaskStatus,
    TopicItem,
    TopicItemKind,
    TopicItemStatus,
)
from app.db.repositories import ChatRepository, MessageRepository, TaskRepository, TopicRepository
from app.db.session import get_session
from app.env_file import read_env, update_env
from app.i18n import LANGUAGES, current_language, has_text, tr, use_language
from app.llm.base import LLMError
from app.llm.openai_client import is_local_endpoint, llm_block_reason
from app.services.digest_service import DigestService
from app.services.retention import RetentionService
from app.services.sandbox import MAX_SANDBOX_CHARS, plain_text, run_sandbox
from app.services.scheduler import _make_llm
from app.utils.logging import configure_logging, get_logger
from app.utils.time import ensure_aware_utc, is_valid_digest_time, is_valid_timezone, now_utc

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "panel_static"
ENV_PATH = Path(".env")
COOKIE_NAME = "digest_panel"
SESSION_SECONDS = 12 * 3600
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 60

_IDS_RE = re.compile(r"^\s*-?\d+(\s*[,;]\s*-?\d+)*\s*$")


@dataclass(frozen=True)
class Field:
    key: str
    group_key: str
    kind: str = "text"  # text, secret, bool, int, ids, url, time, timezone, select
    options: tuple[str, ...] = ()
    minimum: int = 0

    @property
    def label(self) -> str:
        return tr(f"field.{self.key}")

    @property
    def group(self) -> str:
        return tr(f"group.{self.group_key}")

    @property
    def help(self) -> str:
        key = f"field.{self.key}.help"
        return tr(key) if has_text(key) else ""


# An allowlist on purpose: the panel can change these and nothing else in the file.
FIELDS: tuple[Field, ...] = (
    Field("BOT_TOKEN", "telegram", "secret"),
    Field("ALLOWED_CHAT_IDS", "telegram", "ids"),
    Field("ADMIN_USER_IDS", "telegram", "ids"),
    Field("LLM_PROVIDER", "model", "select", ("openai", "mock")),
    Field("OPENAI_BASE_URL", "model", "url"),
    Field("OPENAI_MODEL", "model", "text"),
    Field("OPENAI_API_KEY", "model", "secret"),
    Field("LLM_EXTERNAL_ALLOWED", "model", "bool"),
    Field("LLM_REASONING_EFFORT", "model", "select", ("", "none", "low", "medium", "high")),
    Field("LLM_MAX_OUTPUT_TOKENS", "model", "int", minimum=256),
    Field("LLM_TIMEOUT_SECONDS", "model", "int", minimum=5),
    Field("ANALYSIS_INTERVAL_SECONDS", "analysis", "int", minimum=5),
    Field("ANALYSIS_BATCH_MAX_MESSAGES", "analysis", "int", minimum=1),
    Field("ANALYSIS_BATCH_MAX_CHARS", "analysis", "int", minimum=200),
    Field("ANALYSIS_MIN_BATCH_MESSAGES", "analysis", "int", minimum=1),
    Field("ANALYSIS_MAX_WAIT_SECONDS", "analysis", "int", minimum=0),
    Field("CLARIFICATIONS_ENABLED", "analysis", "bool"),
    Field("CLARIFICATION_COOLDOWN_HOURS", "analysis", "int", minimum=0),
    Field("DEFAULT_LANGUAGE", "reports", "select", LANGUAGES),
    Field("DEFAULT_TIMEZONE", "reports", "timezone"),
    Field("DEFAULT_DIGEST_TIME", "reports", "time"),
    Field("DIGEST_CATCHUP_HOURS", "reports", "int", minimum=0),
    Field("MESSAGE_RETENTION_DAYS", "storage", "int"),
    Field("RAW_UPDATE_RETENTION_DAYS", "storage", "int"),
    Field("DASHBOARD_SECRET", "task_site", "secret"),
    Field("PUBLIC_BASE_URL", "task_site", "url"),
    Field("APP_ENV", "server", "select", ("local", "production")),
    Field("DATABASE_URL", "server", "secret"),
    Field("REDIS_URL", "server", "text"),
    Field("WEBHOOK_URL", "server", "url"),
    Field("WEBHOOK_SECRET", "server", "secret"),
    Field("TELEGRAM_API_BASE", "server", "url"),
)

FIELDS_BY_KEY = {f.key: f for f in FIELDS}
# All most people ever touch; the rest stays folded away.
BASIC_KEYS = {"BOT_TOKEN", "ADMIN_USER_IDS"}


def validate_value(field: Field, value: str) -> str | None:
    """Returns what is wrong with a value, in words for the person typing it."""
    if "\n" in value or "\r" in value:
        return tr("panel.one_line")
    if not value:
        return None if field.kind in ("ids", "url", "select", "secret", "text") else tr("panel.required")
    if field.kind == "bool" and value not in ("true", "false"):
        return tr("panel.expected_bool")
    if field.kind == "int" and (not value.isdigit() or int(value) < field.minimum):
        return tr("panel.expected_int", minimum=field.minimum)
    if field.kind == "ids" and not _IDS_RE.match(value):
        return tr("panel.expected_ids")
    if field.kind == "url" and not (urlparse(value).scheme in ("http", "https") and urlparse(value).hostname):
        return tr("panel.expected_url")
    if field.kind == "time" and not is_valid_digest_time(value):
        return tr("panel.expected_time")
    if field.kind == "timezone" and not is_valid_timezone(value):
        return tr("panel.unknown_timezone_example")
    if field.kind == "select" and value not in field.options:
        return tr("panel.pick_from_list")
    return None


def _signature(expires: int) -> str:
    key = hashlib.sha256(settings.admin_panel_password.encode("utf-8")).digest()
    return hmac.new(key, str(expires).encode("ascii"), hashlib.sha256).hexdigest()


def make_session_cookie(now: float | None = None) -> str:
    expires = int(now if now is not None else time.time()) + SESSION_SECONDS
    return f"{expires}.{_signature(expires)}"


def session_is_valid(cookie: str | None, now: float | None = None) -> bool:
    if not cookie or "." not in cookie or not settings.admin_panel_password:
        return False
    raw_expires, signature = cookie.split(".", 1)
    if not raw_expires.isdigit():
        return False
    expires = int(raw_expires)
    if expires < (now if now is not None else time.time()):
        return False
    return hmac.compare_digest(_signature(expires), signature)


class LoginThrottle:
    """Slows down password guessing. Per process, which is all a single-admin panel needs."""

    def __init__(self) -> None:
        self.failures = 0
        self.locked_until = 0.0

    def seconds_left(self) -> int:
        return max(0, int(self.locked_until - time.time()) + 1) if self.locked_until > time.time() else 0

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= MAX_FAILED_LOGINS:
            self.locked_until = time.time() + LOCKOUT_SECONDS
            self.failures = 0

    def reset(self) -> None:
        self.failures = 0
        self.locked_until = 0.0


throttle = LoginThrottle()


class CollectingReport(doctor.Report):
    """The doctor's checks, gathered for the page instead of printed."""

    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict[str, str]] = []

    def ok(self, text: str) -> None:
        self.items.append({"state": "ok", "text": text, "fix": ""})

    def warn(self, text: str, fix: str = "") -> None:
        self.items.append({"state": "warn", "text": text, "fix": fix})

    def fail(self, text: str, fix: str) -> None:
        self.failed += 1
        self.items.append({"state": "fail", "text": text, "fix": fix})


class LoginBody(BaseModel):
    password: str


class SettingsBody(BaseModel):
    values: dict[str, str]
    confirm_external: bool = False


class ChatBody(BaseModel):
    timezone: str | None = None
    digest_time: str | None = None
    digest_enabled: bool | None = None
    weekly_digest_enabled: bool | None = None
    weekly_digest_day: int | None = None
    digest_chat_id: int | None = None
    clear_digest_chat: bool = False
    language: str | None = None  # empty means follow DEFAULT_LANGUAGE


class AccessBody(BaseModel):
    telegram_chat_id: int
    allowed: bool


class TaskStatusBody(BaseModel):
    status: str


class DigestPreviewBody(BaseModel):
    chat_id: int
    days: int = 7


class ExportBody(BaseModel):
    password: str


class PurgeBody(BaseModel):
    confirm: bool = False


class StackBody(BaseModel):
    name: str
    base_url: str
    model: str
    api_key: str = ""
    reasoning_effort: str = ""
    max_output_tokens: int = 2500
    timeout_seconds: int = 60


class ApplyBody(BaseModel):
    confirm_external: bool = False


class SandboxBody(BaseModel):
    stack_id: str
    text: str
    confirm_external: bool = False


class PullBody(BaseModel):
    model: str


def require_session(request: Request) -> None:
    if not session_is_valid(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(status_code=401, detail=tr("panel.sign_in"))


app = FastAPI(title="Digest Panel", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def protect(request: Request, call_next):
    use_language(request.headers.get("x-panel-language"))
    # SameSite covers browsers that honour it; this also refuses cross-site writes from others.
    if request.method not in ("GET", "HEAD"):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": tr("panel.foreign_origin")}, status_code=403)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/login")
async def login(body: LoginBody, response: Response) -> dict[str, bool]:
    wait = throttle.seconds_left()
    if wait:
        raise HTTPException(status_code=429, detail=tr("panel.too_many_attempts", seconds=wait))
    if not hmac.compare_digest(body.password.encode("utf-8"), settings.admin_panel_password.encode("utf-8")):
        throttle.record_failure()
        logger.warning("panel.login_failed")
        raise HTTPException(status_code=401, detail=tr("panel.wrong_password"))
    throttle.reset()
    response.set_cookie(
        COOKIE_NAME, make_session_cookie(), max_age=SESSION_SECONDS, httponly=True, samesite="strict", path="/"
    )
    return {"ok": True}


@app.post("/api/logout")
async def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/session")
async def session_state(request: Request) -> dict[str, bool]:
    return {"authenticated": session_is_valid(request.cookies.get(COOKIE_NAME))}


@app.get("/api/settings", dependencies=[Depends(require_session)])
async def get_settings() -> dict[str, Any]:
    stored = read_env(ENV_PATH)
    fields = []
    for field in FIELDS:
        # A value set by the environment (Docker) wins over the file and cannot be edited here.
        locked = field.key in os.environ
        value = os.environ[field.key] if locked else stored.get(field.key, "")
        entry: dict[str, Any] = {
            "key": field.key, "label": field.label, "group": field.group, "kind": field.kind,
            "help": field.help, "options": list(field.options), "locked": locked,
            "basic": field.key in BASIC_KEYS,
        }
        if field.kind == "secret":
            entry["is_set"] = bool(value)  # the value itself never reaches the browser
        else:
            entry["value"] = value
        fields.append(entry)
    return {"fields": fields}


@app.post("/api/settings", dependencies=[Depends(require_session)])
async def save_settings(body: SettingsBody) -> dict[str, Any]:
    errors: dict[str, str] = {}
    changes: dict[str, str] = {}
    current = read_env(ENV_PATH)

    for key, raw in body.values.items():
        field = FIELDS_BY_KEY.get(key)
        if field is None:
            errors[key] = tr("panel.not_editable")
            continue
        if key in os.environ:
            errors[key] = tr("panel.locked")
            continue
        value = raw.strip()
        if field.kind == "secret" and not value:
            continue  # blank means keep the stored secret
        problem = validate_value(field, value)
        if problem:
            errors[key] = problem
        elif current.get(key, "") != value:
            changes[key] = value

    turning_on = changes.get("LLM_EXTERNAL_ALLOWED") == "true"
    if turning_on and not body.confirm_external:
        errors["LLM_EXTERNAL_ALLOWED"] = tr("panel.confirm_owner")
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    if changes:
        update_env(ENV_PATH, changes)
        reload_settings()
        logger.info("panel.settings_changed", keys=sorted(changes))
    return {"changed": sorted(changes), "restart_needed": bool(changes)}


def _setup_steps(models: list[str]) -> list[str]:
    """What stands between this install and a working bot, in the order to do it."""
    steps: list[str] = []
    if settings.bot_token in ("", "placeholder"):
        steps.append(tr("panel.step_token"))
    if not settings.admins:
        steps.append(tr("panel.step_admin"))
    if not settings.allowed_chats:
        steps.append(tr("panel.step_chat"))
    if llm_block_reason():
        steps.append(tr("panel.step_model"))
    elif is_local_endpoint(settings.openai_base_url) and models and settings.openai_model not in models:
        steps.append(tr("panel.step_pull", model=settings.openai_model))
    return steps


def _item_view(item: TopicItem) -> dict[str, Any]:
    return {"text": item.text, "topic": item.topic.title}


@app.get("/api/summary", dependencies=[Depends(require_session)])
async def summary(
    chat_id: int | None = None, days: int = 7, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """The page people open the panel for: what was decided and what is left to do."""
    chats = await ChatRepository(session).get_all_active()
    # Chats the bot works in first, then the busiest history.
    chats.sort(key=lambda c: (c.telegram_chat_id not in settings.allowed_chats, c.id))
    models = (await installed_models())["models"]
    result: dict[str, Any] = {
        "setup": _setup_steps(models),
        "chats": [{"id": c.id, "title": c.title} for c in chats if c.telegram_chat_id < 0],
        "chat_id": None,
    }
    group_chats = [c for c in chats if c.telegram_chat_id < 0]
    chat = next((c for c in group_chats if c.id == chat_id), group_chats[0] if group_chats else None)
    if chat is None:
        return result

    end = now_utc()
    start = end - timedelta(days=max(1, min(days, 365)))
    items = await TopicRepository(session).items_in_period(chat.id, start, end)
    open_items = [i for i in items if i.status == TopicItemStatus.open]

    def of_kind(kind: TopicItemKind) -> list[dict[str, Any]]:
        return [_item_view(i) for i in open_items if i.kind == kind]

    tasks = await TaskRepository(session).get_active(chat.id)
    pending, _ = await MessageRepository(session).pending_stats(chat.id)
    last = await session.execute(
        select(Digest)
        .where(Digest.chat_id == chat.id, Digest.sent_at.isnot(None))
        .order_by(Digest.sent_at.desc())
        .limit(1)
    )
    digest = last.scalar_one_or_none()
    result.update(
        chat_id=chat.id,
        decisions=of_kind(TopicItemKind.decision),
        blockers=of_kind(TopicItemKind.blocker),
        questions=of_kind(TopicItemKind.question),
        tasks=[
            {"id": t.id, "text": t.title, "owner": t.assignee_text, "deadline": t.deadline}
            for t in tasks
        ],
        pending=pending,
        digest={
            "sent_at": ensure_aware_utc(digest.sent_at).isoformat(),
            "text": plain_text(digest.text),
        } if digest and digest.sent_at else None,
    )
    return result


@app.post("/api/tasks/{task_id}/status", dependencies=[Depends(require_session)])
async def set_task_status(
    task_id: int, body: TaskStatusBody, session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    try:
        status = TaskStatus(body.status)
    except ValueError:
        raise HTTPException(status_code=422, detail=tr("panel.unknown_status")) from None
    repo = TaskRepository(session)
    task = await repo.get_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=tr("panel.task_not_found"))
    await repo.set_status(task, status)
    return {"status": status.value}


@app.post("/api/digest-preview", dependencies=[Depends(require_session)])
async def digest_preview(
    body: DigestPreviewBody, session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    """Build a digest to read here. Nothing is sent, and it is not kept."""
    chat = await ChatRepository(session).get_by_id(body.chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail=tr("panel.chat_not_found"))
    end = now_utc()
    start = end - timedelta(days=max(1, min(body.days, 365)))
    digest = await DigestService(session, _make_llm()).build_digest(chat, start, end)
    text = plain_text(digest.text)
    await session.rollback()
    return {"text": text}


@app.get("/api/overview", dependencies=[Depends(require_session)])
async def overview(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    pending_total = 0
    chats = []
    for chat in await ChatRepository(session).get_all_active():
        chats.append(await _chat_view(session, chat))
        pending_total += chats[-1]["pending"]
    local = is_local_endpoint(settings.openai_base_url)
    return {
        "model": settings.openai_model,
        "model_location": "mock" if settings.llm_provider != "openai" else ("local" if local else "external"),
        "analysis_blocked": llm_block_reason(),
        "pending": pending_total,
        "allowed_chats": len(settings.allowed_chats),
        "restart_pending": Path(settings.restart_flag_path).exists(),
    }


@app.post("/api/check", dependencies=[Depends(require_session)])
async def run_checks() -> dict[str, Any]:
    report = CollectingReport()
    await doctor.check_telegram(report)
    doctor.check_access(report)
    await doctor.check_database(report)
    await doctor.check_llm(report)
    return {"items": report.items, "failed": report.failed}


@app.get("/api/models", dependencies=[Depends(require_session)])
async def installed_models() -> dict[str, list[str]]:
    """What Ollama already has, so the model can be picked instead of typed."""
    base = settings.local_llm_url.rstrip("/").removesuffix("/v1")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{base}/api/tags")
        return {"models": sorted(m["name"] for m in response.json().get("models", []))}
    except (httpx.HTTPError, ValueError, KeyError):
        return {"models": []}


async def _chat_view(session: AsyncSession, chat: Chat) -> dict[str, Any]:
    pending, _ = await MessageRepository(session).pending_stats(chat.id)
    result = await session.execute(select(func.max(Digest.sent_at)).where(Digest.chat_id == chat.id))
    last_digest = result.scalar_one_or_none()
    return {
        "id": chat.id,
        "telegram_chat_id": chat.telegram_chat_id,
        "title": chat.title,
        "allowed": chat.telegram_chat_id in settings.allowed_chats,
        "timezone": chat.timezone,
        "digest_time": chat.digest_time,
        "digest_enabled": chat.digest_enabled,
        "weekly_digest_enabled": chat.weekly_digest_enabled,
        "weekly_digest_day": chat.weekly_digest_day,
        "digest_chat_id": chat.digest_chat_id,
        "language": chat.language,
        "pending": pending,
        "last_digest_at": ensure_aware_utc(last_digest).isoformat() if last_digest else None,
    }


@app.get("/api/chats", dependencies=[Depends(require_session)])
async def list_chats(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    chats = await ChatRepository(session).get_all_active()
    views = [await _chat_view(session, c) for c in chats]
    stored = {c["telegram_chat_id"] for c in views}
    allowed = settings.allowed_chats
    waiting = [
        {"telegram_chat_id": int(chat_id), "title": info.get("title")}
        for chat_id, info in seen_chats.load().items()
        if int(chat_id) not in allowed and int(chat_id) not in stored and info.get("present", True)
    ]
    # An allowed chat the bot has not heard from yet has no row, but must still be visible.
    pending = [{"telegram_chat_id": chat_id} for chat_id in sorted(allowed - stored)]
    return {"chats": views, "waiting": waiting, "allowed_without_messages": pending}


async def _telegram(client: httpx.AsyncClient, method: str, **params: Any) -> dict[str, Any]:
    base = settings.telegram_api_base or "https://api.telegram.org"
    response = await client.get(f"{base}/bot{settings.bot_token}/{method}", params=params)
    return response.json()


async def _presence(
    client: httpx.AsyncClient, bot_id: int, telegram_chat_id: int, title: str | None
) -> dict[str, Any]:
    member = await _telegram(client, "getChatMember", chat_id=telegram_chat_id, user_id=bot_id)
    state = "out"
    moved_to = None
    if member.get("ok"):
        if member["result"]["status"] in ("member", "administrator", "creator", "restricted"):
            state = "in"
            chat = await _telegram(client, "getChat", chat_id=telegram_chat_id)
            title = chat.get("result", {}).get("title", title)
    else:
        # A group that became a supergroup gets a new id, and Telegram says which.
        moved_to = member.get("parameters", {}).get("migrate_to_chat_id")
        if moved_to:
            state = "moved"
    return {
        "telegram_chat_id": telegram_chat_id,
        "title": title,
        "state": state,
        "moved_to": moved_to,
        "allowed": telegram_chat_id in settings.allowed_chats,
    }


@app.post("/api/chats/presence", dependencies=[Depends(require_session)])
async def chat_presence(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """
    Ask Telegram about every chat we have heard of. Telegram has no call that lists a bot's
    chats, so this covers known ones; a new group appears once the bot sees it was added.
    """
    known: dict[int, str | None] = {int(k): v.get("title") for k, v in seen_chats.load().items()}
    for chat in await ChatRepository(session).get_all_active():
        if chat.telegram_chat_id < 0:
            known.setdefault(chat.telegram_chat_id, chat.title)
    for chat_id in settings.allowed_chats:
        known.setdefault(chat_id, None)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            me = await _telegram(client, "getMe")
            if not me.get("ok"):
                raise HTTPException(status_code=422, detail=tr("panel.token_refused"))
            bot = me["result"]
            chats = [await _presence(client, bot["id"], i, t) for i, t in known.items()]
    except (httpx.HTTPError, ValueError, KeyError) as e:
        # Only the type: the request URL contains the token.
        logger.warning("panel.presence_failed", error=type(e).__name__)
        raise HTTPException(status_code=502, detail=tr("panel.telegram_unreachable")) from None
    return {"bot": bot.get("username"), "chats": chats}


@app.post("/api/chats/access", dependencies=[Depends(require_session)])
async def change_access(body: AccessBody) -> dict[str, Any]:
    """Allow or forbid a chat. The bot reads this list at start, so it needs a restart."""
    if "ALLOWED_CHAT_IDS" in os.environ:
        raise HTTPException(status_code=422, detail=tr("panel.chats_locked"))
    ids = _parse_id_list(read_env(ENV_PATH).get("ALLOWED_CHAT_IDS", ""))
    if body.allowed:
        ids.add(body.telegram_chat_id)
    else:
        ids.discard(body.telegram_chat_id)
    update_env(ENV_PATH, {"ALLOWED_CHAT_IDS": ",".join(str(i) for i in sorted(ids))})
    reload_settings()
    logger.info("panel.access_changed", chat_id=body.telegram_chat_id, allowed=body.allowed)
    return {"restart_needed": True}


@app.post("/api/chats/{chat_id}/purge", dependencies=[Depends(require_session)])
async def purge_chat(
    chat_id: int, body: PurgeBody, session: AsyncSession = Depends(get_session)
) -> dict[str, int]:
    """Remove a chat's messages, topics and digests, for example after a test chat."""
    if not body.confirm:
        raise HTTPException(status_code=422, detail=tr("panel.confirm_delete"))
    if await ChatRepository(session).get_by_id(chat_id) is None:
        raise HTTPException(status_code=404, detail=tr("panel.chat_not_found"))
    removed = await RetentionService(session).purge_chat(chat_id)
    logger.info("panel.chat_purged", chat_id=chat_id, removed=removed)
    return {"removed": removed}


@app.patch("/api/chats/{chat_id}", dependencies=[Depends(require_session)])
async def update_chat(
    chat_id: int, body: ChatBody, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    repo = ChatRepository(session)
    chat = await repo.get_by_id(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail=tr("panel.chat_not_found"))
    if body.timezone is not None and not is_valid_timezone(body.timezone):
        raise HTTPException(status_code=422, detail=tr("panel.unknown_timezone"))
    if body.digest_time is not None and not is_valid_digest_time(body.digest_time):
        raise HTTPException(status_code=422, detail=tr("panel.expected_time"))
    if body.weekly_digest_day is not None and not 0 <= body.weekly_digest_day <= 6:
        raise HTTPException(status_code=422, detail=tr("panel.bad_weekday"))
    if body.language and body.language not in LANGUAGES:
        raise HTTPException(status_code=422, detail=tr("panel.bad_language"))
    if body.digest_chat_id is not None and body.digest_chat_id not in settings.allowed_chats:
        raise HTTPException(status_code=422, detail=tr("panel.target_not_allowed"))

    await repo.update_settings(
        chat,
        timezone=body.timezone,
        digest_time=body.digest_time,
        digest_enabled=body.digest_enabled,
        weekly_digest_enabled=body.weekly_digest_enabled,
        weekly_digest_day=body.weekly_digest_day,
    )
    if body.language is not None:
        await repo.set_language(chat, body.language or None)
    if body.clear_digest_chat:
        await repo.set_digest_target(chat, None)
    elif body.digest_chat_id is not None:
        await repo.set_digest_target(chat, body.digest_chat_id)
    return await _chat_view(session, chat)


@app.post("/api/restart", dependencies=[Depends(require_session)])
async def request_restart() -> dict[str, bool]:
    """The bot sees this file within seconds and exits; its supervisor starts it again.
    A flag rather than the Docker socket, which would hand this page control of the host."""
    Path(settings.restart_flag_path).touch()
    logger.info("panel.restart_requested")
    return {"ok": True}


def _sqlite_database() -> Path | None:
    url = make_url(settings.database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return None
    return Path(url.database)


@app.get("/api/migration", dependencies=[Depends(require_session)])
async def migration_info(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """What a move to another server would carry."""
    database = _sqlite_database()
    messages = (await session.execute(select(func.count()).select_from(Message))).scalar_one()
    chats = (await session.execute(select(func.count()).select_from(Chat))).scalar_one()
    return {
        "database_included": database is not None,
        "database_mb": round(database.stat().st_size / 1_000_000, 1) if database and database.exists() else 0,
        "messages": messages,
        "chats": chats,
        "custom_stacks": len(stacks.custom_stacks()),
    }


@app.post("/api/export", dependencies=[Depends(require_session)])
async def export_bundle(body: ExportBody) -> FileResponse:
    """
    Everything a new server needs, as one download. It holds the tokens and the whole chat
    history, so an open session is not enough: the password is asked again.
    """
    wait = throttle.seconds_left()
    if wait:
        raise HTTPException(status_code=429, detail=tr("panel.too_many_attempts", seconds=wait))
    if not hmac.compare_digest(body.password.encode("utf-8"), settings.admin_panel_password.encode("utf-8")):
        throttle.record_failure()
        raise HTTPException(status_code=401, detail=tr("panel.wrong_password"))
    throttle.reset()

    workdir = Path(tempfile.mkdtemp(prefix="digest_export_"))
    name = f"digest-bot-{datetime.now():%Y%m%d-%H%M}.zip"
    output = workdir / name
    await asyncio.to_thread(build_bundle, output, PROJECT_ROOT)
    logger.info("panel.exported")
    return FileResponse(
        output,
        filename=name,
        media_type="application/zip",
        background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True),
    )


def _stack_view(stack: stacks.Stack, active: stacks.Stack) -> dict[str, Any]:
    return {
        "id": stack.id,
        "name": stack.name,
        "base_url": stack.base_url,
        "model": stack.model,
        "reasoning_effort": stack.reasoning_effort,
        "max_output_tokens": stack.max_output_tokens,
        "timeout_seconds": stack.timeout_seconds,
        "builtin": stack.builtin,
        "external": stacks.is_external(stack),
        "has_key": bool(stack.api_key),
        "active": (stack.base_url, stack.model) == (active.base_url, active.model),
    }


@app.get("/api/stacks", dependencies=[Depends(require_session)])
async def list_stacks() -> dict[str, Any]:
    active = stacks.current_stack()
    return {
        "stacks": [_stack_view(s, active) for s in stacks.all_stacks()],
        "current": _stack_view(active, active),
    }


@app.post("/api/stacks", dependencies=[Depends(require_session)])
async def add_stack(body: StackBody) -> dict[str, Any]:
    errors: dict[str, str] = {}
    problem = validate_value(FIELDS_BY_KEY["OPENAI_BASE_URL"], body.base_url.strip())
    if problem or not body.base_url.strip():
        errors["base_url"] = problem or tr("panel.required")
    if not stacks.MODEL_NAME_RE.match(body.model.strip()):
        errors["model"] = tr("panel.model_name_needed")
    if not body.name.strip() or "\n" in body.name:
        errors["name"] = tr("panel.stack_name_needed")
    if "\n" in body.api_key or "\r" in body.api_key:
        errors["api_key"] = tr("panel.one_line")
    if body.reasoning_effort not in FIELDS_BY_KEY["LLM_REASONING_EFFORT"].options:
        errors["reasoning_effort"] = tr("panel.pick_from_list")
    if body.max_output_tokens < 256:
        errors["max_output_tokens"] = tr("panel.expected_tokens")
    if body.timeout_seconds < 5:
        errors["timeout_seconds"] = tr("panel.expected_timeout")
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    stack = stacks.Stack(
        id=f"custom-{secrets.token_hex(4)}",
        name=body.name.strip(),
        base_url=body.base_url.strip(),
        model=body.model.strip(),
        api_key=body.api_key.strip(),
        reasoning_effort=body.reasoning_effort,
        max_output_tokens=body.max_output_tokens,
        timeout_seconds=body.timeout_seconds,
    )
    stacks.save_custom(stack)
    return _stack_view(stack, stacks.current_stack())


@app.delete("/api/stacks/{stack_id}", dependencies=[Depends(require_session)])
async def remove_stack(stack_id: str) -> dict[str, bool]:
    if not stacks.delete_custom(stack_id):
        raise HTTPException(status_code=404, detail=tr("panel.stack_not_deletable"))
    return {"ok": True}


@app.post("/api/stacks/{stack_id}/apply", dependencies=[Depends(require_session)])
async def apply_stack(stack_id: str, body: ApplyBody) -> dict[str, Any]:
    stack = stacks.find_stack(stack_id)
    if stack is None:
        raise HTTPException(status_code=404, detail=tr("panel.stack_not_found"))
    external = stacks.is_external(stack)
    if external and not body.confirm_external:
        raise HTTPException(status_code=422, detail=tr("panel.stack_confirm_owner"))
    if external and not (stack.api_key or settings.openai_api_key):
        raise HTTPException(status_code=422, detail=tr("panel.stack_key_in_settings"))

    changes = stacks.env_changes(stack, external_allowed=external)
    locked = sorted(key for key in changes if key in os.environ)
    if locked:
        raise HTTPException(status_code=422, detail=tr("panel.stack_locked", keys=", ".join(locked)))
    update_env(ENV_PATH, changes)
    reload_settings()
    logger.info("panel.stack_applied", stack=stack.id, external=external)
    return {"ok": True, "restart_needed": True}


@app.post("/api/sandbox", dependencies=[Depends(require_session)])
async def sandbox(body: SandboxBody) -> dict[str, Any]:
    """Try a stack on a pasted conversation. The bot and its data are not involved."""
    stack = stacks.current_stack() if body.stack_id == "current" else stacks.find_stack(body.stack_id)
    if stack is None:
        raise HTTPException(status_code=404, detail=tr("panel.stack_not_found"))
    if not body.text.strip():
        raise HTTPException(status_code=422, detail=tr("panel.paste_chat"))
    if len(body.text) > MAX_SANDBOX_CHARS:
        raise HTTPException(status_code=422, detail=tr("panel.text_too_long", limit=MAX_SANDBOX_CHARS))
    external = stacks.is_external(stack)
    if external and not body.confirm_external and not settings.llm_external_allowed:
        raise HTTPException(status_code=422, detail=tr("panel.confirm_no_work_data"))
    if external and not (stack.api_key or settings.openai_api_key):
        raise HTTPException(status_code=422, detail=tr("panel.stack_key_needed"))

    llm = stacks.make_client(stack, allow_external=external)
    try:
        result = await run_sandbox(llm, body.text, current_language())
    except LLMError as e:
        raise HTTPException(status_code=502, detail=tr("panel.model_no_answer", error=e)) from e
    return {**result, "stack": stack.name, "model": stack.model}


pull_state: dict[str, Any] = {"model": None, "status": "idle", "percent": 0, "error": None}
_pull_task: asyncio.Task | None = None


async def _pull_model(base: str, model: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", f"{base}/api/pull", json={"model": model}) as response:
                async for line in response.aiter_lines():
                    event = json.loads(line) if line.strip() else {}
                    if "error" in event:
                        raise RuntimeError(str(event["error"]))
                    if event.get("total"):
                        pull_state["percent"] = int(event.get("completed", 0) * 100 / event["total"])
        pull_state.update(status="done", percent=100)
    except (httpx.HTTPError, ValueError, RuntimeError) as e:
        logger.warning("panel.model_pull_failed", model=model, error=type(e).__name__)
        pull_state.update(status="failed", error=tr("panel.pull_failed"))


@app.post("/api/models/pull", dependencies=[Depends(require_session)])
async def pull_model(body: PullBody) -> dict[str, Any]:
    global _pull_task
    model = body.model.strip()
    if not stacks.MODEL_NAME_RE.match(model):
        raise HTTPException(status_code=422, detail=tr("panel.model_name_needed"))
    if pull_state["status"] == "running":
        raise HTTPException(status_code=409, detail=tr("panel.pull_busy"))
    pull_state.update(model=model, status="running", percent=0, error=None)
    base = settings.local_llm_url.rstrip("/").removesuffix("/v1")
    _pull_task = asyncio.create_task(_pull_model(base, model))
    return pull_state


@app.get("/api/models/pull", dependencies=[Depends(require_session)])
async def pull_status() -> dict[str, Any]:
    return pull_state


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    """Run with:  python -m app.admin_panel"""
    configure_logging()
    if not settings.admin_panel_password:
        raise SystemExit("Set ADMIN_PANEL_PASSWORD in .env: the panel does not start without a password.")
    uvicorn.run(app, host=settings.panel_host, port=settings.panel_port, log_level="warning")


if __name__ == "__main__":
    main()
