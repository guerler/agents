"""Every break must still name a line that exists, exactly once.

falsify.py edits real source to prove a scenario can fail. `Break.apply` refuses to patch when its
anchor is not unique, so a stale break stops the suite rather than testing nothing -- but it only
says so when someone runs the suite against a model and a Galaxy. Seventeen of nineteen breaks had
decayed before this test existed and the suite could not run at all, which meant no scenario had a
current proof that it can fail. Anchors are checked here instead, where a rename shows immediately.

The thirteen anchors below are the decay found on 2026-09-28; the eight after them went when the
brain moved to TypeScript on 2026-10-05, and every break still names a Python path. Re-anchoring one is not a text edit:
the break has to inject a defect that still sinks the scenarios it names, and only a real run
proves it does. Each is expected to fail until then, strictly, so a fixed anchor fails here and
the entry gets deleted.
"""

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

# The decay found on 2026-09-28, by family, file and position in falsify.BREAKS.
DECAYED = {
    "artifacts:tools:0.0",
    "record.write:galaxy_tools:1.0",
    "record.format:galaxy_tools:2.0",
    "toolCount:galaxy_tools:4.0",
    "toolInputs:galaxy_tools:6.0",
    "chatReply:agent:7.0",
    "jobState:galaxy_tools:9.0",
    "emptyResult:galaxy_tools:10.1",
    "emptyResult:galaxy_tools:10.2",
    "galaxyExecution:galaxy_tools:11.0",
    "workflowInvoke:galaxy_tools:12.0",
    "visualizationSaved:galaxy_tools:13.0",
    "visualizationArtifact:tools:14.0",
    # The rest were current until the Python brain was removed on 2026-10-05. Each needs the
    # same defect injected into src/agent/ and a run showing it still sinks its scenarios.
    "chatText:galaxy_tools:3.0",
    "planGate:prompt:5.0",
    "destructiveGate:galaxy_destructive:8.0",
    "emptyResult:galaxy_tools:10.0",
    "visualizationSpared:visualizations:15.0",
    "visualizationTracks:visualizations:16.0",
    "visualizationSettings:visualizations:17.0",
    "visualizationShape:visualizations:18.0",
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
            out.append(pytest.param(brk.family, path, find, module.ROOT / path, marks=marks, id=name))
    return out


@pytest.mark.parametrize("family,path,find,target", _anchors())
def test_the_anchor_a_break_patches_is_present_exactly_once(family, path, find, target):
    assert target.exists(), f"{family}: {path} is gone from the subject"
    found = target.read_text().count(find)
    assert found == 1, f"{family}: anchor appears {found}x in {path}; the break cannot patch it"
