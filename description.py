"""The description an agent publishes about itself, and the views this repo reads it through.

Nothing here parses the agent's source. A checkout is asked for its description; a JSON file
is read as one. Everything below is a projection of that document.
"""

import json
import os
import pathlib
import subprocess

# How an agent is asked to describe itself, by name: its built session, relative to the checkout.
DESCRIBE = {"olit": ["node", "dist/session.mjs", "--describe"]}


def load(source=None, agent="olit"):
    """A description, from a JSON file or from a checkout that can produce one."""
    source = source or os.environ.get("AGENT_DESCRIPTION") or os.environ.get("OLIT_ROOT")
    if source is None:
        raise SystemExit("no agent description: set AGENT_DESCRIPTION to a file or a checkout")
    path = pathlib.Path(source).expanduser().resolve()
    if path.is_file():
        return json.loads(path.read_text())
    out = subprocess.run(
        [*DESCRIBE[agent], "--root", str(path)],
        cwd=path,
        capture_output=True,
        text=True,
    )
    if out.returncode:
        raise SystemExit(f"{agent} could not describe itself:\n{out.stderr}")
    return json.loads(out.stdout)


def defines(description, module, symbol):
    """Whether the agent still defines this symbol in this module, keyed as it publishes it."""
    return symbol in (description["symbols"].get(module) or [])


def tool_table(description):
    """name -> fingerprint of (description, parameter names), for comparison upstream."""
    return {name: tool["signature"] for name, tool in description["tools"].items()}


def delegated_tools(description):
    """Tools the agent does not run itself, so no static read of its code sees the result."""
    return {name for name, tool in description["tools"].items() if tool.get("runner") != "olit"}


def write_tools(description):
    """Tools a read-back cannot verify: checking the result would mean performing the change."""
    return {name for name, tool in description["tools"].items() if tool.get("capability") == "write"}


def promised_fields(description):
    """name -> the result fields the agent declares and reads back off a live result."""
    return {name: tool.get("promised_fields") or [] for name, tool in description["tools"].items()}


def tool_contracts(description):
    return {name: {"params": tool["params"], "prose": tool["prose"]} for name, tool in description["tools"].items()}


def policy(description, name):
    return description["policy"]["guards" if name == "guards" else name]
