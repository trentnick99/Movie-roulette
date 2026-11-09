import { chromium } from "@playwright/test";
const [,, url, out, w, h, clickName] = process.argv;
const b = await chromium.launch({ executablePath: "/tmp/chromium", args: ["--no-sandbox"] });
const p = await b.newPage({ viewport: { width: Number(w || 1440), height: Number(h || 900) } });
await p.goto("http://127.0.0.1:8765" + url);
await p.waitForTimeout(1200);
if (clickName) { await p.getByRole("button", { name: new RegExp(clickName) }).first().click(); await p.mouse.move(0,0); await p.waitForTimeout(500); }
await p.screenshot({ path: out });
await b.close();
