# agents

Evaluation machinery for Galaxy agents. The agent under test lives in its own repository and
is treated as a system under test: named by `OLIT_ROOT`, pinned to a commit when results are
being produced.

![Passing scenarios per baseline](evals.svg)

Each point is a baseline run of the full suite, labelled by date, with passing scenarios out of
the tier's total beside it. A scenario passes when every run of it passes.

- **Grounded:** Olit's own scenarios, run against a live Galaxy. They ask whether the agent does
  real Galaxy work correctly.
- **Parity:** scenarios taken from Orbit's own eval suite. They ask whether Olit behaves like
  Orbit on Orbit's tests.

```bash
export OLIT_ROOT=~/loom/browser
export GALAXY_URL=... GALAXY_API_KEY=...

python3 evals/run.py                 # behavioural scenarios against a real model and Galaxy
python3 evals/falsify.py             # break the agent on purpose; an eval that still passes is not measuring
LOOM_ROOT=~/loom python3 seams/check.py    # provenance: what olit carries from Orbit, and what it invented
bash comparison/run-loom.sh          # the same task, run through Orbit, for comparison
```

## The boundary

An agent owns the tests that establish its own correctness. This repo owns everything that
judges an agent from outside: scenarios, harnesses, scoring, falsification, comparison
between agents, and the seam registry that tracks what olit carries from Orbit.

Nothing here reads the agent's source. An agent publishes a description of its own surface,
and every check is a projection of that document:

```bash
cd $OLIT_ROOT && npm run build:session && node dist/session.mjs --describe --root .
```

`description.py` loads it, from a checkout or from a saved JSON file. `subject.py` names the
checkout. Adding a second agent means teaching those two modules how it describes itself, and
adding its registry; it does not mean changing the machinery.

## Layout

```
description.py   an agent's published surface, and the views the checks read it through
subject.py       which checkout is under test, and how its code is reached
evals/           scenarios, the in-process harness, assertions, falsification, fixtures
seams/           the Orbit provenance registry and its checks
comparison/      the same task driven through Orbit, for a paired result
```
