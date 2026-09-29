"""What the upstreams olit tracks look like right now, without adopting any of them.

    python3 upstream_check.py

Fetches loom's refs and runs the seam checker against a throwaway worktree of the fetched
branch, so drift is reported without pulling; then runs olit's own freshness and integrity
checks for the pinned semantic dependencies. Nothing here merges, captures, snapshots or
certifies: adopting a change stays a separate deliberate act.

Deliberately not tracked: Pyodide, which is ordinary dependency freshness, and Galaxy itself,
which is a live server rather than a pinned upstream.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
LOOM = pathlib.Path(os.environ.get("LOOM_ROOT", pathlib.Path.home() / "loom"))
OLIT = pathlib.Path(os.environ.get("OLIT_ROOT", pathlib.Path.home() / "galaxy-visualizations/packages/olit"))
REGISTRY = HERE / "seams" / "registry.json"
BRANCH = os.environ.get("LOOM_BRANCH", "main")


def run(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def npm_latest(package):
    """The version the registry serves, or None when it cannot be asked."""
    try:
        with urllib.request.urlopen(f"https://registry.npmjs.org/{package}/latest", timeout=20) as answer:
            return json.loads(answer.read()).get("version")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def loom_seams():
    """The seam report against the fetched branch, and the files behind whatever drifted."""
    if not (LOOM / ".git").exists():
        return [f"loom       no checkout at {LOOM}"]
    fetched = run(["git", "fetch", "--quiet", "origin", BRANCH], cwd=LOOM)
    if fetched.returncode:
        return [f"loom       could not fetch: {fetched.stderr.strip().splitlines()[-1:] or ''}"]
    ref = f"origin/{BRANCH}"
    here = run(["git", "rev-parse", "--short", "HEAD"], cwd=LOOM).stdout.strip()
    there = run(["git", "rev-parse", "--short", ref], cwd=LOOM).stdout.strip()
    out = [f"loom       checkout {here}, {ref} {there}" + ("" if here == there else "  (checkout is behind)")]

    tree = pathlib.Path(tempfile.mkdtemp(prefix="loom-upstream-"))
    shutil.rmtree(tree)
    added = run(["git", "worktree", "add", "--detach", str(tree), ref], cwd=LOOM)
    if added.returncode:
        return out + [f"           could not read {ref}: {added.stderr.strip()}"]
    # The pi layer fingerprints the installed package, which a fresh worktree has none of.
    modules = LOOM / "node_modules"
    if modules.is_dir():
        (tree / "node_modules").symlink_to(modules)
    try:
        report = subprocess.run(
            [sys.executable, "seams/check.py"],
            cwd=HERE,
            capture_output=True,
            text=True,
            env={**os.environ, "LOOM_ROOT": str(tree), "OLIT_ROOT": str(OLIT)},
        )
        body = (report.stdout or report.stderr).strip()
        out += ["           " + line for line in body.splitlines()]
    finally:
        run(["git", "worktree", "remove", "--force", str(tree)], cwd=LOOM)
    return out + reading_list(body, ref)


def drifted_ids(report):
    return [line.split()[1] for line in report.splitlines() if line.startswith("DRIFT") and len(line.split()) > 1]


def reading_list(report, ref):
    """What to read for each drifted seam: a fingerprint says a symbol moved, not how."""
    ids = drifted_ids(report)
    if not ids:
        return []
    rows = {r["id"]: r for r in json.loads(REGISTRY.read_text())["seams"]}
    sources = {}
    for seam_id in ids:
        loom_meta = (rows.get(seam_id) or {}).get("loom") or {}
        if loom_meta.get("file"):
            sources.setdefault(loom_meta["file"], set()).add(loom_meta.get("symbol") or "")
    out = [
        "",
        "           Read the change before certifying it -- a fingerprint says a symbol moved,",
        "           not how:",
    ]
    for path, symbols in sorted(sources.items()):
        named = ", ".join(sorted(s for s in symbols if s))
        out.append(f"             {path}  {named}")
        out.append(f"               git -C {LOOM} show {ref}:{path}")
    for path in layer_paths(ids):
        out.append(f"             {path}")
        out.append(f"               git -C {LOOM} show {ref}:{path}")
    return out


# A layer entry names a directory and a file shape rather than a symbol.
LAYER_FILES = {"eval-scenarios": "{dir}/{name}/scenario.json", "eval-lib": "{dir}/{name}.ts"}


def layer_paths(ids):
    """The upstream file behind each drifted layer entry, where its layer names one."""
    stored = json.loads(REGISTRY.read_text())["layers"]
    out = []
    for seam_id in ids:
        if not seam_id.startswith("layer.") or "/" not in seam_id:
            continue
        layer, name = seam_id[len("layer.") :].split("/", 1)
        shape = LAYER_FILES.get(layer)
        source = (stored.get(layer.replace("-", "_")) or {}).get("source") or ""
        if shape and source.startswith("loom "):
            out.append(shape.format(dir=source[len("loom ") :], name=name))
    return sorted(set(out))


def pi():
    """The pi the seam registry pins, against the version npm serves."""
    pinned = (json.loads(REGISTRY.read_text()).get("layers") or {}).get("pi") or {}
    package, version = pinned.get("package"), pinned.get("version")
    if not package:
        return "pi         no version pinned in the seam registry"
    latest = npm_latest(package)
    if latest is None:
        return f"pi         pinned at {version}; npm could not be asked"
    if latest == version:
        return f"pi         up to date at {version} ({package})"
    return (
        f"pi         BEHIND: the loop was audited against {version}, npm has {latest}\n"
        f"           read pi-loop-audit.md, then re-record the pi layer"
    )


def olit_checks():
    """olit's own pin freshness and vendored integrity, run where they live."""
    out = []
    for args in (["node", "scripts/check_stale.js"], ["python3", "scripts/check_vendored.py"]):
        answer = run(args, cwd=OLIT)
        out += (answer.stdout or answer.stderr).strip().splitlines()
    return out


def main():
    print(f"upstreams olit tracks, read at {OLIT.name}\n")
    for line in loom_seams():
        print(line)
    print()
    print(pi())
    for line in olit_checks():
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
