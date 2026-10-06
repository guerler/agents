# Eval snapshots

One JSON per full eval-suite run, written by `python3 evals/ingest_run.py <suite --json output>`.

The raw suite result carries every transcript and runs to several megabytes. These
snapshots keep only what stays useful: the per-scenario verdict, the failures behind it,
the worst step count, the tools each run called, and the tier totals. Tens of KB each, so the
history is cheap to keep and a later run can be compared scenario by scenario.

    run-<date>.json              one suite run
    run-<date>-2.json            a second run the same day

## Shape

    date, model, commit, note, runs
    tiers.parity     { scenarios, clean, failing, environment }
    tiers.grounded   { scenarios, clean, failing, environment }
    scenarios[]      { scenario, tier, runs[], tools[][], verdict, max_steps, failures[] }

**The two tiers are counted apart and must stay that way.** `parity` is the Loom-shared
set, which measures whether Olit behaves as the reference evaluation expects. `grounded`
reads authoritative Galaxy state. A parity failure and a grounded failure are different
kinds of evidence, and a combined number would hide which one moved.

`environment` counts scenarios lost to a provider fault or an external host, never charged
to the agent. A scenario that both failed and errored across its runs counts as failing.

`verdict` is `pass` only when every run of that scenario passed, so an unstable scenario
reads as failing rather than being averaged away.

`tools` holds one list per run, in call order. It is what shows the route the agent took, so a
scenario that starts passing for a different reason is visible rather than just green.

## Overview chart

`overview.svg` plots each tier's clean share across every snapshot, labelled by date and, where
the snapshot records one, the Olit `build`. Regenerate it after ingesting a run:

    python3 evals/chart.py
