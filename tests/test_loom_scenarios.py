"""loom scenarios arrive with the tool names pi's MCP support gives galaxy-mcp's tools; graded by the bare names."""

import os

from evals.lib import loom_scenarios

LOOM = os.path.expanduser(os.environ.get("LOOM_ROOT", "~/loom"))


def test_tool_names_drop_the_mcp_prefix():
    adapted = loom_scenarios.adapt(
        {
            "assertions": {
                "toolCalls": {
                    "mustInclude": [{"name": "mcp__galaxy__run_tool", "argsContains": {"x": 1}}],
                    "mustNotInclude": ["mcp__galaxy__invoke_workflow", "galaxy_upload_local_file"],
                }
            }
        }
    )
    calls = adapted["assertions"]["toolCalls"]
    assert calls["mustInclude"] == [{"name": "run_tool", "argsContains": {"x": 1}}]
    assert calls["mustNotInclude"] == ["invoke_workflow", "galaxy_upload_local_file"]


def test_no_imported_scenario_names_a_prefixed_tool():
    scenarios = loom_scenarios.load(os.path.join(LOOM, "evals", "scenarios"))
    for scenario in scenarios:
        calls = scenario["assertions"].get("toolCalls", {})
        names = [w.get("name") for w in calls.get("mustInclude", [])] + calls.get("mustNotInclude", [])
        assert not [n for n in names if str(n).startswith(loom_scenarios.MCP_PREFIX)], scenario["id"]
