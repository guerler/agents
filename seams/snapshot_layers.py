"""Re-certify the whole-layer seams: write today's upstream state into registry.json.

Running this is the deliberate act of saying "I have looked at what changed upstream and
the agent is correct against it". `check.py` then holds us to that until it is run again.

Certification is per layer and never implicit: name the layers you reviewed, so a layer
nobody looked at keeps failing `check.py` instead of going green on a snapshot.

  python3 seams/snapshot_layers.py --only tool_surface,tool_contract --mcp path/to/galaxy_mcp/server.py
  python3 seams/snapshot_layers.py --all
"""

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import layers  # noqa: E402

import description  # noqa: E402

REGISTRY = pathlib.Path(__file__).resolve().parent / "registry.json"
LOOM = os.environ.get("LOOM_ROOT", str(pathlib.Path.home() / "loom"))

# Certifiable layers. A layer is only ever rewritten when it is named, so a green check
# means someone read that layer's upstream changes, not that a snapshot ran.
LAYERS = (
    "eval_scenarios",
    "eval_lib",
    "loom_modules",
    "identity_prompt",
    "skills",
    "pi",
    "tool_surface",
    "policy",
    "tool_request",
    "tool_contract",
)

# Differences that are forced by the browser architecture, not drift. Each must stay
# justified in orbit-faithfulness.md §2h; anything outside this set is a finding.
# What pi and loom do, read from pi-agent-core@0.87.1 and pi-ai@0.87.1. Values, not
# fingerprints: a diff here should read as a decision rather than a hash that moved.
POLICY_UPSTREAM = {
    "llm_request": {
        "source": "brain/olit/substrate/llm/api/openai_completions.py",
        "label": "PORTED",
        "upstream": {
            "sampling": {"max_tokens": None, "temperature": None, "tool_choice": None, "top_p": None},
            "with_tools": {"tool_choice": None},
        },
        "note": "pi-ai emits temperature and max_tokens only when set (api/openai-completions.js:589,595) "
        "and reaches top_p only through the samplingParams bag; loom sets none of them, so an "
        "Orbit request runs at the provider's own defaults. Olit must not impose its own or a "
        "benchmark measures the defaults rather than the runtime. pi reaches tool_choice only when "
        "a caller passes one (api/openai-completions.js:626) and loom never does, so a request "
        "carrying tools states no choice either; the with_tools slice exists because a request "
        "built without tools cannot show that field.",
    },
    "loop": {
        "source": "brain/olit/loop/agent.py, brain/olit/compaction.py, brain/olit/loop/paging.py",
        "upstream": {
            "keep_recent_tokens": 20000,
            "max_steps": None,
            "max_tool_result_bytes": None,
            "reserve_tokens": 16384,
            "row_bytes_cap": None,
            "row_cap": None,
            "tool_execution": "parallel",
            "tool_result_max_chars": 2000,
        },
        "labels": {
            "keep_recent_tokens": "PORTED",
            "max_steps": "ADDED",
            "max_tool_result_bytes": "ADDED",
            "reserve_tokens": "PORTED",
            "row_bytes_cap": "ADDED",
            "row_cap": "ADDED",
            "tool_execution": "DIVERGES",
            "tool_result_max_chars": "PORTED",
        },
        "notes": {
            "max_steps": "A backstop for an unattended tab; pi's loop is `while (true)`. Raised 40 -> 100 on 2026-09-24: cryptic-exon-q1 spent 40 steps on a real RNA-seq analysis and was still working. Configurable, and a spent budget appends a `max-steps` entry to the run's `guards`.",
            "max_tool_result_bytes": "pi caps bash and read only and never truncates an MCP result. Olit "
            "discards a single oversized result rather than losing the turn.",
            "row_cap": "With row_bytes_cap, the two limits pi's truncate uses, applied to Galaxy list reads.",
            "row_bytes_cap": "A row count alone does not bound cost when the rows are fat.",
            "tool_execution": "KNOWN LIMITATION. pi runs a batch through executeToolCallsParallel unless a "
            "tool declares executionMode sequential. Olit dispatches in call order, so a "
            "batch of five reads costs five round-trips. Closing it means splitting "
            "`dispatch` into pi's sequential prepare and concurrent execute phases; the "
            "gates, the destructive-op modal and the single Pyodide namespace all sit in "
            "the prepare half. Tracked separately, not forced for parity.",
        },
    },
    "guards": {
        "source": "brain/olit/loop/tools.py, brain/olit/loop/agent.py",
        "label": "ADDED",
        "note": "Olit's own refusals. pi has none of them. Each names itself in the run's `guards` list so "
        "an eval can tell a trajectory a guard shaped from one the model chose.",
    },
}

