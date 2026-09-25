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
