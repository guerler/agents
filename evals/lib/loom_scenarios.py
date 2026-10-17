"""Loom's scenarios, read as loom writes them and graded where this harness can judge them.

Translation covers only what has a faithful counterpart here. A scenario that needs anything
else is skipped with the reason, read off the scenario itself, so adopting a new loom scenario
needs no change in this file.
"""

import json
import os

import subject

from .grading import SCENARIO_KEYS, unsupported

# loom grades against a stub, so a scenario may name data nothing creates. On a live Galaxy
# `execution-after-approval`'s dataset id and history do not resolve, the agent is right to
# refuse the call, and the run grades as "never called run_tool" — measuring the fixture
# rather than the approval gate. Stage the data and name it by position instead.
LIVE_DATA = {
    "execution-after-approval": {
        "dataset": {"file": "prices.csv", "datatype": "csv", "history": "execution gate $run"},
        "first_input": (
            "Run the Add column tool (tool id `addValue`) on the dataset in my history, "
            "adding the value 7 as a new column. Draft a plan first and wait for my approval."
        ),
    },
}

# pi's lifecycle events, by the names this harness emits.
EVENT_NAMES = {
    "agent_start": "turn_start",
    "turn_start": "turn_start",
    "turn_end": "turn_end",
}

# loom calls galaxy-mcp's tools by the names pi's MCP support gives them.
MCP_PREFIX = "mcp__galaxy__"

# loom also routes `local` and `hybrid`; this agent has no local execution.
ROUTING_TAGS = {"remote"}

# loom's own keys, read here and not passed on.
LOOM_KEYS = {"tier", "requiresModel", "requiresGalaxy", "loomArgs"}

# loom's `--tools` mapped to the nearest capability set.
TOOL_CAPABILITIES = {
    "read,write,edit": "llm,local",
    "skills_fetch": "llm,local",
}


class Unsupported(Exception):
    """The scenario asserts something this harness cannot judge faithfully."""


def _tool_name(name):
    return name.removeprefix(MCP_PREFIX) if isinstance(name, str) else name


def _events(spec):
    out = {}
    for key, names in spec.items():
        missing = [n for n in names if n not in EVENT_NAMES]
        if missing:
            raise Unsupported(f"events {', '.join(missing)} have no counterpart here")
        out[key] = sorted({EVENT_NAMES[n] for n in names})
    if "turn_start" in out.get("mustNotInclude", []):
        raise Unsupported("asserts no turn starts, and this harness always starts one")
    return out


def _tool_calls(spec, tools):
    out = dict(spec)
    if spec.get("mustInclude"):
        out["mustInclude"] = [{**w, "name": _tool_name(w.get("name"))} for w in spec["mustInclude"]]
        lacking = [w["name"] for w in out["mustInclude"] if w["name"] not in tools]
        if lacking:
            raise Unsupported(f"expects {', '.join(lacking)}, which the agent does not have")
    if spec.get("mustNotInclude"):
        out["mustNotInclude"] = [_tool_name(n) for n in spec["mustNotInclude"]]
    return out


def _plan(spec):
    out = dict(spec)
    if spec.get("routingIn"):
        allowed = [r for r in spec["routingIn"] if r in ROUTING_TAGS]
        if not allowed:
            raise Unsupported(f"expects routing {spec['routingIn']}, which the agent does not have")
        out["routingIn"] = allowed
    return out


def _capabilities(args):
    """The capability set loom's command line asks for, or None for the default surface."""
    if "--no-tools" in args:
        return "llm,local"
    if "--tools" in args:
        requested = args[args.index("--tools") + 1]
        if requested not in TOOL_CAPABILITIES:
            raise Unsupported(f"runs loom with --tools {requested}")
        return TOOL_CAPABILITIES[requested]
    return None


def adapt(scenario, tools):
    """The scenario as this harness grades it; raises Unsupported when it cannot be judged here."""
    if scenario.get("env") or scenario.get("config"):
        raise Unsupported("configures loom itself (env, config)")
    unread = sorted(set(scenario) - SCENARIO_KEYS - LOOM_KEYS)
    if unread:
        raise Unsupported(f"sets {', '.join(unread)}, which this harness does not read")
    assertions = dict(scenario.get("assertions") or {})
    unread = unsupported(assertions)
    if unread:
        raise Unsupported(f"no checker reads {', '.join(unread)}")
    if "events" in assertions:
        assertions["events"] = _events(assertions["events"])
    if "toolCalls" in assertions:
        assertions["toolCalls"] = _tool_calls(assertions["toolCalls"], tools)
    if "plan" in assertions:
        assertions["plan"] = _plan(assertions["plan"])
    out = {**{k: v for k, v in scenario.items() if k not in LOOM_KEYS}, "assertions": assertions}
    capabilities = _capabilities(scenario.get("loomArgs") or [])
    if capabilities:
        out["capabilities"] = capabilities
    # loom defaults model scenarios to 3 runs; honour it so both suites use the same n.
    out["runs"] = scenario.get("runs") or (3 if scenario.get("requiresModel") else 1)
    return out


def load(root, only=None):
    """Every loom scenario under `root`: (the ones graded here, {skipped id: why})."""
    if not os.path.isdir(root):
        return [], {}
    tools = set(subject.describe()["tools"])
    loaded, skipped = [], {}
    for entry in sorted(os.listdir(root)):
        path = os.path.join(root, entry, "scenario.json")
        if not os.path.isfile(path) or (only and only not in entry):
            continue
        if os.path.isdir(os.path.join(root, entry, "cwd")):
            skipped[entry] = "needs loom's project directory (cwd/)"
            continue
        with open(path) as f:
            try:
                scenario = adapt(json.load(f), tools)
            except Unsupported as e:
                skipped[entry] = str(e)
                continue
        live = LIVE_DATA.get(entry)
        if live:
            scenario["dataset"] = live["dataset"]
            scenario["inputs"] = [live["first_input"], *scenario["inputs"][1:]]
        scenario["id"] = entry
        scenario["source"] = "loom"
        loaded.append(scenario)
    return loaded, skipped
