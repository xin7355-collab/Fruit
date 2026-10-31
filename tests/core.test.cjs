// core.js 單元測試（Node 內建測試器）：node --test tests/core.test.cjs
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const ROOT = path.resolve(__dirname, "..");
const C = require(path.join(ROOT, "core.js"));

test("兩支 App 的 core.js 內容一致", () => {
  assert.equal(fs.readFileSync(path.join(ROOT, "core.js"), "utf8"), fs.readFileSync(path.join(ROOT, "merchant", "core.js"), "utf8"));
});

test("節日：重現舊版寫死的 2025–2026 節日，且之後年份照樣算得出來", () => {
  const f = Object.fromEntries(C.festivals(2025, 2027).map(([d, n]) => [d, n]));
  const old = [["2025-01-29","春節"],["2025-04-04","清明"],["2025-05-31","端午"],["2025-10-06","中秋"],
               ["2026-02-17","春節"],["2026-04-05","清明"],["2026-06-19","端午"],["2026-09-25","中秋"]];
  for (const [d, n] of old) assert.equal(f[d], n, `${d} 應為 ${n}`);
  assert.equal(f["2027-02-06"], "春節"); assert.equal(f["2027-06-09"], "端午"); assert.equal(f["2027-09-15"], "中秋");
  assert.equal(f["2026-12-22"], "冬至"); assert.equal(f["2027-04-05"], "清明"); assert.equal(f["2025-12-21"], "冬至");
  assert.equal(f["2026-08-27"], "中元");
  assert.ok(C.festivals(2020, 2035).length >= 16 * 6, "2020–2035 每年 6 個節日");
});

test("農曆：閏月與一般日期", () => {
  assert.equal(C.toLunar("2026-02-17").label, "1/1");
  assert.equal(C.toLunar("2025-10-06").label, "8/15");
  assert.equal(C.toLunar("2025-07-25").label, "閏6/1");
  assert.equal(C.toLunar("2019-12-31"), null);
  // 閏月的下一個月不可再標「閏」（舊版 bug）
  assert.equal(C.toLunar("2025-08-23").label, "7/1");
  assert.equal(C.toLunar("2025-09-06").label, "7/15");
  assert.equal(C.toLunar("2020-06-25").label, "5/5");
  assert.equal(C.toLunar("2020-05-23").label, "閏4/1");
});

test("採收預判：同週前後 2 週；不足退回近 90 筆", () => {
  const s = {};
  for (let i = 0; i < 400; i++) { const d = C.addDays("2025-01-01", i); s[d] = { avg: d.slice(5, 7) === "03" ? 50 : 10 }; }
  const p = C.predictPrice(s, "2026-03-15");
  assert.equal(p.same, true); assert.equal(p.med, 50);
  const q = C.predictPrice({ "2026-01-01": { avg: 5 }, "2026-01-02": { avg: 6 }, "2026-01-03": { avg: 7 }, "2026-01-04": { avg: 8 }, "2026-01-05": { avg: 9 } }, "2026-07-01");
  assert.equal(q.same, false); assert.equal(q.med, 7);
  assert.equal(C.predictPrice({}, "2026-07-01"), null);
});

test("俗名：高麗菜找得到甘藍；批發名本身也找得到", () => {
  assert.equal(C.nameHit("甘藍", "高麗"), "高麗菜");
  assert.equal(C.nameHit("甘藍", "甘藍"), "");
  assert.equal(C.nameHit("番石榴", "芭樂"), "芭樂");
  assert.equal(C.nameHit("甘藍", "西瓜"), null);
  assert.ok(C.nameStarts("蕹菜", "空心"));
  for (const t of Object.values(C.ALIASES)) assert.ok(t.length > 0);
});

test("名稱旁的常用俗名：每個俗名都要搜得到同一個批發名（顯示跟搜尋一致）", () => {
  assert.equal(C.commonOf("番石榴"), "芭樂");
  assert.equal(C.commonOf("甘藍"), "高麗菜");
  assert.equal(C.commonOf("青蔥"), null);          // 批發名本身就是常用名：不加
  assert.equal(C.commonOf("constructor"), null);   // 不會撈到物件內建屬性
  for (const [w, a] of Object.entries(C.COMMON)) {
    const hit = C.nameHit(w, a);
    assert.ok(hit !== null, `${w}(${a})：搜尋「${a}」要找得到 ${w}`);
  }
});

