from __future__ import annotations

import re
from dataclasses import dataclass, field

import pytz
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    Chat,
    Message,
    TaskStatus,
    Topic,
    TopicItem,
    TopicItemKind,
    TopicItemStatus,
)
from app.db.repositories import MessageRepository, TaskRepository, TopicRepository, UserRepository
from app.llm.base import BaseLLMClient, LLMResponseError
from app.llm.schemas import (
    BatchMessage,
    ConversationAnalysis,
    ExtractedItem,
    ExtractedTopic,
    ResolvedItem,
    TopicContext,
    TopicContextItem,
)
from app.services.clarification_service import ClarificationService, ClarificationToAsk
from app.services.task_clusterer import TaskClusterer, same_wording
from app.utils.dates import parse_deadline
from app.utils.logging import get_logger
from app.utils.time import chat_timezone, ensure_aware_utc, now_utc

logger = get_logger(__name__)

# Topic memory sent with each batch: full detail for the freshest topics, titles only
# for older ones so the model can still recognise a return to an old discussion.
KNOWN_TOPICS_LIMIT = 8
OPEN_ITEMS_PER_TOPIC = 6
OLDER_TOPICS_LIMIT = 40
REPLY_CONTEXT_CHARS = 200
SAME_ITEM_THRESHOLD = 0.85

_INJECTION_RE = re.compile(
    r"(ignore|disregard|forget|override)\b.{0,40}\b(instruction|prompt|rule)s?"
    r"|(игнорируй|проигнорируй|забудь|отмени)\w*\b.{0,40}\b(инструкци|промпт|правил)"
    r"|\b(system prompt|системн\w+ промпт)\b"
    r"|\byou are now\b|\bты теперь\b",
    re.IGNORECASE | re.DOTALL,
)
_HEDGE_RE = re.compile(
    r"предлага|может быть|возможно|наверное|как вариант|обсудим|подумаем|не реша"
    r"|\bmaybe\b|\bperhaps\b|\bi suggest\b|\bi propose\b|\bwhat if\b|\blet'?s discuss\b|\bmight\b",
    re.IGNORECASE,
)
_CONFIRM_RE = re.compile(
    r"(?<!не )(?:решили|договорились|утвердили|утверждаем|подтвержда|подтвердил|согласовали|принято)"
    r"|\b(?:agreed|decided|confirmed|approved)\b|\bgo with\b",
    re.IGNORECASE,
)


# What closing something sounds like. "не работает" and "not done yet" must not count.
_DONE_RE = re.compile(
    r"(?<!не )(?:готов|сделал|сделан|исправ|почини|решил|решен|решён|устран|закры|заверш|отмен"
    r"|обновил|настроил|выполн|продлил|работает)"
    r"|(?<!not )(?<!n't )\b(?:fixed|resolved|done|closed|finished|completed|cancell?ed|merged"
    r"|deployed|renewed)\b",
    re.IGNORECASE,
)


_CANCEL_RE = re.compile(r"отмен|cancel", re.IGNORECASE)


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _quote_is_grounded(quote: str | None, sources: list[Message]) -> bool:
    """True when the quote really occurs in the messages it claims to come from.
    Word overlap rather than strict substring: models tend to fix a typo or drop a word."""
    if not quote:
        return False
    corpus = " ".join(_normalize(m.text or "") for m in sources)
    needle = _normalize(quote)
    if needle in corpus:
        return True
    words = re.findall(r"\w+", needle)
    return bool(words) and sum(w in corpus for w in words) / len(words) >= 0.8


def _author_labels(message: Message) -> list[str]:
    if message.user is None:
        return []
    return [label for label in (message.user.username, message.user.display_name) if label]


def _ground_owner(owner: str | None, sources: list[Message]) -> str | None:
    """Keep an owner only if the sources name them or they wrote one of the sources."""
    if not owner:
        return None
    name = _normalize(owner).lstrip("@")
    # Russian names decline ("Виктору"), so compare by stem.
    stem = name[: max(4, len(name) - 2)]
    for message in sources:
        haystacks = [_normalize(message.text or ""), *(_normalize(a) for a in _author_labels(message))]
        if any(name in h or stem in h for h in haystacks):
            return owner
    return None


