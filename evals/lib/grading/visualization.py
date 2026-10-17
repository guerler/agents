"""Checks on the visualizations a run saved in Galaxy."""

import re
import urllib.error
import urllib.parse
import urllib.request

from .artifacts import _setting, _settings_alternatives, _settings_hold
from .base import Failure, _resolve_staged


def _track_dataset(track):
    """The dataset a track names, however galaxy-charts stored it."""
    if not isinstance(track, dict):
        return None
    value = track.get("urlDataset")
    if isinstance(value, dict):
        return value.get("id")
    return value


# galaxy-charts stores these as a bare value; only data/data_table/data_json take an entry.
SCALAR_INPUT_TYPES = {"boolean", "color", "text", "textarea", "integer", "float", "select", "data_column"}


def _object_valued_scalars(galaxy, detail):
    """Parameters the plugin declares as scalars but whose stored value is an entry.

    Read from the plugin declaration rather than olit's own contract, so a hole in the
    tool's validation cannot hide behind the same hole here.
    """
    config = (detail.get("latest_revision") or {}).get("config") or {}
    plugin = galaxy.call(f"api/plugins/{detail.get('type')}") or {}
    declared = {}
    for group in ("settings", "tracks"):
        for param in plugin.get(group) or []:
            if isinstance(param, dict) and param.get("name"):
                declared[param["name"]] = param.get("type")

    entries = [config.get("settings") or {}, *(config.get("tracks") or [])]
    return [
        f"{key}={value!r}"
        for entry in entries
        if isinstance(entry, dict)
        for key, value in entry.items()
        if declared.get(key) in SCALAR_INPUT_TYPES and isinstance(value, (dict, list))
    ]


# What a data_column stores, stated here rather than read from the agent's own vendored
# contract: a wrong contract must not be able to hide behind a check that trusts it.
COLUMN_VALUE = re.compile(r"^(auto|\d+)$")


def _named_columns(galaxy, detail):
    """data_column values naming a column instead of indexing one.

    Read from the plugin declaration for the same reason _object_valued_scalars does.
    """
    config = (detail.get("latest_revision") or {}).get("config") or {}
    plugin = galaxy.call(f"api/plugins/{detail.get('type')}") or {}
    declared = {}
    for group in ("settings", "tracks"):
        for param in plugin.get(group) or []:
            if isinstance(param, dict) and param.get("name"):
                declared[param["name"]] = param.get("type")

    entries = [config.get("settings") or {}, *(config.get("tracks") or [])]
    return [
        f"{key}={value!r}"
        for entry in entries
        if isinstance(entry, dict)
        for key, value in entry.items()
        if declared.get(key) == "data_column" and isinstance(value, str) and not COLUMN_VALUE.match(value)
    ]


def _excluded_columns(galaxy, detail, wanted):
    """Inputs holding a column the request asked to drop.

    An index that looks valid can still name the wrong column. Renumbering the survivors of
    a removal to a contiguous run is the way that happens, and it is invisible to every
    other check here: the values are indices, the count is right, the reply reads correctly.
    """
    config = (detail.get("latest_revision") or {}).get("config") or {}
    entries = [config.get("settings") or {}, *(config.get("tracks") or [])]
    found = []
    for name, banned in wanted.items():
        banned = {str(b) for b in banned}
        for entry in entries:
            if isinstance(entry, dict) and str(entry.get(name)) in banned:
                found.append(f"{name}={entry[name]!r}")
    return found


