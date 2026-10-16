"""Every break must still name a line that exists, exactly once.

falsify.py edits real source to prove a scenario can fail. `Break.apply` refuses to patch when its
anchor is not unique, so a stale break stops the suite rather than testing nothing -- but it only
says so when someone runs the suite against a model and a Galaxy. Anchors are checked here
instead, where a rename shows immediately.

On 2026-10-05 the breaks moved from the Python brain to src/agent/. Four were retired rather than
moved, because the code they broke now belongs to galaxy-ops (falsify.RETIRED says why for each).
An anchor that exists is not yet a proof: only a falsify run shows a break still sinks its
scenarios, and PROVEN records the ones that have been shown to on the TypeScript agent.
"""

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Anchors that have stopped matching and await a validated re-anchor; strictly expected to fail.
DECAYED: set[str] = set()

# Families a falsify run has shown to sink their scenarios on the TypeScript agent, each against a
# scenario that passed without the break (gemini-3.1-flash-lite, 2026-10-05/06). The rest are
# anchored, typecheck when applied, and fail olit's unit tests, but await a live run.
PROVEN = {
    "chatReply",
    "chatText",
    "destructiveGate",
    "jobState",
    "record.write",
    "toolInputs",
    "visualizationArtifact",
    "visualizationSaved",
    "visualizationSpared",
}


def _falsify():
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("falsify", ROOT / "evals" / "falsify.py")
    module = importlib.util.module_from_spec(spec)
    argv, sys.argv = sys.argv, ["falsify.py"]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = argv
    return module


def _anchors():
    module = _falsify()
    out = []
    for index, brk in enumerate(module.BREAKS):
        for position, (path, find, _) in enumerate(brk.edits):
            name = f"{brk.family}:{pathlib.Path(path).stem}:{index}.{position}"
            decayed = pytest.mark.xfail(strict=True, reason="anchor decayed; needs a validated re-anchor")
            marks = [decayed] if name in DECAYED else []
            out.append(pytest.param(brk.family, path, find, module.subject.source(path), marks=marks, id=name))
    return out


@pytest.mark.parametrize("family,path,find,target", _anchors())
def test_the_anchor_a_break_patches_is_present_exactly_once(family, path, find, target):
    assert target.exists(), f"{family}: {path} is gone from the subject"
    found = target.read_text().count(find)
    assert found == 1, f"{family}: anchor appears {found}x in {path}; the break cannot patch it"


def test_a_retired_break_is_documented_and_gone_from_the_suite():
    module = _falsify()
    active = {brk.family for brk in module.BREAKS}
    assert set(module.RETIRED).isdisjoint(active)
    assert all(reason.strip() for reason in module.RETIRED.values())


def test_every_break_targets_the_typescript_agent():
    for brk in _falsify().BREAKS:
        for path, _, _ in brk.edits:
            assert path.startswith("src/agent/"), f"{brk.family} still names {path}"


def test_every_proven_family_is_a_live_break():
    assert PROVEN <= {brk.family for brk in _falsify().BREAKS}


def test_every_break_names_scenarios_that_exist():
    scenarios = {path.name for path in (ROOT / "evals" / "scenarios").iterdir()}
    missing = {brk.family: [name for name in brk.scenarios if name not in scenarios] for brk in _falsify().BREAKS}
    assert {family: names for family, names in missing.items() if names} == {}
