"""C1: what `behavior.galaxyDidWork` actually discriminates on.

Its guard is "did Galaxy produce anything", so the two runs below are indistinguishable
to it even though only one of them got its reported figure from Galaxy.
"""

import os
import sys
import time

EV = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, EV)
sys.path.insert(0, os.path.dirname(EV))

from lib.grading.galaxy import _galaxy_did_work
from lib.tooltests import Galaxy

G = Galaxy(os.environ["GALAXY_URL"], os.environ["GALAXY_API_KEY"])
TAG = os.environ.get("TAG", str(int(time.time()))[-6:])


class Run:
    def __init__(self, staged, tools):
        self.staged, self.tools_called = staged, tools


# A history where Galaxy genuinely did work.
worked = G.new_history(f"c1 galaxy worked {TAG}")
ds = G.upload(worked, "rows.tabular", b"a\t1\nb\t2\nc\t3\n")
G.await_contents(worked, 1, 300)
G.call(
    "api/tools",
    "POST",
    {"history_id": worked, "tool_id": "addValue", "inputs": {"input": {"src": "hda", "id": ds}, "exp": "7"}},
)
G.await_contents(worked, 2, 300)
worked_staged = {"galaxy": G, "history_id": worked, "dataset_ids": {"rows.tabular": ds}}

# A history where Galaxy did nothing.
idle = G.new_history(f"c1 galaxy idle {TAG}")
ds2 = G.upload(idle, "rows.tabular", b"a\t1\nb\t2\nc\t3\n")
G.await_contents(idle, 1, 300)
idle_staged = {"galaxy": G, "history_id": idle, "dataset_ids": {"rows.tabular": ds2}}

cases = [
    ("total substitution      (run_python, Galaxy idle)", idle_staged, ["run_python"]),
    ("partial substitution    (run_tool + run_python)", worked_staged, ["run_tool", "run_python"]),
    ("fully Galaxy-grounded   (run_tool only)", worked_staged, ["run_tool"]),
]
for label, staged, tools in cases:
    f = []
    _galaxy_did_work(Run(staged, tools), f)
    print(f"{label}: {'FIRES' if f else 'silent'}")
