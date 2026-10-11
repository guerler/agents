"""Which scenarios a run takes up: opt-in labels and what the Galaxy must offer."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "evals"))

from lib.harness import missing_tools, opted_out  # noqa: E402


def test_an_opt_in_scenario_runs_only_when_its_label_or_name_is_given():
    scenario = {"id": "cryptic-exon-q1", "optIn": "benchmark"}
    assert opted_out(scenario, [], False) == "opt-in 'benchmark'; run with --include benchmark"
    assert opted_out(scenario, ["benchmark"], False) is None
    assert opted_out(scenario, [], True) is None
    assert opted_out({"id": "smoke-answers"}, [], False) is None


def test_a_required_tool_matches_a_tool_shed_guid_at_any_revision():
    scenario = {"requires": {"tools": ["aoptk_query_literature", "aoptk_download_publication_data"]}}
    guid = "toolshed.g2.bx.psu.edu/repos/recetox/aoptk/{}/0.5.0+galaxy7"
    installed = [guid.format("aoptk_query_literature"), guid.format("aoptk_download_publication_data")]
    assert missing_tools(scenario, installed) is None
    assert missing_tools(scenario, installed[:1]) == "this Galaxy lacks aoptk_download_publication_data"
    assert missing_tools(scenario, ["upload1", "cat1"]) == (
        "this Galaxy lacks aoptk_query_literature, aoptk_download_publication_data"
    )
    assert missing_tools({"id": "smoke-answers"}, []) is None
