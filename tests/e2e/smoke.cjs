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

// E2E_ROOT 可指向別的目錄（例如轉成新格式的資料副本）；E2E_SPLIT=1 時另外驗證「品種歷史點了才下載」
const ROOT = process.env.E2E_ROOT || path.resolve(__dirname, "..", "..");
// 資料已是新格式（prices_index.json fmt=2：品種歷史拆檔）就自動一併驗「品種歷史點了才下載」
const SPLIT = process.env.E2E_SPLIT === "1" || (() => {
  try { return JSON.parse(fs.readFileSync(path.join(ROOT, "prices_index.json"), "utf8")).fmt === 2; } catch (e) { return false; }
})();
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

async function checkNewFeatures(page, name, json, base) {
  // 俗名搜尋：「高麗菜」要找到「甘藍」
  await page.fill("#cropSearch", "高麗菜");
  await page.waitForTimeout(500);
  const sugTxt = await page.evaluate(() => (document.getElementById("cropSug") || {}).textContent || "");
  const cardTxt = await page.evaluate(() => [...document.querySelectorAll(".mcard")].map((c) => c.textContent).join("|"));
  check(sugTxt.includes("甘藍") || cardTxt.includes("甘藍"), `${name} 搜「高麗菜」找得到甘藍`);
  await page.fill("#cropSearch", "");
  await page.dispatchEvent("#cropSearch", "input");
  await page.goto(page.url(), { waitUntil: "networkidle" });
  if (await page.locator("#clModal.on").count()) await page.click("#closeCl");
  // 看板排序：選「較上週漲最多」要記住；「最便宜／最貴（對比近 90 日）」已拿掉（2026-10-31 使用者要求不下貴賤判斷）
  check(await page.locator("#bSort").count() === 1, `${name} 看板有排序選單`);
  const opts = await page.evaluate(() => [...document.querySelectorAll("#bSort option")].map((o) => o.value));
  check(!opts.includes("cheap") && !opts.includes("dear"), `${name} 排序沒有「最便宜／最貴（對比近 90 日）」（${opts.join(",")}）`);
  await page.selectOption("#bSort", "up");
  await page.waitForTimeout(400);
  const key = name === "merchant" ? "jb_sort" : "vs_sort";   // 兩支同網域：各用自己的前綴
  check((await page.evaluate((k) => localStorage.getItem(k), key)) === "up", `${name} 排序選擇有記住（${key}）`);
  await page.evaluate((k) => localStorage.setItem(k, "cheap"), key);   // 舊版存的值：要當成預設，不能壞掉
  await page.evaluate(() => renderBoard());
  check((await page.inputValue("#bSort")) === "", `${name} 舊版排序設定「cheap」自動當成預設順序`);
  await page.selectOption("#bSort", "");
  await page.waitForTimeout(300);
  // 詳細頁：月份熱圖、開價檢查、去年同期圖例
  const right = page.locator(".mcard .mc-right:not(.nolink)");
  if (await right.count()) await right.first().click(); else await page.locator(".mcard").first().click();
  await page.waitForTimeout(600);
  if (!(await page.locator("#mktDetail").isVisible())) {
    const child = page.locator(".mc-child .mc-right:not(.nolink)");
    if (await child.count()) { await child.first().click(); await page.waitForTimeout(800); }
  }
  check(await page.locator("#mktDetail .mheat").count() === 1, `${name} 詳細頁有 12 個月價格熱圖`);
  check(await page.locator("#mktDetail details.offer").count() === 1, `${name} 詳細頁有開價檢查`);
  await page.evaluate(() => { const d = document.querySelector("#mktDetail details.offer"); if (d) d.open = true; });
  await page.fill("#ofPrice", "10");
  await page.waitForTimeout(200);
  const res = await page.textContent("#ofRes");
  check(/元\/公斤/.test(res) && /(划算|合理|偏貴|太貴|很好|偏低|壓價)/.test(res), `${name} 開價檢查有結論（${res.slice(0, 24)}…）`);
  check((await page.textContent("#qcap")).includes("去年同期"), `${name} 走勢圖有去年同期虛線`);
  check(await noHScroll(page), `${name} 新功能沒有造成橫向捲動`);
  if (name === "merchant") {
    // 零售參考：塞一筆零售倍數，詳細頁要出現零售價與推算區間
    await page.evaluate(() => { const c = wholesaleOf(DETAIL) || DETAIL;
      RETAIL = { items: { [c]: [{ item: c, latest: ["2025-12", 60], mult: 3, lo: 2.5, hi: 3.5, n: 12 }] } }; renderDetail(DETAIL, CURVAR); });
    const rt = (await page.textContent("#mktDetail .retail").catch(() => "")) || "";
    check(rt.includes("零售參考") && rt.includes("倍") && rt.includes("較舊"), `merchant 詳細頁顯示零售參考（含資料月份較舊提示）`);
  }
  // 每日摘要：有就要能按 ✕ 收起（同一個資料日不再出現）
  await page.goto(page.url(), { waitUntil: "networkidle" });
  if (await page.locator("#clModal.on").count()) await page.click("#closeCl");
  const dgN = await page.locator(".digest").count();
  check(dgN === 1, `${name} 看板頂端有每日摘要（${name === "merchant" ? "今天進貨參考" : "今日提示"}）`);
  if (dgN) {
    const dgTxt = await page.textContent(".digest");
    check(/較上週|成交量|西螺/.test(dgTxt), `${name} 摘要列出較上週漲跌／成交量（${dgTxt.slice(0, 26)}…）`);
    check(!/適合|偏貴|偏便宜|比平常(貴|便宜)/.test(dgTxt), `${name} 摘要只講事實、不下「適合出貨／多進」判斷`);
    await page.click(".dg-x");
    await page.waitForTimeout(300);
    check(await page.locator(".digest").count() === 0, `${name} 按 ✕ 後摘要收起`);
    await page.evaluate((k) => localStorage.removeItem(k), name === "merchant" ? "jb_digest" : "vs_digest");
  }
  if (name === "merchant") {
    await page.click('.tab[data-t="compare"]');
    await page.waitForTimeout(400);
    const sm = await page.evaluate(() => [...document.querySelectorAll(".cmprow small")].map((s) => s.textContent).join("|"));
    check(sm.includes("比各市場平均") && (sm.includes("你的市場") || sm.includes("比")), `merchant 比價列有「比你的市場」「比各市場平均」`);
    await page.fill("#cmpFreight", "5");
    await page.waitForTimeout(500);
    const sm2 = await page.evaluate(() => [...document.querySelectorAll(".cmprow small")].map((s) => s.textContent).join("|"));
    check(sm2.includes("含運") && (await page.evaluate(() => localStorage.getItem("jb_freight"))) === "5", "merchant 填運費後顯示含運價、並記住設定");
    check(await noHScroll(page), "merchant 比價頁加資訊後沒有橫向捲動");
    await page.screenshot({ path: path.join(OUT, "merchant-compare-new.png") });
    await page.fill("#cmpFreight", "");
    // 回測發現「全部綜合」會把不同品種混著比 → 預設比主要品種；選全部綜合要有提醒；價差 2 倍以上要標示
    const pick = await page.evaluate(() => { const sel = document.getElementById("cmpCrop");
      const c = [...sel.options].map((o) => o.value).find((v) => cmpVarieties(v).length > 1); if (!c) return null;
      sel.value = c; sel.dispatchEvent(new Event("change")); return { c, v: document.getElementById("cmpVar").value }; });
    if (pick) {
      check(pick.v !== "", `merchant 比價預設比主要品種（${pick.c}·${pick.v}），不是全部綜合`);
      await page.selectOption("#cmpVar", "");
      await page.waitForTimeout(300);
      check(await page.locator(".cmpmix").count() === 1, "merchant 選「全部綜合」時提醒各市場品種不同");
    }
    const odd = await page.evaluate(() => { const w = cropMap()[document.getElementById("cmpCrop").value];
      const mks = marketNames().filter((m) => INDEX.latest[m] && INDEX.latest[m][w]); if (mks.length < 3) return false;
      INDEX.latest[mks[0]][w] = { ...INDEX.latest[mks[0]][w], avg: INDEX.latest[mks[0]][w].avg * 10 }; renderCompare(); return true; });
    if (odd) check(await page.locator(".cmpodd").count() >= 1, "merchant 價差 2 倍以上的市場標「可能等級／包裝不同」");
    check(await page.locator("details.cmpbt").count() === 1, "merchant 比價頁附回測說明（跨市場進貨真的比較省嗎）");
    check(await noHScroll(page), "merchant 比價頁加警示後沒有橫向捲動");
    await page.click('.tab[data-t="market"]');
  }
  // 今日休市橫幅：把今天塞進這個市場的休市日，看板頂端要出現提示
  await page.goto(page.url(), { waitUntil: "networkidle" });
  if (await page.locator("#clModal.on").count()) await page.click("#closeCl");
  await page.evaluate(() => { const e = INDEX.markets.find((m) => m.name === MARKET); e.closed = [...(e.closed || []), todayISO()]; renderBoard(); });
  check(await page.locator(".closedbar").count() === 1, `${name} 休市日顯示「今天休市」提示`);
  if (name === "farmer") {
    // 手續費可調：設 10% 後，詳細頁「農民實得」要跟著變
    await page.evaluate(() => { localStorage.setItem("vs_fee", "10"); });
    const pr = page.locator(".mcard .mc-right:not(.nolink)");
    if (await pr.count()) await pr.first().click(); else await page.locator(".mcard").first().click();
    await page.waitForTimeout(500);
    const note = (await page.textContent("#mktDetail .rolenote")) || "";
    check(note.includes("10%"), `farmer 手續費設 10% 後「農民實得」跟著變（${note.slice(0, 30)}…）`);
    // 產地價：塞一筆今天的產地價給這個作物，詳細頁要出現「產地價」與送拍賣的比較
    const ok = await page.evaluate(() => { const c = wholesaleOf(DETAIL) || DETAIL;
      ORIGIN = { items: { [c]: [{ name: c, latest: [todayISO(), 20], series: {} }] } }; renderDetail(DETAIL, CURVAR); return c; });
    const ot = (await page.textContent("#mktDetail .origin").catch(() => "")) || "";
    check(ot.includes("產地價") && ot.includes("送"), `farmer 詳細頁顯示產地價與送拍賣比較（${ok}）`);
    await page.evaluate(() => localStorage.removeItem("vs_fee"));
  }
  await page.screenshot({ path: path.join(OUT, `${name}-detail-new.png`), fullPage: true });
}

