"""Checks on the conversation itself: messages, events, tool calls, chat text, plan, behavior, budget."""

import re

from ..plan import count_plans, parse_latest_plan, step_has_description
from .base import EXECUTION_TOOLS, Failure, _issued_calls, _resolve_staged
from .galaxy import _galaxy_did_work


def _events(spec, run, failures, exercised):
    """loom's `events` family: proof the turn ran, or proof it was refused before running.

    Without this a scenario can only infer that the agent executed, from some other
    assertion happening to pass -- which is how a turn that never ran scores full marks.
    """
    if not spec:
        return
    exercised.add("behavior")
    seen = run.events
    for name in spec.get("mustInclude") or []:
        if name not in seen:
            failures.append(
                Failure("events.mustInclude", f"no `{name}` event; the turn did not get that far", "behavior")
            )
    for name in spec.get("mustNotInclude") or []:
        if name in seen:
            failures.append(
                Failure("events.mustNotInclude", f"`{name}` fired, which this scenario forbids", "behavior")
            )


def _messages(spec, run, failures, exercised):
    if not spec:
        return
    exercised.add("behavior")
    if spec.get("repliesInChat") and getattr(run, "exhausted", False):
        failures.append(Failure("messages.repliesInChat", "the turn hit the step cap while still working", "behavior"))
    elif spec.get("repliesInChat") and not run.chat_text.strip():
        failures.append(Failure("messages.repliesInChat", "the turn produced no chat text", "behavior"))


def _tool_calls(spec, run, failures, exercised):
    """loom's `toolCalls.mustInclude`, including its `argsContains` form.

    Names are read from the recorded run, which holds every call. The transcript is only
    consulted for `argsContains`, because it is what compaction thins.
    """
    if not spec:
        return
    exercised.add("behavior")
    called = list(run.tools_called or [])
    issued = _issued_calls(run)
    for want in spec.get("mustInclude") or []:
        name = want.get("name")
        contains = {k: _resolve_staged(v, run) for k, v in (want.get("argsContains") or {}).items()}
        if not contains:
            if name not in called:
                failures.append(Failure("toolCalls.mustInclude", f"never called {name}", "behavior"))
            continue
        if any(issued_name == name and all(str(v) in args for v in contains.values()) for issued_name, args in issued):
            continue
        detail = f"never called {name} with {contains}"
        if name in called and not any(issued_name == name for issued_name, _ in issued):
            detail += "; the call was made but its arguments did not survive compaction"
        failures.append(Failure("toolCalls.mustInclude", detail, "behavior"))
    for name in spec.get("mustNotInclude") or []:
        if name in called:
            failures.append(
                Failure("toolCalls.mustNotInclude", f"called {name}, which this scenario forbids", "behavior")
            )


def _chat_text(spec, run, failures, exercised):
    """loom's `chatText.mustInclude`: the answer itself has to contain something."""
    if not spec:
        return
    exercised.add("behavior")
    text = run.chat_text or ""
    for needle in spec.get("mustInclude") or []:
        if needle not in text:
            failures.append(Failure("chatText.mustInclude", f"chat never contained {needle!r}", "behavior"))
    for needle in spec.get("mustNotInclude") or []:
        if needle in text:
            failures.append(Failure("chatText.mustNotInclude", f"chat contained banned {needle!r}", "behavior"))
    for pattern in spec.get("mustMatch") or []:
        rx = _compile(pattern, "chatText.mustMatch", failures)
        if rx and not rx.search(text):
            failures.append(Failure("chatText.mustMatch", f"chat never matched /{pattern}/", "behavior"))
    for pattern in spec.get("mustNotMatch") or []:
        rx = _compile(pattern, "chatText.mustNotMatch", failures)
        if rx and rx.search(text):
            failures.append(Failure("chatText.mustNotMatch", f"chat matched banned /{pattern}/", "behavior"))
    for fact in spec.get("mustIncludeFromGalaxy") or []:
        _chat_includes_from_galaxy(fact, run, text, failures)


def _chat_includes_from_galaxy(fact, run, text, failures):
    """A value the server states, such as its version, read from it rather than written down."""
    staged = getattr(run, "staged", None)
    where = f"{fact['get']} {fact['field']}"
    if not staged:
        failures.append(Failure("chatText.mustIncludeFromGalaxy", f"no Galaxy to read {where} from", "behavior"))
        return
    value = str((staged["galaxy"].call(fact["get"]) or {}).get(fact["field"]) or "")
    if not value:
        failures.append(Failure("chatText.mustIncludeFromGalaxy", f"Galaxy stated no {where}", "behavior"))
    elif value not in text:
        failures.append(
            Failure("chatText.mustIncludeFromGalaxy", f"chat never contained {value!r} ({where})", "behavior")
        )


def _compile(pattern, assertion, failures):
    """A bad pattern fails the run, not the matrix."""
    try:
        return re.compile(pattern)
    except re.error as exc:
        failures.append(Failure(assertion, f"invalid regex /{pattern}/: {exc}", "behavior"))
        return None


