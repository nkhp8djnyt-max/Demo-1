// End-to-end run of the whole flow in Chromium against the mock server
// (python -m devtools.mock_telegram). Saves screenshots to docs/.
//   node devtools/e2e.mjs [baseUrl]
// telegram.org is fetched live unless TG_SDK points to a local copy of telegram-web-app.js.
import { chromium } from "playwright";
import fs from "fs";

const BASE = process.argv[2] || "http://127.0.0.1:8000";
const OUT = new URL("../docs/", import.meta.url).pathname;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const errors = [];
const step = (s) => console.log("✓", s);
function check(cond, msg) { if (!cond) throw new Error("FAILED: " + msg); }

const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
async function newPage(opts) {
  const ctx = await browser.newContext(opts);
  if (process.env.TG_SDK) {
    const body = fs.readFileSync(process.env.TG_SDK, "utf8");
    await ctx.route("https://telegram.org/js/telegram-web-app.js", (r) => r.fulfill({ body, contentType: "application/javascript" }));
  }
  const page = await ctx.newPage();
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));
  return page;
}
const shot = (el, name) => el.screenshot({ path: OUT + name });

// ---------- 1. Inside "Telegram" (simulator) ----------
const page = await newPage({ viewport: { width: 1360, height: 900 }, deviceScaleFactor: 1 });
await page.goto(BASE + "/dev/");
await page.click('[data-send="/start"]');
await page.waitForSelector("#customer [data-open]");
check((await page.textContent("#customer")).includes("Welcome to BrewBook"), "/start reply");
step("/start shows Open menu button");
await page.click("#customer [data-open]");
const app = page.frameLocator("#app");
await app.locator(".item").first().waitFor();
check(await app.locator(".item").count() === 12, "12 menu items");
const phone = page.locator(".phone");
await sleep(400);
await shot(phone, "tg-1-menu-light.png");
step("Mini App opened inside the simulator with real telegram-web-app.js, 12 items");

await app.locator('[data-item="burnt-sugar-latte"]').click();
await app.locator('[data-size="L"]').click();
await app.locator('[data-milk="oat"]').click();
await app.locator('[data-q="1"]').click();
const main = page.locator("#tg-main");
check((await main.textContent()).includes("$12.20"), "sheet main button shows 2× L oat = $12.20, got " + await main.textContent());
await shot(phone, "tg-2-options.png");
await main.click();
await app.locator('[data-item="cardamom-bun"]').click();
await sleep(200);
check((await main.textContent()).includes("3 items · $15.70"), "menu main button: " + await main.textContent());
step("options sheet, add to cart, MainButton totals");
await main.click();
await app.locator("#comment").fill("Name on the cup: Mia");
await sleep(300);
check((await main.textContent()).includes("785"), "pay with stars button: " + await main.textContent());
await shot(phone, "tg-3-cart.png");
await main.click();
await page.waitForSelector("#modal:not([hidden])");
await page.click("#pay-yes");
await app.locator(".ticket-no").waitFor();
await app.locator(".step.new.now").waitFor({ timeout: 8000 });
step("Stars: openInvoice -> pre_checkout_query -> successful_payment -> status New");
await page.waitForFunction(() => document.querySelector("#baristas").textContent.includes("⭐ 785 Stars"));
check((await page.textContent("#customer")).includes("is paid"), "customer got paid message");
await shot(phone, "tg-4-paid.png");
await shot(page, "tg-flow-overview.png");
step("barista chat got the order with buttons; customer got confirmation");

await page.click('#baristas [data-tap$=":preparing"]');
await app.locator(".step.preparing.now").waitFor({ timeout: 8000 });
check((await page.textContent("#customer")).includes("being prepared"), "customer preparing message");
await page.click('#baristas [data-tap$=":ready"]');
await app.locator(".step.ready.now").waitFor({ timeout: 8000 });
check((await page.textContent("#customer")).includes("is ready"), "customer ready message");
await sleep(300);
await shot(phone, "tg-5-ready.png");
await shot(page, "tg-flow-ready.png");
step("barista buttons: New -> Preparing -> Ready, ticket and customer chat update");

// Second order, crypto
await main.click(); // Back to menu
await app.locator('[data-item="ember-espresso"]').click();
await main.click();
await app.locator('[data-pay="crypto"]').click();
check((await main.textContent()).includes("$2.80 in crypto"), "crypto button: " + await main.textContent());
await main.click();
await page.waitForSelector("#modal:not([hidden])");
await shot(page, "tg-crypto-invoice.png");
await page.click("#cp-yes");
await app.locator(".step.new.now").waitFor({ timeout: 10000 });
await page.waitForFunction(() => document.querySelector("#baristas").textContent.includes("Crypto Pay (testnet)"));
step("Crypto Pay: invoice -> signed webhook -> status New");

// Dark theme toggle live
await page.click("#theme");
await sleep(400);
check(await app.locator("html").getAttribute("data-theme") === "dark", "theme_changed -> dark");
await main.click();
await sleep(300);
await shot(phone, "tg-6-menu-dark.png");
step("Telegram theme_changed switches the app to dark");

await page.click('[data-send="/orders"]');
await page.waitForFunction(() => document.querySelector("#customer").textContent.includes("Recent orders"));
step("/orders lists recent orders for the owner");

// ---------- 2. Plain browser (BROWSER_DEMO) ----------
for (const scheme of ["light", "dark"]) {
  const p = await newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, colorScheme: scheme });
  await p.goto(BASE + "/app/?demo_visitor=portfolio-" + scheme);
  await p.locator(".item").first().waitFor();
  check(!(await p.locator("#browser-note").isHidden()), "browser note visible");
  await sleep(300);
  await p.screenshot({ path: `${OUT}browser-menu-${scheme}.png` });
  if (scheme === "light") {
    await p.click('[data-item="moss-matcha-latte"]');
    await p.click("#main-fallback-btn");
    await p.click("#main-fallback-btn");
    check(await p.locator('[data-pay="demo"]').count() === 1 && await p.locator('[data-pay="stars"]').count() === 0, "browser shows demo payment only");
    await p.screenshot({ path: `${OUT}browser-cart.png` });
    await p.click("#main-fallback-btn");
    await p.locator("#demo-pay-btn").waitFor();
    await p.screenshot({ path: `${OUT}browser-demo-pay.png` });
    await p.click("#demo-pay-btn");
    await p.locator(".step.new.now").waitFor();
    await p.screenshot({ path: `${OUT}browser-ticket.png` });
    step("browser demo: guest order paid with the labelled demo payment");
  }
}

// ---------- 3. Without initData the API refuses ----------
const raw = await (await fetch(BASE + "/api/bootstrap")).json();
check(raw.detail && raw.detail.includes("Telegram"), "API without initData is 401");
step("API without initData returns 401");

await browser.close();
const real = errors.filter((e) => !/favicon/.test(e));
if (real.length) { console.log("Console errors:\n" + real.join("\n")); process.exit(1); }
console.log("All browser flows passed, no console errors.");
