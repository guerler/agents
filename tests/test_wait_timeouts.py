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
