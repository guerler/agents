"""The Galaxy state a scenario starts from, staged on the environment's clock."""

import asyncio
import json
import os
import pathlib
import runpy
import time

from . import tooltests

DEFAULT_GALAXY_WAIT_SECONDS = 600


def galaxy_wait_timeout():
    """How long a Galaxy wait may take. The environment's clock, set per run, never per scenario."""
    stated = os.environ.get("OLIT_EVAL_GALAXY_WAIT", "").strip()
    return float(stated) if stated else DEFAULT_GALAXY_WAIT_SECONDS


class GalaxyClock:
    """The wall clock spent waiting on Galaxy, a wait still in progress included.

    Each wait is bounded on its own by `galaxy_wait_timeout`; this clock keeps the waits out of
    the agent's budget and records how long they took.
    """

    def __init__(self):
        self.total = 0.0
        self._since = None

    async def wait(self, awaitable):
        self._since = time.monotonic()
        try:
            return await awaitable
        finally:
            self.total += time.monotonic() - self._since
            self._since = None

    def now(self):
        return self.total + (time.monotonic() - self._since if self._since is not None else 0.0)


async def _timed(waited, func, *args):
    """Run a Galaxy wait off the event loop, on the Galaxy clock."""
    return await waited.wait(asyncio.to_thread(func, *args))


def _stage(scenario, config):
    """The fixtures a scenario starts from, on the environment's clock."""
    wait = galaxy_wait_timeout()
    if scenario.get("dataset"):
        staged = stage_dataset(config, scenario["dataset"], wait)
        if scenario.get("workflows"):
            staged["workflow_ids"] = import_workflows(staged["galaxy"], scenario["workflows"])
            staged.setdefault("imported_workflow_ids", []).extend(staged["workflow_ids"].values())
        return staged
    if scenario.get("workflows"):
        return stage_workflows(config, scenario["workflows"], wait)
    if scenario.get("emptyHistory"):
        return stage_empty(config, scenario["emptyHistory"])
    if scenario.get("toolTest"):
        return stage_tool_test(config, scenario["toolTest"], wait)
    return None


def stage_tool_test(config, spec, timeout):
    """A history holding a tool test's inputs, plus the expectation used to grade it."""
    galaxy = tooltests.Galaxy(config["galaxy_root"], config.get("galaxy_key", ""))
    tool_id = spec["tool"]
    index = spec.get("testIndex", 0)
    history_id, ids, test = tooltests.stage(galaxy, tool_id, timeout, index)
    return {
        "galaxy": galaxy,
        "history_id": history_id,
        "dataset_ids": ids,
        "test": test,
        "tool_id": tool_id,
    }


def stage_empty(config, name):
    """An empty history, in the shape the assertions read. For acquisition scenarios."""
    galaxy = tooltests.Galaxy(config["galaxy_root"], config.get("galaxy_key", ""))
    history_id = galaxy.new_history(name or "olit eval")
    return {
        "galaxy": galaxy,
        "history_id": history_id,
        "dataset_ids": {},
        "test": None,
        "tool_id": None,
    }


def _empty_history(config, scenario):
    """A fresh, empty history for scenarios that stage no data."""
    galaxy = tooltests.Galaxy(config["galaxy_root"], config.get("galaxy_key", ""))
    return galaxy.new_history(f"olit eval: {scenario.get('id', 'scenario')}")


def import_workflows(galaxy, spec):
    """The fixture workflows, freshly imported. Returns {filename: workflow_id}.

    Nothing is looked up by name to make room: a workflow of the same name may be the user's.
    The run deletes what it imported, by the ids returned here (remove_imported_workflows).
    """
    base = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "workflows"
    definitions = {name: json.loads((base / name).read_text()) for name in spec["files"]}
    return {name: galaxy.call("api/workflows", "POST", {"workflow": d})["id"] for name, d in definitions.items()}


def remove_imported_workflows(staged):
    """Delete exactly the workflows this run imported, by id."""
    if not staged:
        return
    for workflow_id in staged.get("imported_workflow_ids", []):
        try:
            staged["galaxy"].call(f"api/workflows/{workflow_id}", "DELETE")
        except Exception:
            # Cleanup must never decide a run; a workflow left behind is named in the log.
            print(f"  could not delete imported workflow {workflow_id}", flush=True)


def stage_workflows(config, spec, timeout):
    """Import fixture workflows, so questions about the instance have a countable answer."""
    galaxy = tooltests.Galaxy(config["galaxy_root"], config.get("galaxy_key", ""))
    ids = import_workflows(galaxy, spec)
    history_id = galaxy.new_history(spec.get("history") or "olit eval")
    return {
        "galaxy": galaxy,
        "history_id": history_id,
        "dataset_ids": {},
        "workflow_ids": ids,
        "imported_workflow_ids": list(ids.values()),
        "test": None,
        "tool_id": None,
    }


def _fixture_path(name):
    path = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / name
    if not path.exists():
        gen = path.with_suffix(path.suffix + ".gen.py")
        if not gen.exists():
            gen = path.parent / (path.stem + ".gen.py")
        path.write_text(runpy.run_path(str(gen))["build"]())
    return path


