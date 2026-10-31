/* 精準進貨 Service Worker — 讓純前端站台可安裝、可離線。
   策略：
   - HTML / JSON 資料：network-first（連得上就拿最新，離線退回上次快取；HTML 再退回 index.html 殼）。
   - 圖示等靜態資源：cache-first。
   改版時把 CACHE 版本號 +1 即可讓舊快取失效（activate 會清掉非當前版本）。 */
const CACHE = "jingzhun-buy-v10";
const SHELL = ["./", "./index.html", "./core.js", "./privacy.html", "./terms.html", "./manifest.json",
               "./icon-192.png", "./icon-512.png", "./icon-maskable-512.png", "./apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE)
      // 逐檔快取：單一檔失敗不會像 addAll 那樣整批失敗、導致殼快取全空（白畫面主因之一）
      .then((c) => Promise.all(SHELL.map((u) => c.add(u).catch(() => {}))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      // 只清自己這支 App 的舊快取：精準務農/精準進貨同網域共用 Cache Storage，不可互刪
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE && k.indexOf("jingzhun-buy-") === 0).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;   // 只管同源；第三方(如地圖磚)直接放行

  const isHTML = req.mode === "navigate" || (req.headers.get("accept") || "").includes("text/html");
  const isJSON = url.pathname.endsWith(".json");

  if (isHTML) {
    // 頁面殼：network-first —— 連得上一律拿最新（改版立即可見），
    // 更新快取；離線／網路失敗才退回上次快取殼，避免白畫面。
    e.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
          return res;
        })
        .catch(() => caches.match(req).then((c) => c || caches.match("./index.html")))
    );
  } else if (isJSON) {
    // 資料：network-first（要最新行情/天氣），離線退回上次快取
    e.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
          return res;
        })
        .catch(() => caches.match(req))
    );
  } else {
    // cache-first
    e.respondWith(
      caches.match(req).then((r) =>
        r || fetch(req).then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
          return res;
        })
      )
    );
  }
});