ALLOWED_TOOL_DIVERGENCE = {
    "connect": "no connection step: olit is served by Galaxy",
    "download_dataset": "no local filesystem; returns content instead",
    "upload_file": "no access to the user's disk",
    "get_workflow_input_template": "drops the optional `verbose` parameter",
    "invoke_workflow": "drops the optional `parameters_normalized` parameter",
    "get_history_contents": "DIVERGES: galaxy-mcp fetches every item in the history and pages client-side, so it can report `total_items`; an 8,000-dataset history makes that untenable. olit lets Galaxy page, asks for one row past the limit, and returns the {items, shown, truncated, next_offset} envelope. Also omits `dataset_id` from each item (see the agent-safe projection principle).",
    "upload_file_from_url": "DIVERGES: passes `auto_decompress: true` on the fetch element. Galaxy's own uploader defaults it to true (client/src/composables/upload/uploadOptionModel.ts:67); the API defaults to false (lib/galaxy/schema/fetch_data.py:46) and galaxy-mcp passes nothing, so a `.gz` URL declared as its uncompressed type lands as gzip bytes wearing the wrong label. Observed on 4/4 cryptic-exon-q1 runs: the model names the logical format and drops the `.gz`, and every downstream tool then fails.",
    "get_histories": "DIVERGES: galaxy-mcp returns every history by default and adds a `pagination` block with `total_items` only when a limit is given, paying for a second unbounded fetch to count. olit always bounds the page at ROW_CAP, asks Galaxy for one row past the limit, and returns the {items, shown, truncated, next_offset} envelope it already uses for list_workflows and get_tool_panel. No `total`: the server was never asked for one.",
    "recommend_biocontainer": "ADDED: galaxy-mcp resolves a conda package set through "
    "`galaxy.tool_util.deps.mulled.recommend`, which needs galaxy-tool-util and cannot run in "
    "Pyodide. The udt-authoring skill names the second route for exactly this case, the quay.io "
    "tag API, and quay.io serves it cross-origin so the browser can take it. Keeps galaxy-mcp's "
    "response shape, and answers `found: false` for a multi-package set rather than guessing: a "
    "mulled-v2 tag is a hash over the set, which a tag listing cannot reveal.",
    "update_page": "ADDED: takes `section_heading`/`section_content` to replace one section, and `expect_hash` to refuse a write when the page changed since it was read. Uses Galaxy's own djb2 hash and section semantics (sectionDiffUtils.ts, page_assistant.py) so Olit and Galaxy's page assistant agree. Also sends `edit_source: agent`.",
    "get_tool_panel": "ADDED: galaxy-mcp returns the raw panel, ~25k tokens on a 170-tool server and unbounded on a public one. olit returns section, id, name and description, and takes a `section` filter. Returns the paging envelope {items, shown, total, truncated, next_offset} instead of a bare list, and takes limit/offset.",
    "list_workflows": 'DIVERGES: galaxy-mcp filters `name` as a lowercase substring of the workflow name. olit compares alphanumerics only and also matches tags, so "rna seq" finds "RNA-seq quantification" and anything tagged rna-seq. Delegating to Galaxy\'s own ?search is not the fix: it drops raw terms under four characters, so "rna seq" there matches every workflow. Returns the paging envelope {items, shown, total, truncated, next_offset} instead of a bare list, and takes limit/offset.',
    "list_visualizations": "ADDED: galaxy-mcp has no visualization surface at all. Asks Galaxy which installed plugins accept a dataset (api/plugins?dataset_id=) rather than matching extensions client-side, which gets subclassing right, and marks the visualization the datatype itself declares. Answers only what can render the dataset: the columns it used to return made a column lookup double as a plugin advertisement, and tabular charting drifted off vintent_dataset because of it. Columns come from get_dataset_details.",
    "show_visualization": "ADDED: renders a dataset with an installed plugin and saves nothing, the common case. galaxy-mcp has no equivalent; Galaxy's own UI reaches this through /visualizations/display, which needs the plugin name as well as the id.",
    "save_visualization": "ADDED: stores a durable Galaxy visualization the user keeps and can share. Split from show_visualization so that displaying a chart does not add a row to the user's visualization list, and because only a saved config can carry settings and tracks.",
    "get_visualization_details": "ADDED: galaxy-mcp has no visualization surface. Returns one plugin's inputs joined with what galaxy-charts stores for each input type, so settings and tracks are written to a stated shape instead of inferred from parameter names, and a caller can tell a genome input from a track input. The shapes come from galaxy-charts' published contract, vendored under brain/olit/vendor and pinned; Galaxy declares which inputs exist, galaxy-charts owns what they store. Per plugin rather than on the listing: the caller needs exactly one.",
    "get_visualization": "ADDED: galaxy-mcp has no visualization surface. Returns a saved visualization's current settings and tracks so a revision changes them rather than rebuilding them: save_visualization replaces the config wholesale, and reconstructing it blind drops whatever the plugin itself wrote there. Same read-before-write shape as get_page before update_page.",
    "vega_dataset": "ADDED: galaxy-mcp has no visualization surface. The route for a chart no "
    "installed plugin covers: a Vega-Lite spec the model writes, rendered inline and placeable in "
    "the record with {{artifact}}, where save_visualization would need a plugin and would add a "
    "row to the user's visualization list. olit owns only the data boundary -- it points the spec "
    "at the dataset rather than letting a spec carry a copy of the rows -- so columns are named as "
    "the dataset names them, positionally where it names none.",
    "get_visualization_options": "ADDED: galaxy-mcp has no visualization surface. Resolves a parameter's options from wherever the declaration says they live -- a remote json list, a Galaxy data table, or the declared options -- which is the same lookup galaxy-charts performs to build the input form, using the browser fetch olit already has. Without it the agent invents an option, and where the stored value is an object copied verbatim it cannot invent a usable one. Each option carries the value to store, so matching a label and filling the field is one call; `search` narrows a long list.",
    "list_history_ids": "DIVERGES: upstream added limit/offset after olit's own paging work; olit "
    "has not ported them and returns the whole list. Unported, not a decision.",
    "list_user_tools": "DIVERGES: upstream added limit/offset; olit has not ported them and returns "
    "the whole list. Unported, not a decision.",
    "search_iwc_workflows": "DIVERGES: upstream added limit/offset; olit has not ported them and "
    "returns the whole match set. Unported, not a decision.",
    "search_tools_by_keywords": "DIVERGES: upstream added limit/offset; olit has not ported them and "
    "returns the whole match set. Unported, not a decision.",
    "search_tools_by_name": "DIVERGES: upstream added limit/offset; olit has not ported them and "
    "returns the whole match set. Unported, not a decision.",
    "run_tool": "DIVERGES: upstream added tool_version, which asks Galaxy for a particular version "
    "and falls back when it is absent; olit lets Galaxy choose. Unported, not a decision.",
    "create_page": "DIVERGES: upstream refuses the whole tool on a server too old to attach a page "
    "to a history; olit has no version gate and its description does not claim one.",
    "list_pages": "DIVERGES: upstream refuses the whole tool on a server without the history_id "
    "query parameter; olit has no version gate.",
    "get_server_info": "DIVERGES: upstream reports unsupported_tools and version_known, both of "
    "which come from its version gate; olit has neither.",
    "run_user_tool": "DIVERGES: upstream preflights supplied datasets and collections against the "
    "tool definition before submitting; olit sends the call and lets Galaxy judge it.",
    "get_page_revision": "DIVERGES: upstream reports content_editor_source, saying where the "
    "editable markdown came from; olit returns the markdown without it.",
    "revert_page_revision": "DIVERGES: upstream returns the new revision the way get_page_revision "
    "does, with content_editor_source; olit does not report that field.",
    "get_job_details": "DIVERGES: galaxy-mcp fetches api/jobs/{id}; olit adds full=true so a failed job's stderr is readable, which is what invocation_outcome tells the model to reach for. That flag also carries tool_stdout/tool_stderr/job_stdout/job_stderr/stdout/stderr, hundreds of KB for a chatty tool, so each log field is trimmed to its last 4 KB on a line boundary with a notice naming the full size.",
}


