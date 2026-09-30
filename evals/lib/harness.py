"""Drive the agent's brain headlessly for one scenario."""

import asyncio
import json
import logging
import os
import pathlib
import runpy
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
import subject  # noqa: E402

subject.importable()

from olit.runtime import Session  # noqa: E402
from olit.substrate.llm import REGISTRY  # noqa: E402

from . import tooltests  # noqa: E402
from .plan import parse_latest_plan  # noqa: E402
from .record import recording  # noqa: E402

# The eval substrate is a real Galaxy, deliberately.


class RunResult:
    def __init__(
        self,
        messages,
        logs,
        tools_called,
        error=None,
        status_code=None,
        events=None,
        artifacts=None,
        exhausted=False,
        staged=None,
        steps=0,
        max_steps=0,
        refused=None,
        config=None,
    ):
        self.messages = messages
        # What the session ran under, so a caller can find the page it opened.
        self.config = config
        # Charts and diagrams routed to the shell, never into the model's context.
        self.artifacts = artifacts or []
        # The turn hit MAX_STEPS. The shell says so; grading must not read it as silence.
        self.exhausted = exhausted
        self.steps = steps
        self.max_steps = max_steps
        self.refused = refused or []
        # A tool-test scenario's staged history and the expectation it is graded against.
        self.staged = staged
        self.logs = logs
        self.tools_called = tools_called
        # Every event the brain emitted, plus turn boundaries synthesised by the harness.
        self.events = events or []
        self.error = error
        # The provider's HTTP status, preserved so grading never sniffs the message.
        self.status_code = status_code

    @property
    def chat_text(self):
        """Everything the shell renders, in order, including the `finish` summary."""
        parts = []
        for m in self.messages:
            if not isinstance(m, dict) or not m.get("content"):
                continue
            if m.get("role") == "assistant":
                parts.append(m["content"])
            elif m.get("role") == "tool" and m.get("name") == "finish":
                parts.append(m["content"])
        return "\n\n".join(parts)


def declared_base(model):
    """The endpoint a model entry names, with an `${ENV}` reference resolved."""
    base = model.get("baseUrl") or ""
    if base.startswith("${") and base.endswith("}"):
        base = os.environ.get(base[2:-1], "")
    return base


def upstream(model):
    """Where this model's completions actually go, which is what a recorder stands in for."""
    provider = REGISTRY.get(model.get("provider"))
    return declared_base(model) or (provider.base_url if provider else None)


def build_config(model, capabilities=None, base_url=None):
    """Resolve through the brain's provider registry, so evals and the app agree."""
    # Shared scenarios carry loom's restricted surface; others get the full one.
    capabilities = capabilities or os.environ.get("OLIT_EVAL_CAPABILITIES", "llm,local,read,write")
    base = base_url or declared_base(model)
    config = {
        "galaxy_root": "http://stub.invalid/",
        "ai_provider": model.get("provider"),
        "ai_model": model["model"],
        # Write is granted, or "did not execute" would assert about an unadvertised tool.
        "capabilities": capabilities.split(","),
    }
    if base:
        config["ai_base_url"] = base.rstrip("/")
    max_steps = os.environ.get("OLIT_EVAL_MAX_STEPS", "").strip()
    if max_steps:
        config["max_steps"] = int(max_steps)
    galaxy_root = os.environ.get("GALAXY_URL", "").strip()
    config["galaxy_root"] = galaxy_root.rstrip("/") + "/"
    config["galaxy_key"] = os.environ.get("GALAXY_API_KEY", "")
    key = _api_key(model)
    if key:
        config["ai_api_key"] = key
    return config


def _api_key(model):
    """The registry names the env var; envRequires is the fallback for custom entries."""
    provider = REGISTRY.get(model.get("provider"))
    if provider and provider.auth_env:
        return os.environ.get(provider.auth_env, "")
    for name in model.get("envRequires", []):
        if name.endswith("_KEY"):
            return os.environ.get(name, "")
    return ""


def label_record_page(config, scenario_id, model_id):
    """Title the session's record page after the scenario, and say where to read it.

    The agent already saved it: `notebook_resume` creates a Galaxy page under a slug derived
    from the session id. Untitled that way it is a uuid among hundreds, so this names it.
    """
    slug = f"olit-{config.get('session_id')}"
    galaxy = tooltests.Galaxy(config["galaxy_root"], config.get("galaxy_key", ""))
    try:
        pages = galaxy.call(f"api/pages?search=slug:{slug}") or []
        page = next((p for p in pages if p.get("slug") == slug and not p.get("deleted")), None)
        if not page:
            return None
        title = f"eval: {scenario_id} [{model_id}]"
        galaxy.call(f"api/pages/{page['id']}", method="PUT", body={"title": title})
        return f"{config['galaxy_root']}u/{page.get('username')}/p/{slug}"
    except Exception:
        # Reporting must never decide a run; the page is saved either way.
        return None