async function checkNoJudgmentAndNames(page, name) {
  const t = await page.evaluate(() => { const fr = [...document.querySelectorAll("#catbar button")].find((b) => b.textContent.includes("水果"));
    const veg = document.querySelector("#board").textContent; if (fr) fr.click(); return veg; });
  await page.waitForTimeout(400);
  const all = t + (await page.textContent("#board"));
  check(!/偏貴|偏便宜/.test(all) && (await page.locator(".mcard .tag:not(.season)").count()) === 0, `${name} 看板卡片沒有偏貴／偏便宜標籤`);
  check(!/綜合/.test(all), `${name} 看板卡片沒有「綜合」均價`);
  if (name === "merchant") {
    const mv = await page.locator(".mc-mv").count();
    check(mv > 0, `merchant 多品種卡片顯示「主要品種」的實際價（${mv} 張）`);
    const banana = await page.evaluate(() => { const c = availableCrops().find((x) => x === "香蕉"); return c ? mainVar(c) : "（無香蕉）"; });
    check(banana === "香蕉" || banana === "（無香蕉）", `merchant 香蕉的主要品種是同名品種「香蕉」，不是量很少的芭蕉（${banana}）`);
  }
  const alias = await page.evaluate(() => [...document.querySelectorAll(".mcard .alias, .mcard .mc-nm small.alias")].map((e) => e.textContent).slice(0, 3));
  const vegAlias = /\((高麗菜|空心菜|大白菜|地瓜葉|小黃瓜|芭樂|哈密瓜|香瓜|火龍果|柳丁)\)/.test(all + alias.join(""));
  check(vegAlias, `${name} 名稱旁有常用俗名（例：番石榴(芭樂)；看到 ${alias.join("、") || "無"}）`);
}

async function checkLargeFont(ctx, url, name) {
  const pre = name === "merchant" ? "jb_" : "vs_";
  const p = await ctx.newPage();
  await p.addInitScript((pre) => { try { localStorage.setItem(pre + "fs", "xl"); localStorage.removeItem(pre + "seenver"); } catch (e) {} }, pre);
  await p.goto(url, { waitUntil: "networkidle" });
  await p.waitForTimeout(700);
  // 特大字體：「更新內容」彈窗的 ✕ 要在畫面內按得到（曾經整頁變寬、✕ 跑到畫面外）
  if (await p.locator("#clModal.on").count()) {
    const r = await p.locator("#closeCl").boundingBox();
    check(!!r && r.x >= 0 && r.x + r.width <= 390, `${name} 特大字體：更新內容彈窗的 ✕ 在畫面內（x=${r && Math.round(r.x)}）`);
    await p.click("#closeCl");
  }
  const g = await p.locator("#gear").boundingBox();
  check(!!g && g.x + g.width <= 390, `${name} 特大字體：右上 ⚙️ 沒被擠出畫面`);
  check(await noHScroll(p), `${name} 特大字體：看板沒有橫向捲動`);
  const card = p.locator(".mcard .mc-right:not(.nolink), button.mcard").first();
  if (await card.count()) { await card.click(); await p.waitForTimeout(500); check(await noHScroll(p), `${name} 特大字體：詳細頁沒有橫向捲動`); }
  for (const t of name === "merchant" ? ["compare", "weather"] : ["forecast", "harvest"]) {
    await p.click(`.tab[data-t="${t}"]`); await p.waitForTimeout(400);
    check(await noHScroll(p), `${name} 特大字體：分頁「${t}」沒有橫向捲動`);
  }
  await p.screenshot({ path: path.join(OUT, `${name}-xl.png`) });
  await p.evaluate((pre) => localStorage.removeItem(pre + "fs"), pre);
  await p.close();
}

