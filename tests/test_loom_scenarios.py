"""loom's scenarios are read as loom writes them; what cannot be judged here is skipped and says why."""

import os

import pytest

from evals.lib import loom_scenarios
from evals.lib.loom_scenarios import Unsupported, adapt

LOOM = os.path.expanduser(os.environ.get("LOOM_ROOT", "~/loom"))
TOOLS = {"run_tool", "invoke_workflow"}


def test_tool_names_drop_the_mcp_prefix():
    adapted = adapt(
        {
            "assertions": {
                "toolCalls": {
                    "mustInclude": [{"name": "mcp__galaxy__run_tool", "argsContains": {"x": 1}}],
                    "mustNotInclude": ["mcp__galaxy__invoke_workflow", "galaxy_upload_local_file"],
                }
            }
        },
        TOOLS,
    )
    calls = adapted["assertions"]["toolCalls"]
    assert calls["mustInclude"] == [{"name": "run_tool", "argsContains": {"x": 1}}]
    assert calls["mustNotInclude"] == ["invoke_workflow", "galaxy_upload_local_file"]


def test_what_cannot_be_judged_here_is_refused_with_its_reason():
    cases = [
        ({"env": {"LOOM_MODEL_SCRIPT": "x"}}, "configures loom"),
        ({"fixture": "notebook.md"}, "sets fixture, which this harness does not read"),
        ({"assertions": {"activity": {}, "notebook": {}}}, "no checker reads activity, notebook"),
        ({"assertions": {"plan": {"exists": True, "source": "notebook"}}}, "no checker reads plan.source"),
        ({"assertions": {"events": {"mustNotInclude": ["agent_start"]}}}, "always starts one"),
        ({"assertions": {"events": {"mustInclude": ["message_end"]}}}, "message_end have no counterpart"),
        ({"assertions": {"toolCalls": {"mustInclude": [{"name": "lessons_search"}]}}}, "lessons_search"),
        ({"assertions": {"plan": {"routingIn": ["local"]}}}, "expects routing ['local']"),
        ({"loomArgs": ["--tools", "read,bash"]}, "--tools read,bash"),
    ]
    for scenario, reason in cases:
        with pytest.raises(Unsupported, match=reason.replace("[", r"\[").replace("]", r"\]")):
            adapt(scenario, TOOLS)


def test_routing_keeps_the_tags_the_agent_has():
    adapted = adapt({"assertions": {"plan": {"routingIn": ["remote", "hybrid"]}}}, TOOLS)
    assert adapted["assertions"]["plan"]["routingIn"] == ["remote"]


def test_every_loaded_loom_scenario_is_fully_graded():
    loaded, skipped = loom_scenarios.load(os.path.join(LOOM, "evals", "scenarios"))
    for scenario in loaded:
        assert not loom_scenarios.unsupported(scenario["assertions"]), scenario["id"]
        calls = scenario["assertions"].get("toolCalls", {})
        names = [w.get("name") for w in calls.get("mustInclude", [])] + calls.get("mustNotInclude", [])
        assert not [n for n in names if str(n).startswith(loom_scenarios.MCP_PREFIX)], scenario["id"]
    assert all(skipped.values())