def _visualization(spec, run, failures, exercised):
    """Did a saved visualization land in Galaxy, pointing at the intended dataset.

    Read from the server rather than the transcript: the agent narrating a chart is not
    evidence that one exists, and an artifact in the pane is not a Galaxy object.
    """
    if not spec:
        return
    exercised.add("behavior")
    staged = getattr(run, "staged", None)
    if not staged:
        failures.append(Failure("visualization", "scenario staged no dataset", "behavior"))
        return

    galaxy = staged["galaxy"]
    wanted_dataset = _resolve_staged(spec.get("referencesDataset"), run)
    saved = galaxy.call("api/visualizations") or []
    if not isinstance(saved, list):
        saved = []

    matching = []
    for entry in saved:
        try:
            detail = galaxy.call(f"api/visualizations/{entry.get('id')}") or {}
        except urllib.error.HTTPError as exc:
            # A visualization whose plugin is no longer installed still lists but cannot be
            # shown. It predates this run, so it is not evidence either way; anything else raises.
            if exc.code != 404:
                raise
            continue
        config = (detail.get("latest_revision") or {}).get("config") or {}
        if wanted_dataset and config.get("dataset_id") != wanted_dataset:
            continue
        matching.append(detail)

    # `absent` grades the opposite invariant: showing a chart must not save one.
    if spec.get("absent"):
        if matching:
            failures.append(
                Failure(
                    "visualization.absent",
                    f"{len(matching)} saved visualization(s) reference the staged dataset; "
                    "displaying a chart must not add one to the user's visualizations",
                    "behavior",
                )
            )
        return

    if not matching:
        failures.append(
            Failure(
                "visualization.exists",
                "no saved visualization references the staged dataset; the chart was never " "written to Galaxy",
                "behavior",
            )
        )
        return

    # A revision in place leaves one visualization; a duplicate would satisfy the rest anyway.
    if spec.get("count") is not None and len(matching) != spec["count"]:
        failures.append(
            Failure(
                "visualization.count",
                f"{len(matching)} saved visualizations reference the staged dataset, wanted {spec['count']}",
                "behavior",
            )
        )

    allowed = spec.get("type")
    if allowed:
        allowed = [allowed] if isinstance(allowed, str) else list(allowed)
        types = [v.get("type") for v in matching]
        if not any(t in allowed for t in types):
            failures.append(
                Failure(
                    "visualization.type", f"saved visualization(s) of type {types}, wanted one of {allowed}", "behavior"
                )
            )

    alternatives = _settings_alternatives(spec.get("settingsContain"))
    for path, wanted in (alternatives[0] if len(alternatives) == 1 else {}).items():
        seen = set()
        for v in matching:
            node = _setting((v.get("latest_revision") or {}).get("config") or {}, path)
            if node is not None:
                seen.add(str(node))
        if not any(str(wanted) in s for s in seen):
            failures.append(
                Failure(
                    "visualization.settingsContain",
                    f"no saved visualization has settings.{path} containing {wanted!r}; "
                    f"found {sorted(seen) or 'nothing'}",
                    "behavior",
                )
            )

    # A plugin reads a bare value; an entry stored in its place renders an empty chart while
    # the agent reports success, so the chat and the pane both look right.
    if spec.get("scalarValues"):
        for v in matching:
            wrong = _object_valued_scalars(galaxy, v)
            if wrong:
                failures.append(
                    Failure(
                        "visualization.scalarValues",
                        f"saved config stores an entry where the plugin declares a scalar: " f"{', '.join(wrong)}",
                        "behavior",
                    )
                )

    if spec.get("columnsAreIndices"):
        for v in matching:
            named = _named_columns(galaxy, v)
            if named:
                failures.append(
                    Failure(
                        "visualization.columnsAreIndices",
                        f"saved config names columns where the plugin indexes them: {', '.join(named)}",
                        "behavior",
                    )
                )

    for spec_name, banned in (spec.get("columnsExclude") or {}).items():
        for v in matching:
            held = _excluded_columns(galaxy, v, {spec_name: banned})
            if held:
                failures.append(
                    Failure(
                        "visualization.columnsExclude",
                        f"saved config still charts a column the request removed: {', '.join(held)}",
                        "behavior",
                    )
                )

    # Adding a track means the saved config gained a dataset, which no assertion about the
    # chat or the pane can see: the agent reports success either way.
    for want in spec.get("tracksDataset") or []:
        wanted_track = _resolve_staged(want, run)
        tracked = {
            _track_dataset(t)
            for v in matching
            for t in ((v.get("latest_revision") or {}).get("config") or {}).get("tracks") or []
        }
        if wanted_track not in tracked:
            failures.append(
                Failure(
                    "visualization.tracksDataset",
                    f"no saved visualization tracks {want}; tracks reference {sorted(tracked - {None})}",
                    "behavior",
                )
            )

    if len(alternatives) > 1 and not any(
        _settings_hold(alt, lambda path, v=v: _setting((v.get("latest_revision") or {}).get("config") or {}, path))
        for v in matching
        for alt in alternatives
    ):
        failures.append(
            Failure(
                "visualization.settingsContain",
                f"no saved visualization holds any of the alternatives {alternatives}",
                "behavior",
            )
        )

    # Each wanted setting and track has to hold in one visualization, not across several.
    def holds_all(v):
        config = (v.get("latest_revision") or {}).get("config") or {}
        settings_hold = not alternatives or any(
            _settings_hold(alt, lambda path: _setting(config, path)) for alt in alternatives
        )
        tracked = {_track_dataset(t) for t in config.get("tracks") or []}
        tracks_hold = all(_resolve_staged(w, run) in tracked for w in spec.get("tracksDataset") or [])
        return settings_hold and tracks_hold

    wants = spec.get("settingsContain") or spec.get("tracksDataset")
    met = not any(f.assertion in ("visualization.settingsContain", "visualization.tracksDataset") for f in failures)
    if wants and met and not any(holds_all(v) for v in matching):
        failures.append(
            Failure(
                "visualization.oneVisualization",
                f"the wanted settings and tracks are split across {len(matching)} saved visualizations; "
                "no single one holds them all",
                "behavior",
            )
        )
