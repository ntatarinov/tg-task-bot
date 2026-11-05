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
from app.i18n import chat_language, t
from app.llm.base import BaseLLMClient, LLMError
from app.utils.logging import get_logger
from app.utils.periods import format_label
from app.utils.time import chat_timezone, ensure_aware_utc, now_utc

logger = get_logger(__name__)

MAX_CHUNK_CHARS = 3800  # Telegram's limit is 4096; leave room for entity overhead
MAX_ITEM_CHARS = 400
MAX_SOURCE_LINKS = 3
MAX_SUMMARY_LINES = 10
MERGE_BUDGET_CHARS = 8000


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
        passed the analyzer's checks; the model only smooths "Discussed" here. Nothing
        is marked as sent — that happens in deliver(), after Telegram accepts it.
        """
        tz = chat_timezone(chat.timezone)
        lang = chat_language(chat)
        views = await self._collect_topics(chat, start, end)
        summaries = await self._summaries(views, lang)
        replaced = await self.topic_repo.superseded_by([i.id for v in views.values() for i in v.items])
        manual_tasks = await self._manual_tasks(chat, start, end)

        blocks = [f"<b>{t(lang, f'digest.title.{kind}')} — {format_label(start, end, tz)}</b>"]
        if views:
            for number, (topic_id, view) in enumerate(views.items(), 1):
                blocks.append(self._render_topic(chat, number, view, summaries[topic_id], replaced))
        else:
            blocks.append(t(lang, "digest.no_topics"))
        if manual_tasks:
            blocks.append(self._render_manual_tasks(manual_tasks, lang))
        if views:
            blocks.append(self._render_totals(kind, list(views.values()), lang))
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

    async def _summaries(self, views: dict[int, _TopicView], lang: str) -> dict[int, str]:
        """One fragment is used as is. Several go through the model to remove repetition,
        and if it is unavailable the digest still goes out with the fragments joined."""
        not_defined = t(lang, "digest.not_defined")
        result = {topic_id: " ".join(v.fragments) or not_defined for topic_id, v in views.items()}
        to_merge: dict[int, list[str]] = {}
        used = 0
        for topic_id, view in views.items():
            size = sum(len(f) for f in view.fragments)
            if len(view.fragments) > 1 and used + size <= MERGE_BUDGET_CHARS:
                to_merge[topic_id] = view.fragments
                used += size
        if to_merge:
            try:
                result.update(await self.llm.merge_summaries(to_merge, language=lang))
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
        lang = chat_language(chat)
        not_defined = t(lang, "digest.not_defined")
        lines = [f"<b>{t(lang, 'digest.topic', number=number, title=html.escape(view.title))}</b>"]
        if view.project:
            lines.append(t(lang, "digest.project", project=html.escape(view.project)))
        lines.append(t(lang, "digest.discussed", summary=html.escape(summary)))

        decisions = view.of_kind(TopicItemKind.decision)
        tasks = view.of_kind(TopicItemKind.task)

        lines.extend(self._section(chat, t(lang, "digest.proposals"), view.of_kind(TopicItemKind.proposal)))
        if decisions:
            lines.append(f"{t(lang, 'digest.decisions')}:")
            for item in decisions:
                line = f"• {self._item_text(chat, item)}"
                old = replaced.get(item.id)
                if old is not None:
                    line += t(lang, "digest.replaces", old=html.escape(_clip(old.text)))
                lines.append(line)
        else:
            lines.append(t(lang, "digest.no_decisions"))

        if tasks:
            lines.append(f"{t(lang, 'digest.tasks')}:")
            for item in tasks:
                deadline = html.escape(item.deadline) if item.deadline else not_defined
                lines.append(f"• {t(lang, 'digest.task_line', item=self._item_text(chat, item), deadline=deadline)}")
        else:
            lines.append(t(lang, "digest.no_tasks"))

        owners = sorted({i.owner for i in tasks if i.owner})
        unassigned = any(not i.owner for i in tasks)
        if owners or unassigned:
            names = [html.escape(o) for o in owners]
            if unassigned:
                names.append(t(lang, "digest.some_unassigned", not_defined=not_defined))
            lines.append(t(lang, "digest.owners", names=", ".join(names)))
        else:
            lines.append(t(lang, "digest.owners", names=not_defined))

        questions = view.of_kind(TopicItemKind.question)
        lines.extend(self._section(chat, t(lang, "digest.blockers"), view.of_kind(TopicItemKind.blocker)))
        if questions:
            lines.extend(self._section(chat, t(lang, "digest.questions"), questions))
        else:
            lines.append(t(lang, "digest.no_questions"))
        lines.extend(self._section(chat, t(lang, "digest.next_steps"), view.of_kind(TopicItemKind.next_action)))
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
    def _render_manual_tasks(tasks: list[Task], lang: str) -> str:
        lines = [f"<b>{t(lang, 'digest.manual_tasks')}</b>"]
        for task in tasks:
            owner = html.escape(task.assignee_text) if task.assignee_text else t(lang, "digest.not_defined")
            lines.append(f"• {t(lang, 'digest.manual_task_line', title=html.escape(_clip(task.title)), owner=owner)}")
        return "\n".join(lines)

    @staticmethod
    def _render_totals(kind: str, views: list[_TopicView], lang: str) -> str:
        not_defined = t(lang, "digest.not_defined")

        def texts(item_kind: TopicItemKind) -> list[str]:
            return [html.escape(_clip(i.text, 160)) for v in views for i in v.of_kind(item_kind)]

        owners: dict[str, list[str]] = defaultdict(list)
        for view in views:
            for item in view.of_kind(TopicItemKind.task):
                owners[item.owner or not_defined].append(html.escape(_clip(item.text, 160)))

        lines = [f"<b>{t(lang, f'digest.totals.{kind}')}</b>"]
        lines.extend(_totals_section(t(lang, "digest.totals_decided"), texts(TopicItemKind.decision), lang))
        lines.extend(_totals_section(t(lang, "digest.totals_todo"), texts(TopicItemKind.task), lang))
        owners_title = t(lang, "digest.totals_owners")
        if owners:
            lines.append(f"{owners_title}:")
            for owner, tasks in sorted(owners.items()):
                lines.append(f"• {html.escape(owner)}: {'; '.join(tasks[:MAX_SUMMARY_LINES])}")
        else:
            lines.append(f"{owners_title}: {not_defined}")
        lines.extend(_totals_section(t(lang, "digest.totals_questions"), texts(TopicItemKind.question), lang))
        return "\n".join(lines)

    async def _render_notice(self, chat: Chat, start: datetime, end: datetime) -> str | None:
        """A digest must not look complete when it isn't."""
        pending, _ = await self.message_repo.pending_stats(chat.id)
        failed = await self.message_repo.count_failed(chat.id, start, end)
        lang = chat_language(chat)
        lines = []
        if pending:
            lines.append(t(lang, "digest.pending_notice", count=pending))
        if failed:
            lines.append(t(lang, "digest.failed_notice", count=failed))
        return "\n".join(lines) or None


def _clip(text: str, limit: int = MAX_ITEM_CHARS) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _totals_section(title: str, entries: list[str], lang: str) -> list[str]:
    if not entries:
        return [f"{title}: {t(lang, 'digest.none')}"]
    lines = [f"{title}:", *(f"• {e}" for e in entries[:MAX_SUMMARY_LINES])]
    if len(entries) > MAX_SUMMARY_LINES:
        lines.append(t(lang, "digest.more", count=len(entries) - MAX_SUMMARY_LINES))
    return lines


def _before(moment: datetime, end: datetime) -> bool:
    return ensure_aware_utc(moment) < ensure_aware_utc(end)