async function checkTyphoonMap(ctx, url, name) {
  // 颱風詳情改版（2026-10-31）：彩色底圖＋已發生路徑／預報路徑／暴風圈／70% 範圍／你的位置；標籤不可重疊；
  // 追蹤文字用使用者設定的縣市（精準進貨設台中就不能出現「對雲林」）。底圖磚在 CI 連不到也沒關係：擋掉、只驗向量圖層。
  const pre = name === "merchant" ? "jb_" : "vs_", county = name === "merchant" ? "台中" : "雲林";
  const p = await ctx.newPage(); const errs = [];
  p.on("pageerror", (e) => errs.push(e.message));
  // 只算我們自己網頁的錯誤：windy 內嵌頁（第三方）在 CI 有網路時會印它自己的錯（定位權限被擋、EventSource），不是 App 的問題
  p.on("console", (m) => { const loc = (m.location() || {}).url || "";
    if (m.type() === "error" && !/^https?:\/\/(?!127\.0\.0\.1)/.test(loc) && !/tile\.openstreetmap|windy|ERR_FAILED|net::/.test(m.text())) errs.push(m.text()); });
  await p.route("**/typhoon_status.json", (r) => r.fulfill({ path: path.join(__dirname, "fixtures", "typhoon_status_infa.json"), contentType: "application/json" }));
  await p.route(/tile\.openstreetmap\.org|windy\.com/, (r) => r.abort());   // 底圖磚、windy 都是外部服務：測試不依賴它們
  await p.addInitScript(([pre, c]) => { try { localStorage.setItem(pre + "region", c); } catch (e) {} }, [pre, county + (county === "台中" ? "市" : "縣")]);
  await p.goto(url, { waitUntil: "networkidle" });
  if (await p.locator("#clModal.on").count()) await p.click("#closeCl");
  // 精準進貨：颱風改在「天氣 → 颱風動態」子分頁（沒有彈窗了）；精準務農：田園巡查的颱風詳情彈窗
  await p.evaluate((n) => (n === "merchant" ? goTyphoon() : openTyphoon()), name);
  await p.waitForSelector("#tyMap .leaflet-tooltip", { timeout: 8000 }).catch(() => {});
  await p.waitForTimeout(400);
  const st = await p.evaluate(() => {
    const tips = [...document.querySelectorAll("#tyMap .leaflet-tooltip")].map((t) => { const r = t.getBoundingClientRect(); return { t: t.textContent, r: [r.left, r.top, r.right, r.bottom] }; });
    let overlap = 0;
    for (let i = 0; i < tips.length; i++) for (let j = i + 1; j < tips.length; j++) {
      const a = tips[i].r, b = tips[j].r; if (a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3]) overlap++; }
    return { paths: document.querySelectorAll("#tyMap path").length, tips: tips.map((x) => x.t), overlap,
      status: (document.querySelector(".tyname") || {}).textContent || "", info: (document.querySelector(".tyinfo") || {}).textContent || "",
      legend: (document.querySelector(".tylegend") || {}).textContent || "",
      iframes: document.querySelectorAll("iframe").length,
      links: [...document.querySelectorAll(".tybtn")].filter((a) => a.offsetParent).map((a) => a.href) };
  });
  check(st.paths >= 10, `${name} 颱風地圖畫出路徑、暴風圈、機率範圍（${st.paths} 個圖形）`);
  check(st.tips.includes(county) && st.tips.some((t) => t.includes("現在")), `${name} 颱風地圖標出你的位置（${county}）與颱風目前位置`);
  check(st.overlap === 0, `${name} 颱風地圖的標籤互不重疊（${st.tips.join("、")}）`);
  check(st.status.includes(`對${county}`) && !(county !== "雲林" && st.status.includes("雲林")), `${name} 追蹤文字用你設定的縣市（${st.status.trim()}）`);
  check(/14 級/.test(st.info) && /940 hPa/.test(st.info), `${name} 地圖下方有颱風強度（${st.info}）`);
  check(/預報路徑/.test(st.legend) && /70% 機率範圍/.test(st.legend), `${name} 地圖圖例完整`);
  check(await noHScroll(p), `${name} 颱風詳情沒有橫向捲動`);
  check(st.iframes === 0, `${name} 颱風詳情沒有 windy 內嵌即時影像（使用者要求拿掉）`);
  check(["cwa.gov.tw", "ncdr.nat.gov.tw", "windy.com"].every((h) => st.links.some((u) => u.includes(h))),
    `${name} 保留外部連結：氣象署、NCDR、windy 全螢幕（${st.links.length} 個）`);
  await p.screenshot({ path: path.join(OUT, `${name}-typhoon-map.png`) });
  if (name === "merchant") await p.evaluate(() => switchTab("market")); else await p.evaluate(() => closeTyphoon());
  check(!(await p.locator("#tyMap .leaflet-pane").count()), `${name} 離開颱風詳情會釋放地圖`);
  check(errs.length === 0, `${name} 颱風詳情沒有 JS 錯誤${errs.length ? "：" + errs.slice(0, 2).join(" | ") : ""}`);
  await p.close();
}

