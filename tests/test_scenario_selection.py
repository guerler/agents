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


def test_a_scenario_names_the_required_tools_galaxy_lacks():
    scenario = {"requires": {"tools": ["aoptk_query_literature", "aoptk_download_publication_data"]}}
    assert missing_tools(scenario, lambda t: True) is None
    assert missing_tools(scenario, lambda t: t == "aoptk_query_literature") == (
        "this Galaxy lacks aoptk_download_publication_data"
    )
    assert missing_tools(scenario, lambda t: False) == (
        "this Galaxy lacks aoptk_query_literature, aoptk_download_publication_data"
    )
    assert missing_tools({"id": "smoke-answers"}, lambda t: False) is None
