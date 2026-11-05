from __future__ import annotations

from abc import ABC, abstractmethod

from app.llm.schemas import BatchMessage, ConversationAnalysis, TopicContext


class LLMError(Exception):
    """Base class. Every failure surfaces as one of these and is never mistaken for an empty result."""


class LLMUnavailableError(LLMError):
    """The model could not be reached or refused. The same batch is safe to retry as is."""


class LLMResponseError(LLMError):
    """The model answered with something unusable. A smaller batch may succeed."""


class BaseLLMClient(ABC):
    @abstractmethod
    async def analyze_conversation(
        self,
        messages: list[BatchMessage],
        topics: list[TopicContext],
        older_topics: list[dict],
        language: str = "en",
    ) -> ConversationAnalysis:
        """Group a batch of messages into topics and extract what was said in them.
        `language` is the language of the text the model writes, not of the messages."""
        ...

    @abstractmethod
    async def merge_summaries(self, fragments: dict[int, list[str]], language: str = "en") -> dict[int, str]:
        """Rewrite each topic's summary fragments as one text, adding nothing new."""
        ...