async function checkWeatherTabs(ctx, url) {
  // 2026-10-31 使用者要求：天氣分頁分「近期天氣／颱風動態」，有颱風時旁邊亮紅點；24 小時改左右滑動
  const tz = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 16).replace("T", " ");   // 台灣時間：讓預報不被當成過期
  const open = async (typhoon) => {
    const p = await ctx.newPage(); const errs = [];
    p.on("pageerror", (e) => errs.push(e.message));
    await p.route("**/typhoon_status.json", async (r) => {
      if (typhoon) return r.fulfill({ path: path.join(__dirname, "fixtures", "typhoon_status_infa.json"), contentType: "application/json" });
      return r.fulfill({ json: { updated: tz, active: false, tracking: false, track: null } });
    });
    await p.route("**/weather_forecast.json", async (r) => {
      const res = await r.fetch(); let j = {}; try { j = await res.json(); } catch (e) {}
      j.updated = tz; return r.fulfill({ response: res, json: j });
    });
    await p.route(/tile\.openstreetmap\.org|windy\.com/, (r) => r.abort());
    await p.goto(url, { waitUntil: "networkidle" });
    if (await p.locator("#clModal.on").count()) await p.click("#closeCl");
    return { p, errs };
  };
  let { p, errs } = await open(true);
  check(await p.locator('.tab[data-t="weather"].dot').count() === 1, "merchant 有颱風時主分頁「天氣」亮紅點");
  await p.click('.tab[data-t="weather"]'); await p.waitForTimeout(400);
  const tabs = await p.evaluate(() => [...document.querySelectorAll(".wxtabs button")].map((b) => b.textContent.trim()));
  check(tabs.join("|") === "近期天氣|颱風動態", `merchant 天氣分頁有子分頁（${tabs.join("、")}）`);
  check(await p.locator(".wxtabs .rdot").count() === 1, "merchant「颱風動態」旁亮紅點");
  const strip = await p.evaluate(() => { const s = document.querySelector(".wxs"); if (!s) return null;
    return { cards: s.querySelectorAll(".wxc").length, sw: s.scrollWidth, cw: s.clientWidth }; });
  check(!!strip && strip.cards >= 2 && strip.sw > strip.cw + 20, `merchant 未來 24 小時是左右滑動的卡片（${strip ? strip.cards + " 張、內容寬 " + strip.sw + " > " + strip.cw : "沒有預報資料"}）`);
  if (strip) { await p.evaluate(() => { document.querySelector(".wxs").scrollLeft = 9999; }); await p.waitForTimeout(200); }
  check(await noHScroll(p), "merchant 天氣分頁整頁沒有橫向捲動（只有 24 小時那一列能滑）");
  await p.screenshot({ path: path.join(OUT, "merchant-weather-now.png") });
  const warn = p.locator("#wxWarn");
  if (await warn.count()) { await warn.click(); await p.waitForTimeout(800);
    check(await p.locator('.wxtabs button.on[data-w="ty"]').count() === 1, "merchant 點颱風橫幅切到「颱風動態」"); }
  else { await p.click('.wxtabs button[data-w="ty"]'); await p.waitForTimeout(800); }
  await p.waitForSelector("#tyMap .leaflet-pane", { timeout: 8000 }).catch(() => {});
  check(await p.locator("#tyMap .leaflet-pane").count() > 0, "merchant「颱風動態」顯示路徑地圖");
  await p.screenshot({ path: path.join(OUT, "merchant-weather-typhoon.png") });
  await p.click('.wxtabs button[data-w="now"]'); await p.waitForTimeout(300);
  check(await p.locator("#tyMap .leaflet-pane").count() === 0, "merchant 切回「近期天氣」釋放地圖");
  check(errs.length === 0, `merchant 天氣分頁沒有 JS 錯誤${errs.length ? "：" + errs.slice(0, 2).join(" | ") : ""}`);
  await p.close();
  ({ p, errs } = await open(false));
  check(await p.locator('.tab[data-t="weather"].dot').count() === 0, "merchant 沒有颱風時主分頁不亮紅點");
  await p.click('.tab[data-t="weather"]'); await p.waitForTimeout(300);
  check(await p.locator(".wxtabs .rdot").count() === 0, "merchant 沒有颱風時「颱風動態」不亮紅點");
  await p.click('.wxtabs button[data-w="ty"]'); await p.waitForTimeout(300);
  const t = (await p.textContent("#weatherWrap")) || "";
  check(/目前沒有颱風/.test(t) && (await p.locator(".tybtn").count()) === 3, "merchant 沒有颱風時「颱風動態」說明目前沒有颱風、仍有外部連結");
  check(errs.length === 0, `merchant 沒颱風時天氣分頁沒有 JS 錯誤${errs.length ? "：" + errs.slice(0, 2).join(" | ") : ""}`);
  await p.close();
}

async function checkMerchantR4(ctx, browser, iphone, url) {
  // 2026-10-31 使用者要求：水果排在蔬菜前（看板、比價、設定）、比價可打字找、天氣頁換地區、設定拿掉天氣
  const p = await ctx.newPage(); const errs = [];
  p.on("pageerror", (e) => errs.push(e.message));
  await p.goto(url, { waitUntil: "networkidle" });
  if (await p.locator("#clModal.on").count()) await p.click("#closeCl");
  const first = (sel) => p.evaluate((s) => { const b = document.querySelector(s); return b ? b.textContent.trim() : ""; }, sel);
  const b0 = await first("#catbar button");
  check(b0.includes("水果") && (await first("#catbar button.on")).includes("水果"), `merchant 看板第一顆是水果、預設選它（${b0}）`);
  await p.click('.tab[data-t="compare"]'); await p.waitForTimeout(500);
  check((await first("#cmpCat button")).includes("水果"), "merchant 比價第一顆是水果");
  // 打字搜尋：俗名「芭樂」→ 番石榴
  const hasGuava = await p.evaluate(() => Object.keys(cropMap()).includes("番石榴"));
  await p.fill("#cmpSearch", "芭樂"); await p.waitForTimeout(300);
  const sug = await p.evaluate(() => [...document.querySelectorAll("#cmpSug button")].map((b) => b.dataset.c));
  if (hasGuava) {
    check(sug[0] === "番石榴", `merchant 比價打「芭樂」第一個建議是番石榴（${sug.join(",")}）`);
    await p.click("#cmpSug button"); await p.waitForTimeout(500);
    check((await p.inputValue("#cmpCrop")) === "番石榴" && (await p.locator(".cmprow").count()) > 0, "merchant 點建議後比價換成番石榴、有排名");
    check((await p.inputValue("#cmpSearch")) === "" && !(await p.locator("#cmpSug").isVisible()), "merchant 選好後搜尋框清空、建議收起");
  } else check(false, "merchant 測試資料沒有番石榴，無法驗打字搜尋");
  await p.fill("#cmpSearch", "高麗菜"); await p.press("#cmpSearch", "Enter"); await p.waitForTimeout(500);
  check((await p.inputValue("#cmpCrop")) === "甘藍" && (await first("#cmpCat button.on")).includes("蔬菜"), "merchant 打「高麗菜」按 Enter → 甘藍、類別自動切到蔬菜");
  await p.fill("#cmpSearch", "不存在的菜"); await p.waitForTimeout(300);
  check(((await p.textContent("#cmpSug")) || "").includes("找不到"), "merchant 找不到時有說");
  await p.fill("#cmpSearch", "");
  check(await noHScroll(p), "merchant 比價加了搜尋框沒有橫向捲動");
  await p.screenshot({ path: path.join(OUT, "merchant-compare-search.png") });
  // 設定：沒有天氣地區、我的品項水果在前
  await p.click("#gear"); await p.waitForTimeout(400);
  const st = (await p.textContent("#modal")) || "";
  check(!/天氣預報地區/.test(st) && (await p.locator("#regionSelect").count()) === 0, "merchant 設定裡沒有天氣地區了");
  check((await first("#cropPick .pickh span")).includes("水果"), "merchant 設定「我的品項」水果在前");
  await p.keyboard.press("Escape"); await p.goto(url, { waitUntil: "networkidle" });
  // 天氣頁：✏️ 修改
  await p.click('.tab[data-t="weather"]'); await p.waitForTimeout(400);
  check((await p.locator("#wxLoc").count()) === 1 && (await p.locator("#wxEdit").count()) === 1, "merchant 天氣標題旁有「📍 定位」「✏️ 修改」");
  check(!(await p.locator("#wxRegion").isVisible()), "merchant 地區選單平常收起");
  await p.click("#wxEdit"); await p.waitForTimeout(200);
  const opts = await p.evaluate(() => [...document.querySelectorAll("#wxRegion option")].map((o) => o.value));
  const pick = opts.find((c) => c.includes("花蓮")) || opts[opts.length - 1];
  await p.selectOption("#wxRegion", pick); await p.waitForTimeout(300);
  check(((await p.textContent(".wx-h")) || "").includes(pick) && (await p.evaluate(() => localStorage.getItem("jb_region"))) === pick, `merchant 手動改地區 → 標題變 ${pick}、有記住`);
  check(!(await p.locator("#wxRegion").isVisible()), "merchant 選完選單自動收起");
  const hb = await p.evaluate(() => { const h = document.querySelector(".wx-hrow").getBoundingClientRect(), b = document.querySelector(".wx-hbtns").getBoundingClientRect();
    return b.right <= window.innerWidth + 1 && b.top < h.top + 40; });
  check(hb && await noHScroll(p), "merchant 兩顆按鈕在標題右邊、沒有超出畫面");
  // 標題和兩顆按鈕同一列（2026-11-01 使用者要求）：字級中、大都要同一列
  for (const fs of ["m", "l"]) {
    await p.evaluate((f) => { localStorage.setItem("jb_fs", f); applyFont(); renderWeather(); }, fs); await p.waitForTimeout(200);
    const row = await p.evaluate(() => { const r = (s) => document.querySelector(s).getBoundingClientRect();
      const h = r(".wx-h"), a = r("#wxLoc"), b = r("#wxEdit"); const c = (x) => x.top + x.height / 2;
      return { same: Math.abs(c(h) - c(a)) < 8 && Math.abs(c(a) - c(b)) < 2, right: b.right <= innerWidth + 1, title: document.querySelector(".wx-h").textContent.trim() }; });
    check(row.same && row.right, `merchant 字級${fs === "m" ? "中" : "大"}：「${row.title}」和定位、修改在同一列`);
  }
  await p.evaluate(() => { localStorage.removeItem("jb_fs"); applyFont(); renderWeather(); });
  await p.screenshot({ path: path.join(OUT, "merchant-weather-head.png") });
  check(errs.length === 0, `merchant 本輪新功能沒有 JS 錯誤${errs.length ? "：" + errs.slice(0, 2).join(" | ") : ""}`);
  await p.close();
  // 📍 現在位置：同一個 iPhone 環境，給台中座標；沒權限時要說怎麼開
  for (const [perm, want] of [[true, "高雄市"], [false, "定位服務"]]) {
    const c2 = await browser.newContext({ ...iphone, serviceWorkers: "block", geolocation: { latitude: 22.63, longitude: 120.30 }, permissions: perm ? ["geolocation"] : [] });
    const q = await c2.newPage();
    await q.goto(url, { waitUntil: "networkidle" });
    if (await q.locator("#clModal.on").count()) await q.click("#closeCl");
    await q.click('.tab[data-t="weather"]'); await q.waitForTimeout(300);
    await q.click("#wxLoc"); await q.waitForTimeout(1500);
    const t = (perm ? await q.textContent(".wx-h") : await q.textContent("#wxMsg")) || "";
    check(t.includes(want), `merchant 現在位置（${perm ? "有權限→高雄（預設是台中，要真的換過去）" : "沒權限→教怎麼開"}）：${t.trim().slice(0, 30)}`);
    await c2.close();
  }
}

