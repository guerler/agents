#!/usr/bin/env python3
"""Scenario-driven evals for olit. Ported from loom's `evals/run.ts`."""

import argparse
import copy
import json
import logging
import os
import sys
import time
import uuid

logging.basicConfig(level=logging.ERROR)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from lib import loom_scenarios, watch  # noqa: E402
from lib.assertions import DIMENSIONS, evaluate, validate_scenarios  # noqa: E402
from lib.harness import (  # noqa: E402
    label_record_page,
    load_scenarios,
    missing_tools,
    opted_out,
    remove_imported_workflows,
    required_env,
    run_scenario,
    save_session,
)
from lib.tooltests import Galaxy  # noqa: E402

# HTTP 429 means out of budget, not a behavioural failure; graded separately.
QUOTA_STATUS = 429


def is_quota(row):
    """True when a result was rate-limited. `row` is a result dict or a RunResult."""
    status = row.get("statusCode") if isinstance(row, dict) else getattr(row, "status_code", None)
    return status == QUOTA_STATUS


def available_models(matrix, only, every=False):
    usable, skipped = [], []
    default = matrix.get("default") or []
    for model in matrix["models"]:
        if only and only not in model["id"]:
            continue
        if not only and not every and default and model["id"] not in default:
            continue
        missing = [v for v in required_env(model) if not os.environ.get(v)]
        (skipped if missing else usable).append((model, missing))
    return [m for m, _ in usable], skipped


