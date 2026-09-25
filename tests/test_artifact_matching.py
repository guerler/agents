"""Each mustInclude entry grades the artifact it describes, not always the first one."""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evals"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.assertions import _artifacts  # noqa: E402

SCATTER = {"kind": "vega-lite", "title": "Glucose vs BMI", "spec": {"mark": "point"}}
REGRESSION = {"kind": "vega-lite", "title": "Linear Regression", "spec": {"mark": "line"}}


def grade(spec, made):
    run = types.SimpleNamespace(artifacts=made)
    failures = []
    _artifacts(spec, run, failures, set())
    return [f.detail for f in failures]


def test_two_charts_are_each_graded_against_their_own_mark():
    spec = {"mustInclude": [{"kind": "vega-lite", "mark": "point"}, {"kind": "vega-lite", "mark": "line"}]}
    assert grade(spec, [SCATTER, REGRESSION]) == []


def test_order_does_not_matter():
    spec = {"mustInclude": [{"kind": "vega-lite", "mark": "point"}, {"kind": "vega-lite", "mark": "line"}]}
    assert grade(spec, [REGRESSION, SCATTER]) == []


def test_a_missing_second_chart_fails_rather_than_matching_the_first():
    spec = {"mustInclude": [{"kind": "vega-lite", "mark": "point"}, {"kind": "vega-lite", "mark": "line"}]}
    failures = grade(spec, [SCATTER])
    assert len(failures) == 1 and "mark='line'" in failures[0]


def test_the_failure_names_what_was_produced():
    spec = {"mustInclude": [{"kind": "vega-lite", "mark": "area"}]}
    assert "'point'" in grade(spec, [SCATTER])[0]


def test_a_title_substring_can_select_when_marks_agree():
    spec = {"mustInclude": [{"kind": "vega-lite", "titleContains": "regression"}]}
    assert grade(spec, [SCATTER, REGRESSION]) == []
    assert grade(spec, [SCATTER]) != []


def test_a_single_chart_with_the_right_mark_still_passes():
    assert grade({"mustInclude": [{"kind": "vega-lite", "mark": "point"}]}, [SCATTER]) == []


LAYERED_FIT = {
    "kind": "vega-lite",
    "title": "Linear Regression",
    "spec": {
        "data": {"values": [{"BMI": 33.6, "Glucose": 148.0, "yhat": 122.3}]},
        "layer": [
            {"mark": {"type": "point"}, "encoding": {"x": {"field": "BMI"}, "y": {"field": "Glucose"}}},
            {"mark": {"type": "line"}, "encoding": {"x": {"field": "BMI"}, "y": {"field": "yhat"}}},
        ],
    },
}


def test_a_layered_fit_counts_as_encoding_its_fitted_values():
    """vintent draws a fit as two layers, so a top-level-only read sees an empty chart."""
    spec = {"mustInclude": [{"kind": "vega-lite", "titleContains": "regression", "encodes": ["yhat"]}]}
    assert grade(spec, [LAYERED_FIT]) == []


def test_a_scatter_without_fitted_values_fails_the_same_assertion():
    scatter = {
        "kind": "vega-lite",
        "title": "Linear Regression",
        "spec": {
            "layer": [{"mark": {"type": "point"}, "encoding": {"x": {"field": "BMI"}, "y": {"field": "Glucose"}}}]
        },
    }
    out = grade({"mustInclude": [{"kind": "vega-lite", "titleContains": "regression", "encodes": ["yhat"]}]}, [scatter])
    assert len(out) == 1 and "does not encode yhat" in out[0]


def test_an_unlayered_chart_still_has_its_encodings_read():
    flat = {
        "kind": "vega-lite",
        "title": "Scatter",
        "spec": {"mark": "point", "encoding": {"x": {"field": "BMI"}, "y": {"field": "Glucose"}}},
    }
    assert grade({"mustInclude": [{"kind": "vega-lite", "encodes": ["BMI", "Glucose"]}]}, [flat]) == []
