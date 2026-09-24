"""The agent under test: where its checkout is, and how its code is reached.

An evaluation runs against a specific commit of an agent. The checkout is named once, here,
so nothing else in this repo has to know where it lives or how it is installed.
"""

import functools
import os
import pathlib
import sys

import description

ENV = "OLIT_ROOT"

# Where an agent's importable code sits inside its checkout.
PACKAGE = {"olit": "brain"}


def root(agent="olit"):
    """The checkout under test."""
    value = os.environ.get(ENV)
    if not value:
        raise SystemExit(f"set {ENV} to a checkout of the agent under test")
    path = pathlib.Path(value).expanduser().resolve()
    if not (path / PACKAGE[agent]).is_dir():
        raise SystemExit(f"{path} does not look like an {agent} checkout")
    return path


def importable(agent="olit"):
    """Put the agent's package on the path, for a harness that drives it in process."""
    package = str(root(agent) / PACKAGE[agent])
    if package not in sys.path:
        sys.path.insert(0, package)
    return package


@functools.lru_cache(maxsize=None)
def describe(agent="olit"):
    """What the agent publishes about itself."""
    return description.load(str(root(agent)), agent=agent)
