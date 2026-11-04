from __future__ import annotations

import html
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from aiogram import Bot
from aiogram.types import LinkPreviewOptions
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chat, Digest, Task, TopicItem, TopicItemKind, TopicItemStatus
from app.db.repositories import DigestRepository, MessageRepository, TaskRepository, TopicRepository
from app.llm.base import BaseLLMClient, LLMError
from app.utils.logging import get_logger
from app.utils.periods import format_label
from app.utils.time import chat_timezone, ensure_aware_utc, now_utc

logger = get_logger(__name__)

NOT_DEFINED = "Не определено"
MAX_CHUNK_CHARS = 3800  # Telegram's limit is 4096; leave room for entity overhead
MAX_ITEM_CHARS = 400
MAX_SOURCE_LINKS = 3
MAX_SUMMARY_LINES = 10
MERGE_BUDGET_CHARS = 8000

TITLES: dict[str, str] = {
    "daily": "Ежедневный дайджест",
    "weekly": "Еженедельный дайджест",
    "manual": "Дайджест",
}
TOTALS_TITLES: dict[str, str] = {
    "daily": "Общие итоги дня",
    "weekly": "Общие итоги недели",
    "manual": "Общие итоги периода",
}


def message_link(telegram_chat_id: int, telegram_message_id: int) -> str | None:
    """Deep links exist only for supergroups (ids of the form -100xxxxxxxxxx)."""
    raw = str(telegram_chat_id)
    if raw.startswith("-100"):
        return f"https://t.me/c/{raw[4:]}/{telegram_message_id}"
    return None


def split_for_telegram(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """
    Split on block boundaries, then on lines. Deterministic, because a retry resumes
    from a chunk index. Tags never span lines, so every chunk stays valid HTML.
    """
    chunks: list[str] = []
    current = ""

    def push(piece: str, separator: str) -> None:
        nonlocal current
        if current and len(current) + len(separator) + len(piece) > limit:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}{separator}{piece}" if current else piece

    for block in text.split("\n\n"):
        if len(block) <= limit:
            push(block, "\n\n")
            continue
        for line in block.split("\n"):
            push(line[:limit], "\n")
    if current:
        chunks.append(current)
    return chunks


@dataclass
class _TopicView:
    title: str
    project: str | None
    fragments: list[str] = field(default_factory=list)
    items: list[TopicItem] = field(default_factory=list)

    def of_kind(self, kind: TopicItemKind) -> list[TopicItem]:
        return [i for i in self.items if i.kind == kind and i.status == TopicItemStatus.open]


