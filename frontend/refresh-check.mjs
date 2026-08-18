import { chromium } from "playwright";
const EMAIL = "fahd1787081490c@example.com", shots = "/tmp/claude-0/-home-user-signup/40224454-31bb-5894-9062-2c88d3d267e9/scratchpad";
const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
const calls = [];
page.on("response", (r) => { if (r.url().includes("/api/")) calls.push(`${r.status()} ${r.url().split("/api/v1")[1]}`); });

await page.goto("http://127.0.0.1:3000/en/login", { waitUntil: "networkidle" });
await page.getByLabel(/email/i).fill(EMAIL);
await page.getByLabel(/password/i).fill("Correct-Horse-Battery-9");
await page.getByRole("button", { name: /sign in|log in/i }).click();
await page.waitForURL(/knowledge-base/, { timeout: 20000 });
await page.waitForTimeout(2000);
console.log("BEFORE EXPIRY:", await page.locator("tbody tr").count(), "rows");

// The access token lives 8 seconds in this run. Wait past it, then act.
calls.length = 0;
await page.waitForTimeout(11000);
await page.goto("http://127.0.0.1:3000/en/tenders", { waitUntil: "networkidle" });
await page.waitForTimeout(2500);

console.log("AFTER EXPIRY — tender rows:", await page.locator("ul li a").count());
console.log("still signed in:", !page.url().includes("/login"));
console.log("CALLS:"); for (const c of calls) console.log("  ", c);
await page.screenshot({ path: `${shots}/refresh-after.png` });
await browser.close();
