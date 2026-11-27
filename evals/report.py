#!/usr/bin/env python3
"""The Agent Tool Evaluation Report: one self-contained HTML page from with-and-without results.

Reads only what `run.py --without TOOL --json FILE` writes: the per-run results and the manifest
beside them. The tool, the scenario roles and how each effect is reachable without the tool all
come from that data; nothing here knows a particular tool, agent or model.
"""

import argparse
import json
import math
import os
import statistics
import sys
from collections import Counter
from html import escape

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from lib.manifest import manifest_path  # noqa: E402

ARMS = ("with", "without")
# Assertion families that describe how the agent worked rather than what it achieved.
PROCESS = ("toolCalls", "events")
EFFICIENCY = ("budget",)
# How an effect is reachable without the tool, and what the tool is to it then.
CLASSES = {
    "agent": ("performance", "performance improvement", "reachable through the agent's other tools"),
    "galaxy": ("interface", "capability extension: agent interface", "available in Galaxy, not through the agent"),
    "none": ("galaxy", "capability extension: beyond Galaxy", "not available in Galaxy at all"),
    "unknown": ("unknown", "unclassified", "reachability not established"),
}
REST = "*"


def wilson(k, n, z=1.96):
    if not n:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def family(assertion):
    return assertion.split(".", 1)[0]


def outcome_failures(row):
    return [f for f in row["failures"] if family(f["assertion"]) not in PROCESS + EFFICIENCY]


def excluded(row):
    """Why a run measured nothing about the agent, or None. A run past its own budget is a failure, not this."""
    error = row.get("error")
    if not error or "past its" in error:
        return None
    if row.get("stalled"):
        return "Galaxy did not finish staging"
    if row.get("statusCode"):
        return f"HTTP {row['statusCode']}"
    return error.split("\n", 1)[0][:90]


def passed(row):
    return not row.get("error") and not outcome_failures(row)


def matches(assertion, effect):
    return assertion == effect or assertion.startswith(effect + ".")


def effect_outcome(row, effect, named):
    """True or False for whether a run achieved one declared effect, None when it cannot tell.

    `*` is every outcome check no named effect covers. Graders may stop a family at its first
    failing check, so a named effect whose family failed elsewhere was possibly never evaluated.
    """
    if row.get("error"):
        return False
    fails = [f["assertion"] for f in outcome_failures(row)]
    if effect == REST:
        return not [a for a in fails if not any(matches(a, e) for e in named)]
    if [a for a in fails if matches(a, effect)]:
        return False
    if [a for a in fails if family(a) == family(effect)]:
        return None
    return True


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def pct(x):
    return f"{round(100 * x)}%"


