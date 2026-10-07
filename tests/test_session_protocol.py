"""The built session speaks JSON lines on stdout and nothing else, and runs Python headless,
isolated from the session: Python sees none of its environment, where the keys live.

Both broke unseen: Pyodide was handed a file: URL it read as a relative path, so every scenario
that ran Python failed with ENOENT, and once fixed, its package loader narrated on stdout, where
the harness reads only protocol lines.
"""

import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "evals"))

from lib.scripted import scripted, tool_results  # noqa: E402

import subject  # noqa: E402

MEAN = "import numpy as np\nfloat(np.mean([3, 5, 10]))"
ENV = "import js\nsorted(js.Object.keys(js.process.env).to_py())"


def test_python_runs_headless_and_stdout_stays_protocol():
    script = [[("run_python", {"code": MEAN})], [("run_python", {"code": ENV})]]
    with scripted(script) as url:
        config = {"galaxy_root": "http://galaxy.invalid/", "ai_base_url": url, "ai_model": "scripted"}
        requests = [{"op": "create", "config": config}, {"op": "turn", "text": "run it"}, {"op": "close"}]
        out = subprocess.run(
            subject.session(),
            input="".join(json.dumps(r) + "\n" for r in requests),
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ, "OLIT_SESSION_SECRET": "sk-not-for-python"},
        )
    lines = [line for line in out.stdout.splitlines() if line.strip()]
    replies = [json.loads(line) for line in lines]
    results = tool_results(replies[1]["result"]["entries"])
    assert results[0]["content"] == "6.0", out.stderr[-500:]
    # Only what Node sets up for the IPC channel itself.
    seen = results[1]["content"]
    assert "OLIT_SESSION_SECRET" not in seen and "PATH" not in seen, seen


def test_a_restart_offers_the_history_records_and_recovers_only_the_chosen_session():
    import http.server
    import threading

    record = {
        "id": "a1b2c3d4e5f60718",
        "slug": "olit-sess-7",
        "title": "Olit Notebook (sess-7)",
        "create_time": "2026-10-01T09:00:00",
        "update_time": "2026-10-02T09:00:00",
    }

    class Galaxy(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = [record] if self.path.startswith("/api/pages?history_id=h1") else {}
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Galaxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with scripted([]) as url:
            config = {
                "galaxy_root": f"http://127.0.0.1:{server.server_port}/",
                "ai_base_url": url,
                "ai_model": "scripted",
                "history_id": "h1",
            }
            requests = [
                {"op": "create", "config": config},
                {"op": "restart"},
                {"op": "recover", "page_id": record["id"]},
                {"op": "export"},
                {"op": "close"},
            ]
            out = subprocess.run(
                subject.session(),
                input="".join(json.dumps(r) + "\n" for r in requests),
                capture_output=True,
                text=True,
                timeout=120,
            )
    finally:
        server.shutdown()
    replies = [json.loads(line) for line in out.stdout.splitlines() if line.strip()]
    offered = replies[1]["result"]["records"]
    assert [r["pageId"] for r in offered] == [record["id"]], out.stderr[-500:]
    session = replies[3]["result"]["session"]
    assert session["id"] == "sess-7" and session["recordPageId"] == record["id"]
    # The record and identity came back; the lost conversation did not.
    assert session["turn"] == 0
