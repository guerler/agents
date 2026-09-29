#!/usr/bin/env python3
"""Reduce a full eval-suite result to a durable snapshot.

    python3 evals/ingest_run.py /tmp/suite.json [--date YYYY-MM-DD]

The raw result carries every transcript and runs to megabytes; what is worth keeping over
time is the per-scenario verdict and the tier totals. Snapshots land in `evals/snapshots/`
as `run-<date>.json`, one per suite run, and are what a later run is compared against.

Tiers are scored apart on purpose: parity failures and Galaxy-grounded failures are
different evidence and must never be added into one number.
"""

import argparse
import collections
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
OUT_DIR = HERE / "snapshots"
LOOM_SCENARIOS = pathlib.Path(os.path.expanduser("~/loom/evals/scenarios"))


def tier_of(name, shared):
    return "parity" if name in shared else "grounded"


def verdict(runs):
    if any(r == "error" for r in runs) and not any(r == "fail" for r in runs):
        return "environment"
    if any(r == "fail" for r in runs):
        return "fail"
    return "pass"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("result", help="the suite's --json output")
    ap.add_argument("--date", help="override the run date (default: the file's mtime)")
    ap.add_argument("--commit", help="override the olit commit this ran against")
    ap.add_argument("--note", default="", help="one line on what was being tested")
    ap.add_argument("--out", default=None, help=f"where the snapshot lands (default: {OUT_DIR})")
    args = ap.parse_args()

    raw = json.loads(pathlib.Path(args.result).read_text())
    if isinstance(raw, dict):
        raw = raw.get("results") or []
    if not raw:
        sys.exit("no results in that file")

    shared = set(os.listdir(LOOM_SCENARIOS)) if LOOM_SCENARIOS.is_dir() else set()
    if not shared:
        print("warning: loom scenarios not found, every scenario will read as grounded", file=sys.stderr)

    date = args.date or dt.date.fromtimestamp(pathlib.Path(args.result).stat().st_mtime).isoformat()

    by_scenario = collections.OrderedDict()
    for r in raw:
        name = r["scenario"]
        state = "error" if r.get("error") else ("fail" if r.get("failures") else "pass")
        entry = by_scenario.setdefault(
            name,
            {
                "scenario": name,
                "tier": tier_of(name, shared),
                "runs": [],
                "steps": [],
                "tools": [],
                "failures": [],
            },
        )
        entry["runs"].append(state)
        # What the agent actually called: the route it took is what a later run is compared on.
        entry["tools"].append(r.get("toolsCalled") or [])
        if r.get("steps"):
            entry["steps"].append(r["steps"])
        for f in r.get("failures") or []:
            detail = f"{f.get('assertion')}: {f.get('detail')}"
            if detail not in entry["failures"]:
                entry["failures"].append(detail)
        if r.get("error") and r["error"] not in entry["failures"]:
            entry["failures"].append(f"error: {r['error'][:160]}")

    scenarios = []
    for entry in by_scenario.values():
        entry["verdict"] = verdict(entry["runs"])
        entry["max_steps"] = max(entry["steps"]) if entry["steps"] else None
        del entry["steps"]
        scenarios.append(entry)

    tiers = {}
    for tier in ("parity", "grounded"):
        rows = [s for s in scenarios if s["tier"] == tier]
        tiers[tier] = {
            "scenarios": len(rows),
            "clean": sum(1 for s in rows if s["verdict"] == "pass"),
            "failing": sum(1 for s in rows if s["verdict"] == "fail"),
            "environment": sum(1 for s in rows if s["verdict"] == "environment"),
        }

    snapshot = {
        "date": date,
        "model": raw[0].get("model"),
        "commit": args.commit or git_commit(),
        "note": args.note,
        "runs": len(raw),
        "tiers": tiers,
        "scenarios": sorted(scenarios, key=lambda s: (s["tier"], s["scenario"])),
    }

    out_dir = pathlib.Path(args.out).expanduser() if args.out else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"run-{date}.json"
    if out.exists():
        stem = out.stem
        n = 2
        while (out_dir / f"{stem}-{n}.json").exists():
            n += 1
        out = out_dir / f"{stem}-{n}.json"
    out.write_text(json.dumps(snapshot, indent=1) + "\n")
    print(out, f"({out.stat().st_size // 1024 or 1} KB)")
    for tier, t in tiers.items():
        print(
            f"  {tier:9} {t['clean']}/{t['scenarios']} clean, "
            f"{t['failing']} failing, {t['environment']} environment"
        )


def git_commit():
    olit = pathlib.Path(os.path.expanduser("~/galaxy-visualizations"))
    try:
        return subprocess.run(
            ["git", "-C", str(olit), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return None


if __name__ == "__main__":
    main()
