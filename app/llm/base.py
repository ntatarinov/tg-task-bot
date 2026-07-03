from __future__ import annotations

from abc import ABC, abstractmethod

from app.llm.schemas import DigestSummary, MessageAnalysis


class BaseLLMClient(ABC):
    @abstractmethod
    async def analyze_message(
        self,
        message_text: str,
        context_messages: list[dict],
        active_tasks: list[dict],
        reply_to_text: str | None = None,
    ) -> MessageAnalysis:
        """Analyze a message and extract task information."""
        ...

    @abstractmethod
    async def summarize_for_digest(
        self,
        messages: list[dict],
        tasks: list[dict],
    ) -> DigestSummary:
        """Generate a summary for the daily digest."""
        ...