class Evaluation:
    def __init__(self, paths):
        self.rows, self.manifests = [], []
        for path in paths:
            with open(path) as f:
                self.rows += json.load(f)
            with open(manifest_path(path)) as f:
                self.manifests.append(json.load(f))
        withheld = {tuple(a["withhold"]) for m in self.manifests for a in m["arms"] if a["name"] == "without"}
        if len(withheld) != 1:
            raise SystemExit(f"the results must withhold one and the same tool set, found {sorted(withheld)}")
        self.tools = list(next(iter(withheld)))
        self.scenarios = {}
        for m in self.manifests:
            self.scenarios.update(m["scenarios"])
        self.models = list(dict.fromkeys(r["model"] for r in self.rows))
        self.target = [s for s, d in self.scenarios.items() if set(self.tools) & set(d.get("targets") or [])]
        self.control = [s for s, d in self.scenarios.items() if set(self.tools) & set(d.get("control") or [])]
        self.other = [s for s in self.scenarios if s not in self.target + self.control]

    def runs(self, model=None, arm=None, scenarios=None):
        return [
            r
            for r in self.rows
            if (model is None or r["model"] == model)
            and (arm is None or r.get("arm") == arm)
            and (scenarios is None or r["scenario"] in scenarios)
        ]

    def effects(self, scenario):
        declared = self.scenarios[scenario].get("reachableWithout") or {}
        named = [e for e in declared if e != REST]
        rest = declared.get(REST, {"via": "unknown"})
        return {**{e: declared[e] for e in named}, REST: rest}, named

    def cell(self, model, arm):
        rows = self.runs(model, arm, self.target)
        valid = [r for r in rows if not excluded(r)]
        ok = sum(passed(r) for r in valid)
        trials = Counter(r["scenario"] for r in rows)
        k = max(trials.values(), default=0)
        every = sum(
            1 for s in self.target if trials.get(s) == k and k and all(passed(r) for r in rows if r["scenario"] == s)
        )
        costs = [(r.get("usage") or {}).get("cost") for r in valid]
        cost = sum(costs) if costs and None not in costs else None
        return {
            "runs": len(rows),
            "excluded": len(rows) - len(valid),
            "valid": len(valid),
            "ok": ok,
            "ci": wilson(ok, len(valid)),
            "k": k,
            "passAll": every,
            "steps": median(r["steps"] for r in valid),
            "wall": median(r.get("wallSeconds") for r in valid),
            "tokens": median(
                (
                    ((r.get("usage") or {}).get("promptTokens") or 0)
                    + ((r.get("usage") or {}).get("completionTokens") or 0)
                    if r.get("usage") and r["usage"].get("promptTokens")
                    else None
                )
                for r in valid
            ),
            "cost": cost,
            "costPerSuccess": cost / ok if cost is not None and ok else None,
        }

    def by_class(self, model, arm):
        """Effect successes over valid target runs, grouped by how the effect is reachable without the tool.

        Each class maps to (achieved, determined, effect names, undetermined).
        """
        out = {}
        for s in self.target:
            effects, named = self.effects(s)
            for effect, route in effects.items():
                via = route.get("via", "unknown")
                for r in self.runs(model, arm, [s]):
                    if excluded(r):
                        continue
                    k, n, names, u = out.get(via, (0, 0, set(), 0))
                    got = effect_outcome(r, effect, named)
                    if got is None:
                        out[via] = (k, n, names | {effect}, u + 1)
                    else:
                        out[via] = (k + got, n + 1, names | {effect}, u)
        return out

    def contradictions(self):
        """Effects declared unreachable without the tool that a run without it reached anyway."""
        found = []
        for s in self.target:
            effects, named = self.effects(s)
            for effect, route in effects.items():
                if route.get("via") not in ("galaxy", "none"):
                    continue
                reached = [r for r in self.runs(arm="without", scenarios=[s]) if effect_outcome(r, effect, named)]
                if reached:
                    found.append((s, effect, route["via"], len(reached)))
        return found

    def called(self, rows):
        return sum(1 for r in rows if set(self.tools) & set(r.get("toolsCalled") or []))


# --- rendering --------------------------------------------------------------------------------


def heat(rate):
    return f"color-mix(in oklab, var(--heat-hi) {round(rate * 100)}%, var(--heat-lo))"


def heat_cell(rows):
    valid = [r for r in rows if not excluded(r)]
    if not rows:
        return '<td class="na">—</td>'
    if not valid:
        return f'<td class="na">0 valid of {len(rows)}</td>'
    k = sum(passed(r) for r in valid)
    rate = k / len(valid)
    ink = "var(--on-hi)" if rate >= 0.6 else "var(--fg)"
    return f'<td class="hm" style="background:{heat(rate)};color:{ink}">{k}/{len(valid)}</td>'


def bars(title, rows, unit, fmt):
    """Paired horizontal bars, with and without, on one scale."""
    values = [v for _, w, wo in rows for v in (w, wo) if v is not None]
    vmax = max(values) if values else 1
    width, label_w, bar_h, gap = 520, 170, 14, 10
    plot = width - label_w - 80
    h = len(rows) * (2 * bar_h + gap + 6) + 30
    out = [f'<svg viewBox="0 0 {width} {h}" role="img" aria-label="{escape(title)}">']
    y = 8
    for name, w, wo in rows:
        out.append(f'<text x="0" y="{y + bar_h + 4}" class="ax">{escape(name)}</text>')
        for i, (v, arm) in enumerate(((w, "with"), (wo, "without"))):
            yy = y + i * (bar_h + 4)
            if v is None:
                out.append(f'<text x="{label_w}" y="{yy + bar_h - 3}" class="ax muted">not reported</text>')
                continue
            bw = max(1.5, plot * v / vmax) if vmax else 1.5
            out.append(f'<rect x="{label_w}" y="{yy}" width="{bw:.1f}" height="{bar_h}" class="b-{arm}" rx="2"/>')
            out.append(f'<text x="{label_w + bw + 6:.1f}" y="{yy + bar_h - 3}" class="val">{fmt(v)}</text>')
        y += 2 * bar_h + gap + 6
    out.append(f'<text x="{label_w}" y="{h - 4}" class="ax muted">{escape(unit)}</text></svg>')
    return "".join(out)


