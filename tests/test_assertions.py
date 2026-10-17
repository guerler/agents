"""Graders read what the run left, and fail what only looks done."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "evals"))

import subject  # noqa: E402


def test_not_empty_fails_a_page_that_is_actually_empty():
    """The original check only looked for the starter text, so `""` passed it."""
    from lib.assertions import _record

    class FakeGalaxy:
        def __init__(self, content):
            self._content = content

        def call(self, path):
            if path.startswith("api/pages?"):
                return [{"id": "p1", "slug": "olit-s1"}]
            return {"content": "", "content_editor": self._content}

    class Run:
        def __init__(self, content):
            self.staged = {"galaxy": FakeGalaxy(content), "history_id": "h1", "session_id": "s1"}

    def grade(content):
        failures, _ = [], set()
        _record({"notEmpty": True}, Run(content), failures, set())
        return [f.assertion for f in failures]

    STARTER = subject.describe()["record"]["starter"]

    assert grade("") == ["record.notEmpty"]
    assert grade("   \n ") == ["record.notEmpty"]
    # the starter alone is not a written record
    assert grade(STARTER) == ["record.notEmpty"]
    assert grade("## Record\n\nRan Grouping1; mean Glucose 141.3") == []


def test_not_empty_accepts_content_appended_below_the_starter():
    """The agent had written; it just left the placeholder above its entry."""
    from lib.assertions import _record

    STARTER = subject.describe()["record"]["starter"]

    class FakeGalaxy:
        def __init__(self, content):
            self._c = content

        def call(self, path):
            if path.startswith("api/pages?"):
                return [{"id": "p1", "slug": "olit-s1"}]
            return {"content": "", "content_editor": self._c}

    class Run:
        def __init__(self, content):
            self.staged = {"galaxy": FakeGalaxy(content), "history_id": "h1", "session_id": "s1"}

    def grade(content):
        failures = []
        _record({"notEmpty": True}, Run(content), failures, set())
        return [f.assertion for f in failures]

    assert grade(STARTER) == ["record.notEmpty"]
    # a line the agent added counts, wherever it sits relative to the starter
    assert grade(STARTER + "\n## Findings\n\nmean Glucose 141.3\n") == []
    assert grade("## Findings\n\nmean Glucose 141.3\n\n" + STARTER) == []
    # a starter line quoted inside real content must not subtract from it
    assert grade(STARTER + "\n## Record\n") == ["record.notEmpty"]
    assert grade(STARTER + "\n## Record\n\nRan Grouping1\n") == []


def test_chat_text_includes_what_finish_said():
    """The shell renders `finish`'s summary as the closing reply, so grading must see it."""
    from lib.harness import RunResult

    run = RunResult(
        [
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "finish"}}]},
            {"role": "tool", "name": "finish", "content": "The histogram shows a normal spread."},
        ],
        [],
        [],
    )
    assert "normal spread" in run.chat_text


def test_chat_text_ignores_other_tool_results():
    """A tool's data is not something the user read."""
    from lib.harness import RunResult

    run = RunResult(
        [
            {"role": "assistant", "content": "Here is the answer."},
            {"role": "tool", "name": "get_history_contents", "content": '[{"id": "abc"}]'},
        ],
        [],
        [],
    )
    assert run.chat_text == "Here is the answer."


def test_a_paused_dataset_is_not_work_the_agent_should_have_waited_for():
    """Galaxy pauses a dependent job when its input fails, and that state never advances."""
    from lib.assertions import _history

    class FakeGalaxy:
        def __init__(self, states):
            self._states = states

        def call(self, path):
            return [
                {
                    "history_content_type": "dataset",
                    "id": f"d{i}",
                    "name": f"out {i}",
                    "state": state,
                    "deleted": False,
                }
                for i, state in enumerate(self._states)
            ]

    class Run:
        def __init__(self, states):
            self.staged = {"galaxy": FakeGalaxy(states), "history_id": "h1", "dataset_ids": {}}

    paused = []
    _history({"producedDatasets": 1}, Run(["ok", "paused"]), paused, set())
    assert paused == [], paused

    queued = []
    _history({"producedDatasets": 1}, Run(["ok", "queued"]), queued, set())
    assert [f.detail for f in queued] == ["answered while output was still ['out 1']"]


def test_a_visualization_check_holds_in_one_visualization_not_across_several():
    from lib.assertions import _visualization

    def viz(vid, settings, tracks):
        return {
            "id": vid,
            "type": "igv",
            "latest_revision": {"config": {"dataset_id": "bed", "settings": settings, "tracks": tracks}},
        }

    class FakeGalaxy:
        def __init__(self, saved):
            self.saved = {v["id"]: v for v in saved}

        def call(self, path):
            if path == "api/visualizations":
                return [{"id": vid} for vid in self.saved]
            return self.saved[path.rsplit("/", 1)[1]]

    class Run:
        def __init__(self, saved):
            self.staged = {"galaxy": FakeGalaxy(saved), "dataset_ids": {"tracks.bed": "bed", "f.gff3": "gff"}}

    spec = {
        "referencesDataset": "$staged:tracks.bed",
        "settingsContain": {"locus": "chr1"},
        "tracksDataset": ["$staged:f.gff3"],
    }

    def grade(saved, extra=None):
        failures = []
        _visualization({**spec, **(extra or {})}, Run(saved), failures, set())
        return [f.assertion for f in failures]

    revised = viz("v1", {"locus": "chr1"}, [{"urlDataset": {"id": "gff"}}])
    assert grade([revised]) == []
    split = [viz("v1", {"locus": "chr1"}, []), viz("v2", {}, [{"urlDataset": {"id": "gff"}}])]
    assert grade(split) == ["visualization.oneVisualization"]
    assert grade([revised, viz("v2", {}, [])], {"count": 1}) == ["visualization.count"]


