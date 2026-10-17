"""Drive the agent headlessly for one scenario."""

import asyncio
import contextlib
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
import subject  # noqa: E402

from .config import build_config, upstream  # noqa: E402
from .entries import digest  # noqa: E402
from .plan import parse_latest_plan  # noqa: E402
from .record import recording  # noqa: E402
from .session import Session, TurnFailed  # noqa: E402
from .staging import GalaxyClock, _empty_history, _stage, _timed, galaxy_wait_timeout  # noqa: E402

# The entries a run appends; anything settle hands back holding one is a follow-up run.
FOLLOW_UP_KINDS = {"pi.user", "pi.assistant", "pi.tool-result"}


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
        stalled=False,
        waited=0.0,
        document=None,
    ):
        # The chat-shaped transcript grading reads.
        self.messages = messages
        # The conversation as a saved session, which the page would store.
        self.document = document
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
        # The run never reached the agent: Galaxy did not finish the work it was waiting on.
        self.stalled = stalled
        # Seconds spent waiting on Galaxy: staging plus settles. Recorded, never graded.
        self.waited = waited
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


async def _run(scenario, model, base_url=None):
    config = build_config(model, scenario.get("capabilities"), base_url)
    if scenario.get("systemPrompt"):
        config["instructions"] = scenario["systemPrompt"]
    # Staging is the environment's work, so it is timed by the environment's clock and sits
    # outside the budget that measures the agent.
    waited = GalaxyClock()
    try:
        staged = await _timed(waited, _stage, scenario, config)
    except Exception as e:
        return RunResult([], [], [], error=f"{type(e).__name__}: {e}", stalled=True, waited=waited.total)
    # The session is bound to its history from the start, as the page binds it from the URL.
    config["history_id"] = staged["history_id"] if staged else _empty_history(config, scenario)
    session = Session(config)
    budget = scenario_timeout(scenario)
    try:
        await session.init()
        return await _within_agent_budget(_drive(scenario, session, config, staged, waited), budget, waited)
    except asyncio.TimeoutError:
        return RunResult([], [], [], error=f"the agent ran past its {budget:.0f}s budget", waited=waited.total)
    finally:
        await session.close()


async def _drive(scenario, session, config, staged, waited=None):
    waited = waited if waited is not None else GalaxyClock()
    tools_called = []
    logs = []
    events = []
    refused = []
    # Graded at the end; the session itself keeps them for the turns that follow.
    artifacts = []
    # Every turn the run had, in the chat shape grading reads, read off the entries it appended.
    graded = [{"role": "system", "content": scenario.get("systemPrompt", "")}]
    state = {"exhausted": False, "steps": 0, "last": []}
    restart_after = scenario.get("restartAfter")
    approval = scenario.get("approveWhenPlanned")

    def absorb(entries, exhausted=False):
        run = digest(entries)
        graded.extend(run["chat"])
        logs.extend(run["logs"])
        tools_called.extend(run["tools"])
        refused.extend(run["refused"])
        artifacts.extend(run["artifacts"])
        state["exhausted"] = state["exhausted"] or exhausted
        # The most any single run needed, which is what the cap actually constrains.
        state["steps"] = max(state["steps"], run["steps"])
        state["last"] = run["chat"]
        return run

    async def turn(text):
        result = await session.turn(text)
        run = absorb(result.get("entries"), result.get("reason") == "turn_limit")
        # Started means the model answered at least once, as pi's agent_start does.
        if run["steps"]:
            events.append("turn_start")
        # Only after the turn returns: a turn that dies mid-flight must not look complete.
        events.append("turn_end")

    for index, text in enumerate(scenario["inputs"], start=1):
        if restart_after and index == restart_after + 1:
            own = (await session.export())["session"]["id"]
            records = await session.restart()
            events.append("session_restart")
            if records:
                # The returning user's decision, which the scenario states: continue their own
                # session from the history's records, or start a new one.
                mine = [r for r in records if r["sessionId"] == own] if scenario.get("recoverSession") else []
                await session.recover(mine[0]["pageId"] if mine else None)
                events.append("session_recovered" if mine else "session_new")
        await turn(text)
        if staged:
            await _settle(session, events, galaxy_wait_timeout(), waited, absorb)
        # One approval, only for a run that stopped at the gate; the gate itself is untouched.
        if approval and _awaiting_approval(state["last"]):
            approval, text = None, scenario["approveWhenPlanned"]
            events.append("approval_sent")
            await turn(text)
            if staged:
                await _settle(session, events, galaxy_wait_timeout(), waited, absorb)
    document = await session.export()
    if staged:
        # The record is the session's, named after its id, which the agent chose.
        staged["session_id"] = document["session"]["id"]
    return RunResult(
        graded,
        logs,
        tools_called,
        events=events,
        artifacts=artifacts,
        exhausted=state["exhausted"],
        staged=staged,
        steps=state["steps"],
        max_steps=max_steps(config),
        config=config,
        refused=refused,
        waited=waited.total,
        document=document,
    )


