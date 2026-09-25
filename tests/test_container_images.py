"""A declared container image is graded against the registry, not against a pattern."""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evals"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import lib.assertions as A  # noqa: E402

YAML = """class: GalaxyUserTool
id: clean
container: {image}
shell_command: python3 -c 'pass'
"""


def grade(image, monkeyed, spec={"resolves": True}):
    run = types.SimpleNamespace(chat_text=YAML.format(image=image))
    failures = []
    real = A._image_exists
    A._image_exists = lambda i: monkeyed
    try:
        A._container(spec, run, failures, set())
    finally:
        A._image_exists = real
    return [f.detail for f in failures]


def test_an_image_the_registry_has_passes():
    assert grade("quay.io/biocontainers/samtools:1.17--h00cdaf9_0", True) == []


def test_an_invented_tag_fails():
    out = grade("quay.io/biocontainers/pandas:2.2.1--pyhd8ed1ab_0", False)
    assert len(out) == 1 and "would fail to pull" in out[0]


def test_an_unverifiable_image_fails_rather_than_passing_quietly():
    out = grade("python:3.11-slim", None)
    assert len(out) == 1 and "nothing can confirm it exists" in out[0]


def test_a_reply_with_no_container_line_fails():
    run = types.SimpleNamespace(chat_text="here is a tool definition")
    failures = []
    A._container({"resolves": True}, run, failures, set())
    assert [f.assertion for f in failures] == ["container.declared"]


def test_the_line_is_read_through_quoting_and_a_docker_scheme():
    for written in ('"docker://quay.io/biocontainers/x:1"', "'quay.io/biocontainers/x:1'"):
        run = types.SimpleNamespace(chat_text=YAML.format(image=written))
        assert A.CONTAINER_LINE.findall(run.chat_text) == ["quay.io/biocontainers/x:1"]


def test_resolution_is_skipped_when_the_scenario_only_wants_a_container():
    """`resolves: false` still requires the line, but asks the registry nothing."""
    assert grade("python:3.11-slim", None, spec={"resolves": False}) == []
