"""The agent under test: where its checkout is, and how its code is reached.

An evaluation runs against a specific commit of an agent. The checkout is named once, here,
so nothing else in this repo has to know where it lives or how it is built.
"""

import functools
import os
import pathlib

import description

ENV = "OLIT_ROOT"
# Where loom keeps the scenarios this repo also runs.
LOOM_SCENARIOS = "~/orbit/evals/scenarios"

# What marks a checkout of the agent: the directories its loop lives in.
SOURCE = {"olit": ("core/src/agent", "olit/src/agent")}

# The agent as one Node module, speaking JSON lines; built from the checkout, never vendored.
SESSION = {"olit": "dist/session.mjs"}
BUILD = {"olit": "npm run build:session"}


def root(agent="olit"):
    """The checkout under test."""
    value = os.environ.get(ENV)
    if not value:
        raise SystemExit(f"set {ENV} to a checkout of the agent under test")
    path = pathlib.Path(value).expanduser().resolve()
    if not all((path / source).is_dir() for source in SOURCE[agent]):
        raise SystemExit(f"{path} does not look like an {agent} checkout")
    return path


def loom_scenarios():
    """loom's scenario directory, which `LOOM_SCENARIOS` overrides."""
    return pathlib.Path(os.environ.get("LOOM_SCENARIOS", LOOM_SCENARIOS)).expanduser()


def source(module, agent="olit"):
    """A module's file, by the `src/agent/...` path the agent's description names it with."""
    for directory in SOURCE[agent]:
        path = root(agent) / directory / pathlib.Path(module).relative_to("src/agent")
        if path.is_file():
            return path
    raise SystemExit(f"{module} is not in {root(agent)}")


def session(agent="olit"):
    """The command that runs one session of the agent, for a harness that drives it."""
    path = root(agent) / SESSION[agent]
    if not path.is_file():
        raise SystemExit(f"{path} is not built; run `{BUILD[agent]}` in {root(agent)}")
    return ["node", str(path)]


@functools.lru_cache(maxsize=None)
def describe(agent="olit"):
    """What the agent publishes about itself."""
    return description.load(str(root(agent)), agent=agent)
