"""Scenario assertions, graded on loom's four decision-correctness dimensions and the rest."""

from .artifacts import _artifacts, _page
from .base import DIMENSIONS, Failure
from .container import _container
from .galaxy import _collection, _history, _invocation, _tool_output
from .record import _record
from .schema import BRANCH_FAMILIES, SCENARIO_KEYS, unsupported, validate_scenarios
from .transcript import _behavior, _budget, _chat_text, _events, _messages, _plan, _tool_calls
from .visualization import _visualization

__all__ = ["DIMENSIONS", "Failure", "SCENARIO_KEYS", "evaluate", "unsupported", "validate_scenarios"]


def evaluate(scenario, run):
    """Grade one finished run. Returns (failures, dimensions_exercised)."""
    failures = []
    exercised = set()
    a = scenario.get("assertions") or {}

    # An empty turn and a bad answer look the same to the content checks below.
    if not run.error and not (run.chat_text or "").strip():
        failures.append(
            Failure(
                "run.noModelOutput",
                f"no assistant text ({len(run.messages or [])} messages, "
                f"{len(run.events or [])} events, status {run.status_code}) -- "
                "check credentials and provider before reading this as a capability failure",
                "other",
            )
        )

    _messages(a.get("messages"), run, failures, exercised)
    _tool_calls(a.get("toolCalls"), run, failures, exercised)
    _chat_text(a.get("chatText"), run, failures, exercised)
    _plan(a.get("plan"), run, failures, exercised)
    _behavior(a.get("behavior"), run, failures, exercised)
    _events(a.get("events"), run, failures, exercised)
    _artifacts(a.get("artifacts"), run, failures, exercised)
    _tool_output(a.get("toolOutput"), run, failures, exercised)
    _invocation(a.get("invocation"), run, failures, exercised)
    _collection(a.get("collection"), run, failures, exercised)
    _visualization(a.get("visualization"), run, failures, exercised)
    _container(a.get("container"), run, failures, exercised)
    _record(a.get("record"), run, failures, exercised)
    _budget(a.get("budget"), run, failures, exercised)
    _history(a.get("history"), run, failures, exercised)
    _page(a.get("page"), run, failures, exercised)
    _any_of(a.get("anyOf"), run, failures, exercised)
    return failures, exercised


# The checker for each family an anyOf branch may assert.
_BRANCH_CHECKERS = {"artifacts": _artifacts, "visualization": _visualization, "toolCalls": _tool_calls}
assert set(_BRANCH_CHECKERS) == BRANCH_FAMILIES


def _any_of(branches, run, failures, exercised):
    """Several acceptable outcomes, where the product accepts whichever one happened.

    Each branch is graded by the same checkers a scenario would use on its own, so a
    branch passes only on observable state. The run fails when every branch fails, and
    the report names what each one wanted.
    """
    if not branches:
        return
    reasons = []
    for branch in branches:
        attempted = [key for key in branch if key in _BRANCH_CHECKERS]
        unknown = [key for key in branch if key not in _BRANCH_CHECKERS]
        if unknown:
            failures.append(
                Failure(
                    "anyOf.unknownDimension", f"{', '.join(sorted(unknown))} cannot be graded inside anyOf", "other"
                )
            )
            return
        got, seen = [], set()
        for key in attempted:
            _BRANCH_CHECKERS[key](branch[key], run, got, seen)
        if not got:
            exercised.update(seen)
            return
        reasons.append("; ".join(f.detail for f in got))
    exercised.add("behavior")
    failures.append(Failure("anyOf", "no acceptable outcome: " + " | ".join(reasons), "behavior"))
