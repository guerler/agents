# Operation parity: galaxy-mcp against galaxy-ops

The same Galaxy operations, run through the Python MCP server and through
`@galaxyproject/galaxy-ops` against the same server and the same state, with the raw pair of
results kept for reading. It exists because olit is considering galaxy-ops as its execution
layer while keeping the Python server's descriptions as its model-facing contract, and the
invariant that has to hold is:

> anything olit's contract promises the model must actually be provided by the result path.

Upstream's own parity check compares which tools exist and what parameters they take, and
says outright that result shapes are outside it. This is the other half, measured rather than
argued, so drift during a migration arrives in a diff.

## Running it

```bash
GALAXY_URL=http://localhost:8080 GALAXY_API_KEY=... MCP_ROOT=~/galaxy-mcp \
  python3 comparison/ops-parity/run.py
```

Needs galaxy-ops built once (`pnpm install && pnpm --filter @galaxyproject/galaxy-ops build`
in `galaxy-agent-tools`), and a Python with the MCP server's dependencies -- its own venv
does. Exits non-zero when a difference is not yet declared.

## What it compares

Not byte equality: the two spell parameters differently by design, ids and timestamps differ
per run, and list order is not promised. What is compared is what a model could learn.

- **fields** -- every path in `data`, with ids and timestamps masked and lists collapsed to
  the union of their elements, so a page of two and a page of two hundred compare equal.
- **totals** -- the one thing paging has to agree on is how many there are altogether.
- **wrapper** -- a list one side names and the other returns bare is one difference about the
  name, not one per field inside it.
- **messages are recorded and never judged.** Each side writes its own prose.

## Reading a result

`results/<operation>.json` holds the case, both envelopes, and nothing else. The table is a
summary of those files; when a verdict looks wrong, the pair is the evidence.

Each difference carries a verdict from `cases.py`: `equivalent`, `cosmetic`,
`upstream-fixable`, `environment`, `incompatible`. A difference with no verdict is reported
`UNREVIEWED` and fails the run, and a verdict whose difference has gone is reported `STALE`
-- both were how two of the first verdicts here turned out to be wrong.

## Adding a case

Append a dict to `CASES`. `$history`, `$dataset`, `$workflow` and `$tool` are filled from the
server at run time. Arguments are written out per side rather than converted, because the
spelling is one of the things being compared.

Uncovered on purpose: anything with side effects (`run_tool`, `upload_file`,
`download_dataset`). They need a staged history and a cleanup path, and their differences are
already understood as execution policy rather than result shape.