# The shell's session document, whose shape `src/session-document.ts` owns.
SESSION_SCHEMA = 1
# The block the brain refreshes each turn, stale the moment it is stored.
RECORD_MARKER = "<!-- olit:record -->"


def storable_messages(messages):
    """`storableMessages`: the seed and the refreshed record block are regenerated on open."""
    return [
        m
        for i, m in enumerate(messages or [])
        if not (i == 0 and m.get("role") == "system")
        and not (m.get("role") == "system" and RECORD_MARKER in (m.get("content") or ""))
    ]


def save_session(config, scenario_id, model_id, run):
    """Save the run as an Olit visualization, so it reopens in the UI as a session.

    The agent saves its record page itself; the session document is the shell's job, and an
    eval has no shell. Without this a run is reviewable only as JSONL.
    """
    root = config["galaxy_root"]
    galaxy = tooltests.Galaxy(root, config.get("galaxy_key", ""))
    now = datetime.now(timezone.utc).isoformat()
    title = f"eval: {scenario_id} [{model_id}]"
    document = {
        "olit_session": SESSION_SCHEMA,
        "session": {
            "id": config["session_id"],
            "title": title,
            "createdAt": now,
            "updatedAt": now,
            "turn": len(storable_messages(run.messages)),
            "models": [{"provider": model_id, "firstTurn": 1, "lastTurn": 1}],
            "usage": {"input": 0, "output": 0, "cost": None},
        },
        "messages": storable_messages(run.messages),
        "artifacts": run.artifacts or [],
        "toolErrors": list(run.refused or []),
    }
    staged = getattr(run, "staged", None) or {}
    if staged.get("history_id"):
        document["history_id"] = staged["history_id"]
    try:
        created = galaxy.call(
            "api/visualizations", method="POST", body={"title": title, "config": document, "type": "olit"}
        )
        return f"{root}visualizations/display?visualization=olit&visualization_id={created['id']}"
    except Exception:
        # Reporting must never decide a run.
        return None


async def _run(scenario, model, base_url=None):
    config = build_config(model, scenario.get("capabilities"), base_url)
    # A run is a session, and a session owns its record page; the browser shell supplies
    # this from the saved document.
    config["session_id"] = str(uuid.uuid4())
    session = await Session(config).init()
    try:
        return await _drive(scenario, session, config)
    finally:
        # Outside the browser a session holds a node process for galaxy-ops; a suite is
        # hundreds of sessions in one interpreter, so each one has to give it back.
        await session.substrate.close()