def ci_strip(ev, cells):
    width, left, top, row = 520, 170, 22, 34
    plot = width - left - 20
    lanes = [(m, a) for m in ev.models for a in ARMS]
    h = top + len(lanes) * row + 8

    def x(p):
        return left + plot * p

    out = [f'<svg viewBox="0 0 {width} {h}" role="img" aria-label="Outcome success with 95% Wilson intervals">']
    for t in (0, 0.25, 0.5, 0.75, 1):
        out.append(f'<line x1="{x(t):.1f}" y1="{top - 6}" x2="{x(t):.1f}" y2="{h - 6}" class="grid"/>')
        out.append(f'<text x="{x(t):.1f}" y="{top - 10}" class="ax" text-anchor="middle">{pct(t)}</text>')
    for i, (m, a) in enumerate(lanes):
        c = cells[(m, a)]
        y = top + i * row + row / 2
        out.append(f'<text x="0" y="{y + 4}" class="ax">{escape(m)} · {a}</text>')
        if not c["valid"]:
            out.append(f'<text x="{left}" y="{y + 4}" class="ax muted">no valid runs</text>')
            continue
        lo, hi = c["ci"]
        out.append(f'<line x1="{x(lo):.1f}" y1="{y}" x2="{x(hi):.1f}" y2="{y}" class="ci-{a}"/>')
        out.append(f'<circle cx="{x(c["ok"] / c["valid"]):.1f}" cy="{y}" r="5" class="pt-{a}"/>')
    out.append("</svg>")
    return "".join(out)


def fraction(k, n):
    return f"{k}/{n}" if n else "—"


def achieved(cls):
    if not cls:
        return fraction(0, 0)
    k, n, _, u = cls
    return fraction(k, n) + (f" <span class='muted'>+{u} undetermined</span>" if u else "")


def findings(ev, cells):
    cards = []
    for via in ("galaxy", "none", "agent", "unknown"):
        lines, names = [], set()
        for m in ev.models:
            w = ev.by_class(m, "with").get(via)
            wo = ev.by_class(m, "without").get(via)
            if not w and not wo:
                continue
            names |= (w or (0, 0, set(), 0))[2] | (wo or (0, 0, set(), 0))[2]
            lines.append(
                f"<tr><th>{escape(m)}</th><td class='num'>{achieved(w)}</td>"
                f"<td class='num'>{achieved(wo)}</td></tr>"
            )
        if not lines:
            continue
        css, title, meaning = CLASSES[via]
        effects = ", ".join(
            f"<code>{escape('every other outcome check' if e == REST else e)}</code>" for e in sorted(names)
        )
        cards.append(
            f'<div class="finding"><span class="kind {css}">{escape(title)}</span>'
            f"<p>{escape(meaning[:1].upper() + meaning[1:])}: {effects}.</p>"
            f'<table class="mini"><thead><tr><th>Effect achieved</th><th>with</th><th>without</th></tr></thead>'
            f"<tbody>{''.join(lines)}</tbody></table></div>"
        )
    eff = []
    for m in ev.models:
        w, wo = cells[(m, "with")], cells[(m, "without")]
        parts = []
        if w["steps"] and wo["steps"]:
            parts.append(f"{wo['steps'] / w['steps']:.1f}× steps")
        if w["wall"] and wo["wall"]:
            parts.append(f"{wo['wall'] / w['wall']:.1f}× wall time")
        if w["tokens"] and wo["tokens"]:
            parts.append(f"{wo['tokens'] / w['tokens']:.1f}× tokens")
        if w["costPerSuccess"] and wo["costPerSuccess"]:
            parts.append(f"{wo['costPerSuccess'] / w['costPerSuccess']:.1f}× cost per success")
        if parts:
            eff.append(f"<tr><th>{escape(m)}</th><td>{' · '.join(parts)}</td></tr>")
    if eff:
        cards.append(
            '<div class="finding"><span class="kind performance">efficiency</span>'
            "<p>Without the tool, relative to with it (medians over valid target runs).</p>"
            f'<table class="mini"><tbody>{"".join(eff)}</tbody></table></div>'
        )
    return "".join(cards)


