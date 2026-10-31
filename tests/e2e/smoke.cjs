/*
 * 手機尺寸冒煙測試（Playwright，Chromium 模擬 iPhone 13）：精準務農＋精準進貨。
 * 自帶靜態伺服器，把 repo 根目錄掛在 /Fruit/ 底下，模擬 GitHub Pages 的實際路徑。
 *
 * 檢查：
 *  - 沒有 JS 例外、沒有 console error
 *  - 讀到真實行情（頁首顯示「實際資料」），歷史檔＋當月檔都 200
 *  - 行情看板有卡片；點第一張卡 → K 線詳細頁打開
 *  - 每個分頁都切得過去、版面沒有橫向捲動（手機上最常見的破版）
 *  - 精準進貨只寫 jb_ 開頭的 localStorage、精準務農只寫 vs_ 開頭（兩支同網域不可互蓋設定）
 * 截圖存 test-results/（CI 失敗時會上傳）。
 *
 * 執行：node tests/e2e/smoke.cjs        （需要 playwright 與 Chromium；CI 會自動安裝）
 */
const http = require("http");
const fs = require("fs");
const path = require("path");
const { chromium, devices } = require("playwright");

const ROOT = path.resolve(__dirname, "..", "..");
const OUT = path.join(ROOT, "test-results");
const MIME = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".json": "application/json",
  ".png": "image/png", ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json", ".css": "text/css" };

