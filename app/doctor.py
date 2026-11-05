from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx

from app.config import settings
from app.db.schema import schema_is_current
from app.db.session import engine
from app.i18n import tr
from app.llm.base import LLMError
from app.llm.openai_client import OpenAILLMClient, is_local_endpoint, llm_block_reason
from app.llm.schemas import BatchMessage
from app.services.scheduler import _make_llm


class Report:
    def __init__(self) -> None:
        self.failed = 0

    def ok(self, text: str) -> None:
        print(f"[ OK ] {text}")

    def warn(self, text: str, fix: str = "") -> None:
        print(f"[ !! ] {text}")
        if fix:
            print(f"       {tr('doctor.what_to_do', fix=fix)}")

    def fail(self, text: str, fix: str) -> None:
        self.failed += 1
        print(f"[FAIL] {text}")
        print(f"       {tr('doctor.what_to_do', fix=fix)}")


async def check_telegram(report: Report) -> None:
    base = settings.telegram_api_base or "https://api.telegram.org"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{base}/bot{settings.bot_token}/getMe")
        body = response.json()
    except (httpx.HTTPError, ValueError) as e:
        report.fail(tr("doctor.telegram_down", error=type(e).__name__), tr("doctor.telegram_down_fix"))
        return
    if not body.get("ok"):
        report.fail(
            tr("doctor.token_rejected", code=body.get("error_code")),
            tr("doctor.token_rejected_fix"),
        )
        return
    me = body["result"]
    report.ok(tr("doctor.telegram_ok", username=me.get("username")))
    if not me.get("can_read_all_group_messages"):
        report.fail(tr("doctor.privacy_on"), tr("doctor.privacy_on_fix"))


def check_access(report: Report) -> None:
    if settings.allowed_chats:
        report.ok(tr("doctor.chats_ok", count=len(settings.allowed_chats)))
    else:
        report.fail(tr("doctor.no_chats"), tr("doctor.no_chats_fix"))
    if settings.admins:
        report.ok(tr("doctor.admins_ok", count=len(settings.admins)))
    else:
        report.fail(tr("doctor.no_admins"), tr("doctor.no_admins_fix"))


async def check_database(report: Report) -> None:
    try:
        async with engine.connect() as conn:
            current = await conn.run_sync(schema_is_current)
    except Exception as e:
        report.fail(tr("doctor.db_down", error=type(e).__name__), tr("doctor.db_down_fix"))
        return
    if current:
        report.ok(tr("doctor.db_ok"))
    else:
        report.fail(tr("doctor.db_outdated"), tr("doctor.db_outdated_fix"))


async def check_llm(report: Report) -> None:
    if settings.llm_provider != "openai":
        report.warn(tr("doctor.mock"), tr("doctor.mock_fix"))
        return
    blocked = llm_block_reason()
    if blocked:
        report.fail(tr("doctor.paused", reason=blocked), tr("doctor.paused_fix"))
        return

    model = settings.openai_model
    where = tr("doctor.where_local" if is_local_endpoint(settings.openai_base_url) else "doctor.where_external")
    llm = _make_llm()
    assert isinstance(llm, OpenAILLMClient)
    if not await llm.model_available():
        fix = (
            tr("doctor.pull_fix", model=model)
            if is_local_endpoint(settings.openai_base_url)
            else tr("doctor.model_gone_fix")
        )
        report.fail(tr("doctor.model_unavailable", model=model, where=where), fix)
        return

    message = BatchMessage(id=1, author="Test", sent_at="2026-01-01 10:00", text="We need to renew the certificates.")
    started = time.monotonic()
    try:
        await llm.analyze_conversation([message], [], [])
    except LLMError as e:
        report.fail(tr("doctor.model_silent", model=model, error=e), tr("doctor.model_silent_fix"))
        return
    report.ok(tr("doctor.model_ok", model=model, where=where, seconds=time.monotonic() - started))


async def main() -> int:
    """Check everything the bot needs. Run with:  python -m app.doctor"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    report = Report()
    if Path(".env").exists():
        report.ok(tr("doctor.env_ok"))
    else:
        report.warn(tr("doctor.no_env"), tr("doctor.no_env_fix"))
    await check_telegram(report)
    check_access(report)
    await check_database(report)
    await check_llm(report)
    await engine.dispose()
    print()
    print(tr("doctor.all_good") if not report.failed else tr("doctor.to_fix", count=report.failed))
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
