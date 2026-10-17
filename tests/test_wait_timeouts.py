"""A run's wall clock has to survive the waits inside it.

Galaxy waits poll with a blocking sleep. Called straight from a coroutine they pin the event
loop, so `asyncio.wait_for` never fires and the scenario budget means nothing; they belong on a
worker thread. The budget is also the one number every wait reads, so no inner cap can outlive it.
"""

import asyncio
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "evals"))

from lib.harness import scenario_timeout  # noqa: E402


def _blocking_wait(seconds):
    time.sleep(seconds)
    return "finished"


def test_a_blocking_wait_run_inline_outlives_the_budget():
    """The old shape: the guard cannot interrupt a sleep that never yields."""

    async def inline():
        return _blocking_wait(0.5)

    started = time.monotonic()

    async def guarded():
        try:
            return await asyncio.wait_for(inline(), timeout=0.05)
        except asyncio.TimeoutError:
            return "timed out"

    outcome = asyncio.run(guarded())
    assert outcome == "finished", "a blocking sleep is not cancellable"
    assert time.monotonic() - started >= 0.5, "the budget was not enforced"


def test_a_blocking_wait_on_a_thread_is_cut_off_at_the_budget():
    """The fixed shape: off the event loop the guard fires, and it fires at the budget.

    The wait itself runs to its own cap on the worker thread, which `asyncio.run` joins on
    shutdown. That is why every cap is the scenario budget: the join can never outlast it.
    """
    elapsed = {}

    async def threaded():
        return await asyncio.to_thread(_blocking_wait, 0.5)

    async def guarded():
        started = time.monotonic()
        try:
            return await asyncio.wait_for(threaded(), timeout=0.05)
        except asyncio.TimeoutError:
            return "timed out"
        finally:
            elapsed["seconds"] = time.monotonic() - started

    outcome = asyncio.run(guarded())
    assert outcome == "timed out"
    assert elapsed["seconds"] < 0.4, "the guard did not fire at the budget"


def test_every_wait_reads_the_same_budget_as_the_guard():
    assert scenario_timeout({"timeoutMs": 36_000_000}) == 36_000.0
    assert scenario_timeout({}) == 150.0


def test_no_wait_carries_a_cap_of_its_own():
    """A hardcoded 600s let staging outlive a 420s scenario, which is how the conflict showed."""
    import inspect

    from lib import harness, staging, tooltests

    for func in (staging._await_invocation, harness._settle, tooltests.Galaxy.await_dataset):
        timeout = inspect.signature(func).parameters["timeout"]
        assert timeout.default is inspect.Parameter.empty, f"{func.__name__} still defaults its own cap"


def test_the_environment_clock_is_set_per_run_not_per_scenario():
    import os

    from lib.staging import DEFAULT_GALAXY_WAIT_SECONDS, galaxy_wait_timeout

    assert galaxy_wait_timeout() == DEFAULT_GALAXY_WAIT_SECONDS
    os.environ["OLIT_EVAL_GALAXY_WAIT"] = "14400"
    try:
        assert galaxy_wait_timeout() == 14400.0
    finally:
        del os.environ["OLIT_EVAL_GALAXY_WAIT"]


def test_staging_waits_on_the_environment_clock_not_the_scenario_budget(monkeypatch):
    """A queued upload must not spend the budget that exists to catch a stalled agent."""
    from lib import staging

    seen = {}
    monkeypatch.setenv("OLIT_EVAL_GALAXY_WAIT", "3600")
    monkeypatch.setattr(staging, "stage_dataset", lambda config, spec, timeout: seen.setdefault("timeout", timeout))
    staging._stage({"dataset": {"file": "x.tabular"}, "timeoutMs": 1_000}, {})
    assert seen["timeout"] == 3600.0, "staging read the scenario budget instead of the environment clock"


def test_a_staging_failure_is_a_stall_and_not_a_behavioural_verdict(monkeypatch):
    from lib import harness, tooltests

    def explode(scenario, config):
        raise tooltests.ToolTestError("x.tabular landed in state running")

    monkeypatch.setattr(harness, "_stage", explode)
    monkeypatch.setattr(harness, "build_config", lambda *a, **k: {})
    run = harness._guarded({"id": "s", "inputs": ["hi"]}, {"id": "m"}, None)
    assert run.stalled is True
    assert "landed in state running" in run.error
    assert run.steps == 0


def test_the_wait_is_recorded_even_when_staging_stalls(monkeypatch):
    from lib import harness, tooltests

    def slow_then_fail(scenario, config):
        time.sleep(0.05)
        raise tooltests.ToolTestError("stalled")

    monkeypatch.setattr(harness, "_stage", slow_then_fail)
    monkeypatch.setattr(harness, "build_config", lambda *a, **k: {})
    run = harness._guarded({"id": "s", "inputs": ["hi"]}, {"id": "m"}, None)
    assert run.waited >= 0.05


def test_a_galaxy_wait_does_not_spend_the_agent_budget():
    """A queued job settling on Galaxy is the environment's clock, so the agent keeps its budget."""
    from lib.harness import _within_agent_budget
    from lib.staging import GalaxyClock

    clock = GalaxyClock()

    async def agent():
        await clock.wait(asyncio.sleep(0.3))
        return "answered"

    assert asyncio.run(_within_agent_budget(agent(), 0.1, clock)) == "answered"
    assert clock.total >= 0.3, "the wait was not recorded"


def test_the_agent_own_time_is_still_cut_off_at_the_budget():
    from lib.harness import _within_agent_budget
    from lib.staging import GalaxyClock

    async def agent():
        await asyncio.sleep(0.5)
        return "answered"

    async def guarded():
        started = time.monotonic()
        try:
            return await _within_agent_budget(agent(), 0.1, GalaxyClock())
        except asyncio.TimeoutError:
            return time.monotonic() - started

    elapsed = asyncio.run(guarded())
    assert isinstance(elapsed, float) and elapsed < 0.4, "the agent outlived its budget"


def test_an_environment_wait_given_as_a_float_still_bounds_the_invocation_poll(monkeypatch):
    """OLIT_EVAL_GALAXY_WAIT is read as a float, and range() takes only integers."""
    from lib import staging

    class Galaxy:
        def call(self, path):
            return {"state": "scheduled"} if path.endswith("invocations/i1") else {"states": {"ok": 1}}

    monkeypatch.setattr(staging.time, "sleep", lambda seconds: None)
    settled, states = staging._await_invocation(Galaxy(), "i1", 1800.0)
    assert settled["state"] == "scheduled" and states == {"ok": 1}


def test_a_poll_ends_on_its_condition_or_its_deadline(monkeypatch):
    from lib import tooltests

    monkeypatch.setattr(tooltests.time, "sleep", lambda seconds: None)
    states = iter(["queued", "running", "ok"])
    assert tooltests.poll(lambda: next(states), lambda s: s == "ok", 60) == "ok"
    asked = []
    assert tooltests.poll(lambda: asked.append(1) or "queued", lambda s: s == "ok", 0) == "queued"
    assert len(asked) == 1
