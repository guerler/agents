"""What every turn produced is graded, follow-ups included; the session keeps them for later turns."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evals"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib import harness  # noqa: E402

CHART = {"kind": "vega-lite", "title": "Bar Chart"}
TABLE = {"kind": "vega-lite", "title": "Follow-up"}


class Session:
    """A session that charts on its first turn, and follows up once with another chart."""

    def __init__(self):
        self.asked = []

    async def turn(self, text, on_event=None):
        self.asked.append(text)
        made = [CHART] if len(self.asked) == 1 else []
        return {"new_transcript": [], "transcript": [], "artifacts": made, "steps": 1, "max_steps": 100}

    async def settle(self, timeout, on_event=None):
        follow = {"text": "[Olit automatic Galaxy follow-up]", "artifacts": [TABLE], "steps": 1}
        return {"settled": [{}], "pending": 0, "follow_ups": [follow] if len(self.asked) == 1 else []}

    async def export(self):
        return {"session": {"id": "s1"}}


def drive(monkeypatch, inputs, staged=None):
    monkeypatch.setattr(harness, "_empty_history", lambda config, scenario: "history1")
    session = Session()
    scenario = {"id": "carry-over", "inputs": inputs}
    run = asyncio.run(harness._drive(scenario, session, {}, staged))
    return session, run


def test_what_a_turn_produced_is_graded(monkeypatch):
    _, run = drive(monkeypatch, ["chart it", "write it into the record"])
    assert run.artifacts == [CHART]


def test_a_follow_up_is_a_turn_of_its_own_and_its_artifacts_count(monkeypatch):
    _, run = drive(monkeypatch, ["chart it"], staged={"history_id": "h1"})
    assert run.artifacts == [CHART, TABLE]
    assert run.events == ["turn_start", "turn_end", "work_settled", "auto_follow_up", "turn_start", "turn_end"]


def test_the_record_is_found_under_the_id_the_session_chose(monkeypatch):
    _, run = drive(monkeypatch, ["chart it"], staged={"history_id": "h1"})
    assert run.staged["session_id"] == "s1"