def _resolve_run_tokens(scenario):
    """Replace `$run` with a token unique to this run."""
    token = uuid.uuid4().hex[:10]

    def sub(value):
        if isinstance(value, str):
            return value.replace("$run", token)
        if isinstance(value, list):
            return [sub(v) for v in value]
        if isinstance(value, dict):
            return {k: sub(v) for k, v in value.items()}
        return value

    return sub(copy.deepcopy(scenario))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", nargs="?", help="substring filter on scenario directory")
    ap.add_argument("--model", help="substring filter on model id; without it, the matrix's default models")
    ap.add_argument("--all-models", action="store_true", help="every available model, not only the defaults")
    ap.add_argument("--json", dest="json_out", help="write raw results here")
    ap.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="extra seconds between runs; the provider's own rate limit already applies",
    )
    ap.add_argument(
        "--repeat",
        type=int,
        default=0,
        metavar="N",
        help="override the run count; scenarios shared with loom carry their own",
    )
    ap.add_argument(
        "--loom",
        default=os.environ.get("LOOM_SCENARIOS", os.path.expanduser("~/loom/evals/scenarios")),
        help="loom's scenario directory, the source of truth for shared scenarios",
    )
    ap.add_argument("--only-local", action="store_true", help="skip the scenarios shared with loom")
    ap.add_argument(
        "--records",
        default=os.path.join(os.path.dirname(HERE), "records", "content"),
        help="where each run's completions are recorded; local disk only, never committed",
    )
    ap.add_argument("--no-records", action="store_true", help="run without recording completions")
    ap.add_argument(
        "--no-pages",
        action="store_true",
        help="skip titling each run's record page after its scenario and printing where to read it",
    )
    ap.add_argument(
        "--no-sessions",
        action="store_true",
        help="skip saving each run as an Olit visualization, which is how a run reopens in the UI",
    )
    ap.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="SUBSTRING",
        help="drop scenarios whose id contains this; repeatable. Excluded ids are named in the header",
    )
    ap.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="LABEL",
        help="run the scenarios whose optIn is this label; repeatable. Naming a scenario also opts it in",
    )
    ap.add_argument(
        "--watch",
        nargs="?",
        const="dev",
        choices=["dev", "galaxy"],
        help="type each scenario into Olit's real page in a visible browser, graded off the conversation it saves: "
        "Olit's dev server (dev, the default), or Olit as GALAXY_URL serves it, signed in as GALAXY_USER (galaxy)",
    )
    ap.add_argument(
        "--video",
        nargs="?",
        const="",
        metavar="DIR",
        help="with --watch, record each run's browser as a video, in DIR or in records/videos",
    )
    ap.add_argument("--slow-mo", type=int, default=0, metavar="MS", help="with --watch, slow each browser action")
    ap.add_argument(
        "--hold", type=int, default=0, metavar="SECONDS", help="with --watch, keep the browser open after a run"
    )
    args = ap.parse_args()

    with open(os.path.join(HERE, "models.json")) as f:
        matrix = json.load(f)
    scenarios = load_scenarios(os.path.join(HERE, "scenarios"), args.scenario)
    if not args.only_local:
        if not os.path.isdir(args.loom):
            print(f"  note: loom not found at {args.loom}; running the local set only")
            shared = []
        else:
            shared, skipped = loom_scenarios.load(args.loom, args.scenario)
            for name, why in sorted(skipped.items()):
                print(f"  skip {name}: {why}")
        scenarios = shared + scenarios

    if args.exclude:
        kept = [s for s in scenarios if not any(term in s["id"] for term in args.exclude)]
        for dropped in sorted({s["id"] for s in scenarios} - {s["id"] for s in kept}):
            print(f"  exclude {dropped}: named on the command line")
        scenarios = kept

    if not os.environ.get("GALAXY_URL", "").strip() or not os.environ.get("GALAXY_API_KEY", "").strip():
        print("  evals need GALAXY_URL and GALAXY_API_KEY: they run against a real Galaxy.")
        return 2
    print(f"  galaxy: {os.environ['GALAXY_URL'].strip()}")

    installed = []
    if any((s.get("requires") or {}).get("tools") for s in scenarios):
        galaxy = Galaxy(os.environ["GALAXY_URL"].strip(), os.environ["GALAXY_API_KEY"].strip())
        installed = [t.get("id") or "" for t in galaxy.call("api/tools?in_panel=false") or []]
    runnable = []
    for scenario in scenarios:
        why = opted_out(scenario, args.include, bool(args.scenario)) or missing_tools(scenario, installed)
        if why:
            print(f"  skip {scenario['id']}: {why}")
        else:
            runnable.append(scenario)
    scenarios = runnable

    models, skipped = available_models(matrix, args.model, args.all_models)

    for model, missing in skipped:
        print(f"  skip {model['id']}: needs {', '.join(missing)}")
    if not models:
        print("\nNo runnable models. Set the env vars above, or see evals/README.md.")
        return 1
    if not scenarios:
        print("\nNo scenarios matched.")
        return 1

    problems = validate_scenarios(scenarios)
    if problems:
        print("\nScenarios this grader cannot read:")
        for problem in problems:
            print(f"  {problem}")
        return 1

    started = time.strftime("%Y-%m-%dT%H-%M-%S")
    # A watched run's completions go through the browser, past the recorder.
    records = None if args.no_records or args.watch else os.path.join(args.records, started)
    if args.video == "":
        args.video = os.path.join(os.path.dirname(HERE), "records", "videos")
    if records:
        os.makedirs(records, exist_ok=True)
        print(f"  records: {records}")

    def capture(scenario, model, run_index):
        if not records:
            return None
        name = f"{scenario['id']}--{model['id']}--run{run_index + 1}.jsonl.gz"
        return os.path.join(records, name)

    repeat = max(1, args.repeat) if args.repeat else 0
    times = f" x {repeat} run(s)" if repeat > 1 else " x per-scenario runs"
    print(f"\n{len(scenarios)} scenario(s) x {len(models)} model(s){times}\n")
    results = []
    for model in models:
        for scenario in scenarios:
            runs = repeat or max(1, int(scenario.get("runs") or 1))
            for run_index in range(runs):
                if args.delay and results:
                    time.sleep(args.delay)
                scenario = _resolve_run_tokens(scenario)
                if args.watch:
                    run = watch.run_watched(scenario, model, args.video, args.slow_mo, args.hold, args.watch)
                else:
                    run = run_scenario(scenario, model, capture=capture(scenario, model, run_index))
                if run.error:
                    failures, exercised = [], set()
                    verdict = "quota" if is_quota(run) else ("STALL" if run.stalled else "ERROR")
                    note = run.error.replace("\n", " ")
                else:
                    try:
                        failures, exercised = evaluate(scenario, run)
                        verdict = "pass" if not failures else "FAIL"
                        note = "" if not failures else failures[0].detail
                    except Exception as exc:
                        failures, exercised = [], set()
                        verdict = "ERROR"
                        note = f"could not grade: {type(exc).__name__}: {exc}"
                        run.error = run.error or note
                # After grading, which reads what the run staged.
                remove_imported_workflows(getattr(run, "staged", None))
                # Flushed per result: a full matrix runs for many minutes, and Python
                tag = f" #{run_index + 1}" if runs > 1 else ""
                budget = f" {run.steps}/{run.max_steps} steps" if run.max_steps else ""
                if run.waited >= 1:
                    budget += f" waited {run.waited:.0f}s"
                print(f"  [{verdict:5s}] {model['id']:24s} {scenario['id']:34s}{tag}{budget} {note[:52]}", flush=True)
                for f in failures[1:]:
                    print(f"          {f}")
                if not args.no_pages and run.config:
                    page = label_record_page(run, scenario["id"], model["id"])
                    if page:
                        print(f"          notebook: {page}", flush=True)
                if args.watch:
                    session = watch.retitle(run, scenario["id"], model["id"]) if run.config else None
                    if session:
                        print(f"          session:  {session}", flush=True)
                elif not args.no_sessions and run.config and run.messages:
                    session = save_session(run.config, scenario["id"], model["id"], run)
                    if session:
                        print(f"          session:  {session}", flush=True)
                results.append(
                    {
                        "model": model["id"],
                        "scenario": scenario["id"],
                        "source": scenario.get("source", "agents"),
                        "run": run_index + 1,
                        "error": run.error,
                        # Galaxy never finished the work; nothing about the agent was measured.
                        "stalled": run.stalled,
                        # Wall clock spent waiting on Galaxy. Information, not a verdict.
                        "waitedSeconds": round(run.waited, 1),
                        "statusCode": run.status_code,
                        "dimensions": sorted(exercised),
                        "failures": [
                            {"assertion": f.assertion, "detail": f.detail, "dimension": f.dimension} for f in failures
                        ],
                        "steps": run.steps,
                        "maxSteps": run.max_steps,
                        "toolsCalled": run.tools_called,
                        "events": run.events,
                        "artifacts": run.artifacts,
                        # A pass is the artifact worth keeping, not just the verdict.
                        "chatText": run.chat_text if run.messages else "",
                        "messages": run.messages or [],
                        "logs": run.logs or [],
                    }
                )
                # A killed suite otherwise loses every result; the file is rewritten each time.
                if args.json_out:
                    with open(args.json_out, "w") as f:
                        json.dump(results, f, indent=2)

    print_leaderboard(results, models)
    print_instability(results)
    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nraw results -> {args.json_out}")
    # Neither a quota limit nor a Galaxy stall is a failure of the agent, so neither fails the suite.
    graded = [r for r in results if not is_quota(r) and not r.get("stalled")]
    return 0 if all(not r["failures"] and not r["error"] for r in graded) else 1