async function pinchMap(p) {
  // 兩指在地圖中央往外撐開（CDP 真的送觸控事件，跟 iPhone 手勢一樣走瀏覽器的觸控流程）
  const box = await p.locator("#tyMap").boundingBox();
  const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
  const cdp = await p.context().newCDPSession(p);
  const pts = (d) => [{ x: cx - d, y: cy, id: 0 }, { x: cx + d, y: cy, id: 1 }];
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: pts(20) });
  let mid = "";
  for (let d = 30; d <= 140; d += 10) {
    await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: pts(d) }); await p.waitForTimeout(16);
    // 手勢進行中看整頁有沒有被放大：Leaflet 量手指距離會除以頁面縮放比例，整頁一起放大＝地圖量不到手指張開（iPhone 上就是放不大）
    if (d === 100) mid = await p.evaluate(() => { const w = document.querySelector(".wrap"); return w ? w.style.transform : ""; });
  }
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await p.waitForTimeout(700);
  return mid;
}

async function checkMapPinch(ctx, url, name) {
  // 2026-11-01 使用者回報：颱風路徑地圖兩指不能放大（精準進貨整頁回彈縮放把手勢吃掉）
  const p = await ctx.newPage();
  await p.route("**/typhoon_status.json", (r) => r.fulfill({ path: path.join(__dirname, "fixtures", "typhoon_status_infa.json"), contentType: "application/json" }));
  await p.route(/tile\.openstreetmap\.org|windy\.com/, (r) => r.abort());
  await p.goto(url, { waitUntil: "networkidle" });
  if (await p.locator("#clModal.on").count()) await p.click("#closeCl");
  if (name === "merchant") await p.evaluate(() => goTyphoon());
  else await p.evaluate(() => openTyphoon());
  await p.waitForSelector("#tyMap .leaflet-pane", { timeout: 8000 }).catch(() => {});
  const z0 = await p.evaluate(() => TyMap.current() && TyMap.current().getZoom());
  const tf = await pinchMap(p);
  const z1 = await p.evaluate(() => TyMap.current() && TyMap.current().getZoom());
  check(z0 != null && z1 > z0, `${name} 颱風地圖兩指撐開會放大地圖（zoom ${z0} → ${z1}）`);
  check(!tf || tf === "scale(1)", `${name} 在地圖上兩指時整頁不跟著放大（手勢中 ${tf || "無"}）`);
  await p.close();
}

