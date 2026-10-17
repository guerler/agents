// Drive Olit's real page through one scenario in a visible browser, for watch mode.
//
// Reads {url, root, login, inputs, historyId, skipJobs, timeoutMs, galaxyWaitMs, settleMs, chart,
// video, slowMo, holdMs} as JSON on argv[2] and prints one JSON line per event; the last is
// {"done": ...} or {"error": ...}. With `login`, the page is Galaxy's own: it signs in through
// Galaxy's login form, makes the run's history current, closes the tool panel, and finds Olit in
// the frame Galaxy mounts it in.
import { createRequire } from "node:module";
import { statSync } from "node:fs";
import path from "node:path";

const args = JSON.parse(process.argv[2]);
const require = createRequire(path.join(process.env.OLIT_ROOT, "package.json"));
const { chromium } = require("playwright");

const emit = (line) => process.stdout.write(JSON.stringify(line) + "\n");
/** A Galaxy page counts as loaded once its HTML is; what the run needs from it is awaited after. */
const LOAD = { waitUntil: "domcontentloaded", timeout: 120_000 };
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
/** Where Olit runs: the page itself, or the frame Galaxy mounts the plugin in. */
let app = page;

const busy = () =>
  app.evaluate(() => document.querySelector("#send-btn")?.classList.contains("hidden") ?? true);
const text = () => app.evaluate(() => document.body.innerText);

async function until(predicate, ms, what) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await page.waitForTimeout(500);
  }
  throw new Error(`timed out waiting for ${what}`);
}

/** The jobs the agent started in the history, read with the page's own access to Galaxy. */
async function agentJobs() {
  const jobs = await page.evaluate(
    async ({ root, historyId }) => {
      const response = await fetch(`${root}api/jobs?history_id=${historyId}&view=collection&limit=500`);
      return response.ok ? response.json() : [];
    },
    { root: args.root, historyId: args.historyId },
  );
  return jobs.filter((j) => !args.skipJobs.includes(j.id));
}

/** Each job's output datasets, read once: an upload is watched as the dataset it creates. */
const outputsOf = new Map();

/**
 * Whether the page has accounted for a finished job: it names the job, or announces one of the
 * datasets the job made settled, which is how Olit reports an upload.
 */
async function accountedFor(job, shown) {
  if (shown.includes(job.id)) return true;
  if (!outputsOf.has(job.id)) {
    const detail = await page.evaluate(
      async ({ root, id }) => {
        const response = await fetch(`${root}api/jobs/${id}`);
        return response.ok ? response.json() : null;
      },
      { root: args.root, id: job.id },
    );
    if (!detail) return false;
    outputsOf.set(job.id, Object.values(detail.outputs || {}).map((o) => o.id));
  }
  return outputsOf
    .get(job.id)
    .some((id) => new RegExp(`Galaxy dataset ${id} (finished|was|is )`).test(shown));
}

/**
 * Quiet: the page idle, every job the agent started terminal and announced as settled, which is
 * what starts a follow-up run, and still idle `settleMs` later. A job the page never announces
 * holds this for at most the watcher's longest poll.
 */
async function quiet(clock) {
  let idleSince = 0;
  let terminalSince = 0;
  let wasBusy = true;
  let waitingOnGalaxy = false;
  for (let last = Date.now(); ; ) {
    const elapsed = Date.now() - last;
    last += elapsed;
    if (waitingOnGalaxy) clock.galaxy -= elapsed;
    else clock.agent -= elapsed;
    if (clock.agent <= 0) throw new Error("the run did not go quiet before its budget");
    if (clock.galaxy <= 0) throw new Error("Galaxy did not finish the run's jobs within its wait");
    waitingOnGalaxy = false;
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
      // Idle on submitted work is the environment's clock, as in a headless run.
      waitingOnGalaxy = active.length > 0;
      const unannounced = [];
      for (const j of jobs) {
        const pending = TERMINAL.has(j.state) && !settled.has(j.id);
        if (pending && !(await accountedFor(j, shown))) unannounced.push(j);
      }
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
}

/** A check's outcome: whether it held, and what was seen either way. */
const outcome = (ok, detail) => ({ ok, detail });

/**
 * What a person checks by eye on the chart the pane shows: it drew marks rather than an error,
 * hovering a mark brings up its tooltip, and the export menu saves a PNG.
 */
async function chartChecks(wanted) {
  const result = {};
  const pane = app.locator("#artifact-content");
  const marks = pane.locator('svg [role="graphics-symbol"]');
  if (await app.evaluate(() => document.body.classList.contains("artifact-collapsed"))) {
    await app.click("#artifact-btn");
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
    const tooltip = app.locator("#vg-tooltip-element.visible");
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

/** Sign in as a person does, then make the run's history the one Galaxy's panel shows. */
async function signIn({ user, password }) {
  await page.goto(`${args.root}login/start`, LOAD);
  await page.fill('#login input[name="login"]', user);
  await page.fill('#login input[name="password"]', password);
  await page.click('#login button[type="submit"]');
  await page.waitForURL((url) => !url.pathname.includes("/login"), { timeout: 60_000 });
  const current = await page.request.get(`${args.root}history/set_as_current?id=${args.historyId}`);
  if (!current.ok()) throw new Error(`could not make the run's history current: ${current.status()}`);
}

/**
 * Close Galaxy's tool panel, so the video gives Olit and the history the width. A fresh browser
 * opens with it showing; the Tools activity toggles it, and Galaxy keeps that in local storage.
 */
async function closeToolPanel() {
  const panel = page.locator("#toolbox-panel");
  try {
    await panel.waitFor({ state: "visible", timeout: 30_000 });
    await page.click("#activity-tools");
    await panel.waitFor({ state: "hidden", timeout: 10_000 });
  } catch {
    emit({ event: "console", text: "the tool panel stayed as it was" });
  }
}

/** The frame holding Olit's input, once Galaxy has mounted the plugin. */
async function olitFrame() {
  for (const frame of page.frames()) {
    if (await frame.locator("#input").count().catch(() => 0)) return frame;
  }
  return null;
}

try {
  if (args.login) await signIn(args.login);
  await page.goto(args.url, LOAD);
  if (args.login) await closeToolPanel();
  await until(async () => (app = await olitFrame()), 120_000, "Olit's page");
  // The status line says ready and the input takes text: neither alone, since the textarea is
  // enabled before Olit boots and a restored conversation can say "ready" in its chat.
  await until(
    async () =>
      / ready\b/i.test(await text()) &&
      (await app.evaluate(() => document.querySelector("#input")?.disabled === false)),
    240_000,
    "the agent to be ready",
  );
  emit({ event: "ready" });
  // The agent's budget starts once it can be asked; waits on Galaxy jobs have their own.
  const clock = { agent: args.timeoutMs, galaxy: args.galaxyWaitMs };

  page.on(
    "console",
    (m) => m.type() === "error" && emit({ event: "console", text: m.text().slice(0, 300) }),
  );
  for (const input of args.inputs) {
    await app.fill("#input", input);
    await app.click("#send-btn");
    emit({ event: "turn_start", input: input.slice(0, 80) });
    await quiet(clock);
    emit({ event: "turn_end" });
  }
  if (args.chart?.length) emit({ event: "page", chart: await chartChecks(args.chart) });

  const saved = page.waitForResponse(
    (r) =>
      r.url().includes("/api/visualizations") && ["POST", "PUT"].includes(r.request().method()),
    { timeout: 60_000 },
  );
  await app.click("#save-btn");
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
