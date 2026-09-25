// Runs a batch of galaxy-ops operations and prints one JSON envelope each.
// One node start for the whole run; the batch arrives on stdin as {baseUrl, apiKey, calls}.
const input = JSON.parse(await new Promise((r) => {
  let buf = "";
  process.stdin.on("data", (c) => (buf += c));
  process.stdin.on("end", () => r(buf));
}));

// Imported by path: this script lives in the agents repo, so a bare specifier would be
// resolved against that tree rather than the galaxy-ops checkout under test.
const { allOperations, createGalaxyContext, runWithEnvelope } = await import(input.opsEntry);

const ctx = createGalaxyContext({ baseUrl: input.baseUrl, apiKey: input.apiKey });
const byName = new Map(allOperations.map((o) => [o.name, o]));
const out = [];
for (const call of input.calls) {
  const op = byName.get(call.op);
  if (!op) {
    out.push({ op: call.op, absent: true });
    continue;
  }
  try {
    out.push({ op: call.op, envelope: await runWithEnvelope(op, call.args, ctx) });
  } catch (err) {
    out.push({ op: call.op, threw: String(err && err.message ? err.message : err) });
  }
}
process.stdout.write(JSON.stringify(out));
