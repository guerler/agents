"""Semantic comparison of two result envelopes.

Byte equality is the wrong bar: the two implementations spell parameters differently by
design, ids and timestamps differ per run, and list order is not promised. What is compared
is what a model could learn from each side -- which fields it is given, and the values of the
fields that carry meaning rather than identity.
"""

import re

# Identity and timing: present on both sides, never the same, never what the model reasons on.
VOLATILE = re.compile(
    r"(^|_)(id|uuid|create_time|update_time|created|updated|url|slug|username|email|"
    r"tool_request_id|encoded_id)$"
)


def _camel_to_snake(key):
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower()


def shape(value, depth=0):
    """What a model can learn from this value: its fields, not its contents.

    Lists collapse to the union of their elements' shapes, so a page of two and a page of
    two hundred compare equal -- the page size is a paging difference, reported separately,
    not a hundred and ninety-eight structural ones.
    """
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = _camel_to_snake(k)
            out[key] = "<volatile>" if VOLATILE.search(key) else shape(v, depth + 1)
        return out
    if isinstance(value, list):
        merged = {}
        for item in value[:20]:
            s = shape(item, depth + 1)
            if isinstance(s, dict):
                merged.update(s)
        return [merged] if merged else ["<scalar>"]
    return type(value).__name__


def _keys(s, prefix=""):
    """Every field path in a shape, so two shapes can be diffed as sets."""
    found = set()
    if isinstance(s, dict):
        for k, v in s.items():
            path = f"{prefix}.{k}" if prefix else k
            found.add(path)
            found |= _keys(v, path)
    elif isinstance(s, list) and s:
        found |= _keys(s[0], f"{prefix}[]")
    return found


def compare(py, ts):
    """The differences between two envelopes, as facts rather than a verdict."""
    diffs = {}
    for field in ("success", "message", "pagination", "data"):
        a, b = py.get(field), ts.get(field)
        if field == "message":
            # Prose, deliberately each side's own. Recorded, never judged.
            continue
        if field == "pagination":
            if _paging_meaning(a) != _paging_meaning(b):
                diffs["pagination"] = {"python": a, "ops": b}
            continue
        if field == "data":
            # A list one side names and the other returns bare carries the same items; that
            # is one difference about the wrapper, not one per field inside it.
            a_in, b_in, wrap = _align(a, b)
            if wrap:
                diffs["data_wrapper"] = wrap
            pk, tk = _keys(shape(a_in)), _keys(shape(b_in))
            only_py, only_ts = sorted(pk - tk), sorted(tk - pk)
            if only_py or only_ts:
                diffs["data"] = {"only_python": only_py, "only_ops": only_ts}
            continue
        if a != b:
            diffs[field] = {"python": a, "ops": b}
    return diffs


def _paging_meaning(p):
    """The one thing paging has to agree on: how many there are in total."""
    if not isinstance(p, dict):
        return None
    for key in ("total_items", "total"):
        if p.get(key) is not None:
            return {"total": p[key]}
    return None


def classify(case, diffs):
    """Each difference against what the case says was already reviewed."""
    declared = case.get("expect") or {}
    verdicts = {}
    for field in diffs:
        verdicts[field] = declared.get(field, "UNREVIEWED")
    for field, status in declared.items():
        if field not in diffs and field != "call":
            verdicts[field] = "STALE (declared, no longer differs)"
    return verdicts


def _align(a, b):
    """Look past a name one side gives to what the other returns bare.

    Only when the two disagree about being wrapped at all: unwrapping whatever looks
    unwrappable would hide a difference on the side that happens to name two things.
    """
    for first, second, who in ((a, b, "python"), (b, a, "ops")):
        if isinstance(first, dict) and not isinstance(second, dict) or (
            isinstance(first, dict) and isinstance(second, dict) and len(first) == 1 != len(second)
        ):
            inner = [k for k, v in first.items() if isinstance(v, (list, dict))]
            if len(inner) == 1 and type(first[inner[0]]) is type(second):
                unwrapped = first[inner[0]]
                pair = (unwrapped, second) if who == "python" else (second, unwrapped)
                return pair[0], pair[1], {"python": inner[0] if who == "python" else None,
                                          "ops": inner[0] if who == "ops" else None}
    return a, b, None