def _stage_file(galaxy, history_id, spec, timeout):
    """A fixture from disk, or a pinned URL Galaxy fetches itself."""
    name = spec.get("name") or spec["file"]
    datatype = spec.get("datatype")
    if spec.get("url"):
        dataset_id = galaxy.fetch_url(history_id, spec["url"], name, datatype)
    else:
        dataset_id = galaxy.upload(history_id, name, _fixture_path(name).read_bytes(), datatype)
    landed = galaxy.await_dataset(dataset_id, timeout)
    if landed.get("state") != "ok":
        raise tooltests.ToolTestError(f"{name} landed in state {landed.get('state')}")
    if datatype and landed.get("extension") != datatype:
        raise tooltests.ToolTestError(f"{name} asked for datatype {datatype} and landed as {landed.get('extension')}")
    return dataset_id


def stage_dataset(config, spec, timeout):
    """A history holding the scenario's fixtures, as a researcher's would when they sit down.

    `file` stages one from `fixtures/`, `url` has Galaxy fetch one, and `files` stages
    several so a scenario can be about combining them.
    """
    galaxy = tooltests.Galaxy(config["galaxy_root"], config.get("galaxy_key", ""))
    wanted = spec.get("files") or [spec]
    history_id = galaxy.new_history(spec.get("history") or "olit eval")
    dataset_ids = {(f.get("name") or f["file"]): _stage_file(galaxy, history_id, f, timeout) for f in wanted}
    dataset_id = dataset_ids[wanted[0].get("name") or wanted[0]["file"]]
    staged = {
        "galaxy": galaxy,
        "history_id": history_id,
        "dataset_ids": dataset_ids,
        "test": None,
        "tool_id": None,
    }
    if spec.get("thenRuns"):
        staged["produced_dataset_id"] = _stage_run(galaxy, history_id, dataset_id, spec["thenRuns"], timeout)
    if spec.get("thenInvokes"):
        staged["invocation_id"], workflow_id = _stage_invocation(
            galaxy, history_id, dataset_id, spec["thenInvokes"], timeout
        )
        staged.setdefault("imported_workflow_ids", []).append(workflow_id)
    return staged


def _stage_run(galaxy, history_id, dataset_id, spec, timeout):
    """Leave a prior job in the history, as a researcher would find after a session.

    Staged rather than requested: asked to do something visibly impossible the agent
    refuses before running, and then nothing exercises how it handles a result that
    already exists. `expectState` is asserted, so a scenario cannot silently test a
    failure that never failed or an empty result that came back full.
    """
    inputs = dict(spec.get("inputs") or {})
    for key, value in list(inputs.items()):
        if value == "$dataset":
            inputs[key] = {"src": "hda", "id": dataset_id}
    submitted = galaxy.call(
        "api/tools", method="POST", body={"history_id": history_id, "tool_id": spec["tool_id"], "inputs": inputs}
    )
    outputs = submitted.get("outputs") or []
    if not outputs:
        raise tooltests.ToolTestError(f"{spec['tool_id']} produced no output to fail")
    produced_id = outputs[0]["id"]
    wanted = spec.get("expectState", "error")
    state = galaxy.await_dataset(produced_id, timeout).get("state")
    if state != wanted:
        raise tooltests.ToolTestError(
            f"{spec['tool_id']} was expected to land in state {wanted!r} but landed in "
            f"{state!r}; the scenario would not be testing what it claims"
        )
    return produced_id


# Job states that are not final; `paused` counts, so a paused job never reads as done.
RUNNING_STATES = ("new", "queued", "running", "paused", "upload", "setting_metadata")
# What Galaxy advances on its own; `paused` waits on a failed input or on the user.
ADVANCING_STATES = ("new", "queued", "running", "upload", "setting_metadata")


# Galaxy 26 moves a settled invocation from `scheduled` to `completed`, so pinning either
# word races the scheduler. What the staging asserts is the job outcome below.
INVOCATION_TERMINAL = ("scheduled", "completed", "cancelled", "failed")


def _await_invocation(galaxy, invocation_id, timeout):
    """The invocation once Galaxy stops driving it, and the states of its jobs."""
    for _ in range(max(1, int(timeout // 2))):
        invocation = galaxy.call(f"api/invocations/{invocation_id}")
        if invocation.get("state") in INVOCATION_TERMINAL:
            jobs = galaxy.call(f"api/invocations/{invocation_id}/jobs_summary")
            states = (jobs or {}).get("states") or {}
            # An invocation reads `scheduled` before its jobs exist, so an empty summary
            # is "not started yet", not "nothing left running".
            if states and not any(states.get(s) for s in RUNNING_STATES):
                return invocation, states
        time.sleep(2)
    return galaxy.call(f"api/invocations/{invocation_id}"), {}


def _stage_invocation(galaxy, history_id, dataset_id, spec, timeout):
    """Leave a finished workflow run in the history, as a researcher would find it.

    `expectJobState` is asserted, so a scenario cannot silently test a run that failed
    in a way it does not describe, or one that quietly succeeded.
    """
    workflow = spec["workflow"]
    workflow_id = import_workflows(galaxy, {"files": [workflow]})[workflow]
    invocation = galaxy.call(
        f"api/workflows/{workflow_id}/invocations",
        "POST",
        {
            "history_id": history_id,
            "inputs": {str(spec.get("inputStep", 0)): {"src": "hda", "id": dataset_id}},
            "inputs_by": "step_index",
        },
    )
    settled, states = _await_invocation(galaxy, invocation["id"], timeout)
    wanted_job = spec.get("expectJobState", "error")
    if settled.get("state") not in INVOCATION_TERMINAL or not states.get(wanted_job):
        raise tooltests.ToolTestError(
            f"{workflow} was expected to settle with a {wanted_job!r} job, and reached "
            f"{settled.get('state')!r} with jobs {states}; the scenario would not be testing "
            f"what it claims"
        )
    return invocation["id"], workflow_id
