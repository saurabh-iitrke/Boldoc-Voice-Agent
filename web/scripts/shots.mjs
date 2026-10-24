// Visual review: walks every screen at desktop + phone sizes, light + dark, and saves PNGs. "dark" is the system's
// dark mode plus the console's switch set to dark (the console is light until the user picks dark; the public site is
// always light).
// Usage: node scripts/shots.mjs [baseUrl] [outDir]   (dev server must run with VITE_API_MODE=mock)
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const base = process.argv[2] ?? "http://localhost:5173";
const out = process.argv[3] ?? "shots";
mkdirSync(out, { recursive: true });

const ME = JSON.stringify({ id: 1, phone_e164: "+919988776655", phone_masked: "+91 99••• ••655", status: "active",
  name: null, email: null, credits: { balance: 2150, docs_estimate: 97 } });
const viewports = { desktop: { width: 1440, height: 900 }, phone: { width: 390, height: 844 } };
const only = (process.env.ONLY ?? "").split(",").filter(Boolean);

const browser = await chromium.launch();
const errors = [];

async function session(name, vp, scheme, signedIn, fn, { picked = scheme === "dark" } = {}) {
  if (only.length && !only.some((o) => name.includes(o))) return;
  const ctx = await browser.newContext({ viewport: viewports[vp], colorScheme: scheme, deviceScaleFactor: 1,
    locale: "en-IN", timezoneId: "Asia/Kolkata", permissions: [] });
  if (signedIn) await ctx.addInitScript((me) => localStorage.setItem("boldoc.mock.me", me), ME);
  if (picked) await ctx.addInitScript(() => localStorage.setItem("boldoc.theme", "dark"));
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(`${name}: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") errors.push(`${name}: console: ${m.text()}`); });
  await fn(page, (label) => page.screenshot({ path: `${out}/${name}-${vp}-${scheme}${label ? "-" + label : ""}.png` }));
  await ctx.close();
}

for (const vp of ["desktop", "phone"]) {
  for (const scheme of ["light", "dark"]) {
    await session("landing", vp, scheme, false, async (page, shot) => {
      await page.goto(`${base}/`);
      await page.getByRole("heading", { name: /Document taiyaar/ }).first().waitFor();
      await page.evaluate(() => document.fonts.ready);
      await shot("");
      await page.getByRole("button", { name: "Play the call" }).click();
      await page.waitForTimeout(15000);  // the document appears with "ready"
      await shot("demo-v1");
      if (scheme === "light") await page.screenshot({ path: `${out}/landing-${vp}-full.png`, fullPage: true });
    });
    await session("signin", vp, scheme, false, async (page, shot) => {
      await page.goto(`${base}/sign-in`);
      await page.waitForLoadState("networkidle");
      await shot("phone");
      await page.getByLabel("Mobile number").fill("9988776655");
      await page.getByRole("button", { name: "Send code" }).click();
      await page.getByText("Enter the code").waitFor();
      await shot("code");
      await page.getByLabel("6-digit code").fill("111111");
      await page.getByRole("alert").waitFor();
      await shot("code-error");
    });
    await session("home", vp, scheme, true, async (page, shot) => {
      await page.goto(`${base}/app`);
      await page.getByText("Your documents").waitFor();
      await page.waitForTimeout(400);
      await shot("");
      if (vp === "desktop") await page.screenshot({ path: `${out}/home-${vp}-${scheme}-full.png`, fullPage: true });
    });
    await session("account", vp, scheme, true, async (page, shot) => {
      await page.goto(`${base}/app/credits`);
      await page.getByText("History").waitFor();
      await shot("credits");
      await page.goto(`${base}/app/settings`);
      await page.getByRole("button", { name: "Save details" }).waitFor();
      await shot("details");
    });
    await session("call", vp, scheme, true, async (page, shot) => {
      // Demo runs 4x faster; snapshots at (real-time) 4 s, 20 s, 34 s, 52 s of the scripted call.
      await page.goto(`${base}/call?via=demo&speed=4`);
      let elapsed = 0;
      for (const [label, at] of [["t04", 4000], ["t20", 20000], ["t34", 34000], ["t52", 52000]]) {
        await page.waitForTimeout((at - elapsed) / 4);
        elapsed = at;
        await shot(label);
      }
      await page.getByText("That was a recorded call.").waitFor({ timeout: 60000 });
      await shot("end");
    });
    // "Talk in browser" is for signed-up accounts: signed out, the hero's button and /call lead to sign-in, which says why
    await session("callsignin", vp, scheme, false, async (page, shot) => {
      await page.goto(`${base}/`);
      await page.getByRole("link", { name: "Talk in browser" }).click();
      await page.getByText("Sign in to talk to BolDoc in your browser.").waitFor();
      const url = new URL(page.url());
      if (url.pathname !== "/sign-in" || url.searchParams.get("next") !== "/app/call") {
        errors.push(`callsignin: landed on ${page.url()}`);
      }
      await page.evaluate(() => document.fonts.ready);
      await shot("");
      await page.goto(`${base}/call`);
      await page.getByText("Sign in to talk to BolDoc in your browser.").waitFor();
    });
    // Signed in: the sheet before the call, the call (mock: the recorded call stands in for Plivo), the end, a refusal
    await session("appcall", vp, scheme, true, async (page, shot) => {
      await page.goto(`${base}/app/call?speed=6`);
      await page.getByRole("button", { name: "Start the call" }).waitFor();
      await page.evaluate(() => document.fonts.ready);
      await shot("ready");
      await page.getByRole("button", { name: "Start the call" }).click();
      await page.waitForTimeout(4000);
      await shot("live");
      await page.getByRole("link", { name: "Open the document" }).waitFor({ timeout: 30000 });
      await shot("ended");
      await page.evaluate(() => localStorage.setItem("boldoc.mock.call", "no_credits"));
      await page.goto(`${base}/app/call`);
      await page.getByRole("button", { name: "Start the call" }).click();
      await page.getByRole("heading", { name: "Your balance has run out" }).waitFor();
      await shot("no-credits");
    });
    await session("doc", vp, scheme, true, async (page, shot) => {
      await page.goto(`${base}/documents/doc_leave`);
      await page.getByText("Download").first().waitFor();
      await shot("leave");
      await page.goto(`${base}/documents/doc_trip`);
      await page.getByText("Download").first().waitFor();
      await shot("trip");
      await page.goto(`${base}/app/documents/doc_dpr`);   // a long document being written, its writer waiting
      await page.getByText("What it will cover").waitFor();
      await shot("writing");
      await page.goto(`${base}/d/doc_notice`);
      await page.getByText("Download").first().waitFor();
      await shot("hindi-public");
      await page.goto(`${base}/d/expired-token`);
      await page.getByText("This link isn't available").waitFor();
      await shot("expired");
    });
  }
  await session("theme", vp, "dark", true, async (page, shot) => {
    const look = () => page.evaluate(() => ({ theme: document.documentElement.dataset.theme ?? "light",
      area: document.documentElement.dataset.area ?? "", bg: getComputedStyle(document.body.firstElementChild
        .firstElementChild ?? document.body).backgroundColor }));
    const expect = (what, got, want) => { if (got !== want) errors.push(`theme: ${what}: ${got} (want ${want})`); };
    await page.goto(`${base}/app`);                    // the system is dark, nothing picked: the console is light
    await page.getByText("Your documents").waitFor();
    expect("default", (await look()).bg, "rgb(243, 243, 241)");
    await shot("default");
    await page.getByRole("button", { name: "Switch to dark mode" }).click();
    expect("switched", (await look()).bg, "rgb(20, 20, 19)");
    await shot("switched");
    await page.reload();                               // kept on this device, dark from the first paint
    await page.getByText("Your documents").waitFor();
    expect("after reload", (await look()).bg, "rgb(20, 20, 19)");
    await page.getByRole("link", { name: "Balance" }).click();
    await page.getByText("History").waitFor();
    expect("credits", (await look()).bg, "rgb(20, 20, 19)");
    await page.goto(`${base}/documents/doc_leave`);    // documents open on paper, light like the download
    await page.getByText("Download").first().waitFor();
    expect("document area", (await look()).area, "");
    await shot("document");
    await page.goto(`${base}/`);                       // the public site stays light
    await page.getByRole("heading", { name: /Document taiyaar/ }).first().waitFor();
    expect("landing area", (await look()).area, "");
    await page.goto(`${base}/app`);
    await page.getByRole("button", { name: "Switch to light mode" }).click();
    expect("back to light", (await look()).bg, "rgb(243, 243, 241)");
  }, { picked: false });
}

await browser.close();
console.log(errors.length ? `ERRORS:\n${errors.join("\n")}` : "no page errors");
if (errors.length) process.exit(1);
