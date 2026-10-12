"""A follow-up run is graded whether or not its user entry came back with the turn before it.

The session's watch can queue a follow-up while the turn is still streaming; that turn then hands
back the follow-up's user entry, and settle hands back only the run it started.
"""

import asyncio
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "evals"))

from lib.harness import GalaxyClock, TurnFailed, _settle  # noqa: E402


class Session:
    def __init__(self, entries):
        self.entries = entries

    async def settle(self, timeout):
        return {"settled": True, "pending": 0, "entries": self.entries}


def assistant(text="", stop="stop", error=None):
    message = {"role": "assistant", "content": [{"type": "text", "text": text}], "stopReason": stop}
    if error:
        message["errorMessage"] = error
    return {"kind": "pi.assistant", "model": [message]}


def settle(entries):
    events, absorbed = [], []
    asyncio.run(_settle(Session(entries), events, 1, GalaxyClock(), absorbed.extend))
    return events, absorbed


def test_a_run_without_its_user_entry_is_still_absorbed():
    reply = assistant("The upload landed with 5 columns.")
    events, absorbed = settle([reply])
    assert absorbed == [reply]
    assert events == ["work_settled", "auto_follow_up", "turn_start", "turn_end"]


def test_a_failed_follow_up_is_reported_rather_than_dropped():
    with pytest.raises(TurnFailed, match="Unknown role: final"):
        settle([assistant(stop="error", error="Hosted_vllmException - Unknown role: final")])


def test_nothing_appended_is_no_follow_up():
    assert settle([]) == (["work_settled"], [])
