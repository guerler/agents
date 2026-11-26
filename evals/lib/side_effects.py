"""What a run changed in the history it was given, recorded without judging it.

Whether a change was asked for is the graders' question; this only reports what differs between
the history before the agent started and after it finished.
"""

# The fields of a history item a change is reported on.
FIELDS = ("name", "visible", "deleted", "extension", "tags")


def snapshot(staged):
    """Every item of the staged history with the fields compared, keyed by id; None when nothing was staged."""
    if not staged:
        return None
    galaxy, history_id = staged["galaxy"], staged["history_id"]
    keys = ",".join(("id", "hid", "history_content_type", *FIELDS))
    items = galaxy.call(f"api/histories/{history_id}/contents?v=dev&keys={keys}&limit=10000")
    return {item["id"]: item for item in items}


def compare(before, after):
    """The fields that changed on items present before the run, and the items it added."""
    if before is None or after is None:
        return None
    changed = []
    for item_id, old in before.items():
        new = after.get(item_id)
        if new is None:
            changed.append(
                {"hid": old.get("hid"), "name": old.get("name"), "field": "present", "before": True, "after": False}
            )
            continue
        for field in FIELDS:
            if _value(old.get(field)) != _value(new.get(field)):
                changed.append(
                    {
                        "hid": old.get("hid"),
                        "name": old.get("name"),
                        "field": field,
                        "before": old.get(field),
                        "after": new.get(field),
                    }
                )
    added = [item for item_id, item in after.items() if item_id not in before]
    return {
        "changed": changed,
        "added": {
            "datasets": sum(1 for i in added if i.get("history_content_type") == "dataset"),
            "collections": sum(1 for i in added if i.get("history_content_type") == "dataset_collection"),
            "hidden": sum(1 for i in added if i.get("visible") is False),
        },
    }


def _value(value):
    return sorted(value) if isinstance(value, list) else value
