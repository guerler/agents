"""Capability floor for literature-nar-papers-downloaded: the known-good route, with no model.

A scripted policy takes the Europe PMC route through Olit's real session, and the scenario's own
grade reads what landed. A failure here belongs to Olit, Galaxy or the tools; a model run that
fails at a step this one passes failed on a decision.

    GALAXY_URL=https://usegalaxy.eu GALAXY_API_KEY=... python3 floors/literature_nar_papers_downloaded.py
"""

import json
import os
import re
import sys
import uuid

EV = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, EV)
sys.path.insert(0, os.path.dirname(EV))

from lib.assertions import evaluate  # noqa: E402
from lib.harness import load_scenarios, run_scenario  # noqa: E402
from lib.scripted import scripted  # noqa: E402
from lib.tooltests import Galaxy  # noqa: E402

SCENARIO = "literature-nar-papers-downloaded"
QUERY = 'JOURNAL:"Nucleic Acids Res" AND TITLE:"Galaxy"'
FOLLOW_UP = "[Olit automatic Galaxy follow-up]"


def installed(galaxy, short):
    """The newest installed revision of a tool, by its short id."""
    ids = [t.get("id") or "" for t in galaxy.call("api/tools?in_panel=false") or []]
    found = [i for i in ids if i == short or f"/{short}/" in i]
    if not found:
        sys.exit(f"this Galaxy lacks {short}")
    return max(found, key=lambda i: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", i)])


def _text(message):
    content = message.get("content")
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content or ""


def _json(text):
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    try:
        return json.loads(text[start:]) if start >= 0 else None
    except ValueError:
        return None


def policy(query_tool, download_tool):
    """Answer from the conversation so far: query, wait, download, wait, report."""

    def reply(request):
        messages = request.get("messages") or []
        history = None
        submitted = {}
        completed = set()
        for message in messages:
            text = _text(message)
            found = re.search(r'history_id="([0-9a-f]+)"', text)
            history = history or (found and found.group(1))
            if message.get("role") == "tool":
                data = (_json(text) or {}).get("data") or {}
                for job in data.get("jobs") or []:
                    outputs = [o["id"] for o in data.get("outputs") or []]
                    submitted[job.get("tool_id")] = (job["id"], outputs)
            elif message.get("role") == "user" and FOLLOW_UP in text:
                for run in _json(text[text.find("\n[") :]) or []:
                    if run.get("outcome") == "completed":
                        completed.add(run.get("id"))
        query = submitted.get(query_tool)
        download = submitted.get(download_tool)
        if not query:
            inputs = {
                "database": "europepmc",
                "full_text_subset": True,
                "licensing": "all",
                "preprint_mode": "all",
                "search_term": QUERY,
            }
            return [("run_tool", {"history_id": history, "tool_id": query_tool, "inputs": inputs})]
        if query[0] not in completed:
            return "The Europe PMC query is running."
        if not download:
            inputs = {
                "input_file": {"src": "hda", "id": query[1][0]},
                "literature|database": "europepmc",
                "literature|type_choice": "pdfs",
            }
            return [("run_tool", {"history_id": history, "tool_id": download_tool, "inputs": inputs})]
        if download[0] not in completed:
            return "The PDF download is running."
        return "The Galaxy NAR papers are in this history as PDFs."

    return reply


def main():
    galaxy = Galaxy(os.environ["GALAXY_URL"], os.environ["GALAXY_API_KEY"])
    query_tool = installed(galaxy, "aoptk_query_literature")
    download_tool = installed(galaxy, "aoptk_download_publication_data")
    scenario = load_scenarios(os.path.join(EV, "scenarios"), SCENARIO)[0]
    scenario = json.loads(json.dumps(scenario).replace("$run", f"floor {uuid.uuid4().hex[:10]}"))
    with scripted(policy(query_tool, download_tool)) as url:
        run = run_scenario(scenario, {"id": "scripted", "model": "scripted", "baseUrl": url})
    print(f"tools called: {run.tools_called}")
    if run.error:
        print(f"ERROR {run.error}")
        return 1
    failures, _ = evaluate(scenario, run)
    for failure in failures:
        print(f"FAIL {failure.assertion}: {failure.detail}")
    print("pass" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
