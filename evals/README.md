# olit evals

Scenario-driven tests of what the agent accomplishes against a real model and a real Galaxy.
The unit suites in loom check that the machinery is correct; these check the behaviour and the
Galaxy state it leaves, which a unit test cannot answer.

```bash
python evals/run.py                    # every scenario on the default model, galaxy-ai
python evals/run.py --all-models       # every scenario x every available model
python evals/run.py plan-creation      # filter scenarios by substring
python evals/run.py --model gemini     # filter models by substring
python evals/run.py --json out.json    # write transcripts + chat text + failures, pass or fail
python evals/run.py --delay 5          # extra pacing on top of the provider's own
python evals/run.py --repeat 3         # run each scenario 3 times; n=1 hides variance
```

**Pacing.** Each model resolves through the agent's provider registry, so the run is
throttled at the endpoint's own rate — Gemini's free tier is 5 requests/minute, and
that number lives in olit's `core/src/agent/providers.ts`. `--delay` adds more on top and defaults to none.
A per-day quota is a different thing and no amount of pacing fixes it. A quota-limited
run is reported as `quota`,
counted in its own column, **not graded, and does not fail the suite** — an exhausted
account is a fact about the key, not about the agent, and must never be readable as
a behavioural failure.

## What the run needs

`run.py` exits without running unless `GALAXY_URL` and `GALAXY_API_KEY` name a Galaxy it
can stage histories in.

```bash
export GALAXY_URL=http://localhost:8080
export GALAXY_API_KEY=...          # User > Preferences > Manage API Key
```

Optional settings, all read from the environment:

| Variable | Default | What it sets |
|---|---|---|
| `OLIT_ROOT` | `~/orbit/browser` (via `make`) | the agent checkout under test; the tests need it too |
| `OLIT_EVAL_GALAXY_WAIT` | 600 | seconds any one wait on Galaxy may take, staging and jobs alike |
| `OLIT_EVAL_CAPABILITIES` | `llm,local,read,write` | the tool families a scenario without its own gets |
| `OLIT_EVAL_MAX_STEPS` | the agent's own | the step cap per turn |
| `OLIT_EVAL_MAX_TOKENS` | none | an output ceiling, for endpoints that reserve credit against the maximum |
| `LOOM_SCENARIOS` | `~/orbit/evals/scenarios` | where loom's scenarios are read from |
| `GALAXY_USER`, `GALAXY_PASSWORD` | none | the account `--watch galaxy` signs in as |
| `OLIT_WATCH_PORT` | 5180 | the dev server `--watch` starts |
| `GALAXY_TEST_DATA`, `GALAXY_ROOT` | none | a galaxy-test-data clone, or a Galaxy checkout whose cache holds one |
| `LOCAL_LLM_URL` | none | the endpoint of the `local-llama` model in `models.json` |

A scenario can say what it needs. `"requires": {"tools": [...]}` names tools by short id; a
Galaxy without them skips the scenario and the header says which are missing. `"optIn": "<label>"`
keeps an expensive scenario out of the default run; `--include <label>`, or naming the scenario,
runs it.

A floor (`floors/`) runs a scenario's known-good route with a scripted model in place of a real
one, graded by the scenario itself. If the floor passes and a model run fails at a step the floor
took, the model's decision failed, not Olit.

A tool-test scenario grades the output against the tool test's own expectation. Galaxy
serves a test's **inputs** through `test_data_download` but answers 404 for its expected
**outputs**, so those live in `fixtures/tool-tests/`, committed, and a run needs nothing
else. `GALAXY_TEST_DATA` still names a galaxy-test-data clone and is searched after the
vendored copy, for an expectation that is not worth committing.

## How it runs

loom spawns `loom --mode json` and parses its event stream, and Olit is driven the same
way: `lib/harness.py` starts the checkout's `dist/session.mjs` (`npm run build:session`),
the same `Session` the browser worker runs, and speaks JSON lines to it. A turn streams its
events before its result, and the result carries the whole transcript. The checkout has to
be rebuilt after a change to its source, or a run grades the previous build.

**Galaxy and the LLM are both real.** A scenario stages its own history on the Galaxy named
by `GALAXY_URL`, so a tool call reaches the server it would reach in the browser.

**`--watch` runs it in a browser you can watch.** `lib/watch.py` starts Olit's dev server against
the same Galaxy, and `lib/watch_drive.mjs` types each input into the real page with Playwright
(Chrome if installed), waiting out Galaxy jobs and the follow-ups they start. The run is graded
off the conversation the page saves to Galaxy, so the verdict means the same as a headless one.
`--video` records it as `records/videos/<timestamp>-<scenario>.mp4` (or in a `DIR` given after it), condensed with
ffmpeg: frames that change nothing, or too little of the picture (a spinner while a job waits), are dropped, so
waits on the model and on jobs leave the video; the end of each turn is held long enough to read. It is encoded as
H.264, which GitHub, Drive and Slack play as is. The knobs are in `settings.py`.
`--slow-mo MS` slows it, `--hold N` keeps the window open. It needs a scenario that does not restart or approve, and on
the dev server one that stages a dataset.

