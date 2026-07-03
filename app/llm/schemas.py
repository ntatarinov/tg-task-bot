from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class MessageAnalysis(BaseModel):
    message_type: Literal[
        "task_signal", "requirement", "decision", "clarification", "question", "noise", "blocker"
    ] = "noise"
    contains_task: bool = False
    new_task: bool = False
    related_task_id: int | None = None
    task_title: str | None = None
    summary: str | None = None
    assignee: str | None = None
    deadline: str | None = None
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    requirements: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    should_ask_clarification: bool = False
    clarification_question: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("task_title", "summary", "assignee", mode="before")
    @classmethod
    def strip_strings(cls, v: str | None) -> str | None:
        if isinstance(v, str):
            return v.strip() or None
        return v

    # The validators below normalize "dirty" LLM output (especially from local
    # models) instead of rejecting the whole response — e.g. a literal
    # `"priority": null` becomes the default rather than a hard validation error.

    @field_validator("message_type", mode="before")
    @classmethod
    def _normalize_message_type(cls, v: object) -> object:
        allowed = {
            "task_signal", "requirement", "decision",
            "clarification", "question", "noise", "blocker",
        }
        return v if v in allowed else "noise"

    @field_validator("priority", mode="before")
    @classmethod
    def _normalize_priority(cls, v: object) -> object:
        return v if v in ("low", "medium", "high", "critical") else "medium"

    @field_validator("contains_task", "new_task", "should_ask_clarification", mode="before")
    @classmethod
    def _normalize_bool(cls, v: object) -> bool:
        return bool(v) if v is not None else False

    @field_validator("requirements", "open_questions", mode="before")
    @classmethod
    def _normalize_list(cls, v: object) -> list[str]:
        if isinstance(v, list):
            return [str(x) for x in v if x is not None]
        return []

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, v: object) -> float:
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, min(1.0, f))

    @field_validator("related_task_id", mode="before")
    @classmethod
    def _normalize_task_id(cls, v: object) -> int | None:
        try:
            return int(v)  # type: ignore[call-overload]
        except (TypeError, ValueError):
            return None


class DigestSummary(BaseModel):
    key_points: list[str] = Field(default_factory=list)
    summary: str = ""
