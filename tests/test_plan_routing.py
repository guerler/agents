"""The retired `[galaxy]` tag grades as the `[remote]` it means."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "evals"))

from lib.plan import parse_latest_plan  # noqa: E402


def _plan(tag):
    return f"## Plan A: chrM variant calling [{tag}]\n\n- [ ] Align the reads\n"


def test_remote_is_read_as_remote():
    assert parse_latest_plan(_plan("remote")).routing == "remote"


def test_the_retired_galaxy_tag_is_read_as_remote():
    assert parse_latest_plan(_plan("galaxy")).routing == "remote"


def test_another_tag_is_left_alone():
    assert parse_latest_plan(_plan("local")).routing == "local"
