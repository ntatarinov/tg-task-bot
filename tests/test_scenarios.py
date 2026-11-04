from __future__ import annotations

from datetime import timedelta

import pytest

from tests.helpers import BASE_TIME, ScriptedLLM, analyze, feed, make_chat
from tests.scenarios import SCENARIOS, load_outcome


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.number)
@pytest.mark.asyncio
async def test_scenario_pipeline(session, scenario):
    """The pipeline stores a good model's answer correctly. See tests/scenarios.py:
    this is about our code, not about any model's understanding."""
    chat = await make_chat(session)
    llm = ScriptedLLM(*scenario.scripted)

    next_id = 1
    for number, batch in enumerate(scenario.batches):
        start = BASE_TIME + timedelta(hours=number)
        await feed(session, chat, batch, start_id=next_id, start=start)
        next_id += len(batch)
        await analyze(session, chat, llm)

    outcome = await load_outcome(session, chat.id)
    assert scenario.check(outcome) == []
