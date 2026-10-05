"""The agent under test: where its checkout is, and how its code is reached.

An evaluation runs against a specific commit of an agent. The checkout is named once, here,
so nothing else in this repo has to know where it lives or how it is built.
"""

import functools
import os
import pathlib

import description

ENV = "OLIT_ROOT"

# What marks a checkout of the agent: the directory its loop lives in.
SOURCE = {"olit": "src/agent"}

# The agent as one Node module, speaking JSON lines; built from the checkout, never vendored.
SESSION = {"olit": "dist/session.mjs"}
BUILD = {"olit": "npm run build:session"}


def root(agent="olit"):
    """The checkout under test."""
    value = os.environ.get(ENV)
    if not value:
        raise SystemExit(f"set {ENV} to a checkout of the agent under test")
    path = pathlib.Path(value).expanduser().resolve()
    if not (path / SOURCE[agent]).is_dir():
        raise SystemExit(f"{path} does not look like an {agent} checkout")
    return path


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
