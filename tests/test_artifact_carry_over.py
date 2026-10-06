"""What every turn produced is graded, follow-ups included; the session keeps them for later turns."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evals"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib import harness  # noqa: E402

CHART = {"kind": "vega-lite", "title": "Bar Chart"}
TABLE = {"kind": "vega-lite", "title": "Follow-up"}


def result(artifacts, text="ok"):
    """A tool result carrying `artifacts`, and the answer after it, as entries."""
    entries = [{"kind": "pi.user", "model": [{"role": "user", "content": text}]}]
    if artifacts:
        call = {"type": "toolCall", "id": "c1", "name": "vega_dataset", "arguments": {}}
        entries.append({"kind": "pi.assistant", "model": [{"role": "assistant", "content": [call]}]})
        tool = {"role": "toolResult", "toolCallId": "c1", "toolName": "vega_dataset", "content": []}
        entries.append({"kind": "pi.tool-result", "model": [{**tool, "details": {"artifacts": artifacts}}]})
    entries.append(
        {"kind": "pi.assistant", "model": [{"role": "assistant", "content": [{"type": "text", "text": "done"}]}]}
    )
    return entries


class Session:
    """A session that charts on its first turn, and follows up once with another chart."""

    def __init__(self):
        self.asked = []

    async def turn(self, text):
        self.asked.append(text)
        return {"status": "done", "entries": result([CHART] if len(self.asked) == 1 else [], text)}

    async def settle(self, timeout):
        follow = result([TABLE], "[Olit automatic Galaxy follow-up]") if len(self.asked) == 1 else []
        return {"settled": [{}], "pending": 0, "entries": follow}

    async def export(self):
        return {"session": {"id": "s1"}}


def drive(monkeypatch, inputs, staged=None):
    monkeypatch.setattr(harness, "_empty_history", lambda config, scenario: "history1")
    session = Session()
    scenario = {"id": "carry-over", "inputs": inputs}
    run = asyncio.run(harness._drive(scenario, session, {"max_steps": 100}, staged))
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