A scenario's `page` assertion grades the page itself, and only a watched run checks it: `"page": {"chart": ["renders",
"hover", "export"]}` asks that the chart in the pane drew marks without an error, that hovering a mark shows a tooltip,
and that the chart's menu saves a PNG.

```bash
python evals/run.py demo-volcano-from-gtn --watch --video
```

`--watch galaxy` opens Olit as `GALAXY_URL` serves it instead, inside Galaxy's own UI with its history panel, so the
video shows the whole Galaxy page. No dev server starts: the plugin Galaxy has installed is the one under test. The
browser signs in through Galaxy's login form as `GALAXY_USER` with `GALAXY_PASSWORD`, the account `GALAXY_API_KEY`
belongs to, makes the run's history current, closes the tool panel so Olit and the history get the width, and drives Olit in
the frame Galaxy mounts it in. A scenario with an
`emptyHistory` opens Olit on that history with no dataset, so the run can bring its own data in. A plugin build reaches
no model but Galaxy's own, so this runs only on Galaxy AI.

**Some scenarios grade the data path.** `analysis-csv-columns` and its siblings fetch real
dataset bytes through `download_dataset` and compute over the file with `run_python`. Grading
only *which tool calls are emitted* left a blind spot: inline dataset content breaking tool-call
JSON, and a gateway 500 once a real tool result was echoed back, sat outside what the plan
scenarios measure. A model that computes over the preview rather than the file answers wrongly
and is marked wrong rather than passing by luck; the `chatText` falsify break proves it.

**A scenario sets its own tool surface.** A loom scenario carries loom's `--tools` restriction
and runs under the matching capability set; other scenarios get the full tool surface the
plugin ships with.

## Models

`models.json` is the matrix. A model whose `envRequires` are unset is **skipped, not
failed**, so a local run works without every credential. Every entry is
OpenAI-compatible, which is also how olit reaches Galaxy's chat proxy in production,
so adding a provider is a JSON entry and no code change.

`models.json` lists every entry; each names its provider and the environment variable its
key comes from. The `or-*` entries reach several vendors through OpenRouter on one key, the
`or-free-*` ones need no credits, and `js2-*` runs on Jetstream2 for ACCESS accounts.

Keys come from the environment. Note `~/.zshrc` is not read by non-interactive
shells — `source ~/.zshrc` first, or put the export in `~/.zshenv`.

## Dimensions

Runs are graded on loom's four decision-correctness dimensions rather than a single
pass/fail, because "did it behave well" is not one question:

- **validity** — a well-formed `## Plan X: <title> [routing]` block with enough
  described steps. The gate: a model that cannot emit a parseable plan fails
  everything downstream.
- **routing** — did it pick the right tag? Scenarios name the *correct* answers, so a
  wrong route is graded wrong rather than waved through. olit routes `[remote]` only;
  the parser still accepts any other tag, `[galaxy]`, `local` and `hybrid` included, so a
  wrong route is a routing failure and not a parse failure.
- **tools** — did it name a plausible analysis tool? A generous allow-set per assay,
  a coarse heuristic rather than an oracle.
- **behavior** — contract checks needing no Galaxy: does an underspecified prompt
  produce a question rather than a fabricated plan, and does the approval gate hold
  (`doesNotExecute`).

## One deliberate difference from loom

loom reads plans from "wherever they land" — notebook or chat — because its matrix
models collapse its four-stage gate in different ways and it did not want to grade
process. olit reads from **chat**, because its gate is explicit that the draft is
drawn in chat and only an *approved* plan reaches the record. A scenario that never
approves anything should therefore find nothing on the record, and that is a property
worth grading rather than papering over.

## Scenarios from loom

Scenarios in loom's `evals/scenarios/` run here too, read unmodified at run time by
`lib/loom_scenarios.py`. It translates only what has a faithful counterpart:

| loom | here |
|---|---|
| `mcp__galaxy__<tool>` | the bare tool name |
| `events` | pi's lifecycle names mapped to the `turn_start`/`turn_end` this harness emits |
| `plan.routingIn` | narrowed to the tags the agent routes (`remote`) |
| `loomArgs: ["--tools", ...]` | the nearest capability set; loom restricts tools, this suite gates families |
| `runs` / `requiresModel` | honoured; loom defaults model scenarios to 3 runs |

Anything else is skipped and the run header says why: an assertion family or key no checker
reads (`activity`, `notebook`, `toolResults`, `plan.source`), a scenario needing loom's project
directory or configuration (`cwd/`, `env`, `config`), a tool or routing tag the agent does not
have, or an assertion that no turn starts. The reasons are read off each scenario, so a new
loom scenario is picked up, or skipped with its reason, with no change here. Point the loader
elsewhere with `--loom <dir>` or `LOOM_SCENARIOS`, or skip loom's with `--only-local`.

A scenario of this repo naming a key no checker reads is refused at load time.

**A few scenarios are model-knowledge probes, and their description says so in its first
line.** They grade what the agent knows rather than whether a feature works, so a red verdict
is a score for the model under test, not a defect to chase. `plan-collections-tags-datatype`
is one: it forbids execution and asks whether the agent knows gzipped reads take
`fastqsanger.gz`. The feature it describes is graded separately, against Galaxy state, by
`collection-from-paired-reads`. Keep the pair apart when reading a snapshot.

## What this is not

**Not a single-run verdict.** Behaviour varies run to run, and n=1 hides it — a scenario
in this suite has already passed and failed on the same model within an hour. `--repeat N`
runs each (scenario, model) tuple N times and lists any tuple whose runs disagreed under
**Unstable**. Check that list before quoting a number: a cell that is not unanimous cannot
honestly be reported as one verdict. loom's `findings.md` specifies this as "Phase 6" and
has not built it yet.

Not a correctness oracle for plans. `mentionsOneOf` checks that a plan names a plausible tool
for the assay, not that the analysis is right; loom leaves that to a judge layer and so does
this. Results are judged where Galaxy holds them: datasets, jobs, invocations, pages.
