"""Shared by every checker: a failure, the dimensions, and the agent's tool sets."""

import description
import subject


class Failure:
    def __init__(self, assertion, detail, dimension):
        self.assertion = assertion
        self.detail = detail
        self.dimension = dimension

    def __repr__(self):
        return f"{self.dimension}/{self.assertion}: {self.detail}"


DIMENSIONS = ("validity", "routing", "tools", "behavior")


# What the gate protects: compute spent or data mutated before approval.
RECORD_TOOLS = frozenset(
    {subject.describe()["record"]["resume_tool"], "create_page", "update_page", "revert_page_revision"}
)
EXECUTION_TOOLS = frozenset(description.write_tools(subject.describe())) - RECORD_TOOLS
# What computes in the browser instead of in Galaxy.
LOCAL_TOOLS = frozenset(name for name, tool in subject.describe()["tools"].items() if tool.get("capability") == "local")


def _resolve_staged(value, run):
    """`$staged:<fixture>` becomes the id the harness actually uploaded this run.

    Scenarios used to assert the stub's constant ids, which pinned them to the stub. The
    id a real Galaxy hands out is only known at run time, so the scenario names the
    fixture and the grader resolves it.
    """
    if not isinstance(value, str) or not value.startswith("$staged:"):
        return value
    wanted = value.split(":", 1)[1]
    staged = getattr(run, "staged", None) or {}
    return (staged.get("dataset_ids") or {}).get(wanted, value)


def _issued_calls(run):
    """Tool calls with their raw arguments, from the transcript.

    Compaction drops older messages, so this is a subset of what the run actually issued.
    Use it only where the arguments are needed; `run.tools_called` is the complete list.
    """
    out = []
    for m in run.messages or []:
        for call in (m.get("tool_calls") or []) if isinstance(m, dict) else []:
            fn = call.get("function") or {}
            out.append((fn.get("name") or "", fn.get("arguments") or ""))
    return out
