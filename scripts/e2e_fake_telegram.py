"""
End-to-end run of the real `app.polling` process against a fake Telegram Bot API.

Nothing here talks to Telegram or an external model: the bot gets a throwaway token,
a throwaway database and the mock LLM. It exercises what unit tests cannot — real
aiogram dispatch, polling, middleware, scheduler jobs and process startup.

    python scripts/e2e_fake_telegram.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pytz
from aiohttp import web

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "123456:FAKE_TOKEN_FOR_LOCAL_TEST_ONLY"
ALLOWED = -1001111111111
FOREIGN = -1002222222222
NEW_GROUP = -1003333333333
ADMIN = 1000
MEMBER = 2000
PORT = 8765


class FakeTelegram:
    def __init__(self) -> None:
        self.queue: list[dict] = []
        self.sent: list[dict] = []
        self.next_update = 1
        self.delete_webhook_args: list[dict] = []

    def push(self, kind: str, payload: dict) -> None:
        self.queue.append({"update_id": self.next_update, kind: payload})
        self.next_update += 1

    def message(self, message_id, chat_id, user_id, text, **extra) -> None:
        self.push("message", self._message(message_id, chat_id, user_id, text, **extra))

    @staticmethod
    def _message(message_id, chat_id, user_id, text, **extra) -> dict:
        chat_type = "private" if chat_id > 0 else "supergroup"
        return {
            "message_id": message_id,
            "date": int(time.time()),
            "chat": {"id": chat_id, "type": chat_type, "title": "E2E"},
            "from": {"id": user_id, "is_bot": False, "first_name": f"User{user_id}", "username": f"user{user_id}"},
            "text": text,
            **extra,
        }

    async def handle(self, request: web.Request) -> web.Response:
        method = request.match_info["method"]
        data = dict(await request.post()) if request.can_read_body else {}
        if method == "getMe":
            result = {"id": 1, "is_bot": True, "first_name": "E2E", "username": "e2e_bot"}
        elif method == "getUpdates":
            offset = int(data.get("offset") or 0)
            pending = [u for u in self.queue if u["update_id"] >= offset]
            if not pending:
                await asyncio.sleep(0.3)
            result = pending
        elif method == "sendMessage":
            self.sent.append(data)
            result = self._message(50000 + len(self.sent), int(data["chat_id"]), 1, data.get("text", ""))
        elif method == "deleteWebhook":
            self.delete_webhook_args.append(data)
            result = True
        else:
            result = True
        return web.json_response({"ok": True, "result": result})


def texts(fake: FakeTelegram, chat_id: int) -> list[str]:
    return [m.get("text", "") for m in fake.sent if int(m["chat_id"]) == chat_id]


async def wait_for(condition, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        await asyncio.sleep(0.2)
    raise AssertionError(f"timed out waiting for: {what}")


def rows(db: Path, sql: str) -> list[tuple]:
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as conn:
        return conn.execute(sql).fetchall()


def start_bot(env: dict[str, str], log: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "app.polling"],
        cwd=ROOT,
        env=env,
        stdout=log.open("ab"),
        stderr=subprocess.STDOUT,
    )


async def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="tgbot_e2e_"))
    db = workdir / "e2e.db"
    log = workdir / "bot.log"
    env = {
        **os.environ,
        "BOT_TOKEN": TOKEN,
        "TELEGRAM_API_BASE": f"http://127.0.0.1:{PORT}",
        "DATABASE_URL": f"sqlite+aiosqlite:///{db.as_posix()}",
        "REDIS_URL": "redis://127.0.0.1:1/0",
        "LLM_PROVIDER": "mock",
        "ALLOWED_CHAT_IDS": str(ALLOWED),
        "ADMIN_USER_IDS": str(ADMIN),
        # Everything a run writes stays in the throwaway folder, out of the real project.
        "SEEN_CHATS_PATH": str(workdir / "seen_chats.json"),
        "STACKS_PATH": str(workdir / "stacks.json"),
        "RESTART_FLAG_PATH": str(workdir / "restart.flag"),
        "ANALYSIS_INTERVAL_SECONDS": "1",
        "ANALYSIS_MIN_BATCH_MESSAGES": "1",
        "DASHBOARD_SECRET": "",
        "PUBLIC_BASE_URL": "",
    }

    fake = FakeTelegram()
    app = web.Application()
    app.router.add_route("*", f"/bot{TOKEN}/{{method}}", fake.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()

    checks: list[str] = []

    def passed(name: str) -> None:
        checks.append(name)
        print(f"PASS {name}", flush=True)

    # An outdated database must stop the bot with instructions, not crash later.
    refused = subprocess.run(
        [sys.executable, "-m", "app.polling"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60
    )
    assert refused.returncode != 0 and "app.init_db" in (refused.stderr + refused.stdout)
    passed("bot refuses to start on an outdated schema")

    subprocess.run([sys.executable, "-m", "app.init_db"], cwd=ROOT, env=env, check=True, capture_output=True, timeout=60)
    passed("init_db creates the schema")

    # Queued before startup, as if sent while the bot was down.
    fake.message(1, ALLOWED, MEMBER, "Нужно настроить бэкапы базы")
    bot = start_bot(env, log)
    try:
        await wait_for(lambda: rows(db, "SELECT COUNT(*) FROM messages")[0][0] == 1
                       if db.exists() else False, 30, "message sent during downtime")
        passed("updates sent during downtime are processed")
        assert fake.delete_webhook_args and fake.delete_webhook_args[0].get("drop_pending_updates") in ("false", "False", None)
        passed("startup keeps pending updates")

        await wait_for(lambda: rows(db, "SELECT COUNT(*) FROM tasks")[0][0] == 1, 30, "batch analysis")
        passed("scheduler analyzes the queue and creates a task")
        await wait_for(lambda: any("Who will take the task" in t for t in texts(fake, ALLOWED)), 15, "clarification")
        passed("clarification question reaches the chat")

        # Same update again, as after a redelivery.
        fake.message(1, ALLOWED, MEMBER, "Нужно настроить бэкапы базы")
        fake.message(2, ALLOWED, MEMBER, "контрольное сообщение")
        await wait_for(lambda: rows(db, "SELECT COUNT(*) FROM messages")[0][0] == 2, 15, "second message")
        passed("redelivered update stored once")

        edited = FakeTelegram._message(2, ALLOWED, MEMBER, "контрольное сообщение, исправлено", edit_date=int(time.time()))
        fake.push("edited_message", edited)
        await wait_for(
            lambda: rows(db, "SELECT text FROM messages WHERE telegram_message_id = 2")[0][0].endswith("исправлено"),
            15, "edit",
        )
        passed("edited message updates the stored text")

        fake.message(3, FOREIGN, MEMBER, "секретная переписка чужого чата")
        fake.message(4, FOREIGN, MEMBER, "/digest")
        fake.message(5, FOREIGN, ADMIN, "/chatid")
        await wait_for(lambda: any(str(FOREIGN) in t for t in texts(fake, FOREIGN)), 15, "/chatid for admin")
        assert rows(db, f"SELECT COUNT(*) FROM chats WHERE telegram_chat_id = {FOREIGN}")[0][0] == 0
        assert rows(db, "SELECT COUNT(*) FROM messages WHERE text LIKE '%секретная%'")[0][0] == 0
        assert len(texts(fake, FOREIGN)) == 1
        passed("foreign chat is ignored; only an admin's /chatid is answered")

        fake.message(6, ALLOWED, MEMBER, "/set_digest_time 21:00")
        await wait_for(lambda: any("for bot admins only" in t for t in texts(fake, ALLOWED)), 15, "refusal")
        assert rows(db, f"SELECT digest_time FROM chats WHERE telegram_chat_id = {ALLOWED}")[0][0] == "18:00"
        passed("ordinary member cannot change settings")

        fake.message(7, ALLOWED, ADMIN, "/set_digest_time 21:00")
        await wait_for(
            lambda: rows(db, f"SELECT digest_time FROM chats WHERE telegram_chat_id = {ALLOWED}")[0][0] == "21:00",
            15, "admin setting",
        )
        passed("admin changes the digest time")

        fake.message(8, ALLOWED, MEMBER, "/digest week")
        await wait_for(lambda: any("<b>Digest — " in t for t in texts(fake, ALLOWED)), 20, "manual digest")
        digest = next(t for t in texts(fake, ALLOWED) if "<b>Digest — " in t)
        assert "бэкап" in digest.lower() and "Summary of the period" in digest
        assert next(m for m in fake.sent if "<b>Digest — " in m.get("text", "")).get("parse_mode") == "HTML"
        passed("manual digest for a period is delivered")

        fake.message(9, ALLOWED, MEMBER, "/status")
        await wait_for(lambda: any("Messages in the queue" in t for t in texts(fake, ALLOWED)), 15, "/status")
        passed("/status answers")

        # The main use: the bot is added to a group, colleagues talk, and the digests
        # go out on their own at the set time.
        fake.push("my_chat_member", {
            "chat": {"id": NEW_GROUP, "type": "supergroup", "title": "Новая группа"},
            "from": {"id": ADMIN, "is_bot": False, "first_name": "Admin"},
            "date": int(time.time()),
            "old_chat_member": {"status": "left", "user": {"id": 1, "is_bot": True, "first_name": "E2E"}},
            "new_chat_member": {"status": "member", "user": {"id": 1, "is_bot": True, "first_name": "E2E"}},
        })
        seen = workdir / "seen_chats.json"
        await wait_for(lambda: seen.exists() and "Новая группа" in seen.read_text(encoding="utf-8"), 15, "new group noted")
        passed("a new group is noticed as soon as the bot is added")

        for message_id, user_id, text in (
            (20, 3001, "Нужно обновить документацию по релизу"),
            (21, 3002, "Кто идёт на обед?"),
            (22, 3003, "Надо проверить бэкапы базы биллинга"),
        ):
            fake.message(message_id, ALLOWED, user_id, text)
        await wait_for(
            lambda: rows(db, "SELECT COUNT(DISTINCT user_id) FROM messages WHERE telegram_message_id >= 20")[0][0] == 3,
            15, "messages from three colleagues",
        )
        passed("messages from every colleague are read")

        now = datetime.now(pytz.timezone("Asia/Almaty"))
        fake.message(23, ALLOWED, ADMIN, f"/set_weekly {['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'][now.weekday()]}")
        fake.message(24, ALLOWED, ADMIN, f"/set_digest_time {now:%H:%M}")

        def scheduled(title: str) -> list[str]:
            return [t for t in texts(fake, ALLOWED) if f"<b>{title} — " in t]

        await wait_for(lambda: scheduled("Daily digest") and scheduled("Weekly digest"), 100, "scheduled digests")
        assert "документацию" in scheduled("Daily digest")[0]
        passed("daily and weekly digests arrive on schedule with the colleagues' work")

        await asyncio.sleep(65)  # one more scheduler tick
        assert len(scheduled("Daily digest")) == 1 and len(scheduled("Weekly digest")) == 1
        passed("each scheduled digest is sent once")

        # The process dies with a message still unanalyzed; it must survive the restart.
        tasks_before = rows(db, "SELECT COUNT(*) FROM tasks")[0][0]
        bot.kill()
        bot.wait(timeout=15)
        fake.message(10, ALLOWED, MEMBER, "Нужно обновить сертификаты на стейдже")
        bot = start_bot(env, log)
        await wait_for(lambda: rows(db, "SELECT COUNT(*) FROM tasks")[0][0] == tasks_before + 1, 40, "work after restart")
        passed("after a crash the bot resumes and catches up")
    finally:
        bot.kill()
        bot.wait(timeout=15)
        await runner.cleanup()

    leaked = TOKEN in log.read_text(encoding="utf-8", errors="replace")
    assert not leaked, "token found in the log"
    passed("token never appears in the log")
    print(json.dumps({"passed": len(checks)}), flush=True)
    shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