def summary_rows(ev, cells):
    out = ""
    for m in ev.models:
        for a in ARMS:
            c = cells[(m, a)]
            lo, hi = c["ci"]
            rate = f"{pct(c['ok'] / c['valid'])}<span class='ci'> {pct(lo)}–{pct(hi)}</span>" if c["valid"] else "—"
            tokens = "—" if c["tokens"] is None else f"{c['tokens'] / 1000:,.0f}k"
            wall = "—" if c["wall"] is None else f"{c['wall']:.0f} s"
            steps = "—" if c["steps"] is None else f"{c['steps']:g}"
            cps = "not reported" if c["costPerSuccess"] is None else f"${c['costPerSuccess']:.3f}"
            out += (
                f"<tr><th>{escape(m)}</th><td><span class='arm arm-{a}'>{a}</span></td>"
                f"<td class='num'>{c['ok']}/{c['runs']}</td><td class='num'>{c['excluded']}</td>"
                f"<td class='num'>{rate}</td><td class='num'>{c['passAll']}/{len(ev.target)}</td>"
                f"<td class='num'>{steps}</td><td class='num'>{wall}</td><td class='num'>{tokens}</td>"
                f"<td class='num'>{cps}</td></tr>"
            )
    return out


def scenario_rows(ev):
    out = ""
    for role, scenarios in (("target", ev.target), ("control", ev.control), ("other", ev.other)):
        for s in scenarios:
            tags = f'<span class="kind role">{role}</span>'
            if role == "target":
                effects, _ = ev.effects(s)
                for effect, route in effects.items():
                    css = CLASSES[route.get("via", "unknown")][0]
                    label = "rest" if effect == REST else effect
                    why = f' title="{escape(route["why"])}"' if route.get("why") else ""
                    tags += f'<span class="kind {css}"{why}>{escape(label)}</span>'
            cells = "".join(heat_cell(ev.runs(m, a, [s])) for m in ev.models for a in ARMS)
            with_runs = ev.runs(arm="with", scenarios=[s])
            use = f"{ev.called(with_runs)}/{len(with_runs)}"
            out += f"<tr><th><code>{escape(s)}</code>{tags}</th>{cells}<td class='use'>{use}</td></tr>"
    return out


def side_effect_rows(ev):
    out = ""
    for m in ev.models:
        for a in ARMS:
            rows = [r for r in ev.runs(m, a) if not excluded(r)]
            measured = [r for r in rows if r.get("sideEffects") is not None]
            fields = Counter(c["field"] for r in measured for c in r["sideEffects"]["changed"])
            touched = sum(1 for r in measured if r["sideEffects"]["changed"])
            added = Counter()
            for r in measured:
                added.update(r["sideEffects"]["added"])
            changes = ", ".join(f"{f} ×{n}" for f, n in sorted(fields.items())) or "none"
            out += (
                f"<tr><th>{escape(m)}</th><td><span class='arm arm-{a}'>{a}</span></td>"
                f"<td class='num'>{len(measured)}/{len(rows)}</td><td class='num'>{touched}</td><td>{escape(changes)}</td>"
                f"<td class='num'>{added['datasets']} / {added['collections']} / {added['hidden']}</td></tr>"
            )
    return out


def excluded_rows(ev):
    grouped = Counter((r["model"], r.get("arm"), excluded(r)) for r in ev.rows if excluded(r))
    totals = Counter((r["model"], r.get("arm")) for r in ev.rows)
    out = ""
    for (m, a, why), n in sorted(grouped.items(), key=lambda kv: (kv[0][0], str(kv[0][1]), -kv[1])):
        out += (
            f"<tr><th>{escape(m)}</th><td><span class='arm arm-{a}'>{a}</span></td>"
            f"<td class='num'>{n}/{totals[(m, a)]}</td><td>{escape(why)}</td></tr>"
        )
    return out or "<tr><td colspan='4'>None.</td></tr>"


def demonstrated(ev):
    gained, unhelped = [], []
    for s in ev.target:
        w = [r for r in ev.runs(arm="with", scenarios=[s]) if not excluded(r)]
        wo = [r for r in ev.runs(arm="without", scenarios=[s]) if not excluded(r)]
        if not w:
            continue
        rw = sum(passed(r) for r in w) / len(w)
        rwo = sum(passed(r) for r in wo) / len(wo) if wo else None
        if rw > 0.5 and (rwo is None or rwo < 0.5):
            gained.append(
                f"<li><code>{escape(s)}</code>: {pct(rw)} with, {pct(rwo) if rwo is not None else '—'} without.</li>"
            )
        if rw < 1 or (rwo is not None and rw <= rwo):
            reasons = Counter(f["assertion"] for r in w for f in outcome_failures(r)) or Counter(
                [excluded(r) or "error" for r in w if r.get("error")]
            )
            top = ", ".join(f"<code>{escape(a)}</code> ×{n}" for a, n in reasons.most_common(2))
            why = f"; most common: {top}" if top else ""
            unhelped.append(
                f"<li><code>{escape(s)}</code>: {pct(rw)} with, {pct(rwo) if rwo is not None else '—'} without{why}.</li>"
            )
    return "".join(gained) or "<li>None.</li>", "".join(unhelped) or "<li>None.</li>"


