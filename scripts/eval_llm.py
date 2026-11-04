"""
Measure how well a real model handles the scenarios in tests/scenarios.py.

Only synthetic conversations are sent, which is why this script may switch on
LLM_EXTERNAL_ALLOWED for itself. It never reads the bot's database.

    python scripts/eval_llm.py --model qwen/qwen3.8-27b --runs 2 --large 3 --out eval.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings  # noqa: E402
from app.db.models import Base, TopicItemKind, TopicItemStatus  # noqa: E402
from app.llm.base import LLMError, LLMUnavailableError  # noqa: E402
from app.llm.openai_client import OpenAILLMClient  # noqa: E402
from app.services.digest_service import DigestService  # noqa: E402
from app.utils.logging import configure_logging  # noqa: E402
from tests.helpers import BASE_TIME, analyze, feed, make_chat  # noqa: E402
from tests.scenarios import SCENARIOS, Outcome, Scenario, load_outcome  # noqa: E402

configure_logging("ERROR")


class PacedClient(OpenAILLMClient):
    """Spaces calls out to stay under a free tier's tokens-per-minute limit."""

    pause_seconds = 20.0

    async def analyze_conversation(self, messages, topics, older_topics):
        await asyncio.sleep(self.pause_seconds)
        for _ in range(5):
            try:
                return await super().analyze_conversation(messages, topics, older_topics)
            except LLMUnavailableError as e:
                # A rate limit says nothing about quality. The bot would simply retry
                # on a later tick, so the evaluation waits too instead of scoring a failure.
                if "429" not in str(e):
                    raise
                await asyncio.sleep(65)
        return await super().analyze_conversation(messages, topics, older_topics)


async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _describe(outcome: Outcome) -> list[dict]:
    return [
        {
            "title": topic.title,
            "project": topic.project,
            "items": [
                {
                    "kind": i.kind.value,
                    "status": i.status.value,
                    "text": i.text,
                    "owner": i.owner,
                    "deadline": i.deadline,
                }
                for i in topic.items
            ],
        }
        for topic in outcome.topics
    ]


async def run_scenario(scenario: Scenario, llm: OpenAILLMClient) -> dict:
    engine, factory = await _session_factory()
    started = time.monotonic()
    rejected = 0
    try:
        async with factory() as session:
            chat = await make_chat(session)
            next_id = 1
            for number, batch in enumerate(scenario.batches):
                await feed(
                    session, chat, batch, start_id=next_id, start=BASE_TIME + timedelta(hours=number)
                )
                next_id += len(batch)
                rejected += sum(r.items_rejected for r in await analyze(session, chat, llm))
            outcome = await load_outcome(session, chat.id)
            failures = scenario.check(outcome)
            described = _describe(outcome)  # before build_digest refreshes these objects
            digest = await DigestService(session, llm).build_digest(
                chat, BASE_TIME - timedelta(hours=1), BASE_TIME + timedelta(hours=6), kind="daily"
            )
            return {
                "number": scenario.number,
                "name": scenario.name,
                "passed": not failures,
                "failures": failures,
                "rejected_items": rejected,
                "seconds": round(time.monotonic() - started, 1),
                "topics": described,
                "digest": digest.text,
            }
    except LLMError as e:
        return {
            "number": scenario.number,
            "name": scenario.name,
            "passed": False,
            "failures": [f"ошибка модели: {e}"],
            "seconds": round(time.monotonic() - started, 1),
        }
    finally:
        await engine.dispose()