async def _drive(scenario, session, config):
    budget = scenario_timeout(scenario)
    staged = None
    if scenario.get("dataset"):
        staged = await asyncio.to_thread(stage_dataset, config, scenario["dataset"], budget)
        if scenario.get("workflows"):
            staged["workflow_ids"] = await asyncio.to_thread(import_workflows, staged["galaxy"], scenario["workflows"])
    elif scenario.get("workflows"):
        staged = await asyncio.to_thread(stage_workflows, config, scenario["workflows"], budget)
    elif scenario.get("emptyHistory"):
        staged = await asyncio.to_thread(stage_empty, config, scenario["emptyHistory"])
    elif scenario.get("toolTest"):
        staged = await asyncio.to_thread(stage_tool_test, config, scenario["toolTest"], budget)

    bound_history = staged["history_id"] if staged else _empty_history(config, scenario)
    # No record page is staged: the session makes its own, as it does in the browser.
    transcripts = await session.prepare(
        [{"role": "system", "content": scenario.get("systemPrompt", "You are olit.")}], None, bound_history
    )

    tools_called = []
    messages = transcripts
    logs = []
    events = []
    refused = []
    # Graded at the end, and handed back to each turn so a page can place an earlier chart.
    artifacts = []
    exhausted = False
    # The most any single turn needed, which is what the cap actually constrains.
    steps = 0
    cap = 0
    # `restartAfter` models closing the browser and coming back with nothing stored.
    restart_after = scenario.get("restartAfter")
    opening = list(messages)
    graded = list(messages)
    approval = scenario.get("approveWhenPlanned")
    for index, turn in enumerate(scenario["inputs"], start=1):
        if restart_after and index == restart_after + 1:
            messages = list(opening)
            events.append("session_restart")
        messages = [*messages, {"role": "user", "content": turn}]
        graded.append({"role": "user", "content": turn})
        events.append("turn_start")
        result = await session.turn(messages, lambda ev: _note(ev, tools_called, events, refused), artifacts=artifacts)
        messages = result.get("messages") or messages
        graded.extend(result.get("new_messages") or [])
        logs.extend(result.get("logs") or [])
        artifacts.extend(result.get("artifacts") or [])
        exhausted = exhausted or bool(result.get("exhausted"))
        steps = max(steps, int(result.get("steps") or 0))
        cap = int(result.get("max_steps") or 0) or cap
        # Only after run() returns: a turn that dies mid-flight must not look complete.
        events.append("turn_end")
        if staged:
            wait = scenario.get("settleTimeoutMs", 180_000) / 1000
            landed = await asyncio.to_thread(_settle_pending, staged, events, wait)
            for _ in range(max_auto_follow_ups()):
                if not landed:
                    break
                events.append("auto_follow_up")
                text = resume_prompt(landed)
                messages = [*messages, {"role": "user", "content": text}]
                graded.append({"role": "user", "content": text})
                events.append("turn_start")
                result = await session.turn(
                    messages, lambda ev: _note(ev, tools_called, events, refused), artifacts=artifacts
                )
                messages = result.get("messages") or messages
                graded.extend(result.get("new_messages") or [])
                logs.extend(result.get("logs") or [])
                artifacts.extend(result.get("artifacts") or [])
                exhausted = exhausted or bool(result.get("exhausted"))
                steps = max(steps, int(result.get("steps") or 0))
                cap = int(result.get("max_steps") or 0) or cap
                events.append("turn_end")
                landed = await asyncio.to_thread(_settle_pending, staged, events, wait)
        # One approval, only for a run that stopped at the gate; the gate itself is untouched.
        if approval and _awaiting_approval(result.get("new_messages")):
            approval, text = None, scenario["approveWhenPlanned"]
            messages = [*messages, {"role": "user", "content": text}]
            graded.append({"role": "user", "content": text})
            events.append("approval_sent")
            events.append("turn_start")
            result = await session.turn(
                messages, lambda ev: _note(ev, tools_called, events, refused), artifacts=artifacts
            )
            messages = result.get("messages") or messages
            graded.extend(result.get("new_messages") or [])
            logs.extend(result.get("logs") or [])
            artifacts.extend(result.get("artifacts") or [])
            exhausted = exhausted or bool(result.get("exhausted"))
            steps = max(steps, int(result.get("steps") or 0))
            cap = int(result.get("max_steps") or 0) or cap
            events.append("turn_end")
            if staged:
                await asyncio.to_thread(
                    _settle_pending, staged, events, scenario.get("settleTimeoutMs", 180_000) / 1000
                )
    return RunResult(
        graded if restart_after else messages,
        logs,
        tools_called,
        events=events,
        artifacts=artifacts,
        exhausted=exhausted,
        staged=staged,
        steps=steps,
        max_steps=cap,
        config=config,
        refused=refused,
    )


def _awaiting_approval(new_messages):
    """True when the turn ended holding a plan whose steps are all still pending.

    Structural, not a reading of the prose: the gate defines the `## Plan` heading and the
    `- [ ]` checkbox states, and parse_latest_plan matches only those.
    """
    text = "\n\n".join(
        m["content"]
        for m in new_messages or []
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("content")
    )
    plan = parse_latest_plan(text)
    return bool(plan and plan.steps and len(plan.pending_steps) == len(plan.steps))


def _note(event, sink, events=None, refused=None):
    kind = event.get("type")
    if events is not None and kind:
        events.append(kind)
    if kind == "tool_start" and event.get("name"):
        sink.append(event["name"])
    if kind == "tool_end" and event.get("refused") and refused is not None:
        refused.append(event.get("name"))


DEFAULT_TIMEOUT_MS = 150_000


def scenario_timeout(scenario):
    """The wall clock one run gets; every wait inside the run is bounded by this same number."""
    return (scenario.get("timeoutMs") or DEFAULT_TIMEOUT_MS) / 1000


def _guarded(scenario, model, base_url, timeout):
    async def guarded():
        return await asyncio.wait_for(_run(scenario, model, base_url), timeout=timeout)

    try:
        return asyncio.run(guarded())
    except asyncio.TimeoutError:
        return RunResult([], [], [], error=f"timed out after {timeout:.0f}s")
    except Exception as e:  # a provider error is a run outcome, not a harness crash
        return RunResult([], [], [], error=f"{type(e).__name__}: {e}", status_code=getattr(e, "status_code", None))


def run_scenario(scenario, model, capture=None):
    """`capture` names a file to record this run's completions into."""
    timeout = scenario_timeout(scenario)
    target = upstream(model) if capture else None
    if not target:
        return _guarded(scenario, model, None, timeout)
    with recording(target, capture) as base_url:
        return _guarded(scenario, model, base_url, timeout)


