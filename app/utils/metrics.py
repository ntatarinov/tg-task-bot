from __future__ import annotations

"""
Prometheus metrics with a graceful fallback: if prometheus_client is not
installed, every counter becomes a no-op and /metrics returns an empty body,
so the bot keeps running without the dependency.
"""

try:
    import prometheus_client as _pc

    _ENABLED = True
except Exception:  # pragma: no cover - exercised only when the lib is absent
    _pc = None  # type: ignore[assignment]
    _ENABLED = False


class _NoopCounter:
    def inc(self, amount: float = 1.0) -> None:
        return None


CONTENT_TYPE: str = _pc.CONTENT_TYPE_LATEST if _ENABLED else "text/plain; charset=utf-8"


def _counter(name: str, documentation: str) -> _NoopCounter:
    if _ENABLED:
        return _pc.Counter(name, documentation)  # type: ignore[return-value]
    return _NoopCounter()


messages_ingested_total = _counter(
    "tg_messages_ingested_total", "Total text messages saved from chats"
)
tasks_created_total = _counter(
    "tg_tasks_created_total", "Total tasks created by the analyzer"
)
clarifications_asked_total = _counter(
    "tg_clarifications_asked_total", "Total clarification questions created"
)
digests_sent_total = _counter(
    "tg_digests_sent_total", "Total digests successfully sent to chats"
)
llm_errors_total = _counter(
    "tg_llm_errors_total", "Total LLM calls that failed and fell back"
)


def render() -> bytes:
    if _ENABLED:
        return _pc.generate_latest()
    return b""
