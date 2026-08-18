import { chromium } from "playwright";
const EMAIL = "fahd1787081490c@example.com", shots = "/tmp/claude-0/-home-user-signup/40224454-31bb-5894-9062-2c88d3d267e9/scratchpad";
const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 950 } })).newPage();
const errs = [];
page.on("pageerror", (e) => errs.push(String(e).slice(0, 200)));
page.on("response", (r) => { if (r.status() >= 400 && r.url().includes("/api/")) errs.push(`${r.status()} ${r.url()}`); });

await page.goto("http://127.0.0.1:3000/en/login", { waitUntil: "networkidle" });
await page.getByLabel(/email/i).fill(EMAIL);
await page.getByLabel(/password/i).fill("Correct-Horse-Battery-9");
await page.getByRole("button", { name: /sign in|log in/i }).click();
await page.waitForURL(/knowledge-base/, { timeout: 20000 });
await page.waitForTimeout(2500);

console.log("TABLE ROWS:", await page.locator("tbody tr").count());
console.log("ROW TEXTS:");
for (const t of await page.locator("tbody tr").allInnerTexts()) console.log("  ", t.replace(/\n/g, " | "));
await page.screenshot({ path: `${shots}/kb-live.png`, fullPage: true });

await page.goto("http://127.0.0.1:3000/ar/knowledge-base", { waitUntil: "networkidle" });
await page.waitForTimeout(1800);
await page.screenshot({ path: `${shots}/kb-live-ar.png`, fullPage: true });
console.log("ERRORS:", errs.length ? errs : "none");
await browser.close();
