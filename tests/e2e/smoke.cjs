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
  await checkLargeFont(ctx, base, "farmer");
  await checkTyphoonMap(ctx, base, "farmer");
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