def _ground_deadline(phrase: str | None, sources: list[Message], tz: pytz.BaseTzInfo) -> str | None:
    """
    Resolve a deadline from the source text, anchored on the day it was written — not on
    today, or "до пятницы" would drift when old messages are analyzed later. The model's
    phrase only tells us a deadline exists; it is never trusted as a date by itself.
    """
    if not phrase:
        return None
    for message in sources:
        anchor = ensure_aware_utc(message.sent_at or now_utc()).astimezone(tz).date()
        resolved = parse_deadline(message.text, anchor)
        if resolved:
            return resolved
    # Something the parser cannot turn into a date ("до конца спринта"): keep it verbatim
    # if it is really in the text, since a vague stated deadline is still a stated one.
    corpus = " ".join(_normalize(m.text or "") for m in sources)
    return phrase[:100] if _normalize(phrase) in corpus else None


def _decision_is_confirmed(quote: str | None, sources: list[Message]) -> bool:
    """
    A decision needs someone to have actually confirmed it. The sources count, not just the
    quote: models sometimes omit the quote from a real "Решили: ...", and sometimes quote a
    status report ("логи в порядке") that no one decided anything about.
    """
    if quote and _HEDGE_RE.search(quote) and not _CONFIRM_RE.search(quote):
        return False
    return any(_CONFIRM_RE.search(text) for text in [quote or "", *(m.text or "" for m in sources)])


def _resolution_is_proven(item: TopicItem, resolved: ResolvedItem, sources: list[Message]) -> bool:
    """
    Closing an item removes it from the digest, so a wrong "resolved" silently hides a live
    problem. It takes a quote from a message other than the item's own: a blocker mentioned
    again is still a blocker. Questions need an answer rather than another question, and
    anything else needs wording that actually says it is done. A decision is never closed:
    only a new decision replaces it, otherwise the current one would vanish from the digest.
    """
    if item.kind == TopicItemKind.decision:
        return False
    own = {s.telegram_message_id for s in item.sources}
    evidence = [m for m in sources if m.telegram_message_id not in own]
    if not evidence or not _quote_is_grounded(resolved.quote, evidence):
        return False
    quote = resolved.quote or ""
    if item.kind == TopicItemKind.question:
        return not quote.rstrip().endswith("?")
    if item.kind == TopicItemKind.proposal:
        return True
    return bool(_DONE_RE.search(quote))


def _same_project(a: str | None, b: str | None) -> bool:
    return not a or not b or _normalize(a) == _normalize(b)


@dataclass
class _GroundedItem:
    kind: TopicItemKind
    text: str
    owner: str | None
    deadline: str | None
    sources: list[Message]
    supersedes_item_id: int | None


@dataclass
class AssigneeNotification:
    """Carries a DM to send an assignee when a brand-new task names them via @username."""

    telegram_user_id: int
    task_title: str
    chat_title: str


@dataclass
class BatchResult:
    processed: int = 0
    topics_touched: int = 0
    items_added: int = 0
    items_rejected: int = 0
    failed_messages: int = 0
    clarification: ClarificationToAsk | None = None
    notifications: list[AssigneeNotification] = field(default_factory=list)