def failure_reasons(ev):
    out = ""
    for a in ARMS:
        reasons = Counter(f["assertion"] for r in ev.runs(arm=a, scenarios=ev.target) for f in outcome_failures(r))
        top = ", ".join(f"<code>{escape(k)}</code> ×{n}" for k, n in reasons.most_common(4)) or "none"
        out += f"<li><b>{a}:</b> {top}</li>"
    return out


def provenance(ev):
    items = []
    for m in ev.manifests:
        agent, harness, galaxy = m["agent"], m["harness"], m["galaxy"]
        version = galaxy.get("version") or {}
        dirty = lambda d: " (uncommitted changes)" if d.get("dirty") else ""  # noqa: E731
        items.append(
            f"<li><b>{escape(m['started'])}</b> · agent <code>{escape(str(agent.get('name')))}</code> "
            f"<code>{escape(str(agent.get('commit'))[:10])}</code> on {escape(str(agent.get('branch')))}{dirty(agent)}, "
            f"description <code>{escape(str(agent.get('description')))}</code> · harness "
            f"<code>{escape(str(harness.get('commit'))[:10])}</code>{dirty(harness)} · Galaxy "
            f"{escape(str(version.get('version_major', '?')))}.{escape(str(version.get('version_minor', '?')))} at "
            f"{escape(galaxy.get('url') or '?')} · models "
            + ", ".join(f"<code>{escape(x['id'])}</code> ({escape(str(x['model']))})" for x in m["models"])
            + f"<br>command <code>{escape(' '.join(m['command']))}</code>; environment "
            + (", ".join(f"<code>{k}={escape(str(v))}</code>" for k, v in m["environment"].items() if v) or "defaults")
            + "</li>"
        )
    return "".join(items)


def scenario_digests(ev):
    return "".join(
        f"<tr><td><code>{escape(s)}</code></td><td>{escape(d.get('source', ''))}</td><td><code>{escape(d['digest'])}</code></td></tr>"
        for s, d in sorted(ev.scenarios.items())
    )


