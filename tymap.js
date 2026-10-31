/* tymap.js —— 颱風路徑地圖（精準務農、精準進貨共用；merchant/tymap.js 必須跟這份一模一樣，測試會比對）
 *
 * 2026-10-31 依使用者要求改版，參考他另一支颱風 App（k7m2q）：
 *   - 底圖改成彩色地圖（OpenStreetMap），不再用手畫的粗略海岸線。
 *   - 只放需要的資料：已發生路徑、目前位置與 7／10 級暴風圈、預報路徑、70% 機率範圍、你的位置。
 *     不放模式系集、風場流線、落雷（非官方或用不到）。
 *   - 預報路徑改成藍色虛線＋白邊（原本粉紅色虛線在底圖上看不清楚）。
 *   - 預報點只標每 24 小時一次的時間（原本每隔一點就標，字疊在一起）。
 * Leaflet 1.9.4（BSD-2，vendor/leaflet/）打開颱風詳情才載入，平常不佔流量；載入失敗或離線時顯示文字說明。
 */
(function (root) {
  "use strict";
  var loading = null, current = null;
  var COLOR = { past: "#334155", fc: "#1d4ed8", fcCase: "#ffffff", now: "#dc2626", r15: "#f97316", r25: "#dc2626",
    cone: "#f59e0b", me: "#16a34a" };
  // 蒲福風級門檻（m/s，中央氣象署 0～17 級）：45 m/s → 14 級
  var BF = [0.3, 1.6, 3.4, 5.5, 8.0, 10.8, 13.9, 17.2, 20.8, 24.5, 28.5, 32.7, 37.0, 41.5, 46.2, 51.0, 56.1];

  function beaufort(ms) { ms = Number(ms); if (!(ms >= 0)) return null; var n = 0; while (n < BF.length && ms >= BF[n]) n++; return n; }
  function category(ms) { ms = Number(ms); if (!(ms > 0)) return ""; return ms >= 51 ? "強烈颱風" : ms >= 32.7 ? "中度颱風" : ms >= 17.2 ? "輕度颱風" : "熱帶性低氣壓"; }
  function fmtTime(iso) { var m = String(iso || "").match(/(\d{4})-(\d{2})-(\d{2})T(\d{2})/); return m ? (+m[2]) + "/" + (+m[3]) + " " + m[4] + "時" : ""; }
  var DIR = { N: "北", NNE: "北北東", NE: "東北", ENE: "東北東", E: "東", ESE: "東南東", SE: "東南", SSE: "南南東", S: "南",
    SSW: "南南西", SW: "西南", WSW: "西南西", W: "西", WNW: "西北西", NW: "西北", NNW: "北北西" };
  function num(x) { var v = Number(x); return isFinite(v) ? v : null; }
  function pt(p) { return p && num(p.lat) !== null && num(p.lng) !== null ? [num(p.lat), num(p.lng)] : null; }

  function loadLeaflet() {
    if (root.L) return Promise.resolve(root.L);
    if (loading) return loading;
    loading = new Promise(function (ok, bad) {
      var css = document.createElement("link"); css.rel = "stylesheet"; css.href = "vendor/leaflet/leaflet.css";
      document.head.appendChild(css);
      var s = document.createElement("script"); s.src = "vendor/leaflet/leaflet.js";
      s.onload = function () { root.L ? ok(root.L) : bad(new Error("Leaflet 沒有載入")); };
      s.onerror = function () { loading = null; bad(new Error("Leaflet 載入失敗")); };
      document.head.appendChild(s);
    });
    return loading;
  }

  // 70% 機率範圍＝每兩個相鄰預報點的圓（半徑 r70）包起來的凸包，連成一條帶狀（跟氣象署潛勢圖同一種畫法）
  function circlePts(lat, lng, km, n) {
    if (!(km > 0)) return [[lat, lng]];
    var out = [], dLat = km / 111, dLng = km / (111 * Math.cos(lat * Math.PI / 180));
    for (var i = 0; i < n; i++) { var a = 2 * Math.PI * i / n; out.push([lat + dLat * Math.sin(a), lng + dLng * Math.cos(a)]); }
    return out;
  }
  function hull(points) {   // Andrew's monotone chain；回傳逆時針（x＝經度、y＝緯度）
    var p = points.slice().sort(function (a, b) { return a[1] - b[1] || a[0] - b[0]; });
    if (p.length < 3) return p;
    var cross = function (o, a, b) { return (a[1] - o[1]) * (b[0] - o[0]) - (a[0] - o[0]) * (b[1] - o[1]); };
    var lower = [], upper = [], i;
    for (i = 0; i < p.length; i++) { while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p[i]) <= 0) lower.pop(); lower.push(p[i]); }
    for (i = p.length - 1; i >= 0; i--) { while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p[i]) <= 0) upper.pop(); upper.push(p[i]); }
    upper.pop(); lower.pop();
    return lower.concat(upper);
  }
  function cone(seq) {   // seq：[{lat,lng,r}]（第一點＝目前位置，r＝0）
    var polys = [];
    for (var i = 0; i + 1 < seq.length; i++) {
      var a = seq[i], b = seq[i + 1];
      var h = hull(circlePts(a.lat, a.lng, a.r, 24).concat(circlePts(b.lat, b.lng, b.r, 24)));
      if (h.length >= 3) polys.push([h]);
    }
    return polys;
  }

  function infoText(w, ref) {
    var n = w.now || (w.track && w.track[0]) || {};
    var wind = num(n.wind), parts = [];
    if (n.t) parts.push(fmtTime(n.t) + (w.now ? " 觀測" : " 預報"));
    if (wind) { var lv = beaufort(wind); parts.push(category(wind) + (lv ? "（" + lv + " 級）" : "") + "，最大風速 " + Math.round(wind) + " m/s"); }
    if (num(n.pres)) parts.push("中心氣壓 " + Math.round(n.pres) + " hPa");
    if (n.dir) parts.push("向" + (DIR[n.dir] || n.dir) + (num(n.spd) ? " " + Math.round(n.spd) + " km/h" : ""));
    return parts.join(" · ");
  }

  function legendHTML() {
    var it = function (sw, txt) { return '<span class="tylg"><i style="' + sw + '"></i>' + txt + "</span>"; };
    return '<div class="tylegend">' +
      it("border-top:3px solid " + COLOR.past, "已發生路徑") +
      it("border-top:3px dashed " + COLOR.fc + ";box-shadow:0 0 0 1px #fff", "預報路徑") +
      it("background:" + COLOR.now + ";border-radius:50%;width:.7em;height:.7em", "目前位置") +
      it("border:2px dashed " + COLOR.r15 + ";border-radius:50%;width:.8em;height:.8em", "7 級暴風圈") +
      it("border:2px solid " + COLOR.r25 + ";border-radius:50%;width:.8em;height:.8em", "10 級暴風圈") +
      it("background:rgba(245,158,11,.35);width:1em;height:.7em", "70% 機率範圍") +
      it("background:" + COLOR.me + ";border-radius:50%;width:.7em;height:.7em", "你的位置") + "</div>";
  }

  // 標籤擺放：颱風離台灣很遠、走得又慢時，預報點在畫面上擠成一團，日期字會疊在一起（舊圖的問題）。
  // 依重要性排（你的位置 > 颱風目前位置 > 最後一個預報點 > 其他依時間），每個標籤試幾個方向，
  // 跟已擺好的框重疊或超出地圖就換方向；預報日期都放不下就不顯示。縮放後重排。
  function textW(t) { var w = 12; for (var i = 0; i < t.length; i++) w += t.charCodeAt(i) > 0x2e80 ? 11.5 : 6.6; return w; }
  function placeLabels(L, map, specs) {
    (map._tyLabels || []).forEach(function (t) { map.removeLayer(t); });
    var size = map.getSize(), placed = [], out = [];
    var box = function (p, dir, w) {
      var h = 18;
      return dir === "right" ? [p.x + 8, p.y - h / 2, w, h] : dir === "left" ? [p.x - 8 - w, p.y - h / 2, w, h]
        : dir === "bottom" ? [p.x - w / 2, p.y + 9, w, h] : [p.x - w / 2, p.y - 9 - h, w, h];
    };
    var hit = function (b) {
      if (b[0] < 2 || b[1] < 2 || b[0] + b[2] > size.x - 2 || b[1] + b[3] > size.y - 2) return true;
      return placed.some(function (q) { return b[0] < q[0] + q[2] + 3 && q[0] < b[0] + b[2] + 3 && b[1] < q[1] + q[3] + 2 && q[1] < b[1] + b[3] + 2; });
    };
    specs.slice().sort(function (a, b) { return b.prio - a.prio; }).forEach(function (sp) {
      var p = map.latLngToContainerPoint(sp.ll), w = textW(sp.text);
      for (var i = 0; i < sp.dirs.length; i++) {
        var b = box(p, sp.dirs[i], w);
        if (!hit(b) || (sp.must && i === sp.dirs.length - 1)) {
          placed.push(b);
          var off = { right: [8, 0], left: [-8, 0], bottom: [0, 9], top: [0, -9] }[sp.dirs[i]];
          out.push(L.tooltip({ permanent: true, direction: sp.dirs[i], offset: off, className: "ty-lbl " + (sp.cls || "") })
            .setLatLng(sp.ll).setContent(sp.text).addTo(map));
          break;
        }
      }
    });
    map._tyLabels = out;
  }

  function draw(L, el, w, ref) {
    destroy();
    var labels = [];
    var map = L.map(el, { zoomControl: true, attributionControl: true, worldCopyJump: false, minZoom: 3, maxZoom: 9 });
    current = map;
    map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>');
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 9,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> 貢獻者｜颱風：中央氣象署' }).addTo(map);
    var bounds = [];
    var past = (w.past || []).map(pt).filter(Boolean);
    var now = pt(w.now) || pt((w.track || [])[0]);
    var fc = (w.track || []).filter(function (p) { return pt(p); });
    // 已發生路徑：深灰實線＋小白點
    if (past.length >= 2) L.polyline(past, { color: COLOR.past, weight: 3, opacity: 0.9 }).addTo(map);
    past.forEach(function (p) { L.circleMarker(p, { radius: 2.5, color: COLOR.past, weight: 1, fillColor: "#fff", fillOpacity: 1 }).addTo(map); });
    bounds = bounds.concat(past.slice(-12));
    // 70% 機率範圍（先畫在底下）
    if (now && fc.length) {
      var seq = [{ lat: now[0], lng: now[1], r: 0 }].concat(fc.map(function (p) { return { lat: +p.lat, lng: +p.lng, r: num(p.r70) || 0 }; }));
      var polys = cone(seq);
      // 只填色不描邊：每段凸包各自的邊線疊在一起，中間會出現一堆虛線（nonzero＝重疊處不會挖空）
      if (polys.length) L.polygon(polys, { stroke: false, fillColor: COLOR.cone, fillOpacity: 0.28, fillRule: "nonzero" }).addTo(map);
    }
    // 預報路徑：白邊＋藍色虛線（在海上、陸上都看得清楚）
    if (fc.length) {
      var line = (now ? [now] : []).concat(fc.map(pt));
      L.polyline(line, { color: COLOR.fcCase, weight: 6, opacity: 0.9 }).addTo(map);
      L.polyline(line, { color: COLOR.fc, weight: 3, dashArray: "8 6", opacity: 1 }).addTo(map);
      var t0 = w.now && w.now.t ? Date.parse(w.now.t) : (fc[0].t ? Date.parse(fc[0].t) : null);
      fc.forEach(function (p, i) {
        var ll = pt(p), last = i === fc.length - 1;
        L.circleMarker(ll, { radius: 4, color: "#fff", weight: 1.5, fillColor: COLOR.fc, fillOpacity: 1 }).addTo(map);
        var h = (t0 && p.t) ? Math.round((Date.parse(p.t) - t0) / 3600000) : null;
        if (p.t && (last || (h !== null && h > 0 && h % 24 === 0)))   // 只標每 24 小時＋最後一點
          labels.push({ ll: ll, text: fmtTime(p.t), dirs: ["right", "left", "top", "bottom"], prio: last ? 80 : 70 - i });
        bounds.push(ll);
      });
    }
    // 目前位置＋暴風圈
    if (now) {
      var cur = w.now || fc[0] || {};
      if (num(cur.r15)) L.circle(now, { radius: cur.r15 * 1000, color: COLOR.r15, weight: 2, dashArray: "6 5", fillColor: COLOR.r15, fillOpacity: 0.1 }).addTo(map);
      if (num(cur.r25)) L.circle(now, { radius: cur.r25 * 1000, color: COLOR.r25, weight: 2, fillColor: COLOR.r25, fillOpacity: 0.12 }).addTo(map);
      L.circleMarker(now, { radius: 7, color: "#fff", weight: 2, fillColor: COLOR.now, fillOpacity: 1 }).addTo(map);
      labels.push({ ll: now, text: "🌀 " + (w.name || "颱風") + "（現在）", cls: "ty-now", dirs: ["bottom", "left", "top", "right"], prio: 90, must: true });
      bounds.push(now);
      if (num(cur.r15)) { var dl = cur.r15 / 111; bounds.push([now[0] + dl, now[1]], [now[0] - dl, now[1]]); }
    }
    // 你的位置
    if (ref && num(ref.lat) !== null) {
      var me = [ref.lat, ref.lng];
      L.circleMarker(me, { radius: 7, color: "#fff", weight: 2, fillColor: COLOR.me, fillOpacity: 1 }).addTo(map);
      labels.push({ ll: me, text: ref.name || "你的位置", cls: "ty-me", dirs: ["left", "right", "top", "bottom"], prio: 100, must: true });
      bounds.push(me);
    }
    if (bounds.length) map.fitBounds(bounds, { padding: [28, 28], maxZoom: 7 });
    else map.setView([23.7, 120.9], 5);
    placeLabels(L, map, labels);
    map.on("zoomend", function () { placeLabels(L, map, labels); });
    return map;
  }

  function destroy() { if (current) { try { current.remove(); } catch (e) { /* 已移除 */ } current = null; } }

  /* el：放地圖的 div；w：typhoon_status.json；ref：{lat,lng,name}（使用者所在縣市）。回傳 Promise（失敗不丟例外）。 */
  function render(el, w, ref) {
    if (!el) return Promise.resolve(false);
    var has = (w && ((w.track && w.track.length) || w.now));
    if (!has) { el.outerHTML = '<div class="tynote">目前沒有路徑資料（颱風可能剛生成或已遠離）。可用下方連結看官方即時路徑。</div>'; return Promise.resolve(false); }
    var info = el.parentNode && el.parentNode.querySelector(".tyinfo");
    if (info) info.textContent = infoText(w, ref);
    return loadLeaflet().then(function (L) { draw(L, el, w, ref); return true; }).catch(function () {
      el.innerHTML = '<div class="tynote">地圖載入失敗（可能沒有網路）。路徑資訊請看上方文字或下方官方連結。</div>';
      return false;
    });
  }

  root.TyMap = { render: render, destroy: destroy, legendHTML: legendHTML, infoText: infoText, beaufort: beaufort,
    category: category, cone: cone, hull: hull, fmtTime: fmtTime };
})(typeof window !== "undefined" ? window : this);