# Every loom extension module, classified. NA is a decision, not an absence; INVESTIGATE
# means a capability olit could hold and has not yet judged. An unlisted module fails the
# check, which is the point: an addition upstream must be classified before it is ignored.
MODULE_CLASSIFICATION = {
    "context": "PORTED: the system prompt, tracked block by block in `seams`",
    "galaxy-page-markdown-guidance": "PORTED: tracked in `seams`",
    "sra-import-gate": "PORTED: brain/olit/loop/sra_import_gate.py",
    "confusables": "PORTED: brain/olit/loop/confusables.py",
    "secret-redaction": "PORTED: brain/olit/loop/secret_redaction.py",
    "skills-discovery": "PORTED: the skills registry",
    "skills": "PORTED: the skills registry",
    "vendor-skills": "PORTED: skills are vendored at build time",
    "notebook-writer": "PORTED: the record",
    "notebook-anchors": "PORTED: record anchors",
    "galaxy-page-binding": "PORTED: one record per history",
    "galaxy-page-sync": "PORTED: record writes",
    "galaxy-pages-api": "PORTED: the page tools",
    "galaxy-pages-sync": "PORTED: record writes",
    "galaxy-markdown-adapter": "PORTED: artifact markdown",
    "galaxy-api": "PORTED: the scoped catalog",
    "galaxy-poller": "PORTED: the job watcher",
    "galaxy-artifact-link-guidance": "PORTED: prompt.ARTIFACT_LINKS, at the tail of the chat "
    "formatting block as upstream interpolates it. Rooted at the session's own Galaxy rather than "
    "naming a server, and loom's YAML fences become olit's ```galaxy directives.",
    "galaxy-poll-guard": "PORTED: brain/olit/loop/galaxy_poll_guard.py, with the guidance "
    "in EXECUTING_A_STEP. loom holds the repeated read for two minutes; a browser turn that sleeps "
    "shows the user nothing, so the repeat is answered from what is known instead. The shell "
    "watcher stays the only judge of what is unfinished.",
    "iwc-plan-check": "MISSING by decision: loom sends one follow-up when a turn drafts a plan "
    "without having consulted IWC, to compensate for a weaker model drafting from memory. Olit "
    "keeps prompting out of the role of an enforceable invariant, and 2026-09-29 evidence for the "
    "behaviour it prevents is not yet separated from a tool-choice fault: plan-consults-iwc run 1 "
    "did consult the registry, through the substring search rather than the ranked recommend, and "
    "got nothing. Revisit when olit's own evals show a plan drafted past an applicable workflow.",
    "skill-triggers": "PORTED: the same two hints, as confusables.py and invocation_outcome.py. "
    "Upstream consolidated them into one table; olit reaches them through its own dispatcher.",
    "dashboard-tools": "NA: no panel surface to arrange. Decided rather than deferred -- the "
    "layout is a file beside notebook.md in an analysis directory, and olit has neither. Its one "
    "pane is the artifact pane, which the shell owns.",
    "dashboard-store": "NA: the transport is a write to .loom-dashboard.json on disk.",
    "dashboard-commands": "NA: a slash command over the dashboard file.",
    "galaxy-live-source": "NA: feeds the dashboard's Galaxy panel, and reads credentials from the "
    "three places a desktop keeps them. olit is served by the Galaxy it talks to and shows a "
    "history through Galaxy's own UI.",
    "progress-updates": "NA: model-free heartbeats on a 60s timer. olit streams every tool call to "
    "the transcript as it happens, so the shell already shows what a heartbeat would say.",
    "mcp-output": "NA: reads an MCP adapter's spill files off disk.",
    "mcp-recovery": "NA: no MCP surface; olit dispatches Galaxy tools by name.",
    "workspace-state-dir": "NA: a per-analysis state directory on disk.",
    "galaxy-job-block": "PORTED: job state in the record",
    "galaxy-upload": "PORTED: upload_file",
    "tools": "PORTED: the tool surface",
    "tools-sync": "PORTED: the tool surface is built at load",
    "evidence-gate": "NA: loom's model owns the checkbox and its poller owns a separate "
    "status block, so the two can contradict. olit's watcher writes the "
    "checkbox itself (applyJobOutcome), and no recorded run has the model "
    "flipping a step that carries an id. Revisit if the flip ever moves to "
    "the model.",
    "auto-resume": "PORTED: bounded automatic Galaxy follow-up in src/auto-resume.ts. Was labelled NA on the reasoning that olit starts no turn, which described the gap rather than a constraint: the browser does not prevent it, and the watcher and submit lifecycle were already there. onSettled used to tell the user to ask; loom's own prompt forbids asking them to ask."
    "posts a message and edits the record; it starts no turn, so there is no "
    "automatic continuation to bound.",
    "invocation-failure-hint": "PARTIAL: the imperative rides the result as `outcome_note` (see the tool.get_invocations.outcome seam); the pointer to loom's two vendored failure references is not ported, because they live in its foundry bundle rather than in the shared galaxy-skills repo",
    "galaxy-cred-drift": "NA: the browser session is the credential; nothing to reconnect",
    "user-instructions": "NA: no project directory in a browser, so no LOOM.md channel",
    "init-gate": "NA: asserts no turn starts; this harness always emits turn_start",
    "evidence-override-command": "NA: slash commands are a desktop shell affordance",
    "execution-commands": "NA: slash commands",
    "feedback-command": "NA: slash commands",
    "instructions-command": "NA: slash commands",
    "skills-command": "NA: slash commands",
    "sync-command": "NA: slash commands",
    "tester-id-command": "NA: slash commands",
    "activity": "NA: activity.jsonl is a desktop pane",
    "activity-hooks": "NA: activity.jsonl is a desktop pane",
    "agent-dir": "NA: no ~/.loom on disk",
    "config": "NA: config arrives from the Charts host",
    "profiles": "NA: no on-disk profiles",
    "state": "NA: no on-disk state",
    "git": "NA: no working copy",
    "local-exec": "NA: Pyodide, no local shell",
    "session-lifecycle": "NA: one worker session per tab",
    "ui-bridge": "NA: Electron IPC",
    "feedback": "NA: desktop feedback flow",
    "galaxy-launcher-error": "NA: no launcher",
    "galaxy-transport-error": "NA: transport is the browser's fetch",
    "galaxy-upload-tus": "NA: tus resumable upload is a desktop concern",
    "index": "NA: extension entry point",
    "types": "NA: type declarations",
}