test("開價檢查：農友（扣 6% 手續費）與商人", () => {
  const day = { avg: 25, high: 30, mid: 25, low: 20 };
  assert.equal(C.judgeOffer(15, "kg", day, "farmer").level, "bad");          // < 20×0.94
  assert.equal(C.judgeOffer(24, "kg", day, "farmer").level, "ok");           // ≥ 25×0.94
  assert.equal(C.judgeOffer(12, "jin", day, "merchant").level, "good");      // 12/0.6=20 ≤ 20
  assert.equal(C.judgeOffer(18, "jin", day, "merchant").level, "warn");      // 18/0.6＝30，剛好等於上價 → 偏貴
  assert.equal(C.judgeOffer(19, "jin", day, "merchant").level, "bad");       // 31.7 > 上價 30 → 太貴
  assert.equal(C.judgeOffer(0, "kg", day, "merchant"), null);
});

test("12 個月價格指數與去年同期平移", () => {
  const s = {};
  for (let i = 0; i < 730; i++) { const d = C.addDays("2024-11-01", i); s[d] = { avg: d.slice(5, 7) === "07" ? 40 : 20 }; }
  const mi = C.monthIndex(s);
  assert.equal(mi[6].idx, 2); assert.equal(mi[0].idx, 1);
  const ly = C.lastYearSeries({ "2024-02-29": { avg: 1 }, "2025-10-30": { avg: 2 } }, "2025-01-01", "2026-12-31");
  assert.deepEqual(Object.keys(ly).sort(), ["2025-02-28", "2026-10-30"]);
});

// ── tymap.js（颱風地圖，兩支 App 共用） ──
const TY = require(path.join(ROOT, "tymap.js")).TyMap;

test("兩支 App 的 tymap.js、vendor/leaflet 內容一致", () => {
  for (const f of ["tymap.js", "vendor/leaflet/leaflet.js", "vendor/leaflet/leaflet.css"])
    assert.equal(fs.readFileSync(path.join(ROOT, f), "utf8"), fs.readFileSync(path.join(ROOT, "merchant", f), "utf8"), f);
});

test("颱風地圖：風級與強度（中央氣象署蒲福風級）", () => {
  assert.equal(TY.beaufort(45), 14);        // 煙花 2026-10-31：最大風速 45 m/s＝14 級
  assert.equal(TY.beaufort(15), 7);         // 7 級風＝暴風圈（Circle15ms）
  assert.equal(TY.beaufort(25), 10);        // 10 級風（Circle25ms）
  assert.equal(TY.beaufort(0.1), 0);
  assert.equal(TY.beaufort("x"), null);
  assert.equal(TY.category(45), "中度颱風");
  assert.equal(TY.category(55), "強烈颱風");
  assert.equal(TY.category(20), "輕度颱風");
  assert.equal(TY.fmtTime("2026-11-01T14:00:00+08:00"), "11/1 14時");
});

test("颱風地圖：70% 機率範圍＝相鄰兩圓的凸包、方向一致（重疊處填色不會被挖空）", () => {
  const sq = TY.hull([[0, 0], [0, 1], [1, 1], [1, 0], [0.5, 0.5]]);
  assert.equal(sq.length, 4);               // 中間的點不在外框上
  const area = (r) => r.reduce((s, p, i) => { const q = r[(i + 1) % r.length]; return s + p[1] * q[0] - q[1] * p[0]; }, 0) / 2;
  const polys = TY.cone([{ lat: 22.2, lng: 142.9, r: 0 }, { lat: 22.8, lng: 143.3, r: 30 }, { lat: 23.5, lng: 143.6, r: 60 }]);
  assert.equal(polys.length, 2);
  for (const [ring] of polys) assert.ok(area(ring) > 0, "每塊都是逆時針（x＝經度、y＝緯度）");
});

test("version.json 跟 index.html 的 VERSION 一致、CHANGELOG 最上面是這一版（不一致就不會跳更新提醒或會一直跳）", () => {
  for (const dir of [ROOT, path.join(ROOT, "merchant")]) {
    const html = fs.readFileSync(path.join(dir, "index.html"), "utf8");
    const ver = html.match(/const VERSION="(v[\d.]+)"/)[1];
    assert.equal(JSON.parse(fs.readFileSync(path.join(dir, "version.json"), "utf8")).v, ver, dir);
    assert.equal(html.match(/const CHANGELOG=\[\s*\{v:"(v[\d.]+)"/)[1], ver, dir);
  }
});
