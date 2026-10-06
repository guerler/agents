#!/usr/bin/env python3
"""Prove a scenario can fail.

A scenario that has never failed is an untested test. Both scenarios written on
2026-09-17 passed against deliberately broken code before they earned their place -- once
because they targeted a code path that was never broken, once because the assertion's own
fallback masked the defect. Neither was visible from reading them.

So each assertion family here names a break in *product* code that should make the
scenarios using it go red. Breaking the grader instead would only test the grader against
itself, which is the mistake this file exists to avoid.

Usage:
    GALAXY_URL=... GALAXY_API_KEY=... python3 evals/falsify.py [--model ID] [family ...]

The working tree is restored with `git checkout` after every break, including on failure
or interrupt: this edits real source, so it must never leave the tree broken.
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import subject  # noqa: E402

ROOT = subject.root()
HERE = pathlib.Path(__file__).resolve().parent


class Break:
    """One targeted defect, the scenarios it should sink, and the assertion that catches it."""

    def __init__(self, family, why, scenarios, expect, path=None, find=None, replace=None, edits=None):
        self.family = family
        self.why = why
        self.scenarios = scenarios
        self.expect = expect
        self.edits = edits or [(path, find, replace)]

    def _paths(self):
        return sorted({ROOT / path for path, _, _ in self.edits})

    def apply(self):
        for path, find, replace in self.edits:
            target = ROOT / path
            source = target.read_text()
            found = source.count(find)
            if found != 1:
                self.restore()
                raise SystemExit(
                    f"{self.family}: anchor appears {found} times in {target.name}; "
                    "the break is stale and would patch the wrong thing"
                )
            target.write_text(source.replace(find, replace))
        build()

    def restore(self):
        for target in self._paths():
            subprocess.run(["git", "checkout", "--", str(target)], cwd=ROOT, check=True)
        build()


def build():
    """The session a run drives is built from source, so an edit counts only once rebuilt."""
    subprocess.run(subject.BUILD["olit"], shell=True, cwd=ROOT, check=True, capture_output=True)


BREAKS = [
    Break(
        family="artifacts",
        why="a produced chart is never handed to the shell, so nothing renders for the user",
        path="src/agent/tool.ts",
        find="  ctx.artifacts.produced.push(artifact);",
        replace="  // FALSIFY: never handed to the shell",
        scenarios=[
            "chart-bar-groups-by-category",
            "chart-histogram-embeds-bins",
            "chart-scatter-references-dataset",
            "chart-treemap-embeds-layout",
        ],
        expect=["artifacts.kind", "artifacts.mustInclude"],
    ),
    Break(
        family="record.write",
        why="the page write is dropped, so the agent reports saving and nothing lands",
        path="src/agent/ops.ts",
        find="      const envelope = (await (policy.around ? policy.around(call) : call())) as unknown as Record<\n",
        replace='      const envelope = (op.name === "update_page" // FALSIFY: write dropped\n'
        '        ? { success: true, data: { id: args.page_id, content_hash: "0" } }\n'
        "        : await (policy.around ? policy.around(call) : call())) as unknown as Record<\n",
        scenarios=["record-page-holds-content", "research-glucose-bmi"],
        expect=["record.notEmpty", "record.mustMention"],
    ),
    Break(
        family="chatText",
        why="only the preview is downloaded, so a sum over it is quietly wrong",
        path="src/agent/galaxy-tools.ts",
        find="export const MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024;",
        replace="export const MAX_DOWNLOAD_BYTES = 512; // FALSIFY: truncated download",
        scenarios=["analysis-csv-columns"],
        expect=["chatText.mustMatch"],
    ),
    Break(
        family="planGate",
        why="the approval-gate convention is dropped from the prompt, so nothing holds the agent "
        "back from acting before the user approves",
        path="src/agent/prompt.ts",
        find="    PLAN_CONVENTION,\n",
        replace="    // FALSIFY: approval gate dropped\n",
        scenarios=["gate-holds-before-approval"],
        expect=["behavior.doesNotExecute", "plan.exists", "plan.routingIn"],
    ),
    Break(
        family="toolInputs",
        why="run_tool submits no inputs, so the job produces something that is not the answer "
        "while the agent reports success",
        path="src/agent/ops.ts",
        find="      const input = inputOf(args, toInput);\n",
        replace="      const input = inputOf(args, toInput);\n"
        '      if (op.name === "run_tool") input.inputs = {}; // FALSIFY: inputs dropped\n',
        scenarios=["analysis-cat-two-datasets"],
        expect=["toolOutput.matchesToolTest", "toolOutput.produced", "toolOutput.honestReport"],
    ),
    Break(
        family="chatReply",
        why="the assistant's words never reach the transcript, so the user is answered with "
        "silence. Sinks every scenario, so it is only pointed at the canary",
        path="src/agent/model.ts",
        find="        out.end(await stream.result());\n",
        replace="        const m = await stream.result(); // FALSIFY: reply dropped\n"
        '        out.end({ ...m, content: m.content.filter((c) => c.type !== "text") });\n',
        scenarios=["smoke-answers"],
        expect=["messages.repliesInChat", "run.noModelOutput"],
    ),
    Break(
        family="destructiveGate",
        why="nothing is classified as destructive, so a delete request runs unchallenged and "
        "the user's data is gone",
        path="src/agent/destructive.ts",
        find="  if (!destroys(name, args)) {\n",
        replace="  return undefined; // FALSIFY: nothing is destructive\n  if (!destroys(name, args)) {\n",
        scenarios=["gate-refuses-destructive"],
        expect=["history.intact", "behavior.doesNotExecute"],
    ),
    Break(
        family="jobState",
        why="a history listing hides datasets in an error state, the 'clean view' bug: the "
        "failed job is not merely unexplained, it is absent, so the agent reports a tidy "
        "history that is not the one the researcher has",
        path="src/agent/ops.ts",
        find="      ctx.watch.add(watchedFrom(op.name, envelope.data));\n",
        replace='      if (op.name === "get_history_contents") { // FALSIFY: error rows hidden\n'
        "        const listed = envelope.data as { contents: { state?: string }[] };\n"
        '        listed.contents = listed.contents.filter((i) => i?.state !== "error");\n'
        "      }\n"
        "      ctx.watch.add(watchedFrom(op.name, envelope.data));\n",
        # Not reports-a-failed-job: galaxy-ops' get_history_details still reports the failed
        # state, so hiding it from one listing no longer leaves the agent blind there.
        scenarios=["refuses-to-analyse-failed-data"],
        expect=["chatText.mustMatch", "chatText.mustNotMatch"],
    ),
    Break(
        family="galaxyExecution",
        why="run_tool reports success without submitting anything, so no job runs and no "
        "dataset appears. The agent can still describe the work it believes it did",
        path="src/agent/ops.ts",
        find="      const call = () => runWithEnvelope(op, input as never, ctx.ops);\n",
        replace="      const call = async () =>\n"
        '        op.name === "run_tool" // FALSIFY: nothing submitted\n'
        "          ? { success: true, data: { jobs: [], outputs: [] } }\n"
        "          : runWithEnvelope(op, input as never, ctx.ops);\n",
        scenarios=["gtn-tutorial-completed"],
        expect=["history.producedDatasets"],
    ),
    Break(
        family="visualizationSaved",
        why="save_visualization reports success without posting anything, so the agent shows a "
        "viewer in the artifact pane while nothing was stored in Galaxy",
        path="src/agent/visualizations.ts",
        find='    const created = await galaxy.post("api/visualizations", { type: name, title, config });',
        replace='    const created = { id: "falsified" }; // FALSIFY: nothing posted',
        scenarios=["viz-structure-saved"],
        expect=["visualization.exists"],
    ),
    Break(
        family="visualizationArtifact",
        why="the artifact is serialized into the tool result instead of being claimed, so it "
        "never reaches the shell and the user sees no viewer",
        path="src/agent/tool.ts",
        find="rendered({ data: claim(value, ctx) })",
        replace="rendered({ data: value }) /* FALSIFY: not claimed */",
        scenarios=["viz-structure-shown", "viz-structure-saved"],
        expect=["artifacts.kind"],
    ),
    Break(
        family="visualizationSpared",
        why="showing a visualization saves one anyway, the behaviour the show/save split exists " "to end",
        path="src/agent/visualizations.ts",
        find="  const params = { visualization: name, dataset_id: a.dataset_id, ...EMBED };",
        replace='  await galaxy.post("api/visualizations", { type: name, title, config: { dataset_id: '
        "a.dataset_id } }); // FALSIFY: showing saves\n"
        "  const params = { visualization: name, dataset_id: a.dataset_id, ...EMBED };",
        scenarios=["viz-structure-shown"],
        expect=["visualization.absent"],
    ),
    Break(
        family="visualizationTracks",
        why="a revision drops the tracks it was given, so the second dataset never reaches the " "saved config",
        path="src/agent/visualizations.ts",
        find="  if (present(a.tracks)) {\n    config.tracks = a.tracks;\n  }",
        replace="  // FALSIFY: tracks dropped",
        scenarios=["viz-igv-second-track"],
        expect=["visualization.tracksDataset"],
    ),
    Break(
        family="visualizationSettings",
        why="a revision drops the settings it was given, so the genome and the locus never " "reach the saved config",
        path="src/agent/visualizations.ts",
        find="  if (present(a.settings)) {\n    config.settings = a.settings;\n  }",
        replace="  // FALSIFY: settings dropped",
        scenarios=["viz-igv-second-track"],
        expect=["visualization.settingsContain"],
    ),
    Break(
        family="visualizationShape",
        why="a conditional's parameters are written beside it instead of inside it; galaxy-charts "
        "reads the genome from inside the conditional and finds nothing, so the view never moves",
        path="src/agent/visualizations.ts",
        find="    config.settings = a.settings;",
        replace="    config.settings = Object.fromEntries(Object.entries(a.settings).flatMap(([k, v]) => "
        'v && typeof v === "object" && !Array.isArray(v) ? Object.entries(v) : [[k, v]])); '
        "// FALSIFY: conditionals flattened",
        scenarios=["viz-igv-second-track"],
        expect=["visualization.settingsContain"],
    ),
]

# Breaks whose defect now lives in galaxy-ops, which runs these tools: an Olit-side edit would
# leave a truthful source the agent can still read, so it would prove nothing. Their scenarios
# still guard the behaviour; falsifying them means a break in galaxy-ops.
RETIRED = {
    "record.format": "create_page is galaxy-ops' operation; the content format is set there.",
    "toolCount": "get_tool_panel is galaxy-ops' operation and pages the panel itself.",
    "emptyResult": "get_dataset_details is galaxy-ops'; stale metadata has to be injected there.",
    "workflowInvoke": "invoke_workflow is galaxy-ops' operation and builds its own request body.",
}

# `session-resumed-after-close`: admitted provisionally, no break discriminates yet.
#
# No break for the workflow-template projection: unit tests pin it instead.
BY_FAMILY = {b.family: b for b in BREAKS}


def run_scenarios(scenarios, model):
    """Run the named scenarios and return {scenario: [assertion, ...]} for what failed."""
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        report = pathlib.Path(tmp) / "result.json"
        for name in scenarios:
            cmd = [sys.executable, str(HERE / "run.py"), name, "--json", str(report)]
            if model:
                cmd += ["--model", model]
            subprocess.run(cmd, cwd=HERE.parent, capture_output=True, text=True)
            if not report.exists():
                out[name] = ["<no result>"]
                continue
            rows = json.loads(report.read_text())
            hit = [r for r in rows if r["scenario"] == name]
            failed = []
            for r in hit:
                failed += [f["assertion"] for f in (r.get("failures") or [])]
                if r.get("error"):
                    failed.append("run.error")
            out[name] = failed
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("families", nargs="*", help="families to falsify (default: all)")
    ap.add_argument("--model", help="substring filter on model id")
    args = ap.parse_args()

    if not os.environ.get("GALAXY_URL", "").strip():
        print("falsification runs the real suite: export GALAXY_URL and GALAXY_API_KEY")
        return 2

    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", "src/agent"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    if dirty:
        print(
            "working tree has local changes under src/agent/; falsification edits\n"
            "source and restores with `git checkout`, which would discard them:\n" + dirty
        )
        return 2

    wanted = args.families or list(BY_FAMILY)
    unknown = [f for f in wanted if f not in BY_FAMILY]
    if unknown:
        print(f"unknown families: {', '.join(unknown)}\nknown: {', '.join(BY_FAMILY)}")
        return 2

    verdicts = []
    for family in wanted:
        brk = BY_FAMILY[family]
        print(f"\n=== {family}: {brk.why}")
        brk.apply()
        try:
            results = run_scenarios(brk.scenarios, args.model)
        finally:
            brk.restore()
        for scenario, failed in results.items():
            caught = [a for a in failed if a in brk.expect]
            ok = bool(caught)
            verdicts.append((family, scenario, ok))
            mark = "caught" if ok else "MISSED"
            detail = ", ".join(caught or failed) or "nothing failed"
            print(f"  [{mark:6}] {scenario:36} {detail}")

    missed = [(f, s) for f, s, ok in verdicts if not ok]
    print(f"\n{len(verdicts) - len(missed)}/{len(verdicts)} scenario-break pairs caught")
    for family, scenario in missed:
        print(f"  MISSED {scenario} under {family}: it passes against broken code")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
