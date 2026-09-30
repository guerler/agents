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

    from lib import harness, tooltests

    for func in (harness._await_invocation, harness._settle_pending, tooltests.Galaxy.await_dataset):
        timeout = inspect.signature(func).parameters["timeout"]
        assert timeout.default is inspect.Parameter.empty, f"{func.__name__} still defaults its own cap"


def test_the_environment_clock_is_set_per_run_not_per_scenario():
    import os

    from lib.harness import DEFAULT_GALAXY_WAIT_SECONDS, galaxy_wait_timeout

    assert galaxy_wait_timeout() == DEFAULT_GALAXY_WAIT_SECONDS
    os.environ["OLIT_EVAL_GALAXY_WAIT"] = "14400"
    try:
        assert galaxy_wait_timeout() == 14400.0
    finally:
        del os.environ["OLIT_EVAL_GALAXY_WAIT"]


def test_staging_waits_on_the_environment_clock_not_the_scenario_budget(monkeypatch):
    """A queued upload must not spend the budget that exists to catch a stalled agent."""
    from lib import harness

    seen = {}
    monkeypatch.setenv("OLIT_EVAL_GALAXY_WAIT", "3600")
    monkeypatch.setattr(harness, "stage_dataset", lambda config, spec, timeout: seen.setdefault("timeout", timeout))
    harness._stage({"dataset": {"file": "x.tabular"}, "timeoutMs": 1_000}, {})
    assert seen["timeout"] == 3600.0, "staging read the scenario budget instead of the environment clock"


def test_a_staging_failure_is_a_stall_and_not_a_behavioural_verdict(monkeypatch):
    from lib import harness

    def explode(scenario, config):
        raise harness.tooltests.ToolTestError("x.tabular landed in state running")

    monkeypatch.setattr(harness, "_stage", explode)
    monkeypatch.setattr(harness, "build_config", lambda *a, **k: {})
    run = harness._guarded({"id": "s", "inputs": ["hi"]}, {"id": "m"}, None)
    assert run.stalled is True
    assert "landed in state running" in run.error
    assert run.steps == 0


def test_the_wait_is_recorded_even_when_staging_stalls(monkeypatch):
    from lib import harness

    def slow_then_fail(scenario, config):
        time.sleep(0.05)
        raise harness.tooltests.ToolTestError("stalled")

    monkeypatch.setattr(harness, "_stage", slow_then_fail)
    monkeypatch.setattr(harness, "build_config", lambda *a, **k: {})
    run = harness._guarded({"id": "s", "inputs": ["hi"]}, {"id": "m"}, None)
    assert run.waited >= 0.05
