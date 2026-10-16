// Drive Olit's real page through one scenario in a visible browser, for watch mode.
//
// Reads {url, inputs, historyId, skipJobs, timeoutMs, settleMs, chart, video, slowMo} as JSON
// on argv[2] and prints one JSON line per event; the last is {"done": ...} or {"error": ...}.
import { createRequire } from "node:module";
import { statSync } from "node:fs";
import path from "node:path";

const args = JSON.parse(process.argv[2]);
const require = createRequire(path.join(process.env.OLIT_ROOT, "package.json"));
const { chromium } = require("playwright");

const emit = (line) => process.stdout.write(JSON.stringify(line) + "\n");
/** Jobs already reported settled, so a later turn does not report them again. */
const settled = new Set();
const TERMINAL = new Set([
  "ok",
  "error",
  "deleted",
  "deleted_new",
  "failed",
  "paused",
  "skipped",
  "stopped",
]);

async function launch() {
  const options = { headless: false, slowMo: args.slowMo || 0 };
  try {
    return await chromium.launch({ ...options, channel: "chrome" });
  } catch {
    return await chromium.launch(options);
  }
}

const browser = await launch();
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  ...(args.video ? { recordVideo: { dir: args.video, size: { width: 1440, height: 900 } } } : {}),
});
const page = await context.newPage();

const busy = () =>
  page.evaluate(() => document.querySelector("#send-btn")?.classList.contains("hidden") ?? true);
const text = () => page.evaluate(() => document.body.innerText);

async function until(predicate, ms, what) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await page.waitForTimeout(500);
  }
  throw new Error(`timed out waiting for ${what}`);
}

/** The jobs the agent started in the history, read through the dev proxy the page uses. */
async function agentJobs() {
  const jobs = await page.evaluate(async (historyId) => {
    const response = await fetch(`/api/jobs?history_id=${historyId}&view=collection&limit=500`);
    return response.ok ? response.json() : [];
  }, args.historyId);
  return jobs.filter((j) => !args.skipJobs.includes(j.id));
}

/**
 * Quiet: the page idle, every job the agent started terminal and announced as settled, which is
 * what starts a follow-up run, and still idle `settleMs` later. A job the page never announces
 * holds this for at most the watcher's longest poll.
 */
async function quiet(deadline) {
  let idleSince = 0;
  let terminalSince = 0;
  let wasBusy = true;
  while (Date.now() < deadline) {
    const now = await busy();
    if (now) {
      // Busy again after going idle: the page started a follow-up run on settled work.
      if (!wasBusy) emit({ event: "auto_follow_up" });
      idleSince = 0;
    } else {
      idleSince ||= Date.now();
      const jobs = await agentJobs();
      const shown = await text();
      const active = jobs.filter((j) => !TERMINAL.has(j.state));
      const unannounced = jobs.filter((j) => TERMINAL.has(j.state) && !shown.includes(j.id));
      terminalSince = active.length || !unannounced.length ? 0 : terminalSince || Date.now();
      const announced =
        !active.length && (!unannounced.length || Date.now() - terminalSince > 150_000);
      if (announced && Date.now() - idleSince >= args.settleMs) {
        if (jobs.some((j) => !settled.has(j.id))) emit({ event: "work_settled" });
        jobs.forEach((j) => settled.add(j.id));
        return;
      }
    }
    wasBusy = now;
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error("the run did not go quiet before its budget");
}

/** A check's outcome: whether it held, and what was seen either way. */
const outcome = (ok, detail) => ({ ok, detail });

/**
 * What a person checks by eye on the chart the pane shows: it drew marks rather than an error,
 * hovering a mark brings up its tooltip, and the export menu saves a PNG.
 */
async function chartChecks(wanted) {
  const result = {};
  const pane = page.locator("#artifact-content");
  const marks = pane.locator('svg [role="graphics-symbol"]');
  if (await page.evaluate(() => document.body.classList.contains("artifact-collapsed"))) {
    await page.click("#artifact-btn");
  }
  try {
    await until(
      async () => (await marks.count()) > 0 || /Could not render chart/.test(await pane.innerText()),
      30_000,
      "the chart",
    );
  } catch {}
  const drawn = await marks.count();
  const failed = (await pane.innerText()).match(/Could not render chart[^\n]*/)?.[0];
  result.renders = outcome(drawn > 0 && !failed, failed || `${drawn} marks drawn`);
  if (!drawn) return result;

  if (wanted.includes("hover")) {
    const tooltip = page.locator("#vg-tooltip-element.visible");
    let shown = "";
    for (let i = 0; i < Math.min(drawn, 20) && !shown; i++) {
      const mark = marks.nth(i);
      if (!(await mark.isVisible())) continue;
      await mark.hover({ force: true });
      shown = await tooltip
        .waitFor({ timeout: 1500 })
        .then(() => tooltip.innerText())
        .catch(() => "");
    }
    result.hover = outcome(!!shown.trim(), shown.trim().replace(/\s+/g, " ").slice(0, 200) || "no tooltip on any mark");
    await page.mouse.move(0, 0);
  }

  if (wanted.includes("export")) {
    try {
      await pane.locator(".vega-embed details summary").click();
      const save = pane.locator(".vega-actions a", { hasText: /png/i });
      const download = page.waitForEvent("download", { timeout: 15_000 });
      await save.click();
      const file = await download;
      const bytes = statSync(await file.path()).size;
      const name = file.suggestedFilename();
      result.export = outcome(/\.png$/i.test(name) && bytes > 0, `${name}, ${bytes} bytes`);
    } catch (e) {
      result.export = outcome(false, String(e?.message ?? e).split("\n")[0]);
    }
    await page.keyboard.press("Escape");
  }
  return result;
}

try {
  const deadline = Date.now() + args.timeoutMs;
  await page.goto(args.url);
  await until(async () => / ready\b/i.test(await text()), 240_000, "the agent to be ready");
  emit({ event: "ready" });

  page.on(
    "console",
    (m) => m.type() === "error" && emit({ event: "console", text: m.text().slice(0, 300) }),
  );
  for (const input of args.inputs) {
    await page.fill("#input", input);
    await page.click("#send-btn");
    emit({ event: "turn_start", input: input.slice(0, 80) });
    await quiet(deadline);
    emit({ event: "turn_end" });
  }
  if (args.chart?.length) emit({ event: "page", chart: await chartChecks(args.chart) });

  const saved = page.waitForResponse(
    (r) =>
      r.url().includes("/api/visualizations") && ["POST", "PUT"].includes(r.request().method()),
    { timeout: 60_000 },
  );
  await page.click("#save-btn");
  const response = await saved;
  const body = await response.json().catch(() => ({}));
  const id = body.id || response.url().split("/api/visualizations/")[1]?.split("?")[0];
  emit({ done: true, visualizationId: id });
} catch (e) {
  emit({ error: String(e?.message ?? e) });
} finally {
  if (args.holdMs) await new Promise((r) => setTimeout(r, args.holdMs));
  await context.close();
  if (args.video) emit({ event: "video", path: await page.video()?.path() });
  await browser.close();
}