class DigestService:
    def __init__(self, session: AsyncSession, llm_client: BaseLLMClient) -> None:
        self.session = session
        self.llm = llm_client
        self.task_repo = TaskRepository(session)
        self.message_repo = MessageRepository(session)
        self.digest_repo = DigestRepository(session)
        self.topic_repo = TopicRepository(session)

    async def build_digest(
        self,
        chat: Chat,
        start: datetime,
        end: datetime,
        kind: str = "manual",
        period_key: str | None = None,
    ) -> Digest:
        """
        Compose a digest from stored topics. Every fact comes from rows that already
        passed the analyzer's checks; the model only smooths "Обсудили" here. Nothing
        is marked as sent — that happens in deliver(), after Telegram accepts it.
        """
        tz = chat_timezone(chat.timezone)
        views = await self._collect_topics(chat, start, end)
        summaries = await self._summaries(views)
        replaced = await self.topic_repo.superseded_by([i.id for v in views.values() for i in v.items])
        manual_tasks = await self._manual_tasks(chat, start, end)

        blocks = [f"<b>{TITLES[kind]} — {format_label(start, end, tz)}</b>"]
        if views:
            for number, (topic_id, view) in enumerate(views.items(), 1):
                blocks.append(self._render_topic(chat, number, view, summaries[topic_id], replaced))
        else:
            blocks.append("За период рабочих тем не выявлено.")
        if manual_tasks:
            blocks.append(self._render_manual_tasks(manual_tasks))
        if views:
            blocks.append(self._render_totals(kind, list(views.values())))
        notice = await self._render_notice(chat, start, end)
        if notice:
            blocks.append(notice)

        return await self.digest_repo.create(
            chat_id=chat.id,
            period_start=start,
            period_end=end,
            text="\n\n".join(blocks),
            kind=kind,
            period_key=period_key,
        )

    async def deliver(self, bot: Bot, digest: Digest, target_chat_id: int) -> bool:
        """Send what has not been sent yet. Only a complete delivery counts as sent."""
        from app.bot.utils import safe_send_message

        chunks = split_for_telegram(digest.text)
        for index in range(digest.sent_chunks, len(chunks)):
            sent = await safe_send_message(
                bot,
                target_chat_id,
                chunks[index],
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            if sent is None:
                await self.session.commit()
                return False
            digest.sent_chunks = index + 1
            await self.session.commit()
        digest.sent_at = now_utc()
        await self.session.commit()
        return True

    async def _collect_topics(self, chat: Chat, start: datetime, end: datetime) -> dict[int, _TopicView]:
        views: dict[int, _TopicView] = {}

        def view_for(topic) -> _TopicView:
            if topic.id not in views:
                views[topic.id] = _TopicView(title=topic.title, project=topic.project)
            return views[topic.id]

        for update in await self.topic_repo.updates_in_period(chat.id, start, end):
            view_for(update.topic).fragments.append(update.summary)
        for item in await self.topic_repo.items_in_period(chat.id, start, end):
            view_for(item.topic).items.append(item)
        return views

    async def _summaries(self, views: dict[int, _TopicView]) -> dict[int, str]:
        """One fragment is used as is. Several go through the model to remove repetition,
        and if it is unavailable the digest still goes out with the fragments joined."""
        result = {topic_id: " ".join(v.fragments) or NOT_DEFINED for topic_id, v in views.items()}
        to_merge: dict[int, list[str]] = {}
        used = 0
        for topic_id, view in views.items():
            size = sum(len(f) for f in view.fragments)
            if len(view.fragments) > 1 and used + size <= MERGE_BUDGET_CHARS:
                to_merge[topic_id] = view.fragments
                used += size
        if to_merge:
            try:
                result.update(await self.llm.merge_summaries(to_merge))
            except LLMError as e:
                logger.warning("digest.merge_failed", error=str(e))
        return result

    async def _manual_tasks(self, chat: Chat, start: datetime, end: datetime) -> list[Task]:
        tasks = await self.task_repo.get_since(chat.id, start)
        return [t for t in tasks if t.source == "manual" and _before(t.created_at, end)]

    def _render_topic(
        self,
        chat: Chat,
        number: int,
        view: _TopicView,
        summary: str,
        replaced: dict[int, TopicItem],
    ) -> str:
        lines = [f"<b>Тема {number}. {html.escape(view.title)}</b>"]
        if view.project:
            lines.append(f"Проект: {html.escape(view.project)}")
        lines.append(f"Обсудили: {html.escape(summary)}")

        decisions = view.of_kind(TopicItemKind.decision)
        tasks = view.of_kind(TopicItemKind.task)

        lines.extend(self._section(chat, "Предложения (не приняты)", view.of_kind(TopicItemKind.proposal)))
        if decisions:
            lines.append("Принятые решения:")
            for item in decisions:
                line = f"• {self._item_text(chat, item)}"
                old = replaced.get(item.id)
                if old is not None:
                    line += f" (заменяет: {html.escape(_clip(old.text))})"
                lines.append(line)
        else:
            lines.append("Принятые решения: нет подтверждённых решений")

        if tasks:
            lines.append("Задачи:")
            for item in tasks:
                deadline = html.escape(item.deadline) if item.deadline else NOT_DEFINED
                lines.append(f"• {self._item_text(chat, item)} — срок: {deadline}")
        else:
            lines.append("Задачи: нет")

        owners = sorted({i.owner for i in tasks if i.owner})
        unassigned = any(not i.owner for i in tasks)
        if owners or unassigned:
            names = [html.escape(o) for o in owners]
            if unassigned:
                names.append(f"{NOT_DEFINED} (часть задач без исполнителя)")
            lines.append(f"Ответственные: {', '.join(names)}")
        else:
            lines.append(f"Ответственные: {NOT_DEFINED}")

        questions = view.of_kind(TopicItemKind.question)
        lines.extend(self._section(chat, "Блокеры и риски", view.of_kind(TopicItemKind.blocker)))
        if questions:
            lines.extend(self._section(chat, "Открытые вопросы", questions))
        else:
            lines.append("Открытые вопросы: нет")
        lines.extend(self._section(chat, "Следующие действия", view.of_kind(TopicItemKind.next_action)))
        return "\n".join(lines)

    def _section(self, chat: Chat, title: str, items: list[TopicItem]) -> list[str]:
        if not items:
            return []
        return [f"{title}:", *(f"• {self._item_text(chat, i)}" for i in items)]

    def _item_text(self, chat: Chat, item: TopicItem) -> str:
        return f"{html.escape(_clip(item.text))}{self._source_refs(chat, item)}"

    @staticmethod
    def _source_refs(chat: Chat, item: TopicItem) -> str:
        ids = sorted(s.telegram_message_id for s in item.sources)[:MAX_SOURCE_LINKS]
        refs = []
        for number, telegram_id in enumerate(ids, 1):
            link = message_link(chat.telegram_chat_id, telegram_id)
            refs.append(f'<a href="{link}">[{number}]</a>' if link else f"[#{telegram_id}]")
        return f" {' '.join(refs)}" if refs else ""

    @staticmethod
    def _render_manual_tasks(tasks: list[Task]) -> str:
        lines = ["<b>Задачи, созданные вручную</b>"]
        for task in tasks:
            owner = html.escape(task.assignee_text) if task.assignee_text else NOT_DEFINED
            lines.append(f"• {html.escape(_clip(task.title))} — ответственный: {owner}")
        return "\n".join(lines)

    @staticmethod
    def _render_totals(kind: str, views: list[_TopicView]) -> str:
        def texts(item_kind: TopicItemKind) -> list[str]:
            return [html.escape(_clip(i.text, 160)) for v in views for i in v.of_kind(item_kind)]

        owners: dict[str, list[str]] = defaultdict(list)
        for view in views:
            for item in view.of_kind(TopicItemKind.task):
                owners[item.owner or NOT_DEFINED].append(html.escape(_clip(item.text, 160)))

        lines = [f"<b>{TOTALS_TITLES[kind]}</b>"]
        lines.extend(_totals_section("Что решили", texts(TopicItemKind.decision)))
        lines.extend(_totals_section("Что необходимо сделать", texts(TopicItemKind.task)))
        if owners:
            lines.append("Кто за что отвечает:")
            for owner, tasks in sorted(owners.items()):
                lines.append(f"• {html.escape(owner)}: {'; '.join(tasks[:MAX_SUMMARY_LINES])}")
        else:
            lines.append(f"Кто за что отвечает: {NOT_DEFINED}")
        lines.extend(_totals_section("Какие вопросы остались нерешёнными", texts(TopicItemKind.question)))
        return "\n".join(lines)

    async def _render_notice(self, chat: Chat, start: datetime, end: datetime) -> str | None:
        """A digest must not look complete when it isn't."""
        pending, _ = await self.message_repo.pending_stats(chat.id)
        failed = await self.message_repo.count_failed(chat.id, start, end)
        lines = []
        if pending:
            lines.append(f"⚠️ Ещё не проанализировано сообщений: {pending}. Они войдут в следующий отчёт.")
        if failed:
            lines.append(f"⚠️ Не удалось проанализировать сообщений: {failed}.")
        return "\n".join(lines) or None


def _clip(text: str, limit: int = MAX_ITEM_CHARS) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _totals_section(title: str, entries: list[str]) -> list[str]:
    if not entries:
        return [f"{title}: нет"]
    lines = [f"{title}:", *(f"• {e}" for e in entries[:MAX_SUMMARY_LINES])]
    if len(entries) > MAX_SUMMARY_LINES:
        lines.append(f"…и ещё {len(entries) - MAX_SUMMARY_LINES}")
    return lines


def _before(moment: datetime, end: datetime) -> bool:
    return ensure_aware_utc(moment) < ensure_aware_utc(end)
