from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx

from app.config import settings
from app.db.schema import schema_is_current
from app.db.session import engine
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
            print(f"       Что сделать: {fix}")

    def fail(self, text: str, fix: str) -> None:
        self.failed += 1
        print(f"[FAIL] {text}")
        print(f"       Что сделать: {fix}")


async def check_telegram(report: Report) -> None:
    base = settings.telegram_api_base or "https://api.telegram.org"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{base}/bot{settings.bot_token}/getMe")
        body = response.json()
    except (httpx.HTTPError, ValueError) as e:
        report.fail(f"Telegram недоступен ({type(e).__name__})", "Проверьте интернет на этой машине.")
        return
    if not body.get("ok"):
        report.fail(
            f"Telegram отклонил токен (код {body.get('error_code')})",
            "Выпустите токен в @BotFather и запишите в BOT_TOKEN в файле .env.",
        )
        return
    me = body["result"]
    report.ok(f"Telegram: бот @{me.get('username')}")
    if not me.get("can_read_all_group_messages"):
        report.fail(
            "Бот видит в группах только команды",
            "@BotFather → /mybots → бот → Bot Settings → Group Privacy → Turn off, "
            "затем удалите бота из группы и добавьте снова.",
        )


def check_access(report: Report) -> None:
    if settings.allowed_chats:
        report.ok(f"Разрешённых чатов: {len(settings.allowed_chats)}")
    else:
        report.fail("ALLOWED_CHAT_IDS пуст, бот игнорирует все чаты", "Впишите ID чатов в .env (команда /chatid).")
    if settings.admins:
        report.ok(f"Администраторов: {len(settings.admins)}")
    else:
        report.fail("ADMIN_USER_IDS пуст, настройки менять некому", "Узнайте свой ID у @userinfobot и впишите в .env.")


async def check_database(report: Report) -> None:
    try:
        async with engine.connect() as conn:
            current = await conn.run_sync(schema_is_current)
    except Exception as e:
        report.fail(f"База данных недоступна ({type(e).__name__})", "Проверьте DATABASE_URL в .env.")
        return
    if current:
        report.ok("База данных: схема актуальна")
    else:
        report.fail("Схема базы устарела", "Выполните: python -m app.init_db")


async def check_llm(report: Report) -> None:
    if settings.llm_provider != "openai":
        report.warn("Тестовый режим LLM_PROVIDER=mock: смысл сообщений не анализируется", "Настройте модель в .env.")
        return
    blocked = llm_block_reason()
    if blocked:
        report.fail(f"Анализ на паузе: {blocked}", "Подключите локальную модель или получите разрешение владельца данных.")
        return

    where = "локально" if is_local_endpoint(settings.openai_base_url) else "внешний сервис"
    llm = _make_llm()
    assert isinstance(llm, OpenAILLMClient)
    if not await llm.model_available():
        fix = (
            f"Выполните: ollama pull {settings.openai_model}"
            if is_local_endpoint(settings.openai_base_url)
            else "Проверьте OPENAI_MODEL и ключ: сервис мог отключить модель."
        )
        report.fail(f"Модель {settings.openai_model} недоступна ({where})", fix)
        return

    message = BatchMessage(id=1, author="Тест", sent_at="2026-01-01 10:00", text="Нужно обновить сертификаты.")
    started = time.monotonic()
    try:
        await llm.analyze_conversation([message], [], [])
    except LLMError as e:
        report.fail(f"Модель {settings.openai_model} не ответила: {e}", "Проверьте, что сервер модели запущен.")
        return
    report.ok(f"Модель {settings.openai_model} ({where}): ответила за {time.monotonic() - started:.0f} с")


async def main() -> int:
    """Check everything the bot needs. Run with:  python -m app.doctor"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    report = Report()
    if Path(".env").exists():
        report.ok("Файл .env найден")
    else:
        report.warn("Файла .env нет, настройки берутся из окружения", "Скопируйте .env.example в .env.")
    await check_telegram(report)
    check_access(report)
    await check_database(report)
    await check_llm(report)
    await engine.dispose()
    print()
    print("Всё готово к работе." if not report.failed else f"Нужно исправить пунктов: {report.failed}.")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