async function checkNavBack(ctx, url) {
  // 2026-11-01 使用者要求：左上角 ‹ 上一頁；返回後回到上次的地方（捲動位置、展開的品種）
  const p = await ctx.newPage(); const errs = [];
  p.on("pageerror", (e) => errs.push(e.message));
  await p.goto(url, { waitUntil: "networkidle" });
  if (await p.locator("#clModal.on").count()) await p.click("#closeCl");
  check(!(await p.locator("#navBack").isVisible()), "merchant 一進來沒有上一頁可回 → 不顯示 ‹");
  // 多品種卡：「N 品種」自己一行、不會被拆成兩行
  const vh = await p.evaluate(() => [...document.querySelectorAll(".mc-vhint")].map((e) => ({ rows: e.getClientRects().length, inName: !!e.closest(".mc-nm") })));
  check(vh.length > 0 && vh.every((v) => v.rows === 1 && !v.inName), `merchant「N 品種」在名稱下一行、不斷行（${vh.length} 張）`);
  // 找最後一張多品種卡：展開、捲到那裡、點價格進 K 線
  const idx = await p.evaluate(() => { const cs = [...document.querySelectorAll("#mktBoard .mcard, .mcard")].filter((c) => c.querySelector(".mc-vhint")); return cs.length ? [...document.querySelectorAll(".mcard")].indexOf(cs[cs.length - 1]) : -1; });
  if (idx < 0) { check(false, "merchant 找不到多品種卡，無法驗返回位置"); await p.close(); return; }
  await p.locator(".mcard").nth(idx).locator(".mc-left").click(); await p.waitForTimeout(300);
  const nm = await p.evaluate(() => PICK);
  // 捲到「要點的那張品種卡」置中，不是整組置中：品種多（例：洋香瓜 10 幾種）整組比螢幕高時，第一張卡會在畫面外，
  // Playwright 點之前會自己把它捲進來 → App 記到的位置跟這裡量的 y0 不同，測試就誤判（2026-11-02 資料重現：1673 vs 1231）
  await p.evaluate(() => { document.querySelector(".vgroup .mc-right:not(.nolink)").scrollIntoView({ block: "center" }); });
  await p.waitForTimeout(200);
  const y0 = await p.evaluate(() => Math.round(scrollY));
  await p.locator(".vgroup .mc-right:not(.nolink)").first().click(); await p.waitForTimeout(600);
  check(await p.locator("#mktDetail").isVisible() && await p.locator("#navBack").isVisible(), "merchant 進 K 線後左上角出現 ‹");
  await p.click("#navBack"); await p.waitForTimeout(500);
  const back = await p.evaluate(() => ({ y: Math.round(scrollY), pick: PICK, open: !!document.querySelector(".vgroup"), list: getComputedStyle(document.getElementById("mktList")).display !== "none" }));
  check(back.list && back.pick === nm && back.open, `merchant 按 ‹ 回看板，剛剛展開的「${nm}」品種還是展開的`);
  check(Math.abs(back.y - y0) <= 4 && y0 > 100, `merchant 按 ‹ 回到原本捲到的位置（${y0} → ${back.y}）`);
  // 詳細頁：不再有重複的「‹ 返回」；品種是下拉，可換同作物其他品種（2026-11-01 使用者要求）
  await p.locator(".vgroup .mc-right:not(.nolink)").first().click(); await p.waitForTimeout(500);
  check(await p.locator("#dtlBack").count() === 0, "merchant 詳細頁沒有重複的「‹ 返回」");
  const vo = await p.evaluate(() => [...document.querySelectorAll("#dtlVar option")].map((o) => o.value));
  const cur = await p.evaluate(() => CURVAR);
  const other = vo.find((v) => v && v !== cur);
  check(vo.length >= 2 && vo.includes(cur) && !!other, `merchant 詳細頁品種是下拉（${vo.join("、")}，目前 ${cur}）`);
  if (other) {
    await p.selectOption("#dtlVar", other); await p.waitForTimeout(500);
    const after = await p.evaluate(() => ({ cv: CURVAR, sel: document.getElementById("dtlVar").value, d: getComputedStyle(document.getElementById("mktDetail")).display }));
    check(after.cv === other && after.sel === other && after.d !== "none", `merchant 下拉換成「${other}」→ K 線改看該品種`);
    check(await noHScroll(p), "merchant 品種下拉沒有造成橫向捲動");
    await p.screenshot({ path: path.join(OUT, "merchant-detail-variety.png") });
  }
  await p.click("#navBack"); await p.waitForTimeout(500);
  check(Math.abs((await p.evaluate(() => Math.round(scrollY))) - y0) <= 4, "merchant 換過品種後按 ‹ 也回到原位置");
  // 換分頁 → 上一頁回原分頁、原位置
  // 用 el.click()：Playwright 的 click 會先把分頁列捲進畫面，記到的位置就變成頁首，驗不到「回原位置」
  await p.evaluate(() => document.querySelector('.tab[data-t="weather"]').click()); await p.waitForTimeout(300);
  await p.click("#navBack"); await p.waitForTimeout(500);
  const wb = await p.evaluate(() => ({ tab: document.querySelector(".tab.on").dataset.t, y: Math.round(scrollY), nav: NAV.length }));
  check(wb.tab === "market" && Math.abs(wb.y - y0) <= 4, `merchant 從天氣按 ‹ 回到看板原位置（${JSON.stringify(wb)} vs ${y0}）`);
  check(!(await p.locator("#navBack").isVisible()), "merchant 退到底後 ‹ 自動藏起來");
  // 比價點市場 → K 線；返回直接回比價（不停在中間的看板）
  await p.click('.tab[data-t="compare"]'); await p.waitForTimeout(500);
  await p.locator(".cmprow").first().click(); await p.waitForTimeout(1200);
  check(await p.locator("#mktDetail").isVisible(), "merchant 比價點市場進 K 線");
  await p.click("#navBack"); await p.waitForTimeout(500);
  check(await p.locator('.tab.on[data-t="compare"]').count() === 1, "merchant 從比價進的 K 線，‹ 直接回比價");
  // 彈窗開著時，上一頁先關彈窗
  await p.click("#gear"); await p.waitForTimeout(300);
  await p.evaluate(() => goBack()); await p.waitForTimeout(200);
  check(!(await p.locator("#modal.on").count()), "merchant 設定開著時，上一頁先關設定");
  check(await noHScroll(p), "merchant 加了 ‹ 沒有橫向捲動");
  check(errs.length === 0, `merchant 上一頁沒有 JS 錯誤${errs.length ? "：" + errs.slice(0, 2).join(" | ") : ""}`);
  await p.screenshot({ path: path.join(OUT, "merchant-navback.png") });
  await p.close();
}