def render(ev):
    cells = {(m, a): ev.cell(m, a) for m in ev.models for a in ARMS}
    k = max((c["k"] for c in cells.values()), default=0)
    gained, unhelped = demonstrated(ev)
    tool = ", ".join(ev.tools)
    control_runs = ev.runs(arm="with", scenarios=ev.control)

    def control_passes(arm):
        valid = [r for r in ev.runs(arm=arm, scenarios=ev.control) if not excluded(r)]
        return fraction(sum(passed(r) for r in valid), len(valid))

    target_runs = ev.runs(arm="with", scenarios=ev.target)
    contradicted = "".join(
        f"<li><code>{escape(e)}</code> in <code>{escape(s)}</code> is declared <b>{via}</b>, "
        f"yet {n} run(s) without the tool achieved it; the declaration needs revisiting.</li>"
        for s, e, via, n in ev.contradictions()
    )
    dates = sorted({m["started"][:10] for m in ev.manifests})
    revisions = sorted({str(m["agent"].get("commit"))[:10] for m in ev.manifests})
    galaxies = sorted(
        {
            f"{(m['galaxy'].get('version') or {}).get('version_major', '?')}.{(m['galaxy'].get('version') or {}).get('version_minor', '?')}"
            for m in ev.manifests
        }
    )
    steps_chart = bars(
        "Median steps",
        [(m, cells[(m, "with")]["steps"], cells[(m, "without")]["steps"]) for m in ev.models],
        "median model steps per valid target run",
        lambda v: f"{v:g}",
    )
    wall_chart = bars(
        "Median wall time",
        [(m, cells[(m, "with")]["wall"], cells[(m, "without")]["wall"]) for m in ev.models],
        "median seconds per valid target run, staging and Galaxy jobs included",
        lambda v: f"{v:.0f} s",
    )
    tokens_chart = bars(
        "Median tokens",
        [(m, cells[(m, "with")]["tokens"], cells[(m, "without")]["tokens"]) for m in ev.models],
        "median prompt and completion tokens per valid target run",
        lambda v: f"{v / 1000:.0f}k",
    )
    costs = [(m, cells[(m, "with")]["costPerSuccess"], cells[(m, "without")]["costPerSuccess"]) for m in ev.models]
    cost_chart = (
        bars(
            "Cost per success",
            costs,
            "USD per successful target run, as the provider reported it",
            lambda v: f"${v:.3f}",
        )
        if any(w is not None or wo is not None for _, w, wo in costs)
        else ""
    )
    heads = "".join(f"<th>{escape(m)} {a}</th>" for m in ev.models for a in ARMS)
    return f"""<title>{escape(tool)} Evaluation</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>{CSS}</style>
<div class="page">
<header>
  <div class="eyebrow">Agent Tool Evaluation Report</div>
  <h1>Tool <code>{escape(tool)}</code></h1>
  <div class="meta">
    <span>Agent <b>{escape(", ".join(sorted({str(m["agent"].get("name")) for m in ev.manifests})))} {escape(", ".join(revisions))}</b></span>
    <span>Galaxy <b>{escape(", ".join(galaxies))}</b></span>
    <span>Models <b>{escape(", ".join(ev.models))}</b></span>
    <span>Corpus <b>{len(ev.target)} target · {len(ev.control)} control · {len(ev.other)} other</b></span>
    <span>Trials <b>{k} per scenario, arm and model, arms interleaved</b></span>
    <span>Runs <b>{escape(", ".join(dates))}</b></span>
    <span class="evidence">exploratory evidence · n = {k}</span>
  </div>
</header>

<section class="verdict" aria-label="Findings">{findings(ev, cells)}</section>
{f'<section class="warn"><h2>Declarations contradicted by the data</h2><ul>{contradicted}</ul></section>' if contradicted else ""}

<section>
  <h2>Outcome success on target scenarios</h2>
  <p>Graded on Galaxy's state and the answer only; whether the tool was called never counts. Runs that measured nothing about the agent (provider or infrastructure failures) are counted apart; end to end keeps them in the denominator.</p>
  <div class="scroll"><table>
    <thead><tr><th>Model</th><th>Arm</th><th>End to end</th><th>Excluded</th><th>Success, valid runs (95% CI)</th><th>pass^{k}</th><th>Median steps</th><th>Median wall</th><th>Median tokens</th><th>Cost / success</th></tr></thead>
    <tbody>{summary_rows(ev, cells)}</tbody></table></div>
</section>

<div class="two">
  <section><h2>Success with uncertainty</h2>
    <p>Wilson 95% intervals. With {k} trials per cell, separated intervals are exploratory, not confirmatory.</p>
    {ci_strip(ev, cells)}
    <div class="legend"><span><i class="sw" style="background:var(--with)"></i>with the tool</span><span><i class="sw" style="background:var(--without)"></i>without</span></div>
  </section>
  <section><h2>Cost and effort</h2>{steps_chart}{wall_chart}{tokens_chart}{cost_chart}</section>
</div>

<section>
  <h2>Per scenario</h2>
  <p>Passes out of valid runs. Each target scenario lists its outcome effects, coloured by how they are reachable without the tool, as the scenario declares it: through the agent's other tools (performance), in Galaxy but not through the agent (agent interface), or not in Galaxy at all. The last column is how often the tool was called when offered.</p>
  <div class="legend">{"".join(f'<span><span class="kind {CLASSES[v][0]}">{escape(CLASSES[v][1])}</span></span>' for v in ("agent", "galaxy", "none", "unknown"))}</div>
  <div class="scroll"><table>
    <thead><tr><th>Scenario</th>{heads}<th>Tool called</th></tr></thead>
    <tbody>{scenario_rows(ev)}</tbody></table></div>
  <p class="notes">Tool called on target scenarios {ev.called(target_runs)}/{len(target_runs)}; on control scenarios {ev.called(control_runs)}/{len(control_runs)}. Control scenarios passed {control_passes("with")} with the tool offered and {control_passes("without")} without it.</p>
</section>

<div class="two">
  <section><h2>Demonstrated</h2><p>Target scenarios the tool turns from failing to passing.</p><ul>{gained}</ul></section>
  <section><h2>Limitations</h2><p>Target scenarios that failed at least once with the tool, or did no better with it than without, and why.</p><ul>{unhelped}</ul>
    <p class="notes">Most common failed outcome checks on target scenarios:</p><ul class="notes">{failure_reasons(ev)}</ul></section>
</div>

<div class="two">
  <section><h2>Side effects</h2>
    <p>What runs changed in the history they were given, compared before and after; reported without judging whether a change was asked for. Grading decides that separately.</p>
    <div class="scroll"><table>
      <thead><tr><th>Model</th><th>Arm</th><th>Measured</th><th>Runs changing staged items</th><th>Changed fields</th><th>Added datasets / collections / hidden</th></tr></thead>
      <tbody>{side_effect_rows(ev)}</tbody></table></div>
  </section>
  <section><h2>Excluded runs</h2>
    <p>Provider and infrastructure failures, by lane and cause; they say nothing about the tool.</p>
    <div class="scroll"><table>
      <thead><tr><th>Model</th><th>Arm</th><th>Runs</th><th>Cause</th></tr></thead>
      <tbody>{excluded_rows(ev)}</tbody></table></div>
  </section>
</div>

<section class="notes">
  <h2>Method and reproducibility</h2>
  <ul>
    <li><b>Arms.</b> Each trial runs with the tool and, back to back, with <code>{escape(tool)}</code> withheld by the agent's <code>withhold_tools</code> session setting; same scenarios, staged data, Galaxy and model.</li>
    <li><b>Scoring.</b> Outcome assertions only. <code>toolCalls.*</code> and <code>events.*</code> are process checks and <code>budget.*</code> is efficiency; neither decides success. A run past its own time budget counts as a failure, not an exclusion.</li>
    <li><b>Classification.</b> Each target scenario declares, per outcome check, how it is reachable without the tool (<code>reachableWithout</code>: <code>agent</code>, <code>galaxy</code>, <code>none</code>), with the reason in its <code>why</code>; hover a tag to read it. The report checks the declaration against the without arm and lists any contradiction above. A named effect counts as achieved only when no other check of its family failed, since a grader may stop a family at its first failing check; otherwise the run is counted as undetermined for that effect.</li>
    <li><b>Inputs.</b> Only the harness results and their manifests; no agent source is read.</li>
    <li><b>Caveats.</b> {k} trials per cell; the corpus was written by the tool's authors; no composite score by design.</li>
  </ul>
  <h2>Revisions and configuration</h2>
  <ul>{provenance(ev)}</ul>
  <details><summary>Scenario digests</summary><table><thead><tr><th>Scenario</th><th>Source</th><th>Digest</th></tr></thead><tbody>{scenario_digests(ev)}</tbody></table></details>
</section>
</div>
"""


