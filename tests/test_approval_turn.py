"""The approval follow-up fires only for a run holding an unapproved plan."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evals"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.harness import _awaiting_approval  # noqa: E402

PLAN = """## Plan A: count the medals [galaxy]

- [ ] 1. **Filter** — keep gold rows
- [ ] 2. **Group** — count by country
"""

STARTED = PLAN.replace("- [ ] 1.", "- [x] 1.")


def reply(text):
    return [{"role": "assistant", "content": text}]


def test_a_plan_with_every_step_pending_is_awaiting_approval():
    assert _awaiting_approval(reply(PLAN))


def test_a_plan_already_under_way_is_not_awaiting_approval():
    assert not _awaiting_approval(reply(STARTED))


def test_prose_asking_to_proceed_is_not_a_plan():
    assert not _awaiting_approval(reply("Shall I go ahead and run this on Galaxy?"))


def test_a_finished_answer_is_not_awaiting_approval():
    assert not _awaiting_approval(reply("I ran Filter and Group; the USA leads with 1596."))


def test_no_reply_is_not_awaiting_approval():
    assert not _awaiting_approval([])
    assert not _awaiting_approval(None)


def test_only_assistant_text_counts():
    assert not _awaiting_approval([{"role": "user", "content": PLAN}])
