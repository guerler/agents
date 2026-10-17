"""Checks on the charts and diagrams a run showed, and on the page that showed them."""

from .base import Failure, _resolve_staged


def _encoded_fields(spec):
    """Every field the chart plots, including inside a layered spec.

    A fit is drawn as two layers with no encoding of its own at the top, so reading only
    `spec.encoding` sees nothing and would call a real chart empty.
    """
    fields = set()
    for block in [spec, *(spec.get("layer") or []), *(spec.get("concat") or [])]:
        if not isinstance(block, dict):
            continue
        for channel in (block.get("encoding") or {}).values():
            for entry in channel if isinstance(channel, list) else [channel]:
                if isinstance(entry, dict) and entry.get("field"):
                    fields.add(entry["field"])
    return fields


def _mark_of(artifact):
    """Shells write mark as a bare string or {"type": ...}; both name the same chart."""
    raw = (artifact.get("spec") or {}).get("mark")
    return raw.get("type") if isinstance(raw, dict) else raw


def _setting(config, path):
    """The value at a dotted path under a visualization config's settings, or None."""
    node = config.get("settings") or {}
    for part in path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def _described(want):
    parts = [f"mark={want['mark']!r}"] if want.get("mark") else []
    if want.get("titleContains"):
        parts.append(f"title containing {want['titleContains']!r}")
    if want.get("plugin"):
        parts.append(f"plugin {want['plugin']!r}")
    if want.get("dataset"):
        parts.append(f"dataset {want['dataset']!r}")
    alternatives = [
        " and ".join(f"settings.{path} containing {value!r}" for path, value in alt.items())
        for alt in _settings_alternatives(want.get("settingsContain"))
    ]
    if alternatives:
        parts.append(" or ".join(f"({a})" for a in alternatives) if len(alternatives) > 1 else alternatives[0])
    return " and ".join(parts) or "that description"


def _settings_alternatives(value):
    """`settingsContain` as alternatives: one mapping, or a list of mappings any one of which may hold."""
    if not value:
        return []
    return list(value) if isinstance(value, list) else [value]


def _settings_hold(alternative, read):
    return all(str(wanted) in str(read(path) or "") for path, wanted in alternative.items())


def _inventory(made):
    def entry(a):
        if a.get("kind") == "visualization":
            return {"kind": "visualization", "plugin": a.get("visualization"), "settings": a.get("settings")}
        return {"kind": a.get("kind"), "mark": _mark_of(a), "title": a.get("title")}

    return [entry(a) for a in made] or "nothing"


def _page(spec, run, failures, exercised):
    """What the page showed: only a watched run drives a page, so a headless one is not graded here."""
    checked = getattr(run, "page", None)
    if not spec or checked is None:
        return
    exercised.add("page")
    for check in spec.get("chart") or []:
        seen = (checked.get("chart") or {}).get(check)
        if not seen or not seen.get("ok"):
            detail = seen.get("detail") if seen else "not reached: the chart drew nothing"
            failures.append(Failure(f"page.chart.{check}", detail, "page"))


def _artifacts(spec, run, failures, exercised):
    """Artifacts routed to the shell: kind, a chart's spec, a visualization's config."""
    if not spec:
        return
    exercised.add("artifacts")
    made = getattr(run, "artifacts", []) or []
    for want in spec.get("mustInclude") or []:
        kind = want.get("kind")
        matches = [a for a in made if not kind or a.get("kind") == kind]
        if not matches:
            failures.append(Failure("artifacts.kind", f"no {kind or 'any'} artifact was produced", "artifacts"))
            continue
        # Each entry picks the artifact meeting its own description, so a turn that charted
        # twice is graded per chart rather than twice over the first one.
        if want.get("mark"):
            matches = [a for a in matches if _mark_of(a) == want["mark"]]
        if want.get("titleContains"):
            needle = want["titleContains"].lower()
            matches = [a for a in matches if needle in (a.get("title") or "").lower()]
        # A visualization artifact carries the whole config it renders, saved or only shown.
        if want.get("plugin"):
            plugins = [want["plugin"]] if isinstance(want["plugin"], str) else list(want["plugin"])
            matches = [a for a in matches if a.get("visualization") in plugins]
        if want.get("dataset"):
            dataset = _resolve_staged(want["dataset"], run)
            matches = [a for a in matches if a.get("dataset_id") == dataset]
        alternatives = _settings_alternatives(want.get("settingsContain"))
        if alternatives:
            matches = [
                a for a in matches if any(_settings_hold(alt, lambda path: _setting(a, path)) for alt in alternatives)
            ]
        if not matches:
            failures.append(
                Failure(
                    "artifacts.mustInclude",
                    f"no {kind or 'any'} artifact matched {_described(want)}; produced {_inventory(made)}",
                    "artifacts",
                )
            )
            continue
        data = (matches[0].get("spec") or {}).get("data") or {}
        if want.get("referencesDataset"):
            if "url" not in data:
                failures.append(
                    Failure(
                        "artifacts.referencesDataset",
                        f"{kind} embeds its rows; expected a dataset reference",
                        "artifacts",
                    )
                )
            else:
                wanted_id = _resolve_staged(want.get("datasetId"), run)
                if wanted_id and wanted_id not in data["url"]:
                    failures.append(
                        Failure("artifacts.datasetId", f"{kind} references {data['url']}, not {wanted_id}", "artifacts")
                    )
        encoded = _encoded_fields(matches[0].get("spec") or {})
        for field in want.get("encodes") or []:
            if field not in encoded:
                failures.append(
                    Failure(
                        "artifacts.encodes", f"{kind} does not encode {field}; it plots {sorted(encoded)}", "artifacts"
                    )
                )