def _builders(agent, existing, mcp_path):
    """One callable per layer, so a layer nobody reviewed is never recomputed."""

    def tool_surface():
        if not mcp_path:
            raise SystemExit("tool_surface needs --mcp path/to/galaxy_mcp/server.py")
        return {
            "source": pathlib.Path(mcp_path).parts[-2],
            "version": layers.mcp_version(mcp_path),
            "allowed_divergence": ALLOWED_TOOL_DIVERGENCE,
            "upstream": layers.mcp_tool_table(mcp_path),
            # What upstream builds, so a passthrough with matching text is still caught.
            "shaped_returns": layers.mcp_shaped_returns(mcp_path),
        }

    return {
        "eval_scenarios": lambda: {
            "source": "loom evals/scenarios",
            "fingerprints": layers.loom_scenarios(LOOM),
        },
        "eval_lib": lambda: {
            "source": "loom evals/lib",
            "note": "grading and normalization; a change here is eval parity even when it does not move a recorded run",
            "fingerprints": layers.loom_eval_lib(LOOM),
        },
        "loom_modules": lambda: {
            "source": "loom extensions/loom",
            "note": "inventory, not a parity list: an added or changed module must be "
            "classified as relevant, NA, or needing investigation",
            "classified": MODULE_CLASSIFICATION,
            "fingerprints": layers.loom_modules(LOOM),
        },
        "identity_prompt": lambda: agent.get("identity_prompt") or (existing.get("identity_prompt") or {}),
        "skills": lambda: agent["skills"],
        "pi": lambda: layers.pi_manifest(LOOM) or (existing.get("pi") or {}),
        "tool_surface": tool_surface,
        "policy": lambda: {n: {**d, "olit": description.policy(agent, n)} for n, d in POLICY_UPSTREAM.items()},
        "tool_request": lambda: description.tool_requests(agent),
        "tool_contract": lambda: description.tool_contracts(agent),
    }


