# Eval snapshots

One JSON per full eval-suite run, written by `python3 evals/ingest_run.py <suite --json output>`.

A baseline's raw output stays on local disk under `records/`, which holds only folders and is never
committed: `records/baselines/<date>.json` and `.log` for the suite's `--json` output and its console,
`records/content/<timestamp>/` for each run's model traffic, `records/videos/` for watched runs.

The raw suite result carries every transcript and runs to several megabytes. These
snapshots keep only what stays useful: the per-scenario verdict, the failures behind it,
the worst step count, the tools each run called, and the totals. Tens of KB each, so the
history is cheap to keep and a later run can be compared scenario by scenario.

    run-<date>.json              one suite run
    run-<date>-2.json            a second run the same day

## Shape

    date, model, commit, note, runs
    totals         { scenarios, clean, failing, environment }
    scenarios[]    { scenario, source, runs[], tools[][], verdict, max_steps, failures[] }

`source` says where a scenario is defined: `agents` for this repo's, `loom` for one read from
loom's scenario directory. Both are graded the same way and counted in one total.

`environment` counts scenarios lost to a provider fault or an external host, never charged
to the agent. A scenario that both failed and errored across its runs counts as failing.

`verdict` is `pass` only when every run of that scenario passed, so an unstable scenario
reads as failing rather than being averaged away.

`tools` holds one list per run, in call order. It is what shows the route the agent took, so a
scenario that starts passing for a different reason is visible rather than just green.

## Overview chart

`evals.svg` at the repository root plots the passing scenarios across every snapshot,
labelled by date. Regenerate it after ingesting a run:

    python3 evals/chart.py           # needs matplotlib

## Archive

`archive/` holds the snapshots up to 2026-10-16 and their chart. They scored a separate parity
tier of loom scenarios, adapted loosely enough that some graded on nothing, so they are kept to
read, not to compare against.
