from __future__ import annotations

import pytest

from app.llm.schemas import DigestSummary, MessageAnalysis


def test_valid_message_analysis():
    data = {
        "message_type": "task_signal",
        "contains_task": True,
        "new_task": True,
        "task_title": "Реализовать API",
        "summary": "Нужно добавить REST API для пользователей",
        "assignee": "@developer",
        "priority": "high",
        "requirements": ["Эндпоинт GET /users", "Эндпоинт POST /users"],
        "open_questions": ["Нужна ли пагинация?"],
        "should_ask_clarification": True,
        "clarification_question": "Нужна ли пагинация в ответе?",
        "confidence": 0.9,
    }
    analysis = MessageAnalysis(**data)
    assert analysis.contains_task is True
    assert analysis.priority == "high"
    assert len(analysis.requirements) == 2
    assert analysis.confidence == pytest.approx(0.9)


def test_message_analysis_defaults():
    analysis = MessageAnalysis()
    assert analysis.message_type == "noise"
    assert analysis.contains_task is False
    assert analysis.confidence == pytest.approx(0.5)
    assert analysis.requirements == []


def test_message_analysis_invalid_priority_normalized():
    # Dirty LLM output is normalized to the default, not rejected.
    assert MessageAnalysis(priority="extreme").priority == "medium"
    assert MessageAnalysis(priority=None).priority == "medium"


def test_message_analysis_confidence_clamped():
    assert MessageAnalysis(confidence=1.5).confidence == pytest.approx(1.0)
    assert MessageAnalysis(confidence=-0.1).confidence == pytest.approx(0.0)
    assert MessageAnalysis(confidence=None).confidence == pytest.approx(0.5)


def test_message_analysis_null_fields_normalized():
    # A local model returning explicit nulls must not break parsing.
    data = {
        "message_type": None,
        "contains_task": None,
        "priority": None,
        "requirements": None,
        "open_questions": None,
        "confidence": None,
        "related_task_id": "not-a-number",
    }
    a = MessageAnalysis(**data)
    assert a.message_type == "noise"
    assert a.contains_task is False
    assert a.priority == "medium"
    assert a.requirements == []
    assert a.related_task_id is None


def test_message_analysis_strips_whitespace():
    analysis = MessageAnalysis(task_title="  Задача с пробелами  ")
    assert analysis.task_title == "Задача с пробелами"


def test_message_analysis_empty_title_becomes_none():
    analysis = MessageAnalysis(task_title="   ")
    assert analysis.task_title is None


def test_valid_digest_summary():
    summary = DigestSummary(
        key_points=["Обсуждали архитектуру", "Договорились о дедлайне"],
        summary="Команда обсудила новый модуль",
    )
    assert len(summary.key_points) == 2


def test_digest_summary_defaults():
    summary = DigestSummary()
    assert summary.key_points == []
    assert summary.summary == ""


def test_extract_json_plain():
    from app.llm.openai_client import _extract_json

    assert _extract_json('{"contains_task": true}') == {"contains_task": True}


def test_extract_json_strips_markdown_fences():
    from app.llm.openai_client import _extract_json

    raw = '```json\n{"contains_task": false, "confidence": 0.3}\n```'
    assert _extract_json(raw) == {"contains_task": False, "confidence": 0.3}


def test_extract_json_ignores_surrounding_text():
    from app.llm.openai_client import _extract_json

    raw = 'Вот результат анализа:\n{"message_type": "noise"}\nНадеюсь, помог!'
    assert _extract_json(raw) == {"message_type": "noise"}
