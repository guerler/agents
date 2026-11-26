import gzip
import json

from lib.grading import validate_scenarios
from lib.record import usage
from lib.side_effects import compare


def _record(path, exchanges):
    with gzip.open(path, "wt", encoding="utf-8") as sink:
        for exchange in exchanges:
            sink.write(json.dumps(exchange) + "\n")
    return str(path)


def test_usage_sums_streamed_and_plain_replies_and_the_stated_cost(tmp_path):
    last = {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}
    streamed = f'data: {{"choices": []}}\n\ndata: {json.dumps(last)}\n\ndata: [DONE]'
    plain = {"usage": {"prompt_tokens": 5, "completion_tokens": 1, "cost": 0.25}}
    path = _record(
        tmp_path / "r.jsonl.gz",
        [
            {"status": 200, "response": streamed},
            {"status": 200, "response": plain},
            {"status": 503, "response": "busy"},
        ],
    )
    assert usage(path) == {
        "exchanges": 3,
        "failedExchanges": 1,
        "promptTokens": 15,
        "completionTokens": 3,
        "cost": 0.25,
    }


def test_usage_without_a_record_is_unknown():
    assert usage(None) is None


def test_compare_reports_changed_fields_and_added_items_without_judging_them():
    before = {"a": {"hid": 1, "name": "x", "visible": True, "deleted": False, "extension": "bed", "tags": []}}
    after = {
        "a": {"hid": 1, "name": "x", "visible": False, "deleted": False, "extension": "bed", "tags": []},
        "c": {"hid": 2, "history_content_type": "dataset_collection", "visible": True},
    }
    assert compare(before, after) == {
        "changed": [{"hid": 1, "name": "x", "field": "visible", "before": True, "after": False}],
        "added": {"datasets": 0, "collections": 1, "hidden": 0},
    }


def test_compare_without_a_snapshot_is_unknown():
    assert compare(None, {}) is None


def test_evaluation_fields_are_validated():
    scenario = {
        "id": "s",
        "name": "s",
        "inputs": ["hi"],
        "targets": ["some_tool"],
        "control": "some_tool",
        "reachableWithout": {"collection.tagged": {"via": "galaxy"}, "*": {"via": "maybe"}},
    }
    problems = validate_scenarios([scenario])
    assert "s: control must be a list of tool names" in problems
    assert any("reachableWithout * needs via" in p for p in problems)
    assert not any("collection.tagged" in p for p in problems)