def large_conversation(size: int = 300) -> list[tuple]:
    """A long noisy chat with facts planted at known positions."""
    rng = random.Random(7)
    filler = [
        ("Аня", "Посмотрела логи CI, вроде всё зелёное."),
        ("Борис", "ok, thanks"),
        ("Виктор", "На стенде опять медленно открывается админка."),
        ("Дина", "Кто идёт на обед?"),
        ("Егор", "Сборка фронта стала быстрее после обновления vite."),
        ("Жанна", "😂😂"),
        ("Борис", "Созвон в 15:00 в силе?"),
        ("Аня", "Да, в силе."),
        ("Виктор", "Обновил документацию по деплою."),
        ("Егор", "Tests are passing on my branch."),
    ]
    planted = {
        40: ("Аня", "По хранению секретов предлагаю HashiCorp Vault вместо файлов на серверах."),
        41: ("Борис", "We should also compare it with AWS Secrets Manager."),
        150: ("Аня", "Решили: для секретов берём HashiCorp Vault, это подтвердил архитектор."),
        151: ("Дина", "Дина возьмёт настройку Vault на стенде, сделаю до 20 ноября."),
        220: ("Виктор", "Блокер: лицензия на сканер AD SCA истекает 12 ноября, продлить некому."),
        280: ("Егор", "Кто отвечает за бэкапы базы биллинга? Непонятно."),
    }
    return [planted.get(i) or rng.choice(filler) for i in range(size)]


def check_large(outcome: Outcome) -> list[str]:
    failures: list[str] = []
    decisions = outcome.open(TopicItemKind.decision)
    tasks = outcome.open(TopicItemKind.task)
    if not any("vault" in d.text.lower() for d in decisions):
        failures.append("решение про Vault потеряно")
    if not any("vault" in t.text.lower() and t.owner and "дин" in t.owner.lower() for t in tasks):
        failures.append("задача Дины по Vault потеряна")
    if not any("2026-11-20" == t.deadline for t in tasks):
        failures.append("срок 20 ноября потерян")
    if not any("sca" in b.text.lower() or "лиценз" in b.text.lower() for b in outcome.open(TopicItemKind.blocker)):
        failures.append("блокер про лицензию AD SCA потерян")
    if not any("бэкап" in i.text.lower() for i in outcome.items):
        failures.append("вопрос про бэкапы потерян")
    if len(decisions) > 4:
        failures.append(f"лишние решения из болтовни: {len(decisions)}")
    # Nobody ever fixes the slow admin panel in this chat, so closing it hides a live problem.
    falsely_closed = [
        i
        for i in outcome.items
        if i.kind == TopicItemKind.blocker
        and "админк" in i.text.lower()
        and i.status == TopicItemStatus.resolved
    ]
    if falsely_closed:
        failures.append("блокер про админку закрыт без оснований")
    return failures


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=settings.openai_model)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--large", type=int, default=0, help="runs of the 300-message scenario")
    parser.add_argument("--pause", type=float, default=20.0, help="seconds between model calls")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--reasoning", default=settings.llm_reasoning_effort, help="reasoning_effort to send")
    parser.add_argument("--max-tokens", type=int, default=settings.llm_max_output_tokens)
    args = parser.parse_args()

    settings.llm_reasoning_effort = args.reasoning
    settings.llm_max_output_tokens = args.max_tokens
    settings.llm_provider = "openai"
    settings.llm_external_allowed = True  # synthetic data only, see the module docstring
    settings.clarifications_enabled = False
    PacedClient.pause_seconds = args.pause
    llm = PacedClient(settings.openai_api_key, args.model, settings.openai_base_url or None)

    results: list[dict] = []
    for run in range(1, args.runs + 1):
        for scenario in SCENARIOS:
            result = await run_scenario(scenario, llm)
            result["run"] = run
            results.append(result)
            mark = "PASS" if result["passed"] else "FAIL"
            print(f"[{args.model}] run {run} #{result['number']} {mark} {result['failures']}", flush=True)
            if args.out:
                args.out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    large = Scenario("11", "Большая переписка, 300 сообщений", [large_conversation()], check_large)
    for run in range(1, args.large + 1):
        result = await run_scenario(large, llm)
        result["run"] = run
        results.append(result)
        mark = "PASS" if result["passed"] else "FAIL"
        print(f"[{args.model}] run {run} #11 {mark} {result['failures']}", flush=True)

    passed = sum(r["passed"] for r in results)
    print(f"[{args.model}] итого {passed}/{len(results)}", flush=True)
    if args.out:
        args.out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
