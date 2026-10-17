"""Tool-call assertions against a transcript that compaction has thinned."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "evals"))

from lib.grading.transcript import _tool_calls  # noqa: E402


class Run:
    """A run whose early tool calls are recorded but no longer in the transcript."""

    def __init__(self, tools_called, messages):
        self.tools_called = tools_called
        self.messages = messages
        self.staged = None


def call(name, arguments="{}"):
    return {"role": "assistant", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}


def grade(spec, run):
    failures, exercised = [], set()
    _tool_calls(spec, run, failures, exercised)
    return [f.detail for f in failures]


def test_a_call_compaction_dropped_still_counts():
    run = Run(["invoke_workflow", "get_page"], [call("get_page")])
    assert grade({"mustInclude": [{"name": "invoke_workflow"}]}, run) == []


def test_a_call_that_never_happened_is_still_reported():
    run = Run(["get_page"], [call("get_page")])
    assert grade({"mustInclude": [{"name": "invoke_workflow"}]}, run) == ["never called invoke_workflow"]


def test_a_forbidden_call_is_caught_even_once_compacted():
    run = Run(["run_tool", "get_page"], [call("get_page")])
    assert grade({"mustNotInclude": ["run_tool"]}, run) == ["called run_tool, which this scenario forbids"]


def test_arguments_are_matched_when_the_transcript_still_has_them():
    run = Run(["upload_file_from_url"], [call("upload_file_from_url", '{"url": "https://example.org/a.txt"}')])
    assert grade({"mustInclude": [{"name": "upload_file_from_url", "argsContains": {"url": "example.org"}}]}, run) == []


def test_unmatched_arguments_say_whether_the_call_was_even_visible():
    run = Run(["upload_file_from_url"], [])
    detail = grade({"mustInclude": [{"name": "upload_file_from_url", "argsContains": {"url": "example.org"}}]}, run)
    assert len(detail) == 1
    assert "did not survive compaction" in detail[0]
