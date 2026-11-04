from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

ItemKind = Literal["proposal", "decision", "task", "question", "blocker", "next_action"]
_ITEM_KINDS = {"proposal", "decision", "task", "question", "blocker", "next_action"}


def _to_int(value: object) -> int | None:
    # Models return ids as 5, "5" or "[5]" depending on the day.
    try:
        return int(str(value).strip(" []#m"))
    except (TypeError, ValueError):
        return None


def _to_int_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    return [i for i in (_to_int(x) for x in value) if i is not None]


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    # A model told to answer "Не определено" for unknowns may fill a field with exactly that.
    if text.lower() in ("", "null", "none", "n/a", "не определено", "требует уточнения", "unknown"):
        return None
    return text


class BatchMessage(BaseModel):
    """One message as shown to the model. `id` is local to the batch."""

    id: int
    author: str
    sent_at: str
    text: str
    reply_to: int | None = None
    reply_to_text: str | None = None
    # Looks like an instruction aimed at the model. It stays visible as context,
    # but nothing can be concluded from it alone.
    untrusted: bool = False


class TopicContextItem(BaseModel):
    id: int
    kind: str
    text: str


class TopicContext(BaseModel):
    id: int
    title: str
    project: str | None = None
    summary: str | None = None
    open_items: list[TopicContextItem] = Field(default_factory=list)


class ExtractedItem(BaseModel):
    kind: ItemKind
    text: str
    quote: str | None = None
    owner: str | None = None
    deadline: str | None = None
    source_ids: list[int] = Field(default_factory=list)
    supersedes_item_id: int | None = None

    # The validators below normalize "dirty" LLM output (especially from local
    # models) instead of rejecting the whole response.

    @field_validator("quote", "owner", "deadline", mode="before")
    @classmethod
    def _normalize_optional_text(cls, v: object) -> str | None:
        return _clean_text(v)

    @field_validator("source_ids", mode="before")
    @classmethod
    def _normalize_ids(cls, v: object) -> list[int]:
        return _to_int_list(v)

    @field_validator("supersedes_item_id", mode="before")
    @classmethod
    def _normalize_item_id(cls, v: object) -> int | None:
        return _to_int(v)


class ResolvedItem(BaseModel):
    """A claim that a known item is closed. Without evidence it is ignored."""

    item_id: int
    quote: str | None = None
    source_ids: list[int] = Field(default_factory=list)

    @field_validator("quote", mode="before")
    @classmethod
    def _normalize_quote(cls, v: object) -> str | None:
        return _clean_text(v)

    @field_validator("source_ids", mode="before")
    @classmethod
    def _normalize_ids(cls, v: object) -> list[int]:
        return _to_int_list(v)


class ExtractedTopic(BaseModel):
    topic_id: int | None = None
    title: str
    project: str | None = None
    summary: str | None = None
    message_ids: list[int] = Field(default_factory=list)
    items: list[ExtractedItem] = Field(default_factory=list)
    resolved_items: list[ResolvedItem] = Field(default_factory=list)

    @field_validator("topic_id", mode="before")
    @classmethod
    def _normalize_topic_id(cls, v: object) -> int | None:
        return _to_int(v)

    @field_validator("project", "summary", mode="before")
    @classmethod
    def _normalize_optional_text(cls, v: object) -> str | None:
        return _clean_text(v)

    @field_validator("message_ids", mode="before")
    @classmethod
    def _normalize_ids(cls, v: object) -> list[int]:
        return _to_int_list(v)

    @field_validator("resolved_items", mode="before")
    @classmethod
    def _drop_malformed_resolutions(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return [
            r
            for r in v
            if isinstance(r, ResolvedItem)
            or (isinstance(r, dict) and _to_int(r.get("item_id")) is not None)
        ]

    @field_validator("items", mode="before")
    @classmethod
    def _drop_malformed_items(cls, v: object) -> list[object]:
        """One malformed item must not cost the whole batch."""
        if not isinstance(v, list):
            return []
        return [
            item
            for item in v
            if isinstance(item, ExtractedItem)
            or (
                isinstance(item, dict)
                and item.get("kind") in _ITEM_KINDS
                and isinstance(item.get("text"), str)
                and item["text"].strip()
            )
        ]


class ConversationAnalysis(BaseModel):
    topics: list[ExtractedTopic] = Field(default_factory=list)

    @field_validator("topics", mode="before")
    @classmethod
    def _drop_malformed_topics(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            raise ValueError("topics must be a list")
        return [
            t
            for t in v
            if isinstance(t, ExtractedTopic)
            or (isinstance(t, dict) and str(t.get("title") or "").strip())
        ]


class MergedSummary(BaseModel):
    topic_id: int
    summary: str


class MergedSummaries(BaseModel):
    summaries: list[MergedSummary] = Field(default_factory=list)
