"""The follow-up turn sends the shell's own message, not one assembled here.

The harness stands in for a browser it cannot run, so the text it sends has to come from the
shell's `buildResumePrompt`; reassembling it from a published fragment dropped the sentence a
failing run carries, and drifted whenever the shell's wording changed.
"""

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evals"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.harness import max_auto_follow_ups, resume_prompt  # noqa: E402

COMPLETED = [{"kind": "job", "id": "j1", "label": "Galaxy job j1", "outcome": "completed"}]
FAILED = [{"kind": "invocation", "id": "i1", "label": "Workflow invocation i1", "outcome": "failed"}]

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node: the shell states its own contract")


@needs_node
def test_the_message_names_the_runs_that_settled():
    prompt = resume_prompt(COMPLETED)

    assert prompt.startswith("[Olit automatic Galaxy follow-up]")
    assert '"id": "j1"' in prompt


@needs_node
def test_a_failed_run_is_told_apart_from_a_completed_one():
    assert "still have jobs running" in resume_prompt(FAILED)
    assert "still have jobs running" not in resume_prompt(COMPLETED)


@needs_node
def test_the_cap_on_automatic_turns_is_the_shells():
    assert max_auto_follow_ups() == 3
