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
import shutil
import subprocess
import tempfile
import time

import settings

from . import config as configuration
from . import harness, staging, tooltests
from .entries import digest

HERE = pathlib.Path(__file__).resolve().parent
PORT = int(os.environ.get("OLIT_WATCH_PORT", settings.WATCH_PORT))
# Drop each frame too close to the one before it, then play what is left at the video's rate.
CONDENSE = f"mpdecimate={settings.VIDEO_DECIMATE},setpts=N/({settings.VIDEO_FPS}*TB)"
# H.264 MP4 plays in GitHub, Drive, Slack and QuickTime.
ENCODE = ["-r", str(settings.VIDEO_FPS), "-an", "-c:v", "libx264", "-crf", str(settings.VIDEO_CRF)]
ENCODE += ["-preset", "slow", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]

_server = None


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
        key = configuration._api_key(model)
        if key:
            env["LLM_KEY"] = key
    return env


def start_server(model):
    """Olit's dev server for this model, started once and stopped when the run exits."""
    global _server
    if _server:
        return
    records = HERE.parents[1] / "records" / "content"
    records.mkdir(parents=True, exist_ok=True)
    log = open(records / "watch-server.log", "w")
    _server = subprocess.Popen(
        ["npm", "run", "dev", "--", "--port", str(PORT), "--strictPort"],
        cwd=harness.subject.root() / "olit",
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


def condense(source, target):
    """The run's video with the frames that change nothing dropped, so the waits on the model and
    on Galaxy's jobs leave it and what is left plays at its own pace."""
    command = ["ffmpeg", "-v", "error", "-y", "-i", str(source), "-vf", CONDENSE, *ENCODE, str(target)]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        return "ffmpeg is not installed"
    except subprocess.CalledProcessError as e:
        lines = (e.stderr or "").strip().splitlines()
        return lines[-1] if lines else str(e)
    return None


def keep_video(recorded, folder, name):
    """Keep the page's recording, condensed, in the videos folder under the run's own name.
    Without ffmpeg the recording is kept as Playwright wrote it."""
    if not recorded:
        return
    video = pathlib.Path(folder) / f"{name}.mp4"
    problem = condense(recorded, video)
    if problem:
        video = video.with_suffix(".webm")
        pathlib.Path(recorded).replace(video)
    shutil.rmtree(pathlib.Path(recorded).parent, ignore_errors=True)
    note = f" (not condensed: {problem})" if problem else ""
    print(f"          video:    {video}{note}", flush=True)


def run_watched(scenario, model, video=None, slow_mo=0, hold=0, target="dev"):
    """One scenario, typed into the page in a visible browser, graded off what the page saved.

    `target` is the page: Olit's dev server, or Olit as Galaxy serves it, inside Galaxy's own UI.
    A plugin build reaches no model but Galaxy's, so that one runs only on Galaxy AI.
    """
    if target == "galaxy" and model.get("provider") != "galaxy":
        return harness.RunResult([], [], [], error="watching inside Galaxy runs only on Galaxy AI")
    if target == "galaxy" and not (os.environ.get("GALAXY_USER") and os.environ.get("GALAXY_PASSWORD")):
        return harness.RunResult([], [], [], error="watching inside Galaxy needs GALAXY_USER and GALAXY_PASSWORD")
    name = f"{time.strftime('%Y-%m-%dT%H-%M-%S')}-{scenario['id']}"
    unsupported = [k for k in ("restartAfter", "approveWhenPlanned") if scenario.get(k)]
    if unsupported:
        return harness.RunResult([], [], [], error=f"watch mode does not drive {', '.join(unsupported)}")
    if target == "dev" and not scenario.get("dataset"):
        return harness.RunResult([], [], [], error="the dev server launches on a staged dataset, and this has none")
    if not (scenario.get("dataset") or scenario.get("emptyHistory")):
        return harness.RunResult([], [], [], error="watch mode launches on a staged history; this scenario has none")

    config = configuration.build_config(model, scenario.get("capabilities"))
    waited = time.time()
    try:
        staged = staging._stage(scenario, config)
    except Exception as e:
        return harness.RunResult([], [], [], error=f"{type(e).__name__}: {e}", stalled=True)
    waited = time.time() - waited
    config["history_id"] = staged["history_id"]
    galaxy = staged["galaxy"]
    dataset_id = next(iter(staged["dataset_ids"].values()), None)
    staging_jobs = [j["id"] for j in galaxy.call(f"api/jobs?history_id={staged['history_id']}&limit=500") or []]

    if target == "galaxy":
        root = os.environ["GALAXY_URL"].strip().rstrip("/") + "/"
        # Without a dataset, Olit opens on Galaxy's current history, which signing in makes this one.
        url = f"{root}visualizations/display?visualization=olit"
        if dataset_id:
            url += f"&dataset_id={dataset_id}"
        login = {"user": os.environ["GALAXY_USER"], "password": os.environ["GALAXY_PASSWORD"]}
    else:
        start_server(model)
        root, url, login = "/", f"http://localhost:{PORT}/?dataset_id={dataset_id}", None
    if video:
        pathlib.Path(video).mkdir(parents=True, exist_ok=True)
    request = {
        "url": url,
        "root": root,
        "login": login,
        "inputs": scenario["inputs"],
        "historyId": staged["history_id"],
        "skipJobs": staging_jobs,
        "timeoutMs": harness.scenario_timeout(scenario) * 1000,
        "galaxyWaitMs": staging.galaxy_wait_timeout() * 1000,
        "settleMs": settings.WATCH_SETTLE_MS,
        "chart": ((scenario.get("assertions") or {}).get("page") or {}).get("chart") or [],
        "video": tempfile.mkdtemp(prefix=f".{name}-", dir=video) if video else None,
        "slowMo": slow_mo,
        "holdMs": hold * 1000,
    }
    events, error, visualization_id, checked = [], None, None, None
    stderr = tempfile.TemporaryFile(mode="w+")
    driver = subprocess.Popen(
        ["node", str(HERE / "watch_drive.mjs"), json.dumps(request)],
        env={**os.environ, "OLIT_ROOT": str(harness.subject.root())},
        stdout=subprocess.PIPE,
        stderr=stderr,
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
        elif message.get("event") == "page":
            checked = message
            for check, seen in (message.get("chart") or {}).items():
                print(f"          chart {check}: {'ok' if seen['ok'] else 'FAILED'}, {seen['detail']}", flush=True)
        elif message.get("event") == "video":
            keep_video(message.get("path"), video, name)
        elif message.get("event") == "auto_follow_up":
            # A follow-up is a run of its own, bracketed as the headless harness does.
            events.extend(["turn_end", "auto_follow_up", "turn_start"])
        elif message.get("event") in ("turn_start", "turn_end", "work_settled"):
            events.append(message["event"])
        elif message.get("event") == "console":
            print(f"          console: {message.get('text', '')}", flush=True)
        if message.get("event") in ("ready", "turn_start", "auto_follow_up", "work_settled"):
            print(f"          {message['event']}: {message.get('input', '')}", flush=True)
    if driver.wait() and not error:
        stderr.seek(0)
        error = f"the driver exited with {driver.returncode}: {stderr.read()[-1000:].strip()}"
    stderr.close()
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
    result = harness.RunResult(
        chat,
        run["logs"],
        run["tools"],
        error=run["error"],
        events=events,
        artifacts=run["artifacts"],
        staged=staged,
        steps=run["steps"],
        max_steps=harness.max_steps(config),
        config=config,
        refused=run["refused"],
        waited=waited,
        document=document,
    )
    result.page = checked
    return result


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