def load_scenarios(root, only=None):
    out = []
    for entry in sorted(os.listdir(root)):
        path = os.path.join(root, entry, "scenario.json")
        if not os.path.isfile(path):
            continue
        if only and only not in entry:
            continue
        with open(path) as f:
            data = json.load(f)
        data["id"] = entry
        out.append(data)
    return out


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
        "session_id": config["session_id"],
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
        "session_id": config["session_id"],
    }


def _empty_history(config, scenario):
    """A fresh, empty history for scenarios that stage no data."""
    galaxy = tooltests.Galaxy(config["galaxy_root"], config.get("galaxy_key", ""))
    return galaxy.new_history(f"olit eval: {scenario.get('id', 'scenario')}")


def import_workflows(galaxy, spec):
    """The fixture workflows, freshly imported. Returns {filename: workflow_id}."""
    base = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "workflows"
    definitions = {name: json.loads((base / name).read_text()) for name in spec["files"]}
    wanted = {d["name"] for d in definitions.values()}
    for w in galaxy.call("api/workflows") or []:
        if w.get("name") in wanted:
            galaxy.call(f"api/workflows/{w['id']}", "DELETE")
    return {name: galaxy.call("api/workflows", "POST", {"workflow": d})["id"] for name, d in definitions.items()}


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
        "test": None,
        "tool_id": None,
        "session_id": config["session_id"],
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
        "session_id": config["session_id"],
    }
    if spec.get("thenRuns"):
        staged["produced_dataset_id"] = _stage_run(galaxy, history_id, dataset_id, spec["thenRuns"], timeout)
    if spec.get("thenInvokes"):
        staged["invocation_id"] = _stage_invocation(galaxy, history_id, dataset_id, spec["thenInvokes"], timeout)
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


# Job states Galaxy will not leave, matching src/invocations.ts.
RUNNING_STATES = ("new", "queued", "running", "paused", "upload", "setting_metadata")
# What Galaxy advances on its own; `paused` waits on a failed input or on the user.
ADVANCING_STATES = ("new", "queued", "running", "upload", "setting_metadata")


def max_auto_follow_ups():
    """How many turns the shell will continue by itself once work lands."""
    return _shell_contract()["max_auto_follow_ups"]


# Galaxy 26 moves a settled invocation from `scheduled` to `completed`, so pinning either
# word races the scheduler. What the staging asserts is the job outcome below.
INVOCATION_TERMINAL = ("scheduled", "completed", "cancelled", "failed")


def _await_invocation(galaxy, invocation_id, timeout):
    """The invocation once Galaxy stops driving it, and the states of its jobs."""
    for _ in range(max(1, timeout // 2)):
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
    return invocation["id"]


def _settle_pending(staged, events, timeout, interval=10):
    """Wait for work Galaxy is advancing, as the shell's watcher does."""
    galaxy, history_id = staged["galaxy"], staged["history_id"]
    deadline = time.time() + timeout
    watched = {}
    while time.time() < deadline:
        try:
            contents = galaxy.call(f"api/histories/{history_id}/contents") or []
        except Exception as exc:
            # A dropped read must not end a long run.
            logging.getLogger(__name__).info("settle poll failed, retrying: %s", exc)
            time.sleep(interval)
            continue
        live = [c for c in contents if isinstance(c, dict) and not c.get("deleted")]
        for c in live:
            if c.get("state") in ADVANCING_STATES:
                watched[c.get("id")] = c
        if not any(c.get("state") in ADVANCING_STATES for c in live):
            break
        time.sleep(interval)
    if not watched:
        return []
    events.append("work_settled")
    landed = {
        c.get("id"): c for c in (galaxy.call(f"api/histories/{history_id}/contents") or []) if isinstance(c, dict)
    }
    return [
        {
            "kind": "job",
            "id": str(i),
            "label": str(landed.get(i, {}).get("name") or i),
            "outcome": "failed" if landed.get(i, {}).get("state") == "error" else "completed",
        }
        for i in watched
        if landed.get(i, {}).get("state") in ("ok", "error")
    ]


# The shell script that answers for its own follow-up contract, relative to the checkout.
SHELL_CONTRACT = "contract/shell.mjs"


def _shell_contract(runs=None):
    """Ask the shell what it would do, rather than reconstruct it here."""
    script = subject.root() / SHELL_CONTRACT
    if not script.is_file():
        raise RuntimeError(f"the agent has no {SHELL_CONTRACT}; the harness cannot stand in for it")
    stated = subprocess.run(
        ["node", "--experimental-strip-types", str(script)],
        input=json.dumps(runs) if runs else "",
        capture_output=True,
        text=True,
    )
    if stated.returncode:
        raise RuntimeError(f"the shell could not state its contract:\n{stated.stderr}")
    return json.loads(stated.stdout)


def resume_prompt(runs):
    """The message the shell would deliver for these runs, built by the shell itself."""
    return _shell_contract(runs)["resume_prompt"]