def outcome(row):
    """One word per run, so repeated runs can be compared."""
    if row["error"]:
        if is_quota(row):
            return "quota"
        return "STALL" if row.get("stalled") else "ERROR"
    return "pass" if not row["failures"] else "FAIL"


def print_instability(results):
    """Name the tuples whose runs disagreed. A flaky cell is a finding, not noise."""
    grouped = {}
    for row in results:
        grouped.setdefault((row["model"], row["scenario"]), []).append(outcome(row))
    unstable = {k: v for k, v in grouped.items() if len(v) > 1 and len(set(v)) > 1}
    if not unstable:
        return
    print("\nUnstable — the same scenario went both ways on repeated runs:")
    for (model, scenario), outcomes in sorted(unstable.items()):
        tally = ", ".join(f"{o}x{outcomes.count(o)}" for o in dict.fromkeys(outcomes))
        print(f"  {model:24s} {scenario:34s} {tally}")
    print("A cell that is not unanimous cannot be reported as a single verdict.")


def print_leaderboard(results, models):
    """Per-model score on each dimension: exercised runs that produced no failure."""
    print(f"\n{'model':24s} " + " ".join(f"{d:>10s}" for d in DIMENSIONS) + f" {'errors':>7s} {'quota':>6s}")
    print("-" * (24 + 11 * len(DIMENSIONS) + 15))
    for model in models:
        rows = [r for r in results if r["model"] == model["id"]]
        cells = []
        for dim in DIMENSIONS:
            exercised = [r for r in rows if dim in r["dimensions"]]
            if not exercised:
                cells.append(f"{'-':>10s}")
                continue
            clean = [r for r in exercised if not any(f["dimension"] == dim for f in r["failures"])]
            cells.append(f"{len(clean)}/{len(exercised):<8d}")
        errors = sum(1 for r in rows if r["error"] and not is_quota(r))
        quota = sum(1 for r in rows if is_quota(r))
        print(f"{model['id']:24s} " + " ".join(cells) + f" {errors:>7d} {quota:>6d}")
    print("\nA dash means no scenario exercised that dimension for this model.")
    if any(is_quota(r) for r in results):
        print("Quota-limited runs were not graded. A daily quota needs a paid key, not a longer delay.")


if __name__ == "__main__":
    sys.exit(main())
