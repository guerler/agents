#!/usr/bin/env python3
"""Run the same Galaxy operations through galaxy-mcp and galaxy-ops, and diff what a model
would learn from each.

    GALAXY_URL=http://localhost:8080 GALAXY_API_KEY=... \
    MCP_ROOT=~/galaxy-mcp python3 comparison/ops-parity/run.py

Raw paired envelopes land in results/ for reading; the table is only a summary of them.
"""

import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cases as case_book  # noqa: E402
import compare as cmp  # noqa: E402

URL = os.environ.get("GALAXY_URL", "http://localhost:8080")
KEY = os.environ.get("GALAXY_API_KEY") or os.environ.get("GALAXY_LOCAL_KEY") or ""
MCP = pathlib.Path(os.path.expanduser(os.environ.get("MCP_ROOT", "~/galaxy-mcp")))
OPS = MCP / "galaxy-agent-tools" / "packages" / "galaxy-ops"
RESULTS = HERE / "results"


def fixtures(server):
    """Ids the cases refer to, taken from the server rather than pinned."""
    got = {}
    histories = server.get_histories(limit=1).data or []
    got["$history"] = histories[0]["id"] if histories else None
    body = server.get_history_contents(history_id=got["$history"]).data if got["$history"] else None
    items = (body or {}).get("contents") if isinstance(body, dict) else (body or [])
    datasets = [c for c in (items or []) if isinstance(c, dict) and c.get("type") == "file"]
    got["$dataset"] = datasets[0]["id"] if datasets else None
    workflows = server.list_workflows().data or []
    got["$workflow"] = workflows[0]["id"] if workflows else None
    got["$tool"] = "cat1"
    return got


def resolve(args, got):
    return {k: got.get(v, v) if isinstance(v, str) and v.startswith("$") else v for k, v in args.items()}


def python_side(server, case, got):
    fn = getattr(server, case["op"], None)
    if fn is None:
        return {"absent": True}
    try:
        result = fn(**resolve(case["py"], got))
        return {
            "success": result.success,
            "message": result.message,
            "pagination": result.pagination.model_dump() if result.pagination else None,
            "data": result.data,
        }
    except Exception as exc:
        return {"threw": f"{type(exc).__name__}: {exc}"}


def ops_side(cases, got):
    calls = [{"op": c["op"], "args": resolve(c["ts"], got)} for c in cases]
    entry = (OPS / "dist" / "index.js").as_uri()
    payload = json.dumps({"baseUrl": URL, "apiKey": KEY, "opsEntry": entry, "calls": calls})
    proc = subprocess.run(
        ["node", str(HERE / "ops_driver.mjs")],
        input=payload, capture_output=True, text=True, cwd=str(OPS),
    )
    if proc.returncode != 0:
        sys.exit(f"ops driver failed:\n{proc.stderr[-2000:]}")
    return {r["op"]: r for r in json.loads(proc.stdout)}


def main():
    sys.path.insert(0, str(MCP / "mcp-server-galaxy-py" / "src"))
    import galaxy_mcp.server as server
    from bioblend.galaxy import GalaxyInstance

    server.galaxy_state.update(
        {"url": URL, "api_key": KEY, "gi": GalaxyInstance(url=URL, key=KEY), "connected": True}
    )
    got = fixtures(server)
    print(f"galaxy {URL} | fixtures {json.dumps({k: bool(v) for k, v in got.items()})}\n")

    ops = ops_side(case_book.CASES, got)
    RESULTS.mkdir(exist_ok=True)
    rows, unreviewed = [], 0

    for case in case_book.CASES:
        op = case["op"]
        py = python_side(server, case, got)
        side = ops.get(op, {})
        ts = side.get("envelope") or {"absent": side.get("absent"), "threw": side.get("threw")}
        (RESULTS / f"{op}.json").write_text(
            json.dumps({"case": case, "python": py, "ops": ts}, indent=1, default=str)
        )
        if side.get("absent") or py.get("absent") or py.get("threw") or side.get("threw"):
            rows.append((op, "call", case.get("expect", {}).get("call", "UNREVIEWED"),
                         py.get("threw") or side.get("threw") or "operation absent on one side"))
            continue
        diffs = cmp.compare(py, ts)
        if not diffs:
            rows.append((op, "-", case_book.EQUIVALENT, "same fields, same totals"))
            continue
        for field, verdict in cmp.classify(case, diffs).items():
            detail = json.dumps(diffs.get(field), default=str)
            rows.append((op, field, verdict, detail[:96]))
            if verdict == "UNREVIEWED":
                unreviewed += 1

    print(f"{'operation':30} {'field':11} {'verdict':17} detail")
    for op, field, verdict, detail in rows:
        print(f"  {op:28} {field:11} {verdict:17} {detail}")
    print(f"\n{len(case_book.CASES)} cases | raw pairs in {RESULTS.relative_to(pathlib.Path.cwd()) if str(RESULTS).startswith(str(pathlib.Path.cwd())) else RESULTS}")
    if unreviewed:
        print(f"{unreviewed} undeclared difference(s): read the pair, then record it in cases.py")
    return 1 if unreviewed else 0


if __name__ == "__main__":
    raise SystemExit(main())