function serve() {
  const srv = http.createServer((req, res) => {
    let p = decodeURIComponent(req.url.split("?")[0]);
    if (!p.startsWith("/Fruit/")) { res.writeHead(404); return res.end(); }
    p = p.slice("/Fruit".length);
    if (p.endsWith("/")) p += "index.html";
    const f = path.join(ROOT, path.normalize(p));
    if (!f.startsWith(ROOT) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { res.writeHead(404); return res.end(); }
    res.writeHead(200, { "Content-Type": MIME[path.extname(f)] || "application/octet-stream" });
    fs.createReadStream(f).pipe(res);
  });
  return new Promise((ok) => srv.listen(0, "127.0.0.1", () => ok(srv)));
}

const failures = [];
function check(cond, msg) { console.log(`${cond ? "  ✓" : "  ✗"} ${msg}`); if (!cond) failures.push(msg); }

async function openApp(ctx, url, name) {
  const page = await ctx.newPage();
  const errors = [], json = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("response", (r) => { if (r.url().endsWith(".json")) json.push([r.status(), new URL(r.url()).pathname]); });
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForSelector("#mode", { timeout: 15000 });
  await page.waitForTimeout(800);
  // 首次開啟會跳「更新內容」彈窗，先關掉
  if (await page.locator("#clModal.on").count()) await page.click("#closeCl");
  console.log(`\n[${name}] ${url}`);
  return { page, errors, json };
}

async function noHScroll(page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
}

async function checkBoardAndDetail(page, name) {
  const cards = await page.locator(".mcard").count();
  check(cards > 0, `${name} 行情看板有卡片（${cards} 張）`);
  check(await noHScroll(page), `${name} 行情看板沒有橫向捲動`);
  if (cards > 0) {
    // 價格區（.mc-right）才是進 K 線；左半邊是「說明」。多品種卡先展開，再點第一個品種的價格區。
    const right = page.locator(".mcard .mc-right:not(.nolink)");
    if (await right.count()) await right.first().click(); else await page.locator(".mcard").first().click();
    await page.waitForTimeout(600);
    if (!(await page.locator("#mktDetail").isVisible())) {
      const child = page.locator(".mc-child .mc-right:not(.nolink)");
      if (await child.count()) { await child.first().click(); await page.waitForTimeout(600); }
    }
    check(await page.locator("#mktDetail").isVisible(), `${name} 點卡片打開 K 線詳細頁`);
    check(await noHScroll(page), `${name} K 線詳細頁沒有橫向捲動`);
    await page.screenshot({ path: path.join(OUT, `${name}-detail.png`) });
  }
}

async function visitTabs(page, name, tabs) {
  for (const t of tabs) {
    await page.click(`.tab[data-t="${t}"]`);
    await page.waitForTimeout(500);
    check(await page.locator(`#view-${t === "market" ? "market" : t}`).isVisible(), `${name} 分頁「${t}」可切換`);
    check(await noHScroll(page), `${name} 分頁「${t}」沒有橫向捲動`);
    await page.screenshot({ path: path.join(OUT, `${name}-${t}.png`) });
  }
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const srv = await serve();
  const base = `http://127.0.0.1:${srv.address().port}/Fruit/`;
  const { defaultBrowserType, ...iphone } = devices["iPhone 13"];   // 用 Chromium 跑 iPhone 尺寸/觸控/UA
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ ...iphone, serviceWorkers: "block" });

  // ── 精準務農 ──
  const f = await openApp(ctx, base, "farmer");
  check((await f.page.textContent("#mode")).includes("實際資料"), "farmer 讀到真實行情（頁首「實際資料」）");
  check(f.json.some(([s, p]) => s === 200 && /\/Fruit\/prices\/\d+\.json$/.test(p)), "farmer 歷史行情檔 200");
  check(f.json.some(([s, p]) => s === 200 && /\/Fruit\/prices\/\d+_recent\.json$/.test(p)), "farmer 當月行情檔 200");
  check(!f.json.some(([s]) => s >= 400), "farmer 沒有 JSON 讀取失敗");
  await f.page.screenshot({ path: path.join(OUT, "farmer-market.png") });
  await checkBoardAndDetail(f.page, "farmer");
  await f.page.goto(base, { waitUntil: "networkidle" });
  await visitTabs(f.page, "farmer", ["forecast", "harvest", "market"]);
  const lsFarmer = await f.page.evaluate(() => Object.keys(localStorage));
  check(lsFarmer.length > 0 && lsFarmer.every((k) => k.startsWith("vs_")), `farmer 只寫 vs_ 設定（${lsFarmer.join(",")}）`);
  check(f.errors.length === 0, `farmer 沒有 JS 錯誤${f.errors.length ? "：" + f.errors.slice(0, 3).join(" | ") : ""}`);

  // ── 精準進貨（同一個瀏覽器環境＝同網域共用 localStorage）──
  const m = await openApp(ctx, base + "merchant/", "merchant");
  check((await m.page.textContent("#mode")).includes("實際資料"), "merchant 讀到真實行情（頁首「實際資料」）");
  check(m.json.length > 0 && m.json.every(([, p]) => p.startsWith("/Fruit/merchant/")), "merchant 只讀自己目錄的 JSON（不會讀到務農的）");
  check(m.json.some(([s, p]) => s === 200 && /_recent\.json$/.test(p)), "merchant 當月行情檔 200");
  check(!m.json.some(([s]) => s >= 400), "merchant 沒有 JSON 讀取失敗");
  await checkBoardAndDetail(m.page, "merchant");
  await m.page.goto(base + "merchant/", { waitUntil: "networkidle" });
  await m.page.click('.tab[data-t="compare"]');
  await m.page.waitForTimeout(500);
  const cmp = await m.page.evaluate(() => [...document.querySelectorAll(".cmprow")].map((el) => ({
    cheap: el.classList.contains("cheap"), old: el.classList.contains("old"), date: (el.querySelector("small") || {}).textContent || "" })));
  const newest = cmp.reduce((a, r) => (r.date > a ? r.date : a), "");
  const cheap = cmp.find((r) => r.cheap);
  check(cmp.length > 0 && !!cheap, `merchant 比價有排名且有「最便宜」（${cmp.length} 個市場）`);
  check(!cheap || (Date.parse(newest) - Date.parse(cheap.date)) / 864e5 <= 4, `merchant「最便宜」不是舊價（${cheap && cheap.date} vs 最新 ${newest}）`);
  check(cmp.filter((r) => r.old).every((r) => (Date.parse(newest) - Date.parse(r.date)) / 864e5 > 4), "merchant 標「舊價」的都超過 4 天沒成交");
  await m.page.goto(base + "merchant/", { waitUntil: "networkidle" });
  await visitTabs(m.page, "merchant", ["compare", "weather", "market"]);
  const lsAll = await m.page.evaluate(() => Object.keys(localStorage));
  const added = lsAll.filter((k) => !lsFarmer.includes(k));
  check(added.length > 0 && added.every((k) => k.startsWith("jb_")), `merchant 只寫 jb_ 設定（${added.join(",")}）`);
  check(lsFarmer.every((k) => lsAll.includes(k)), "merchant 沒有刪掉務農的設定");
  check(m.errors.length === 0, `merchant 沒有 JS 錯誤${m.errors.length ? "：" + m.errors.slice(0, 3).join(" | ") : ""}`);

  await browser.close();
  srv.close();
  console.log(failures.length ? `\n✗ ${failures.length} 項失敗` : "\n✓ 全部通過");
  process.exit(failures.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
