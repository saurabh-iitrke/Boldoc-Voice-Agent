// A real browser call against a live server: sign in with an allowlisted test number, open the call page with a
// fake microphone playing a scripted caller, and watch the live view until the document arrives.
//   node scripts/browser_call.mjs https://boldoc.in /path/caller.wav /path/out [seconds]
// BOLDOC_COOKIE=<session> skips sign-in. Otherwise the OTP code of a test number is read on the server (no SMS):
//   BOLDOC_OTP_CMD='ssh -i KEY ubuntu@HOST "cd app/infra && docker compose exec -T control python -m
//   apps.control.accounts.cli otp +919999900001"'
import { execSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { chromium } from "playwright";

const [base, wav, out, secondsArg] = process.argv.slice(2);
const seconds = Number(secondsArg ?? 120);
const phone = process.env.BOLDOC_TEST_PHONE ?? "+919999900001";
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({
  args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
    `--use-file-for-fake-audio-capture=${wav}`, "--autoplay-policy=no-user-gesture-required"],
});
const ctx = await browser.newContext({ permissions: ["microphone"], viewport: { width: 1280, height: 860 } });
await ctx.addInitScript(() => localStorage.setItem("boldoc.debug", "plivo"));   // the SDK's full log
const web = { "X-BolDoc-Client": "web" };

if (process.env.BOLDOC_COOKIE) {
  // signed in already (e.g. on the server, where the test runs in Playwright's Linux image)
  await ctx.addCookies([{ name: "boldoc_session", value: process.env.BOLDOC_COOKIE, domain: new URL(base).hostname,
    path: "/", secure: true, httpOnly: true, sameSite: "Lax" }]);
} else {
  // sign in: the code of a test number waits on the server
  const otp = await ctx.request.post(`${base}/api/auth/otp`, { data: { phone }, headers: web });
  if (!otp.ok()) throw new Error(`otp: ${otp.status()} ${await otp.text()}`);
  const code = execSync(process.env.BOLDOC_OTP_CMD, { encoding: "utf8" }).trim().split(/\s+/).pop();
  const verify = await ctx.request.post(`${base}/api/auth/verify`, { data: { phone, code }, headers: web });
  if (!verify.ok()) throw new Error(`verify: ${verify.status()} ${await verify.text()}`);
}

const page = await ctx.newPage();
const log = { console: [], states: [], captions: [], doc: null, errors: [] };
const consoleLines = [];
page.on("console", (m) => {
  consoleLines.push(`${m.type()}: ${m.text().slice(0, 400)}`);
  if (m.type() === "error") log.console.push(m.text().slice(0, 200));
});
page.on("pageerror", (e) => log.errors.push(String(e).slice(0, 200)));
page.on("websocket", (ws) => {
  if (!ws.url().includes("/api/live")) return;
  ws.on("framereceived", (f) => {
    try {
      const ev = JSON.parse(String(f.payload));
      if (ev.kind === "caption") log.captions.push(`${ev.speaker}: ${ev.text}`);
      if (ev.kind === "agent") log.states.push(ev.state);
      if (ev.kind === "doc") log.doc = { title: ev.doc.title, version: ev.doc.version };
    } catch { /* not JSON */ }
  });
});

const t0 = Date.now();
await page.goto(`${base}/call?via=browser`, { waitUntil: "domcontentloaded" });
const status = page.locator('[role="status"]').first();
let shot = 0;
const snap = async (name) => page.screenshot({ path: `${out}/${String(++shot).padStart(2, "0")}-${name}.png` });
await page.waitForTimeout(4000);
await snap("connecting");
const deadline = t0 + seconds * 1000;
while (Date.now() < deadline && !log.doc) {
  await page.waitForTimeout(2000);
  if (log.captions.length === 1 && shot === 1) await snap("first-caption");
}
await snap(log.doc ? "document" : "no-document");
const statusText = await status.textContent().catch(() => "");
await page.getByRole("button", { name: "End call" }).click().catch(() => undefined);
await page.waitForTimeout(3000);
await snap("ended");
const summary = { seconds: Math.round((Date.now() - t0) / 1000), status: statusText, ...log,
  states: [...new Set(log.states)] };
writeFileSync(`${out}/summary.json`, JSON.stringify(summary, null, 1));
writeFileSync(`${out}/console.log`, consoleLines.join("\n"));
console.log(JSON.stringify({ ...summary, captions: summary.captions.length, console: summary.console.length }));
await browser.close();