def max_steps(config):
    """The step cap a run was under: the one configured, else the agent's own."""
    return int(config.get("max_steps") or subject.describe()["policy"]["loop"]["max_steps"])


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


DEFAULT_TIMEOUT_MS = 150_000


def scenario_timeout(scenario):
    """The wall clock one run gets; every wait inside the run is bounded by this same number."""
    return (scenario.get("timeoutMs") or DEFAULT_TIMEOUT_MS) / 1000


async def _within_agent_budget(coro, budget, waited):
    """Run `coro` until the agent's own time passes `budget`; time `waited` on Galaxy is not counted."""
    task = asyncio.ensure_future(coro)
    started, before = time.monotonic(), waited.now()
    while not task.done():
        used = time.monotonic() - started - (waited.now() - before)
        if used >= budget:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            raise asyncio.TimeoutError
        await asyncio.wait({task}, timeout=min(budget - used, 1.0))
    return task.result()


def _guarded(scenario, model, base_url):
    try:
        return asyncio.run(_run(scenario, model, base_url))
    except Exception as e:  # a provider error is a run outcome, not a harness crash
        return RunResult([], [], [], error=f"{type(e).__name__}: {e}", status_code=getattr(e, "status_code", None))


def run_scenario(scenario, model, capture=None):
    """`capture` names a file to record this run's completions into."""
    target = upstream(model) if capture else None
    if not target:
        return _guarded(scenario, model, None)
    with recording(target, capture) as base_url:
        return _guarded(scenario, model, base_url)


async def _settle(session, events, timeout, waited, absorb):
    """Wait for the work the session submitted and the runs its follow-ups start, as a tab would.

    The session decides what to follow up on and how many times in a row; the runs it started come
    back as the entries they appended. Galaxy's clock, not the agent's.
    """
    result = await waited.wait(session.settle(timeout))
    if result.get("settled"):
        events.append("work_settled")
    entries = result.get("entries") or []
    # A follow-up queued while the turn was still streaming hands its user entry back with that
    # turn, so its run arrives here without one; it is still a run to grade.
    if any(e.get("kind") in FOLLOW_UP_KINDS for e in entries):
        events.append("auto_follow_up")
        events.append("turn_start")
        run = digest(entries)
        if run["error"]:
            raise TurnFailed(run["error"])
        absorb(entries)
        events.append("turn_end")


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


def opted_out(scenario, include, named):
    """Why an opt-in scenario does not run, or None. Naming it or its label opts in."""
    label = scenario.get("optIn")
    if not label or named or label in include:
        return None
    return f"opt-in {label!r}; run with --include {label}"


def missing_tools(scenario, installed):
    """Why this Galaxy cannot run the scenario, or None.

    A required tool is a short id, matched against an installed id or a Tool Shed guid's
    tool segment, so the requirement holds whichever repository revision is installed.
    """
    wanted = (scenario.get("requires") or {}).get("tools") or []
    absent = [t for t in wanted if not any(i == t or f"/{t}/" in i for i in installed)]
    return f"this Galaxy lacks {', '.join(absent)}" if absent else None
