from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.llm.schemas import ConversationAnalysis, ExtractedItem, ExtractedTopic


def test_valid_conversation_analysis():
    analysis = ConversationAnalysis(topics=[{
        "topic_id": None,
        "title": "Security Baseline",
        "project": "SAP",
        "summary": "Обсудили политику паролей",
        "message_ids": [1, 2],
        "items": [{"kind": "decision", "text": "14 символов", "quote": "решили", "source_ids": [2]}],
        "resolved_items": [{"item_id": "7", "quote": "закрыто", "source_ids": ["2"]}, {"quote": "без id"}],
    }])
    topic = analysis.topics[0]
    assert topic.title == "Security Baseline"
    assert topic.items[0].kind == "decision"
    assert [(r.item_id, r.source_ids) for r in topic.resolved_items] == [(7, [2])]


def test_ids_returned_as_strings_are_normalized():
    # qwen returned source_ids as ["1", "2"] in the live check.
    item = ExtractedItem(kind="task", text="x", source_ids=["1", 2, "[3]", "abc", None])
    assert item.source_ids == [1, 2, 3]
    assert ExtractedTopic(title="t", topic_id="5").topic_id == 5
    assert ExtractedTopic(title="t", topic_id="new").topic_id is None


def test_null_fields_are_normalized():
    # A local model returning explicit nulls must not break parsing.
    topic = ExtractedTopic(title="t", project=None, summary=None, message_ids=None, items=None)
    assert topic.items == []
    assert topic.message_ids == []


@pytest.mark.parametrize("value", ["", "  ", "null", "Не определено", "Требует уточнения", "N/A", None, 5])
def test_placeholder_answers_mean_unknown(value):
    item = ExtractedItem(kind="task", text="x", owner=value, deadline=value)
    assert item.owner is None
    assert item.deadline is None


def test_real_values_are_trimmed():
    assert ExtractedItem(kind="task", text="x", owner="  Виктор ").owner == "Виктор"


def test_one_malformed_item_does_not_cost_the_batch():
    topic = ExtractedTopic(title="t", items=[
        {"kind": "task", "text": "хорошая"},
        {"kind": "unknown_kind", "text": "плохая"},
        {"kind": "task", "text": "   "},
        {"kind": "task"},
        "не объект",
    ])
    assert [i.text for i in topic.items] == ["хорошая"]


def test_topics_without_title_are_dropped():
    analysis = ConversationAnalysis(topics=[{"title": ""}, {"summary": "x"}, "мусор", {"title": "Тема"}])
    assert [t.title for t in analysis.topics] == ["Тема"]


def test_wrong_shape_is_an_error_not_an_empty_result():
    # Treating this as "no topics" would silently lose the batch.
    with pytest.raises(ValidationError):
        ConversationAnalysis(topics="нет тем")


def test_empty_answer_is_valid():
    assert ConversationAnalysis(topics=[]).topics == []


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


def test_extract_json_skips_reasoning_block():
    from app.llm.openai_client import _extract_json

    raw = '<think>нужно вернуть {"topics": ...}</think>\n{"topics": []}'
    assert _extract_json(raw) == {"topics": []}