class ConversationAnalyzer:
    """
    Drains a chat's message queue in batches: one model call per batch, carrying a
    compact memory of existing topics instead of the whole history.

    The model is treated as an unreliable witness. Everything it claims passes through
    deterministic checks against the source messages before it is stored.
    """

    def __init__(
        self, session: AsyncSession, llm_client: BaseLLMClient, ask_clarifications: bool = True
    ) -> None:
        self.session = session
        self.llm = llm_client
        self.ask_clarifications = ask_clarifications
        self.message_repo = MessageRepository(session)
        self.topic_repo = TopicRepository(session)
        self.task_repo = TaskRepository(session)
        self.user_repo = UserRepository(session)
        self.clusterer = TaskClusterer(session)
        self.clarification_svc = ClarificationService(session)

    async def is_due(self, chat: Chat) -> bool:
        """Wait for a worthwhile batch, but never let a quiet chat's messages sit for long."""
        count, oldest = await self.message_repo.pending_stats(chat.id)
        if count == 0:
            return False
        if count >= settings.analysis_min_batch_messages:
            return True
        waited = (now_utc() - ensure_aware_utc(oldest)).total_seconds() if oldest else 0
        return waited >= settings.analysis_max_wait_seconds

    async def analyze_next_batch(self, chat: Chat) -> BatchResult | None:
        """
        Process one batch. Commits on success, so a crash can neither lose the batch
        nor apply it twice. LLMUnavailableError propagates with nothing changed: the
        messages stay queued and their attempts are untouched, because an outage says
        nothing about the messages. Returns None when the queue is empty.
        """
        batch = await self._select_batch(chat)
        if not batch:
            return None

        batch_ids = [m.id for m in batch]
        chat_id = chat.id
        by_local_id = dict(enumerate(batch, 1))
        local_ids = {message.id: index for index, message in by_local_id.items()}
        payload = await self._build_payload(chat, batch, local_ids)
        topics = await self.topic_repo.list_recent(chat.id, OLDER_TOPICS_LIMIT)
        known = topics[:KNOWN_TOPICS_LIMIT]

        try:
            analysis = await self.llm.analyze_conversation(
                messages=payload,
                topics=[self._topic_context(t) for t in known],
                older_topics=[{"id": t.id, "title": t.title} for t in topics[KNOWN_TOPICS_LIMIT:]],
            )
        except LLMResponseError as e:
            logger.warning("analyzer.bad_answer", chat_id=chat_id, size=len(batch), reason=str(e))
            return await self._count_failure(batch_ids)

        try:
            result = await self._apply(chat, analysis, by_local_id, topics)
            await self.message_repo.mark_analysis_done(batch_ids)
            await self.session.commit()
        except Exception as e:
            # Counted like a bad answer, so a bug here cannot retry the same batch forever.
            await self.session.rollback()
            await self.session.refresh(chat)  # the rollback expired it, and callers keep using it
            logger.error("analyzer.apply_failed", chat_id=chat_id, error=type(e).__name__)
            return await self._count_failure(batch_ids)

        result.processed = len(batch_ids)
        logger.info(
            "analyzer.batch_done",
            chat_id=chat_id,
            messages=result.processed,
            topics=result.topics_touched,
            items=result.items_added,
            rejected=result.items_rejected,
        )
        return result

    async def _count_failure(self, message_ids: list[int]) -> BatchResult:
        failed = await self.message_repo.record_bad_answer(
            message_ids, settings.analysis_max_attempts
        )
        await self.session.commit()
        return BatchResult(failed_messages=failed)

    async def _select_batch(self, chat: Chat) -> list[Message]:
        candidates = await self.message_repo.get_pending_batch(
            chat.id, settings.analysis_batch_max_messages
        )
        if not candidates:
            return []
        # Each unusable answer halves the batch, which isolates whatever breaks the model.
        attempts = candidates[0].analysis_attempts or 0
        limit = max(1, settings.analysis_batch_max_messages >> attempts)
        budget = settings.analysis_batch_max_chars >> attempts

        batch: list[Message] = []
        used = 0
        for message in candidates[:limit]:
            used += len(message.text or "")
            if batch and used > budget:
                break
            batch.append(message)
        return batch

    async def _build_payload(
        self, chat: Chat, batch: list[Message], local_ids: dict[int, int]
    ) -> list[BatchMessage]:
        by_telegram_id = {m.telegram_message_id: m for m in batch}
        tz = chat_timezone(chat.timezone)
        payload: list[BatchMessage] = []
        for message in batch:
            reply_to: int | None = None
            reply_to_text: str | None = None
            if message.reply_to_message_id:
                target = by_telegram_id.get(message.reply_to_message_id)
                if target is not None:
                    reply_to = local_ids[target.id]
                else:
                    earlier = await self.message_repo.get_by_telegram_id(
                        chat.id, message.reply_to_message_id
                    )
                    if earlier is not None and earlier.text:
                        reply_to_text = earlier.text[:REPLY_CONTEXT_CHARS]
            text = message.text or ""
            payload.append(
                BatchMessage(
                    id=local_ids[message.id],
                    author=self._author(message),
                    sent_at=ensure_aware_utc(message.sent_at or now_utc())
                    .astimezone(tz)
                    .strftime("%Y-%m-%d %H:%M"),
                    text=text,
                    reply_to=reply_to,
                    reply_to_text=reply_to_text,
                    untrusted=looks_like_injection(text),
                )
            )
        return payload

    @staticmethod
    def _author(message: Message) -> str:
        labels = _author_labels(message)
        return labels[-1] if labels else "unknown"

    @staticmethod
    def _topic_context(topic: Topic) -> TopicContext:
        open_items = [i for i in topic.items if i.status == TopicItemStatus.open]
        return TopicContext(
            id=topic.id,
            title=topic.title,
            project=topic.project,
            summary=(topic.summary or "")[:300] or None,
            open_items=[
                TopicContextItem(id=i.id, kind=i.kind.value, text=i.text[:160])
                for i in open_items[-OPEN_ITEMS_PER_TOPIC:]
            ],
        )

    async def _apply(
        self,
        chat: Chat,
        analysis: ConversationAnalysis,
        by_local_id: dict[int, Message],
        topics: list[Topic],
    ) -> BatchResult:
        result = BatchResult()
        tz = chat_timezone(chat.timezone)
        unassigned_task = None

        for extracted in analysis.topics:
            sources = [by_local_id[i] for i in extracted.message_ids if i in by_local_id]
            grounded = [self._ground(item, by_local_id, tz) for item in extracted.items]
            result.items_rejected += grounded.count(None)
            items = [g for g in grounded if g is not None]
            if not sources:
                sources = [m for g in items for m in g.sources]
            trusted = [m for m in sources if not looks_like_injection(m.text or "")]
            if not trusted:
                # Nothing here rests on a message we can trust, so it never becomes a topic.
                result.items_rejected += len(items)
                continue

            topic = await self._resolve_topic(chat, extracted, topics)
            occurred_at = max(ensure_aware_utc(m.sent_at or now_utc()) for m in trusted)
            await self.topic_repo.link_messages(topic.id, [m.id for m in trusted])
            if extracted.summary:
                topic.summary = extracted.summary
                await self.topic_repo.add_update(topic.id, extracted.summary, occurred_at)
            if not topic.last_message_at or ensure_aware_utc(topic.last_message_at) < occurred_at:
                topic.last_message_at = occurred_at
            result.topics_touched += 1

            for g in items:
                item, is_new = await self._store_item(topic, g, topics)
                if not is_new:
                    continue
                result.items_added += 1
                if item.kind == TopicItemKind.task:
                    if await self._carry_over_task(item, g, topics):
                        continue
                    task, created = await self.clusterer.find_or_create_task(
                        chat=chat,
                        title=item.text,
                        message_ids=[m.id for m in g.sources],
                        assignee=item.owner,
                        deadline=item.deadline,
                        reporter_text=self._reporter(g.sources[0]),
                    )
                    item.task_id = task.id
                    if created:
                        notification = await self._assignee_notification(chat, task)
                        if notification:
                            result.notifications.append(notification)
                        if not task.assignee_text and unassigned_task is None:
                            unassigned_task = task

            await self._resolve_items(extracted, topics, topic, by_local_id)

        if unassigned_task is not None and settings.clarifications_enabled and self.ask_clarifications:
            result.clarification = await self._ask_owner(chat, unassigned_task)
        return result

    def _ground(
        self, item: ExtractedItem, by_local_id: dict[int, Message], tz: pytz.BaseTzInfo
    ) -> _GroundedItem | None:
        sources = [by_local_id[i] for i in item.source_ids if i in by_local_id]
        trusted = [m for m in sources if not looks_like_injection(m.text or "")]
        if not trusted:
            logger.info("analyzer.item_rejected", reason="no_trusted_source", kind=item.kind)
            return None

        kind = TopicItemKind(item.kind)
        if kind == TopicItemKind.decision and not _decision_is_confirmed(item.quote, trusted):
            # "Not considered final" is the safe reading of a decision we cannot verify.
            logger.info("analyzer.decision_downgraded")
            kind = TopicItemKind.proposal

        return _GroundedItem(
            kind=kind,
            text=item.text.strip(),
            owner=_ground_owner(item.owner, trusted),
            deadline=_ground_deadline(item.deadline, trusted, tz),
            sources=trusted,
            supersedes_item_id=item.supersedes_item_id,
        )

    async def _resolve_topic(
        self, chat: Chat, extracted: ExtractedTopic, topics: list[Topic]
    ) -> Topic:
        topic = next((t for t in topics if t.id == extracted.topic_id), None)
        if topic is not None and not _same_project(topic.project, extracted.project):
            # The model linked two different projects; keeping them apart wins.
            logger.info("analyzer.topic_link_rejected", reason="project_mismatch", topic_id=topic.id)
            topic = None
        if topic is None:
            title = _normalize(extracted.title)
            topic = next(
                (
                    t
                    for t in topics
                    if _normalize(t.title) == title and _same_project(t.project, extracted.project)
                ),
                None,
            )
        if topic is None:
            topic = await self.topic_repo.create(chat.id, extracted.title.strip(), extracted.project)
            topics.insert(0, topic)
        elif extracted.project and not topic.project:
            topic.project = extracted.project
        return topic

    async def _store_item(
        self, topic: Topic, g: _GroundedItem, topics: list[Topic]
    ) -> tuple[TopicItem, bool]:
        telegram_ids = [m.telegram_message_id for m in g.sources]
        duplicate = next(
            (
                i
                for i in topic.items
                if i.kind == g.kind
                and i.status == TopicItemStatus.open
                and same_wording(i.text, g.text, SAME_ITEM_THRESHOLD)
            ),
            None,
        )
        # An explicit replacement is news even when it reads like what it replaces.
        if duplicate is not None and g.supersedes_item_id is None:
            duplicate.owner = duplicate.owner or g.owner
            duplicate.deadline = duplicate.deadline or g.deadline
            await self.topic_repo.add_sources(duplicate.id, telegram_ids)
            return duplicate, False

        occurred_at = max(ensure_aware_utc(m.sent_at or now_utc()) for m in g.sources)
        item = await self.topic_repo.add_item(
            topic.id, g.kind, g.text, owner=g.owner, deadline=g.deadline, occurred_at=occurred_at
        )
        await self.topic_repo.add_sources(item.id, telegram_ids)
        topic.items.append(item)

        if g.supersedes_item_id is not None:
            replaced = self._find_item(topics, g.supersedes_item_id)
            if replaced is not None and replaced.kind == g.kind and replaced.id != item.id:
                replaced.status = TopicItemStatus.superseded
                replaced.superseded_by_id = item.id
        return item, True

    async def _carry_over_task(self, item: TopicItem, g: _GroundedItem, topics: list[Topic]) -> bool:
        """A new owner or deadline for an existing task updates that task, not a copy of it."""
        if g.supersedes_item_id is None:
            return False
        replaced = self._find_item(topics, g.supersedes_item_id)
        if replaced is None or replaced.superseded_by_id != item.id or replaced.task_id is None:
            return False
        task = await self.task_repo.get_by_id(replaced.task_id)
        if task is None:
            return False
        item.owner = item.owner or replaced.owner
        item.deadline = item.deadline or replaced.deadline
        await self.task_repo.update(task, assignee_text=item.owner, deadline=item.deadline)
        for message in g.sources:
            await self.clusterer.task_msg_repo.link(task.id, message.id)
        item.task_id = task.id
        return True

    async def _resolve_items(
        self,
        extracted: ExtractedTopic,
        topics: list[Topic],
        topic: Topic,
        by_local_id: dict[int, Message],
    ) -> None:
        for resolved in extracted.resolved_items:
            item = self._find_item(topics, resolved.item_id)
            if item is None or item.topic_id != topic.id or item.status != TopicItemStatus.open:
                continue
            sources = [by_local_id[i] for i in resolved.source_ids if i in by_local_id]
            trusted = [m for m in sources if not looks_like_injection(m.text or "")]
            if _resolution_is_proven(item, resolved, trusted):
                item.status = TopicItemStatus.resolved
                await self._close_task(item, resolved)
            else:
                logger.info("analyzer.resolution_rejected", item_id=item.id, kind=item.kind.value)

    async def _close_task(self, item: TopicItem, resolved: ResolvedItem) -> None:
        task = await self.task_repo.get_by_id(item.task_id) if item.task_id else None
        if task is not None:
            cancelled = _CANCEL_RE.search(resolved.quote or "")
            await self.task_repo.set_status(
                task, TaskStatus.cancelled if cancelled else TaskStatus.done
            )

    @staticmethod
    def _find_item(topics: list[Topic], item_id: int) -> TopicItem | None:
        # Searching only what this chat was shown keeps a hallucinated id from touching
        # another chat's data.
        return next((i for t in topics for i in t.items if i.id == item_id), None)

    @staticmethod
    def _reporter(message: Message) -> str | None:
        if message.user is None:
            return None
        return f"@{message.user.username}" if message.user.username else message.user.display_name

    async def _assignee_notification(self, chat: Chat, task) -> AssigneeNotification | None:
        if not task.assignee_text or not task.assignee_text.startswith("@"):
            return None
        user = await self.user_repo.get_by_username(task.assignee_text)
        if user is None:
            return None
        return AssigneeNotification(
            telegram_user_id=user.telegram_user_id,
            task_title=task.title,
            chat_title=chat.title or "рабочий чат",
        )

    async def _ask_owner(self, chat: Chat, task) -> ClarificationToAsk | None:
        """The question is a fixed template: model output never decides what the bot says."""
        if not await self.clarification_svc.can_ask(chat.id):
            return None
        question = f"Кто возьмёт задачу «{task.title[:200]}»?"
        clarification = await self.clarification_svc.create(task=task, question=question)
        await self.task_repo.update(task, status=TaskStatus.needs_clarification)
        return ClarificationToAsk(clarification_id=clarification.id, question=question)