CSS = """
:root {
  --bg: #f6f7f9; --surface: #ffffff; --fg: #1b2129; --muted: #5d6675; --rule: #dde1e7;
  --with: #13795b; --without: #8b95a5;
  --heat-lo: #eef1f4; --heat-hi: #13795b; --on-hi: #ffffff;
  --interface: #7a4d00; --interface-bg: #fbefd6; --galaxy: #8a1f3d; --galaxy-bg: #f8e1e8;
  --performance: #1d5fa8; --performance-bg: #e3edf9; --unknown: #5d6675; --unknown-bg: #eceff3;
  --sans: "IBM Plex Sans", "Helvetica Neue", Arial, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #12161b; --surface: #1a2027; --fg: #e3e7ec; --muted: #9aa4b2; --rule: #2c343e;
  --with: #3fbf93; --without: #7d8796; --heat-lo: #1f262e; --heat-hi: #2f9c77; --on-hi: #ffffff;
  --interface: #f0c26b; --interface-bg: #3a2e14; --galaxy: #f19ab4; --galaxy-bg: #3d1a25;
  --performance: #8fbdf2; --performance-bg: #1b2c40; --unknown: #a6b0bd; --unknown-bg: #262d36;
  color-scheme: dark; } }
:root[data-theme="dark"] {
  --bg: #12161b; --surface: #1a2027; --fg: #e3e7ec; --muted: #9aa4b2; --rule: #2c343e;
  --with: #3fbf93; --without: #7d8796; --heat-lo: #1f262e; --heat-hi: #2f9c77; --on-hi: #ffffff;
  --interface: #f0c26b; --interface-bg: #3a2e14; --galaxy: #f19ab4; --galaxy-bg: #3d1a25;
  --performance: #8fbdf2; --performance-bg: #1b2c40; --unknown: #a6b0bd; --unknown-bg: #262d36;
  color-scheme: dark; }
body { background: var(--bg); color: var(--fg); font-family: var(--sans); font-size: 14px; line-height: 1.5; margin: 0; }
.page { max-width: 1040px; margin: 0 auto; padding-block: 28px 48px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: grid; gap: 22px; }
header { display: grid; gap: 6px; border-bottom: 2px solid var(--fg); padding-bottom: 14px; }
.eyebrow { font: 500 11px/1 var(--mono); letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }
h1 { font: 600 28px/1.15 var(--sans); margin: 0; text-wrap: balance; }
h1 code { font-family: var(--mono); font-weight: 500; }
h2 { font: 600 15px/1.3 var(--sans); margin: 0 0 8px; text-wrap: balance; }
.meta { display: flex; flex-wrap: wrap; gap: 6px 18px; color: var(--muted); font-size: 12.5px; }
.meta b { color: var(--fg); font-weight: 500; }
.evidence { font: 500 11px/1 var(--mono); padding: 4px 7px; border: 1px solid var(--rule); border-radius: 3px; color: var(--muted); }
.verdict { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
.finding { background: var(--surface); border: 1px solid var(--rule); border-radius: 6px; padding: 12px 14px; display: grid; gap: 8px; align-content: start; }
.finding p { margin: 0; }
.warn { background: var(--surface); border: 1px solid var(--galaxy); border-radius: 6px; padding: 12px 14px; }
.two { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 22px; }
section > p { margin: 0 0 8px; color: var(--muted); max-width: 72ch; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { padding: 6px 8px; border-bottom: 1px solid var(--rule); text-align: left; vertical-align: middle; }
thead th { font: 500 11px/1.3 var(--mono); letter-spacing: .04em; text-transform: uppercase; color: var(--muted); border-bottom: 1px solid var(--fg); }
table.mini th, table.mini td { padding: 3px 6px; }
td.num { font-family: var(--mono); font-variant-numeric: tabular-nums; white-space: nowrap; }
.ci { color: var(--muted); font-size: 11.5px; }
.arm { font: 500 11px/1 var(--mono); padding: 3px 6px; border-radius: 3px; }
.arm-with { background: color-mix(in oklab, var(--with) 18%, transparent); color: var(--with); }
.arm-without { background: color-mix(in oklab, var(--without) 22%, transparent); color: var(--muted); }
td.hm { font-family: var(--mono); text-align: center; width: 72px; font-variant-numeric: tabular-nums; }
td.na { color: var(--muted); text-align: center; font-size: 12px; }
td.use { color: var(--muted); font: 12px var(--mono); text-align: center; }
.kind { display: inline-block; font: 500 10.5px/1 var(--mono); padding: 3px 5px; border-radius: 3px; margin: 2px 0 2px 6px; vertical-align: 1px; }
.role { color: var(--fg); border: 1px solid var(--rule); }
.performance { color: var(--performance); background: var(--performance-bg); }
.interface { color: var(--interface); background: var(--interface-bg); }
.galaxy { color: var(--galaxy); background: var(--galaxy-bg); }
.unknown { color: var(--unknown); background: var(--unknown-bg); }
.finding > .kind { margin-left: 0; justify-self: start; }
svg { width: 100%; height: auto; display: block; }
svg .ax { font: 12px var(--sans); fill: var(--fg); } svg .muted { fill: var(--muted); font-size: 11px; }
span.muted { color: var(--muted); font-size: 12px; white-space: nowrap; }
svg .val { font: 11.5px var(--mono); fill: var(--fg); }
svg .grid { stroke: var(--rule); stroke-width: 1; }
svg .b-with { fill: var(--with); } svg .b-without { fill: var(--without); }
svg .ci-with { stroke: var(--with); stroke-width: 3; stroke-linecap: round; } svg .ci-without { stroke: var(--without); stroke-width: 3; stroke-linecap: round; }
svg .pt-with { fill: var(--with); stroke: var(--surface); stroke-width: 2; } svg .pt-without { fill: var(--without); stroke: var(--surface); stroke-width: 2; }
.legend { display: flex; flex-wrap: wrap; gap: 10px; font-size: 12px; color: var(--muted); margin-bottom: 8px; }
.sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }
ul { margin: 0; padding-left: 18px; display: grid; gap: 4px; }
.notes { font-size: 12.5px; color: var(--muted); }
code { font-family: var(--mono); font-size: .92em; overflow-wrap: anywhere; }
details { margin-top: 8px; }
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "results", nargs="+", help="results files written by run.py --without --json; manifests beside them"
    )
    ap.add_argument("-o", "--out", required=True, help="the HTML page to write")
    args = ap.parse_args()
    with open(args.out, "w") as f:
        f.write(render(Evaluation(args.results)))
    print(f"report -> {args.out}")


if __name__ == "__main__":
    main()