def _plan(spec, run, failures, exercised):
    if not spec:
        return
    exercised.add("validity")
    plan = parse_latest_plan(run.chat_text)

    if spec.get("exists") is False:
        if plan is not None:
            failures.append(Failure("plan.exists", f"expected no plan, found {plan.title!r}", "validity"))
        return

    if plan is None:
        # The gate: everything downstream is unmeasurable without a parseable plan.
        failures.append(Failure("plan.exists", "no `## Plan X: <title> [routing]` block in chat", "validity"))
        # Every declared dimension fails too; not gradeable is not a pass.
        if spec.get("routingIn"):
            exercised.add("routing")
            failures.append(Failure("plan.routingIn", "no plan in chat, so routing could not be graded", "routing"))
        if spec.get("mentionsOneOf"):
            exercised.add("tools")
            failures.append(Failure("plan.mentionsOneOf", "no plan in chat, so tools could not be graded", "tools"))
        return

    most = spec.get("maxDrafts")
    if most is not None:
        drafts = count_plans(run.chat_text)
        if drafts > most:
            failures.append(
                Failure(
                    "plan.maxDrafts",
                    f"drafted {drafts} plans, wanted at most {most}; re-planning after approval "
                    "spends the context window and never executes",
                    "behavior",
                )
            )
            exercised.add("behavior")

    minimum = spec.get("minPendingSteps")
    if minimum is not None and len(plan.pending_steps) < minimum:
        failures.append(
            Failure("plan.minPendingSteps", f"{len(plan.pending_steps)} pending steps, wanted {minimum}", "validity")
        )

    if spec.get("eachStepHasDescription"):
        lines = run.chat_text.splitlines()
        for step in plan.pending_steps:
            # The steps are the latest plan's, so read the latest line naming each: a revision
            # repeats a step's title, and the first draft's line describes it differently.
            idx = max((i for i, ln in enumerate(lines) if step["text"] in ln), default=-1)
            following = lines[idx + 1 : idx + 4] if idx >= 0 else []
            if not step_has_description(step["text"], following):
                failures.append(Failure("plan.eachStepHasDescription", f"bare step: {step['text'][:60]!r}", "validity"))
                break

    if spec.get("routingIn"):
        exercised.add("routing")
        allowed = [r.lower() for r in spec["routingIn"]]
        if plan.routing not in allowed:
            failures.append(Failure("plan.routingIn", f"routed [{plan.routing}], expected one of {allowed}", "routing"))

    if spec.get("mentionsOneOf"):
        exercised.add("tools")
        haystack = run.chat_text.lower()
        if not any(t.lower() in haystack for t in spec["mentionsOneOf"]):
            failures.append(Failure("plan.mentionsOneOf", f"named none of {spec['mentionsOneOf']}", "tools"))


def _behavior(spec, run, failures, exercised):
    if not spec:
        return
    exercised.add("behavior")

    if spec.get("asksClarifyingQuestion"):
        # Inherited from loom, which names this a heuristic; a judge is the real answer.
        if not _asks_for_information(run.chat_text):
            failures.append(Failure("behavior.asksClarifyingQuestion", "did not ask for clarification", "behavior"))
        if parse_latest_plan(run.chat_text) is not None:
            failures.append(
                Failure(
                    "behavior.asksClarifyingQuestion",
                    "fabricated a plan instead of asking for clarification",
                    "behavior",
                )
            )

    if spec.get("galaxyDidWork"):
        _galaxy_did_work(run, failures)

    if spec.get("doesNotExecute"):
        # The gate's whole purpose: nothing side-effectful before approval.
        executed = set(run.tools_called) - set(getattr(run, "refused", []) or [])
        for tool in sorted(executed & EXECUTION_TOOLS):
            failures.append(Failure("behavior.doesNotExecute", f"called {tool} before any approval", "behavior"))


_ASKS_FOR_INFORMATION = re.compile(
    r"\b(could|can|would|will) you (let me know|tell me|share|provide|specify|confirm|clarify)\b"
    r"|\b(please )?(tell me|let me know|specify|clarify|confirm)\b"
    r"|\bi need to know\b|\bwhich of\b",
    re.IGNORECASE,
)


def _asks_for_information(chat):
    return "?" in (chat or "") or bool(_ASKS_FOR_INFORMATION.search(chat or ""))


def _budget(spec, run, failures, exercised):
    """What the task cost, not just whether it finished.

    A turn that finishes is not a turn that worked well: the charting scenarios passed
    their assertions while spending two thirds of their steps re-asking Galaxy for a
    history listing they already had. Cost has to be asserted or the next spin is again
    only visible to someone reading tool lists by hand.
    """
    if not spec:
        return
    exercised.add("budget")

    ceiling = spec.get("maxSteps")
    if ceiling and run.steps and run.steps > ceiling:
        failures.append(Failure("budget.maxSteps", f"took {run.steps} steps against a ceiling of {ceiling}", "budget"))

    allowed = spec.get("maxRepeatedCall")
    if allowed:
        worst, count = _longest_repeat(_issued_calls(run))
        if count > allowed:
            failures.append(
                Failure(
                    "budget.maxRepeatedCall",
                    f"called {worst} with the same arguments {count} times in a row " f"(allowed {allowed})",
                    "budget",
                )
            )


def _longest_repeat(issued):
    """The longest run of one tool called with identical arguments, and its length."""
    worst, count = "", 0
    current, streak = None, 0
    for name, args in issued:
        key = (name, args)
        streak = streak + 1 if key == current else 1
        current = key
        if streak > count:
            worst, count = name, streak
    return worst, count