async function checkYearStatsAndMonths(ctx, url, name) {
  // 2026-11-02：詳細頁「一年百分位／到貨量比」、12 個月圖加到貨量；都只寫數字（看板不下貴賤判斷）
  const pre = name === "merchant" ? "jb_" : "vs_";
  const p = await ctx.newPage(); const errs = [];
  p.on("pageerror", (e) => errs.push(e.message));
  await p.goto(url, { waitUntil: "networkidle" });
  if (await p.locator("#clModal.on").count()) await p.click("#closeCl");
  // 資料天天變：不寫死品項，挑一個近 14 天有成交、近一年資料夠的
  const c = await p.evaluate(() => availableCrops().find((x) => { const pv = priceView(x); return pv && pv.stale <= 14 && Core.yearStats(seriesFor(x)); }));
  if (!c) { check(false, `${name} 找不到有一年資料的品項，無法驗一年百分位`); await p.close(); return; }
  await p.evaluate((x) => openDetail(x, null), c); await p.waitForTimeout(500);
  const ys = await p.evaluate(() => { const d = document.querySelector("#mktDetail .ystat"); return d ? d.textContent : ""; });
  const want = await p.evaluate((x) => { const y = Core.yearStats(seriesFor(x)); return { p: Math.round(y.pct * 100), s: y.sup == null ? null : y.sup.toFixed(2) }; }, c);
  check(ys.includes(`一年百分位 ${want.p}%`) && (want.s === null || ys.includes(`到貨量比 ${want.s}`)), `${name} 詳細頁有一年百分位、到貨量比（${c}：${ys.slice(0, 28)}）`);
  check(!/偏貴|偏便宜|適合|划算/.test(ys), `${name} 一年百分位只寫數字、不下貴賤判斷`);
  const mh = await p.evaluate(() => ({ t: (document.querySelector("#mktDetail .mh-t") || {}).textContent || "",
    n: document.querySelectorAll("#mktDetail .mh").length, q: document.querySelectorAll("#mktDetail .mh .mq").length }));
  check(mh.n === 12 && mh.q === 12 && mh.t.includes("到貨量") && !/適合|便宜月|高價月/.test(mh.t), `${name} 12 個月圖同時有價格與到貨量、標題不下判斷（${mh.t}）`);
  // 模擬淡季：拿掉 6～8 月的成交 → 那 3 格要變灰（幾乎沒貨）
  await p.evaluate((x) => { const s = seriesFor(x); for (const d of Object.keys(s)) if (["06", "07", "08"].includes(d.slice(5, 7))) delete s[d]; renderDetail(x, null); }, c);
  const none = await p.evaluate(() => [...document.querySelectorAll("#mktDetail .mh.none b")].map((b) => b.textContent));
  check(["6月", "7月", "8月"].every((m) => none.includes(m)), `${name} 沒到貨的月份變灰（${none.join("、")}）`);
  // 特大字：月份格與說明不撐寬畫面
  await p.evaluate((k) => { localStorage.setItem(k, "xl"); applyFont(); const d = document.querySelector("#mktDetail .ystat"); if (d) d.open = true; }, pre + "fs");
  await p.waitForTimeout(200);
  const over = await p.evaluate(() => [...document.querySelectorAll("#mktDetail .mh")].filter((e) => e.scrollWidth > e.clientWidth + 1).length);
  check(await noHScroll(p) && over === 0, `${name} 特大字：一年百分位、12 個月圖沒有橫向捲動、格子沒被字撐破（${over}）`);
  await p.locator("#mktDetail .mheat").screenshot({ path: path.join(OUT, `${name}-month-grid-xl.png`) });
  await p.evaluate((k) => localStorage.removeItem(k), pre + "fs");
  check(errs.length === 0, `${name} 一年百分位／月份圖沒有 JS 錯誤${errs.length ? "：" + errs.slice(0, 2).join(" | ") : ""}`);
  await p.close();
}

async function checkTargetPrice(ctx, url) {
  // 2026-11-02：我的品項設目標價 → 看板「今天進貨參考」寫已到價／還差 X 元；只存在手機（jb_target_市場）
  const p = await ctx.newPage(); const errs = [];
  p.on("pageerror", (e) => errs.push(e.message));
  await p.goto(url, { waitUntil: "networkidle" });
  if (await p.locator("#clModal.on").count()) await p.click("#closeCl");
  await p.evaluate(() => localStorage.removeItem("jb_digest"));
  const pick = await p.evaluate(() => { for (const c of watchCrops()) { const v = mainVar(c); const pv = priceView(c, v || undefined);
    if (pv && pv.stale <= 7) return { c, v, avg: pv.avg, mk: MARKET }; } return null; });
  if (!pick) { check(false, "merchant 我的品項裡沒有近 7 天有成交的，無法驗目標價"); await p.close(); return; }
  const key = "jb_target_" + pick.mk, label = pick.v ? `${pick.c}·${pick.v}` : pick.c;
  const openIt = async () => { await p.evaluate(({ c, v }) => openDetail(c, v), pick); await p.waitForTimeout(400);
    await p.evaluate(() => { document.getElementById("tgtBox").open = true; }); };
  const setTo = async (v) => { await p.fill("#tgtIn", String(v)); await p.dispatchEvent("#tgtIn", "change"); await p.waitForTimeout(200); };
  const digest = async () => { await p.click("#navBack"); await p.waitForTimeout(500); return (await p.textContent(".digest").catch(() => "")) || ""; };
  await openIt();
  check((await p.textContent("#tgtSum")).includes("到價提醒"), "merchant 詳細頁有「🎯 目標價」，還沒設時寫「到價提醒」");
  const high = Math.ceil(pick.avg) + 5;
  await setTo(high);
  check((await p.textContent("#tgtSum")).includes("已到價"), `merchant 目標價 ${high} ≥ 現價 ${pick.avg} → 已到價`);
  const st = await p.evaluate((k) => JSON.parse(localStorage.getItem(k) || "null"), key);
  check(!!st && Object.values(st).some((x) => x.c === pick.c && x.p === high), `merchant 目標價只存在手機（${key}）`);
  let dg = await digest();
  check(dg.includes("🎯") && dg.includes(label) && dg.includes("已到價"), `merchant 看板「今天進貨參考」寫 ${label} 已到價`);
  check(!/適合|偏貴|偏便宜/.test(dg), "merchant 目標價提醒只講事實");
  check(await noHScroll(p), "merchant 目標價提醒沒有橫向捲動");
  await p.screenshot({ path: path.join(OUT, "merchant-target-digest.png") });
  // 目標價低於現價 → 還差 X 元；切台斤要換算（存的是元/公斤）
  await openIt();
  const low = Math.round(pick.avg * 5) / 10, gapKg = pick.avg - low, gapTai = gapKg * 0.6;
  const fx = (d) => (d < 0.1 ? d.toFixed(2) : d.toFixed(1));
  await setTo(low);
  check((await p.textContent("#tgtSum")).includes(`還差 ${fx(gapKg)} 元`), `merchant 目標價 ${low} < 現價 ${pick.avg} → 還差 ${fx(gapKg)} 元`);
  await p.click('#dtlUnit button[data-u="tai"]'); await p.waitForTimeout(300);
  const sumTai = await p.textContent("#tgtSum");
  check(sumTai.includes("元/台斤") && sumTai.includes(`還差 ${fx(gapTai)} 元`) && (await p.inputValue("#tgtIn")) === String(+(low * 0.6).toFixed(2)),
    `merchant 切台斤：目標價與差額都換算（${sumTai.trim()}）`);
  await p.click('#dtlUnit button[data-u="kg"]'); await p.waitForTimeout(300);
  dg = await digest();
  check(dg.includes(label) && dg.includes(`還差 ${fx(gapKg)} 元`), `merchant 看板寫 ${label} 還差 ${fx(gapKg)} 元`);
  // 填錯不存；手機不讓存（私密瀏覽）要說、不當機；存的東西壞掉當作沒設
  await openIt();
  await setTo(-5);
  const kept = await p.evaluate(({ k, low }) => Object.values(JSON.parse(localStorage.getItem(k))).some((x) => x.p === low), { k: key, low });
  check(kept && (await p.textContent("#toast")).includes("大於 0"), "merchant 目標價填負數：不存、提示要填大於 0");
  await p.evaluate(() => { window.__si = Storage.prototype.setItem; Storage.prototype.setItem = function () { throw new Error("QuotaExceededError"); }; });
  await setTo(7);
  await p.evaluate(() => { Storage.prototype.setItem = window.__si; });
  check((await p.textContent("#toast")).includes("沒存到"), "merchant 手機不讓存設定 → 提示目標價沒存到");
  await p.click("#tgtClr"); await p.waitForTimeout(200);
  check((await p.evaluate((k) => localStorage.getItem(k), key)) === null && (await p.textContent("#tgtSum")).includes("到價提醒"), "merchant 按「清除」→ 目標價刪掉");
  dg = await digest();
  check(!dg.includes("🎯"), "merchant 清除後看板不再寫目標價");
  await p.evaluate((k) => { localStorage.setItem(k, "{壞掉"); localStorage.removeItem("jb_digest"); renderBoard(); }, key);
  check(await p.locator(".mcard").count() > 0, "merchant 目標價設定壞掉（不是 JSON）→ 當作沒設，看板照常");
  await p.evaluate((k) => localStorage.removeItem(k), key);
  // 不在我的品項的（例：搜尋進來）不能設，告訴使用者去哪裡勾
  const other = await p.evaluate(() => { const w = new Set(watchCrops()); return availableCrops().find((c) => !w.has(c)) || null; });
  if (other) {
    await p.evaluate((c) => openDetail(c, null), other); await p.waitForTimeout(400);
    const t = (await p.textContent("#mktDetail .tgtbox").catch(() => "")) || "";
    check((await p.locator("#tgtIn").count()) === 0 && t.includes("我的品項"), `merchant 不在我的品項的「${other}」：不能設目標價、說去哪裡勾`);
  }
  check(errs.length === 0, `merchant 目標價沒有 JS 錯誤${errs.length ? "：" + errs.slice(0, 2).join(" | ") : ""}`);
  await p.close();
}

