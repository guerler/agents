"""What a scenario may say: every key the harness and its checkers read."""

import re

# Every top-level key the harness reads; `id` and `source` are set by the loaders.
SCENARIO_KEYS = {
    "name",
    "description",
    "inputs",
    "timeoutMs",
    "assertions",
    "dataset",
    "runs",
    "optIn",
    "requires",
    "capabilities",
    "emptyHistory",
    "toolTest",
    "workflows",
    "approveWhenPlanned",
    "restartAfter",
    "recoverSession",
    "systemPrompt",
    "id",
    "source",
}


# The families an anyOf branch may assert; the rest grade the turn as a whole.
BRANCH_FAMILIES = {"artifacts", "visualization", "toolCalls"}

# Every key the checkers read, by family; a scenario naming any other would go ungraded.
ASSERTION_KEYS = {
    "messages": {"repliesInChat"},
    "toolCalls": {"mustInclude", "mustNotInclude"},
    "chatText": {"mustInclude", "mustNotInclude", "mustMatch", "mustNotMatch", "mustIncludeFromGalaxy"},
    "plan": {"exists", "maxDrafts", "minPendingSteps", "eachStepHasDescription", "routingIn", "mentionsOneOf"},
    "behavior": {"asksClarifyingQuestion", "galaxyDidWork", "doesNotExecute"},
    "events": {"mustInclude", "mustNotInclude"},
    "artifacts": {"mustInclude"},
    "toolOutput": {"matchesToolTest", "honestReport"},
    "invocation": {"succeeded", "producesDatasets", "parameterValues"},
    "collection": {"type", "elements", "tagged", "elementDatatype"},
    "visualization": {
        "referencesDataset",
        "absent",
        "count",
        "type",
        "settingsContain",
        "scalarValues",
        "columnsAreIndices",
        "columnsExclude",
        "tracksDataset",
    },
    "container": {"resolves"},
    "record": {"slug", "notEmpty", "mustMention", "mustNotMention", "idsResolve"},
    "budget": {"maxSteps", "maxRepeatedCall"},
    "history": {
        "producedDatasets",
        "noErrors",
        "landedDataset",
        "lineageIntact",
        "intact",
        "landedPdfs",
        "descendsFrom",
    },
    "page": {"chart"},
}


ITEM_KEYS = {
    ("toolCalls", "mustInclude"): {"name", "argsContains"},
    ("artifacts", "mustInclude"): {
        "kind",
        "mark",
        "titleContains",
        "plugin",
        "dataset",
        "datasetId",
        "settingsContain",
        "referencesDataset",
        "encodes",
    },
}


def unsupported(assertions):
    """The families and keys in `assertions` no checker reads, as `family.key` names."""
    out = []
    for family, spec in (assertions or {}).items():
        if family == "anyOf":
            for branch in spec:
                out += [f"anyOf.{key}" for key in branch if key not in BRANCH_FAMILIES]
                out += unsupported({key: value for key, value in branch.items() if key in BRANCH_FAMILIES})
            continue
        keys = ASSERTION_KEYS.get(family)
        if keys is None:
            out.append(family)
            continue
        for key, value in spec.items():
            if key not in keys:
                out.append(f"{family}.{key}")
            elif (family, key) in ITEM_KEYS:
                allowed = ITEM_KEYS[family, key]
                out += [
                    f"{family}.{key}.{k}" for item in value if isinstance(item, dict) for k in item if k not in allowed
                ]
    return sorted(set(out))


def validate_scenarios(scenarios):
    """Every key is read and every regex compiles, so authoring bugs fail at load time."""
    problems = []
    for scenario in scenarios:
        problems += [f"{scenario.get('id')}: nothing reads {key}" for key in sorted(set(scenario) - SCENARIO_KEYS)]
        problems += [
            f"{scenario.get('id')}: no checker reads {name}" for name in unsupported(scenario.get("assertions"))
        ]
        spec = ((scenario.get("assertions") or {}).get("chatText")) or {}
        for assertion in ("mustMatch", "mustNotMatch"):
            for pattern in spec.get(assertion) or []:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    problems.append(f"{scenario.get('id')}: {assertion} /{pattern}/: {exc}")
    return problems
