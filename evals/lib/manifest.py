"""What an evaluation ran under, written beside its results so a reader can run it again."""

import hashlib
import json
import os
import platform
import subprocess
import sys
import time

# What a scenario declares for a tool evaluation, carried into the manifest for the report.
EVALUATION_KEYS = ("targets", "control", "reachableWithout")

# The environment variables that change what a session is configured with.
KNOBS = ("OLIT_EVAL_CAPABILITIES", "OLIT_EVAL_MAX_STEPS", "OLIT_EVAL_MAX_TOKENS")


def manifest_path(results_path):
    """Where the manifest of a results file goes: beside it, named after it."""
    stem = results_path[:-5] if results_path.endswith(".json") else results_path
    return f"{stem}.manifest.json"


def _git(path):
    """The commit a checkout is at, its branch, and whether it differs from that commit."""

    def ask(*args):
        out = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
        return out.stdout.strip() if out.returncode == 0 else None

    return {
        "commit": ask("rev-parse", "HEAD"),
        "branch": ask("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(ask("status", "--porcelain", "--untracked-files=no")),
    }


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def manifest(argv, galaxy, models, scenarios, arms, started, finished=True):
    """The revisions, Galaxy, models, scenarios and configuration of one invocation."""
    import subject

    described = subject.describe()
    try:
        version = galaxy.call("api/version")
    except Exception as exc:
        version = {"error": str(exc)}
    return {
        "started": started,
        "finished": time.strftime("%Y-%m-%dT%H:%M:%S") if finished else None,
        "command": [os.path.basename(sys.executable), *argv],
        "harness": {"repository": "agents", **_git(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))},
        "agent": {
            "name": described.get("agent"),
            **_git(subject.root()),
            "description": _digest(described),
            "tools": sorted(described.get("tools") or {}),
        },
        "galaxy": {"url": os.environ.get("GALAXY_URL", "").strip(), "version": version},
        "models": [{k: m.get(k) for k in ("id", "provider", "model", "baseUrl")} for m in models],
        "arms": arms,
        "scenarios": {
            s["id"]: {
                "name": s.get("name"),
                "source": s.get("source", "agents"),
                "digest": _digest(_declared(s)),
                **{k: s[k] for k in EVALUATION_KEYS if k in s},
            }
            for s in scenarios
        },
        "environment": {name: os.environ.get(name) for name in KNOBS},
        "platform": {"python": platform.python_version(), "system": platform.platform()},
    }


def _declared(scenario):
    """The scenario as its file declares it, without what the loader adds."""
    return {k: v for k, v in scenario.items() if k not in ("id",)}