async function checkUpdateBar(ctx, url, name) {
  // 版本更新提醒：version.json 比較新才出現提示；一樣就不出現
  for (const newer of [true, false]) {
    const p = await ctx.newPage();
    const cur = JSON.parse(fs.readFileSync(path.join(ROOT, name === "merchant" ? "merchant" : "", "version.json"), "utf8")).v;
    await p.route("**/version.json", (r) => r.fulfill({ json: { v: newer ? "v9.9.9" : cur } }));
    await p.goto(url, { waitUntil: "networkidle" });
    await p.waitForTimeout(3600);
    const n = await p.locator("#updBar").count();
    check(newer ? n === 1 : n === 0, `${name} ${newer ? "有新版→出現更新提示" : "已是最新版→不出現提示"}`);
    if (newer && n) { check(await noHScroll(p), `${name} 更新提示沒有造成橫向捲動`); await p.screenshot({ path: path.join(OUT, `${name}-update-bar.png`) }); }
    await p.close();
  }
}

async function checkLazyVariants(page, name, json) {
  const before = json.filter(([, p]) => p.endsWith("_v.json")).length;
  check(before === 0, `${name} 首次載入沒有下載品種歷史（_v.json）`);
  const child = page.locator(".mc-child .mc-right:not(.nolink)");
  let opened = false;
  if (name === "merchant") {
    const exp = page.locator(".mcard .mc-left .mc-exp");
    if (await exp.count()) { await exp.first().click(); await page.waitForTimeout(400); }
  }
  if (await child.count()) { await child.first().click(); opened = true; }
  else {
    const varCard = page.locator(".mcard:has(.mc-var) .mc-r1");
    if (await varCard.count()) { await varCard.first().click(); opened = true; }
  }
  await page.waitForTimeout(1200);
  const after = json.filter(([, p]) => p.endsWith("_v.json")).length;
  check(opened && after >= 1, `${name} 點品種後才下載品種歷史（${after} 個 _v.json）`);
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
  if (SPLIT) await checkLazyVariants(f.page, "farmer", f.json);
  await f.page.goto(base, { waitUntil: "networkidle" });
  await checkBoardAndDetail(f.page, "farmer");
  await f.page.goto(base, { waitUntil: "networkidle" });
  await checkNewFeatures(f.page, "farmer", f.json, base);
  await f.page.goto(base, { waitUntil: "networkidle" });
  await checkNoJudgmentAndNames(f.page, "farmer");
  await checkYearStatsAndMonths(ctx, base, "farmer");
  await checkLargeFont(ctx, base, "farmer");
  await checkTyphoonMap(ctx, base, "farmer");
  await checkUpdateBar(ctx, base, "farmer");
  await checkMapPinch(ctx, base, "farmer");
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
  if (SPLIT) await checkLazyVariants(m.page, "merchant", m.json);
  await m.page.goto(base + "merchant/", { waitUntil: "networkidle" });
  await checkBoardAndDetail(m.page, "merchant");
  await m.page.goto(base + "merchant/", { waitUntil: "networkidle" });
  await checkNewFeatures(m.page, "merchant", m.json, base);
  await m.page.goto(base + "merchant/", { waitUntil: "networkidle" });
  await m.page.click('.tab[data-t="compare"]');
  await m.page.waitForTimeout(500);
  const cmp = await m.page.evaluate(() => [...document.querySelectorAll(".cmprow")].map((el) => ({
    cheap: el.classList.contains("cheap"), old: el.classList.contains("old"), date: ((el.querySelector("small") || {}).textContent || "").slice(0, 10) })));   // 小字＝日期＋價差說明，只取日期
  const newest = cmp.reduce((a, r) => (r.date > a ? r.date : a), "");
  const cheap = cmp.find((r) => r.cheap);
  check(cmp.length > 0 && !!cheap, `merchant 比價有排名且有「最便宜」（${cmp.length} 個市場）`);
  check(!cheap || (Date.parse(newest) - Date.parse(cheap.date)) / 864e5 <= 4, `merchant「最便宜」不是舊價（${cheap && cheap.date} vs 最新 ${newest}）`);
  check(cmp.filter((r) => r.old).every((r) => (Date.parse(newest) - Date.parse(r.date)) / 864e5 > 4), "merchant 標「舊價」的都超過 4 天沒成交");
  await m.page.goto(base + "merchant/", { waitUntil: "networkidle" });
  await checkNoJudgmentAndNames(m.page, "merchant");
  await checkLargeFont(ctx, base + "merchant/", "merchant");
  await checkTyphoonMap(ctx, base + "merchant/", "merchant");
  await checkWeatherTabs(ctx, base + "merchant/");
  await checkMerchantR4(ctx, browser, iphone, base + "merchant/");
  await checkUpdateBar(ctx, base + "merchant/", "merchant");
  await checkMapPinch(ctx, base + "merchant/", "merchant");
  await checkNavBack(ctx, base + "merchant/");
  await checkYearStatsAndMonths(ctx, base + "merchant/", "merchant");
  await checkTargetPrice(ctx, base + "merchant/");
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
