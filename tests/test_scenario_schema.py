"""A key no checker reads is an assertion that silently passes; the loader must name it."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "evals"))

from lib.grading import unsupported, validate_scenarios  # noqa: E402
from lib.harness import load_scenarios  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parents[1] / "evals" / "scenarios"


def test_unread_families_and_keys_are_named():
    cases = [
        ({"plan": {"exists": True}, "budget": {"maxSteps": 9}}, []),
        ({"activity": {"mustInclude": []}}, ["activity"]),
        ({"plan": {"exists": True, "source": "notebook"}}, ["plan.source"]),
        (
            {"toolCalls": {"mustInclude": [{"name": "x", "detailsContains": {}}]}},
            ["toolCalls.mustInclude.detailsContains"],
        ),
        ({"anyOf": [{"toolCalls": {"mustInclude": []}}, {"notebook": {}}]}, ["anyOf.notebook"]),
        ({"anyOf": [{"artifacts": {"mustInclude": [], "nope": 1}}]}, ["artifacts.nope"]),
    ]
    for assertions, expected in cases:
        assert unsupported(assertions) == expected, assertions


def test_every_committed_scenario_is_fully_graded():
    assert validate_scenarios(load_scenarios(str(HERE))) == []
