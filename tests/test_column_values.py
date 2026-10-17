"""A data_column stores an index, and a saved config that names one is graded as wrong."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evals"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.grading.visualization import _named_columns  # noqa: E402

PLUGIN = {
    "tracks": [
        {"name": "x", "type": "data_column"},
        {"name": "y", "type": "data_column"},
        {"name": "label", "type": "data_column"},
        {"name": "name", "type": "text"},
        {"name": "type", "type": "select"},
    ],
    "settings": [{"name": "x_axis_label", "type": "text"}],
}


class Galaxy:
    def call(self, path):
        return PLUGIN


def detail(tracks, settings=None):
    return {"type": "plotly", "latest_revision": {"config": {"tracks": tracks, "settings": settings or {}}}}


def test_indices_and_the_auto_sentinel_pass():
    tracks = [{"x": "auto", "y": "1", "label": "0", "name": "Hydrophobicity", "type": "lines"}]
    assert _named_columns(Galaxy(), detail(tracks)) == []


def test_a_column_name_is_reported():
    tracks = [{"x": "Aminoacid", "y": "Buried", "label": "Aminoacid"}]
    named = _named_columns(Galaxy(), detail(tracks))
    assert "y='Buried'" in named and "x='Aminoacid'" in named


def test_every_offending_track_is_reported_not_just_the_first():
    tracks = [{"y": "Volume"}, {"y": "2"}, {"y": "Beta"}]
    assert _named_columns(Galaxy(), detail(tracks)) == ["y='Volume'", "y='Beta'"]


def test_a_text_input_may_hold_a_name():
    # `name` is the track's label, not a column, so a word there is correct.
    tracks = [{"y": "1", "name": "Hydrophobicity", "type": "lines"}]
    assert _named_columns(Galaxy(), detail(tracks)) == []


def test_a_settings_text_input_is_left_alone():
    assert _named_columns(Galaxy(), detail([], {"x_axis_label": "Aminoacid"})) == []


def test_an_empty_value_is_not_an_index():
    assert _named_columns(Galaxy(), detail([{"y": ""}])) == ["y=''"]


def detail_only(tracks):
    return {"type": "plotly", "latest_revision": {"config": {"tracks": tracks, "settings": {}}}}


def test_the_survivors_of_a_removal_keep_their_original_indices():
    from lib.grading.visualization import _excluded_columns

    kept = [{"y": "1"}, {"y": "2"}, {"y": "3"}, {"y": "6"}, {"y": "7"}]
    assert _excluded_columns(Galaxy(), detail_only(kept), {"y": ["4", "5"]}) == []


def test_renumbering_the_survivors_is_caught():
    from lib.grading.visualization import _excluded_columns

    # Dropping columns 4 and 5 then renumbering 1..5 plots the removed data under the
    # remaining names, which every other check here passes.
    renumbered = [{"y": "1"}, {"y": "2"}, {"y": "3"}, {"y": "4"}, {"y": "5"}]
    held = _excluded_columns(Galaxy(), detail_only(renumbered), {"y": ["4", "5"]})
    assert held == ["y='4'", "y='5'"]


def test_dropping_the_wrong_column_is_caught():
    from lib.grading.visualization import _excluded_columns

    wrong = [{"y": "1"}, {"y": "2"}, {"y": "3"}, {"y": "4"}, {"y": "7"}]
    assert _excluded_columns(Galaxy(), detail_only(wrong), {"y": ["4", "5"]}) == ["y='4'"]


def test_an_integer_in_the_spec_matches_a_string_in_the_config():
    from lib.grading.visualization import _excluded_columns

    assert _excluded_columns(Galaxy(), detail_only([{"y": "4"}]), {"y": [4]}) == ["y='4'"]


def test_another_input_holding_that_value_is_not_the_one_under_test():
    from lib.grading.visualization import _excluded_columns

    # x may legitimately still point at column 4; the request removed it as a y series.
    assert _excluded_columns(Galaxy(), detail_only([{"x": "4", "y": "1"}]), {"y": ["4"]}) == []
