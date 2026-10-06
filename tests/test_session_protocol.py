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
