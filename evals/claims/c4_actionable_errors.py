"""C4: a failed tool call must tell the agent how to succeed, and a hopeless retry must stop.

Both conditions are driven straight through the real tool surface. No model, so what is
under test is the mechanism rather than whether the agent happens to trigger it.
"""

import asyncio
import os
import sys
import time

EV = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, EV)
sys.path.insert(0, os.path.dirname(EV))

from lib.harness import Session
from lib.tooltests import Galaxy

G = Galaxy(os.environ["GALAXY_URL"], os.environ["GALAXY_API_KEY"])
TAG = os.environ.get("TAG", str(int(time.time()))[-6:])


def wait(h, want, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        c = [x for x in (G.call(f"api/histories/{h}/contents") or []) if not x.get("deleted")]
        if len(c) >= want and all(x.get("state") in ("ok", "error") for x in c):
            return c
        time.sleep(3)
    return []


history = G.new_history(f"c4 errors {TAG}")
ds = G.upload(history, "rows.tabular", b"a\t3\nb\t1\nc\t2\n")
wait(history, 1)

SESSION = {
    "galaxy_root": os.environ["GALAXY_URL"].rstrip("/") + "/",
    "galaxy_key": os.environ["GALAXY_API_KEY"],
    "ai_provider": "galaxy",
    "capabilities": ["llm", "local", "read", "write"],
}

bad = {
    "history_id": history,
    "tool_id": "sort1",
    "inputs": {
        "input": {"src": "hda", "id": ds},
        "column": "2",
        "style": "num",
        "order": "DESC",
        "0|other_column": "1",
    },
}
good = {
    "history_id": history,
    "tool_id": "sort1",
    "inputs": {"input": {"src": "hda", "id": ds}, "column": "2", "style": "num", "order": "DESC"},
}


async def main():
    session = await Session(SESSION).init()
    try:
        # --- 1. a rejected parameter comes back with the shape the tool wants --------
        out = await session.call("run_tool", bad)
        helped = "Fill this template" in out["content"] and "column_set_0|other_column" in out["content"]
        print("1. parameter rejection carries the template:", "YES" if helped else f"NO -> {out['content'][:220]}")

        # --- 2. the same hopeless call is cut off rather than retried forever --------
        refusals, attempts = 0, 6
        for _ in range(attempts):
            o = await session.call("run_tool", bad)
            if o.get("guard") == "repeated-failure":
                refusals += 1
        print(
            f"2. identical failing call refused {refusals} of {attempts} follow-up attempts:",
            "YES" if refusals else "NO",
        )

        # --- 3. an Olit process addressed as a Galaxy tool is named -----------------
        o = await session.call("run_tool", {"history_id": history, "tool_id": "lineage_report", "inputs": {}})
        ok = "is an Olit tool" in o["content"]
        print("3. misrouted 'lineage_report' is identified:", "YES" if ok else f"NO -> {o['content'][:160]}")

        # --- control: a valid call still goes through --------------------------------
        o = await session.call("run_tool", good)
        print("4. control, a valid call is not blocked:", "YES" if not o["is_error"] else f"NO -> {o['content'][:160]}")
    finally:
        await session.close()


asyncio.run(main())
G.call(f"api/histories/{history}", "DELETE")
