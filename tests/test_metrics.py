from __future__ import annotations

from app.utils import metrics


def test_counters_increment_without_error():
    # Must not raise whether prometheus_client is present or not.
    metrics.messages_ingested_total.inc()
    metrics.tasks_created_total.inc()
    metrics.clarifications_asked_total.inc()
    metrics.digests_sent_total.inc()
    metrics.llm_errors_total.inc()


def test_render_returns_bytes():
    output = metrics.render()
    assert isinstance(output, bytes)
    assert isinstance(metrics.CONTENT_TYPE, str)
