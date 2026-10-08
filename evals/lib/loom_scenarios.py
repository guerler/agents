"""Load loom's scenarios unchanged and adapt them to this harness.

One source of truth prevents test drift; runtime differences are translated explicitly here.
`scenarios/` holds only what cannot cross.
"""

import json
import os

# Excluded, not translated: adapting these would change what they measure.
NOT_PORTABLE = {
    "init-gate-galaxy-no-connection": "asserts no turn starts; this harness always emits turn_start",
    "standing-instructions-tool-preference": (
        "exercises a workspace LOOM.md; olit runs in a browser with no project directory "
        "and no standing-instructions channel to steer a plan"
    ),
    "evidence-gate-in-progress-contradiction": (
        "asserts loom's /override command, its activity.jsonl events and plan anchors, none "
        "of which olit has; the contradiction itself is covered by record-jobs, which marks "
        "a step `- [!]` when the watcher sees its job fail"
    ),
    "iwc-find-from-question": (
        "asserts a skill.hint activity event for loom's iwc-candidates trigger; olit has no "
        "activity log, so what this uniquely measures is an artifact it does not produce. The "
        "rest of it is olit's own iwc-question-finds-a-workflow: plan-consults-iwc does not "
        "cover it, because asking for a plan is the gating this behaviour denies"
    ),
    # loom v0.8.0's capture cluster: its harness writes a `loom-job` notebook block the moment a
    # submission answers, so each of these forbids `turn_start` to prove the agent was never
    # involved. olit's record is a Galaxy Page the model writes and this harness always emits
    # `turn_start`, so they measure an artifact olit does not produce, exactly as
    # init-gate-galaxy-no-connection does. Held out after all five failed structurally in the
    # 2026-10-04 suite run.
    **{
        name: (
            "asserts no turn starts (events.mustNotInclude agent_start/tool_execution_start/"
            "turn_start), because loom's harness registers the submission without the agent; "
            "olit has no capture hook and always emits turn_start"
        )
        for name in (
            "submission-capture-local-upload",
            "submission-capture-mapped-over-jobs",
            "submission-capture-refuses-to-guess",
            "submission-capture-registers-every-tool",
            "submission-capture-udt-provenance",
        )
    },
}

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

# pi's lifecycle events mapped to the smaller set this harness emits; unmapped names drop.
EVENT_NAMES = {
    "agent_start": "turn_start",
    "turn_start": "turn_start",
    "turn_end": "turn_end",
}

# loom calls galaxy-mcp's tools by the names pi's MCP support gives them; this suite uses the bare
# names galaxy-mcp declares.
MCP_PREFIX = "mcp__galaxy__"


def _tool_name(name):
    return name.removeprefix(MCP_PREFIX) if isinstance(name, str) else name


# loom also teaches `local` and `hybrid`; this build has no local execution.
ROUTING_TAGS = {"remote"}

# loom's `--tools` mapped to the nearest capability set.
TOOL_CAPABILITIES = {
    "read,write,edit": "llm,local",
    "skills_fetch": "llm,local",
}


def _adapt_events(spec):
    out = {}
    for key in ("mustInclude", "mustNotInclude"):
        names = spec.get(key)
        if not names:
            continue
        mapped = [EVENT_NAMES[n] for n in names if n in EVENT_NAMES]
        if mapped:
            out[key] = sorted(set(mapped))
    return out


def _adapt_tool_calls(spec):
    out = dict(spec)
    wanted = out.get("mustInclude")
    if wanted:
        out["mustInclude"] = [{**w, "name": _tool_name(w.get("name"))} for w in wanted]
    banned = out.get("mustNotInclude")
    if banned:
        out["mustNotInclude"] = [_tool_name(n) for n in banned]
    return out


def _adapt_plan(spec):
    out = dict(spec)
    routing = out.get("routingIn")
    if routing and not set(routing) <= ROUTING_TAGS:
        out.pop("routingIn")  # correct answer names a tag this suite lacks; grade the rest
    return out


def _capabilities_for(scenario):
    """Capability string implied by `loomArgs`, or None for the default surface."""
    args = scenario.get("loomArgs") or []
    if "--no-tools" in args:
        return "llm,local"
    if "--tools" in args:
        requested = args[args.index("--tools") + 1]
        return TOOL_CAPABILITIES.get(requested)
    return None


def adapt(scenario):
    """Translate one loom scenario into the shape this harness grades."""
    out = dict(scenario)
    assertions = dict(out.get("assertions") or {})
    if "events" in assertions:
        events = _adapt_events(assertions["events"])
        if events:
            assertions["events"] = events
        else:
            assertions.pop("events")
    if "toolCalls" in assertions:
        assertions["toolCalls"] = _adapt_tool_calls(assertions["toolCalls"])
    if "plan" in assertions:
        assertions["plan"] = _adapt_plan(assertions["plan"])
    out["assertions"] = assertions
    capabilities = _capabilities_for(scenario)
    if capabilities:
        out["capabilities"] = capabilities
    # loom defaults model scenarios to 3 runs; honour it so both suites use the same n.
    out["runs"] = scenario.get("runs") or (3 if scenario.get("requiresModel") else 1)
    return out


def load(root, only=None):
    """Every portable loom scenario, adapted. Empty when loom is not checked out."""
    if not os.path.isdir(root):
        return []
    out = []
    for entry in sorted(os.listdir(root)):
        path = os.path.join(root, entry, "scenario.json")
        if not os.path.isfile(path):
            continue
        if only and only not in entry:
            continue
        if entry in NOT_PORTABLE:
            continue
        with open(path) as f:
            scenario = adapt(json.load(f))
        live = LIVE_DATA.get(entry)
        if live:
            scenario["dataset"] = live["dataset"]
            scenario["inputs"] = [live["first_input"], *scenario["inputs"][1:]]
        scenario["id"] = entry
        scenario["shared"] = True
        out.append(scenario)
    return out
