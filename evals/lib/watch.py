"""Watch mode: run a scenario through Olit's real page in a visible browser.

Olit's dev server serves the page against the scenario's Galaxy, and `watch_drive.mjs` types the
scenario's inputs with Playwright. Grading reads the conversation the page saves to Galaxy, the
same session document the headless run exports, so a watched run is graded the same way.
"""

import atexit
import json
import os
import pathlib
import re
import subprocess
import time

from . import harness, tooltests
from .entries import digest

HERE = pathlib.Path(__file__).resolve().parent
PORT = int(os.environ.get("OLIT_WATCH_PORT", "5180"))
# A follow-up starts within a second or two of the page announcing settled work.
SETTLE_MS = 10_000

_server = None


def _olit_root():
    return pathlib.Path(os.path.expanduser(os.environ.get("OLIT_ROOT", "~/loom/browser")))


def _server_env(model):
    env = {
        **os.environ,
        "GALAXY_ROOT": os.environ["GALAXY_URL"].strip().rstrip("/"),
        "GALAXY_KEY": os.environ["GALAXY_API_KEY"],
    }
    for name in ("LLM_PROVIDER", "LLM_KEY", "LLM_MODEL", "LLM_ROOT", "LLM_PATH"):
        env.pop(name, None)
    # Galaxy AI goes through Galaxy's own /api, which the dev server already proxies.
    if model.get("provider") != "galaxy":
        env["LLM_PROVIDER"] = model["provider"]
        env["LLM_MODEL"] = model["model"]
        key = harness._api_key(model)
        if key:
            env["LLM_KEY"] = key
    return env


def start_server(model):
    """Olit's dev server for this model, started once and stopped when the run exits."""
    global _server
    if _server:
        return
    records = HERE.parents[1] / ".records"
    records.mkdir(exist_ok=True)
    log = open(records / "watch-server.log", "w")
    _server = subprocess.Popen(
        ["npm", "run", "dev", "--", "--port", str(PORT), "--strictPort"],
        cwd=_olit_root() / "olit",
        env=_server_env(model),
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    atexit.register(stop_server)
    deadline = time.time() + 600
    while time.time() < deadline:
        if _server.poll() is not None:
            raise RuntimeError(f"the dev server exited; see {log.name}")
        if re.search(r"Local:\s+http", pathlib.Path(log.name).read_text()):
            return
        time.sleep(1)
    raise RuntimeError("the dev server did not start within 10 minutes")


def stop_server():
    global _server
    if _server and _server.poll() is None:
        os.killpg(_server.pid, 15)
    _server = None


def run_watched(scenario, model, video=None, slow_mo=0, hold=0):
    """One scenario, typed into the page in a visible browser, graded off what the page saved."""
    unsupported = [k for k in ("restartAfter", "approveWhenPlanned") if scenario.get(k)]
    if unsupported:
        return harness.RunResult([], [], [], error=f"watch mode does not drive {', '.join(unsupported)}")
    if not scenario.get("dataset"):
        return harness.RunResult([], [], [], error="watch mode launches on a staged dataset; this scenario has none")

    config = harness.build_config(model, scenario.get("capabilities"))
    waited = time.time()
    try:
        staged = harness._stage(scenario, config)
    except Exception as e:
        return harness.RunResult([], [], [], error=f"{type(e).__name__}: {e}", stalled=True)
    waited = time.time() - waited
    config["history_id"] = staged["history_id"]
    galaxy = staged["galaxy"]
    dataset_id = next(iter(staged["dataset_ids"].values()))
    staging_jobs = [j["id"] for j in galaxy.call(f"api/jobs?history_id={staged['history_id']}&limit=500") or []]

    start_server(model)
    request = {
        "url": f"http://localhost:{PORT}/?dataset_id={dataset_id}",
        "inputs": scenario["inputs"],
        "historyId": staged["history_id"],
        "skipJobs": staging_jobs,
        "timeoutMs": harness.scenario_timeout(scenario) * 1000,
        "settleMs": SETTLE_MS,
        "video": video,
        "slowMo": slow_mo,
        "holdMs": hold * 1000,
    }
    events, error, visualization_id = [], None, None
    driver = subprocess.Popen(
        ["node", str(HERE / "watch_drive.mjs"), json.dumps(request)],
        env={**os.environ, "OLIT_ROOT": str(_olit_root())},
        stdout=subprocess.PIPE,
        text=True,
    )
    for line in driver.stdout:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if message.get("error"):
            error = message["error"]
        elif message.get("done"):
            visualization_id = message.get("visualizationId")
        elif message.get("event") == "video":
            print(f"          video:    {message.get('path')}", flush=True)
        elif message.get("event") == "auto_follow_up":
            # A follow-up is a run of its own, bracketed as the headless harness does.
            events.extend(["turn_end", "auto_follow_up", "turn_start"])
        elif message.get("event") in ("turn_start", "turn_end", "work_settled"):
            events.append(message["event"])
        if message.get("event") in ("ready", "turn_start", "auto_follow_up", "work_settled"):
            print(f"          {message['event']}: {message.get('input', '')}", flush=True)
    driver.wait()
    if error or not visualization_id:
        return harness.RunResult(
            [], [], [], error=error or "the page saved no conversation", events=events, waited=waited
        )

    document = galaxy.call(f"api/visualizations/{visualization_id}")["latest_revision"]["config"]
    staged["session_id"] = document["session"]["id"]
    staged["visualization_id"] = visualization_id
    entries = document.get("entries") or []
    run = digest(entries)
    # Each user entry starts a run, and the cap constrains a run, as the headless harness counts it.
    starts = [i for i, e in enumerate(entries) if e.get("kind") == "pi.user"] or [0]
    run["steps"] = max(digest(entries[a:b])["steps"] for a, b in zip(starts, starts[1:] + [len(entries)]))
    chat = [{"role": "system", "content": scenario.get("systemPrompt", "")}] + run["chat"]
    max_steps = int(config.get("max_steps") or harness.subject.describe()["policy"]["loop"]["max_steps"])
    return harness.RunResult(
        chat,
        run["logs"],
        run["tools"],
        error=run["error"],
        events=events,
        artifacts=run["artifacts"],
        staged=staged,
        steps=run["steps"],
        max_steps=max_steps,
        config=config,
        refused=run["refused"],
        waited=waited,
        document=document,
    )


def retitle(run, scenario_id, model_id):
    """Name the conversation the page saved, as a headless run's saved session is named."""
    staged = run.staged or {}
    if not staged.get("visualization_id"):
        return None
    root = run.config["galaxy_root"]
    galaxy = tooltests.Galaxy(root, run.config.get("galaxy_key", ""))
    title = f"eval: {scenario_id} [{model_id}]"
    try:
        document = {**run.document, "session": {**run.document["session"], "title": title}}
        galaxy.call(
            f"api/visualizations/{staged['visualization_id']}", method="PUT", body={"title": title, "config": document}
        )
    except Exception:
        return None
    return f"{root}visualizations/display?visualization=olit&visualization_id={staged['visualization_id']}"
