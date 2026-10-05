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

import subject  # noqa: E402

REQUESTS = [
    {"op": "create", "config": {"galaxy_root": "http://galaxy.invalid/", "ai_provider": "galaxy"}},
    {"op": "call", "name": "run_python", "args": {"code": "import numpy as np\nfloat(np.mean([3, 5, 10]))"}},
    {"op": "call", "name": "run_python", "args": {"code": "import js\nsorted(js.Object.keys(js.process.env).to_py())"}},
    {"op": "close"},
]


def test_python_runs_headless_and_stdout_stays_protocol():
    out = subprocess.run(
        subject.session(),
        input="".join(json.dumps(r) + "\n" for r in REQUESTS),
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "OLIT_SESSION_SECRET": "sk-not-for-python"},
    )
    lines = [line for line in out.stdout.splitlines() if line.strip()]
    replies = [json.loads(line) for line in lines]
    assert replies[1]["result"]["content"] == "6.0", out.stderr[-500:]
    # Only what Node sets up for the IPC channel itself.
    seen = replies[2]["result"]["content"]
    assert "OLIT_SESSION_SECRET" not in seen and "PATH" not in seen, seen
