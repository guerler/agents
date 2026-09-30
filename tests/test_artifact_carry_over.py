"""Each turn receives what earlier turns produced, the way the browser shell hands it back."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evals"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib import harness  # noqa: E402

CHART = {"kind": "vega-lite", "title": "Bar Chart"}


class Session:
    """A session that charts on its first turn and records what each turn was handed."""

    def __init__(self):
        self.handed = []

    async def prepare(self, transcripts, page_id, history_id):
        return list(transcripts)

    async def turn(self, messages, on_event=None, cancellation=None, confirmation=None, artifacts=None, watching=None):
        self.handed.append(list(artifacts or []))
        made = [CHART] if len(self.handed) == 1 else []
        return {"messages": list(messages), "new_messages": [], "artifacts": made, "steps": 1, "max_steps": 100}


def drive(monkeypatch, inputs):
    monkeypatch.setattr(harness, "_empty_history", lambda config, scenario: "history1")
    session = Session()
    scenario = {"id": "carry-over", "inputs": inputs}
    run = asyncio.run(harness._drive(scenario, session, {}, None))
    return session, run


def test_a_later_turn_is_handed_what_an_earlier_turn_produced(monkeypatch):
    session, _ = drive(monkeypatch, ["chart it", "write it into the record"])
    assert session.handed == [[], [CHART]]


def test_the_first_turn_is_handed_nothing(monkeypatch):
    session, _ = drive(monkeypatch, ["chart it"])
    assert session.handed == [[]]


def test_every_later_turn_keeps_seeing_it(monkeypatch):
    session, _ = drive(monkeypatch, ["chart it", "look at it", "write it into the record"])
    assert session.handed == [[], [CHART], [CHART]]


def test_what_was_handed_over_is_also_what_is_graded(monkeypatch):
    _, run = drive(monkeypatch, ["chart it", "write it into the record"])
    assert run.artifacts == [CHART]