def main():
    argv = sys.argv[1:]
    mcp_path = argv[argv.index("--mcp") + 1] if "--mcp" in argv else None
    if "--only" in argv:
        selected = {name.strip() for name in argv[argv.index("--only") + 1].split(",") if name.strip()}
        unknown = sorted(selected - set(LAYERS))
        if unknown:
            raise SystemExit(f"unknown layer: {', '.join(unknown)}\nlayers: {', '.join(LAYERS)}")
    elif "--all" in argv:
        selected = set(LAYERS)
    else:
        raise SystemExit(
            "certification says a layer was reviewed, so name the ones you reviewed:\n"
            f"  --only <layer>[,<layer>]   or   --all\nlayers: {', '.join(LAYERS)}"
        )

    agent = description.load()
    registry = json.loads(REGISTRY.read_text())
    existing = registry.get("layers") or {}
    builders = _builders(agent, existing, mcp_path)

    layer_data = {}
    for name in LAYERS:
        if name in selected:
            layer_data[name] = builders[name]()
        elif name in existing:
            layer_data[name] = existing[name]
    registry["layers"] = layer_data
    REGISTRY.write_text(json.dumps(registry, indent=2) + "\n")

    print(f"certified: {', '.join(sorted(selected))}")
    carried = [name for name in LAYERS if name not in selected and name in existing]
    if carried:
        print(f"left as previously certified: {', '.join(carried)}")


if __name__ == "__main__":
    main()
