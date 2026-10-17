"""Checks on the record page the run wrote."""

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
import subject  # noqa: E402

from .base import Failure  # noqa: E402


def _record_ids_resolve(spec, run, failures, content):
    """A truncated id addresses nothing, so the step cannot be resumed from."""
    for token in set(re.findall(r"`([0-9a-f]{8,32})`", content or "")):
        if len(token) % 16 == 0:
            continue
        failures.append(
            Failure(
                "record.idsResolve",
                f"the record holds `{token}`, {len(token)} characters; a Galaxy id is a multiple "
                "of 16, so this addresses nothing and the step cannot be resumed from",
                "behavior",
            )
        )


def _record(spec, run, failures, exercised):
    """What the researcher is left with: a page that states what was actually found."""
    if not spec:
        return
    exercised.add("record")
    staged = getattr(run, "staged", None)
    if not staged:
        failures.append(Failure("record", "scenario staged no history to read", "record"))
        return
    galaxy = staged["galaxy"]
    slug = spec.get("slug")
    if slug:
        # A standalone report has no history to scope by.
        pages = galaxy.call(f"api/pages?search=slug:{slug}") or []
        page = next((p for p in pages if p.get("slug") == slug), None)
        missing = f"no page with slug {slug!r}"
    else:
        # The session owns one standalone page under a slug derived from its id, so this
        # names it rather than searching a history that no longer holds it.
        slug = f"olit-{staged.get('session_id')}"
        pages = galaxy.call(f"api/pages?search=slug:{slug}") or []
        page = next((p for p in pages if p.get("slug") == slug and not p.get("deleted")), None)
        missing = f"this session opened no record page (looked for slug {slug!r})"
    if not page:
        failures.append(Failure("record.exists", missing, "record"))
        return
    full = galaxy.call(f"api/pages/{page['id']}") or {}
    # What the editor loads: Galaxy fills `content_editor` only for markdown pages.
    content = full.get("content_editor") or ""
    if not content.strip() and (full.get("content") or "").strip():
        failures.append(
            Failure(
                "record.editable",
                f"page {slug!r} holds content but none of it is editable "
                f"(content_format={full.get('content_format')!r}); it opens empty",
                "record",
            )
        )
        return
    for needle in spec.get("mustMention") or []:
        if needle.lower() not in content.lower():
            failures.append(Failure("record.mustMention", f"the record never mentions {needle!r}", "record"))
    for needle in spec.get("mustNotMention") or []:
        if needle.lower() in content.lower():
            failures.append(Failure("record.mustNotMention", f"the record holds {needle!r}", "record"))
    if spec.get("idsResolve"):
        _record_ids_resolve(spec, run, failures, content)

    if spec.get("notEmpty"):
        planted = {line.strip() for line in subject.describe()["record"]["starter"].splitlines() if line.strip()}
        added = [line for line in content.splitlines() if line.strip() and line.strip() not in planted]
        if not content.strip():
            failures.append(Failure("record.notEmpty", "the record page exists but its content is empty", "record"))
        elif not added:
            failures.append(
                Failure("record.notEmpty", "the record holds only the starter; nothing was written", "record")
            )