def test_a_step_is_read_where_the_latest_plan_writes_it():
    """A revised plan repeats a step's title. The first draft described it after a dash on the
    line, the revision in bullets below it; each is described, and the latest is the one graded."""
    from lib.assertions import _plan

    first = (
        "```plan\n## Plan A: Rename [remote]\n\n### Steps\n\n"
        "- [ ] 1. **Back up** – copy the folder.\n"
        "- [ ] 2. **Define the convention** – decide on the pattern (e.g.\n"
        "  `YYYYMMDD.pdf`), written to a map.\n```"
    )
    revised = (
        "```plan\n## Plan A: Rename [remote]\n\n### Steps\n\n"
        "- [ ] 1. **Back up**\n  - Routing: local\n"
        "- [ ] 2. **Define the convention**\n  - Routing: local\n```"
    )

    class Run:
        chat_text = first + "\n\nRevised:\n\n" + revised

    failures = []
    _plan({"eachStepHasDescription": True}, Run(), failures, set())
    assert [f.detail for f in failures] == []


def test_landed_pdfs_want_the_papers_asked_for_and_not_a_pile():
    """The NAR papers request: the six papers as PDFs pass; the shapes a user actually got fail."""
    from lib.assertions import _history

    papers = {"2016": ["PMC4987906", "W2346143774"], "2024": ["PMC11223835", "W4398182689"]}

    class FakeGalaxy:
        def __init__(self, contents):
            self._contents = contents

        def call(self, path):
            return self._contents

    class Run:
        def __init__(self, contents):
            self.staged = {"galaxy": FakeGalaxy(contents), "history_id": "h1", "dataset_ids": {}}

    def pdf(name, state="ok"):
        return {"history_content_type": "dataset", "id": name, "name": name, "extension": "pdf", "state": state}

    def grade(contents):
        failures = []
        _history({"landedPdfs": {"max": 25, "papers": papers}}, Run(contents), failures, set())
        return [f.detail for f in failures]

    assert grade([pdf("PMC4987906"), pdf("PMC11223835")]) == []
    assert grade([pdf("001_W2346143774_The Galaxy platform"), pdf("PMC11223835")]) == []

    pile = [pdf(f"PMC{i}") for i in range(30)] + [pdf("PMC4987906"), pdf("PMC11223835")]
    assert grade(pile) == ["32 PDFs landed, at most 25 fit the request; the papers asked for are buried"]

    citing = [pdf("001_W2121180999_Quality control and preprocessing of metagenomic datasets")]
    assert grade(citing) == ["no PDF of ['2016', '2024'] among 1 landed"]

    running = [{"history_content_type": "dataset_collection", "id": "c1", "name": "PDFs", "populated_state": "new"}]
    assert grade(running) == ["answered while downloads were still ['PDFs']"]


def test_a_value_the_server_states_is_read_from_it_not_written_down():
    """An instance's version differs between servers, so a scenario names where to read it."""
    from lib.assertions import _chat_text

    class FakeGalaxy:
        def call(self, path):
            return {"version_major": "26.1"} if path == "api/version" else {}

    class Run:
        def __init__(self, text, staged=True):
            self.chat_text = text
            self.staged = {"galaxy": FakeGalaxy(), "history_id": "h1"} if staged else None

    spec = {"mustIncludeFromGalaxy": [{"get": "api/version", "field": "version_major"}]}
    for run, failing in ((Run("This is Galaxy 26.1."), False), (Run("This is Galaxy 26.2."), True)):
        failures = []
        _chat_text(spec, run, failures, set())
        assert bool(failures) is failing, run.chat_text
    failures = []
    _chat_text(spec, Run("26.1", staged=False), failures, set())
    assert failures and "no Galaxy" in failures[0].detail


def test_record_ids_are_judged_by_length_without_asking_galaxy():
    """A job id in the record is no dataset; on a shared server Galaxy answers 403 for it."""
    from lib.assertions import _record_ids_resolve

    failures = []
    _record_ids_resolve({}, None, failures, "job `4838ba20a6d86765d07974451a31d1b7`, truncated `4838ba20a6d8`")
    assert [f.assertion for f in failures] == ["record.idsResolve"]


def test_a_failed_dataset_is_named_by_its_name_or_number():
    from lib.assertions import _names_dataset

    failed = {"name": "Filter on data 1", "hid": 2}
    for text, named in (
        ("the filter on data 1 job failed", True),
        ("dataset 2 is in error", True),
        ("#2 failed", True),
        ("no errors, 12 rows", False),
        ("dataset 21 is ok", False),
    ):
        assert _names_dataset(text, failed) is named, text
    assert not _names_dataset("anything", {"name": "", "hid": None})
