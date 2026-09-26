"""The operations this comparison runs, and what each side is asked.

Arguments are spelled out per side rather than converted, because the spelling is itself
one of the things being compared: galaxy-ops takes camelCase on the wire, the Python server
snake_case, and upstream records which one wins as an open decision.

`expect` is the author's reading of a difference that has already been looked at, in the
same spirit as galaxy-mcp's accepted-divergences: a case whose difference is not declared
here is reported as unreviewed, so new drift arrives in the diff rather than in silence.

Placeholders resolved against the live server before the run: $history, $dataset, $workflow,
$tool. Add a case by adding a dict; nothing else knows the list.
"""

EQUIVALENT = "equivalent"  # the model learns the same thing
COSMETIC = "cosmetic"  # spelling or envelope shape only
UPSTREAM = "upstream-fixable"  # galaxy-ops could reasonably provide this
ENVIRONMENT = "environment"  # genuinely differs by where it runs
INCOMPATIBLE = "incompatible"  # no reconciliation

CASES = [
    {
        "op": "get_tool_run_examples",
        "py": {"tool_id": "$tool"},
        "ts": {"toolId": "$tool"},
        "expect": {"data": COSMETIC},
        "why": "The control case. Python spells an unasked-for version as requested_version: null "
        "where the ops op omits the key; a caller learns the same either way.",
    },
    {
        "op": "get_histories",
        "py": {"limit": 2},
        "ts": {"limit": 2},
        "expect": {},
        "why": "Both report the account's real total. The ops op reads /api/histories/count, which "
        "is cheaper than the unpaged re-listing Python counts.",
    },
    {
        "op": "list_pages",
        "py": {"limit": 2},
        "ts": {"limit": 2},
        "expect": {},
        "why": "Both report total_matches. The ops op reads it off the response header, which only "
        "its run() can see.",
    },
    {
        "op": "get_history_contents",
        "py": {"history_id": "$history"},
        "ts": {"historyId": "$history"},
        "expect": {"pagination": UPSTREAM, "data_wrapper": COSMETIC},
        "why": "The items themselves agree. Python pins deleted/visible, caps at 100 and reports "
        "the total; the ops op does none of that and returns the list bare.",
    },
    {
        "op": "search_tools_by_name",
        "py": {"query": "filter"},
        "ts": {"query": "filter"},
        "expect": {"pagination": UPSTREAM},
        "why": "The matches agree; only the paging envelope is missing on the ops side.",
    },
    {
        "op": "get_tool_panel",
        "py": {},
        "ts": {},
        "expect": {"pagination": UPSTREAM, "data_wrapper": COSMETIC, "data": UPSTREAM},
        "why": "Python summarizes sections, counts their tools and opens one on request; the ops "
        "op returns every entry with its raw panel fields and no counts.",
    },
    {
        "op": "get_dataset_details",
        "py": {"dataset_id": "$dataset"},
        "ts": {"datasetId": "$dataset"},
        "expect": {"data": UPSTREAM},
        "why": "Content preview is deferred on the ops side.",
    },
    {
        "op": "get_tool_input_template",
        "py": {"tool_id": "$tool"},
        "ts": {"toolId": "$tool"},
        "expect": {},
        "why": "Both sides run the same ported semantics; a difference here is a port bug.",
    },
    {
        "op": "get_workflow_input_template",
        "py": {"workflow_id": "$workflow"},
        "ts": {"workflowId": "$workflow"},
        "expect": {},
        "why": "The slot contract, ported. Olit additionally wants the step annotation and "
        "the slot default, which neither side returns today.",
    },
    {
        "op": "get_invocations",
        "py": {"limit": 2},
        "ts": {},
        "expect": {"success": UPSTREAM, "data": UPSTREAM},
        "why": "The ops op is detail-by-id only and fails without one, so listing has nothing to "
        "compare against. Upstream defers listing and filtering by its own comment.",
    },
]
