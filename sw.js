/* 精準務農 Service Worker — 讓純前端站台可安裝、可離線。
   策略：
   - HTML / JSON 資料：network-first（連得上就拿最新，離線退回上次快取；HTML 再退回 index.html 殼）。
   - 圖示等靜態資源：cache-first。
   改版時把 CACHE 版本號 +1 即可讓舊快取失效（activate 會清掉非當前版本）。 */
const CACHE = "jingzhun-farm-v24";
const SHELL = ["./", "./index.html", "./core.js", "./privacy.html", "./terms.html", "./manifest.json",
               "./icon-192.png", "./icon-512.png", "./icon-512-maskable.png", "./apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE)
      // 逐檔、一個接一個快取：單一檔失敗不會像 addAll 那樣整批失敗、導致殼快取全空（白畫面主因之一）；
      // 依序下載不一次併發 9 個請求（手機網路差時較不會整批逾時）
      .then((c) => SHELL.reduce((p, u) => p.then(() => c.add(u).catch(() => {})), Promise.resolve()))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      // 只清自己這支 App 的舊快取：精準務農/精準進貨同網域共用 Cache Storage，不可互刪
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE && k.indexOf("jingzhun-farm-") === 0).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;   // 只管同源；第三方(如地圖磚)直接放行
  if (url.pathname.indexOf(new URL("merchant/", self.registration.scope).pathname) === 0) return;   // /merchant/ 是精準進貨，交給它自己的 SW

  const isHTML = req.mode === "navigate" || (req.headers.get("accept") || "").includes("text/html");
  const isJSON = url.pathname.endsWith(".json");

  if (isHTML) {
    // 頁面殼：stale-while-revalidate —— 有快取就「立即」回快取（回到 App 不再白畫面），
    // 同時背景抓最新更新快取；沒快取才等網路，網路也失敗才退回 index.html 殼。
    e.respondWith(
      caches.match(req).then((cached) => {
        const network = fetch(req)
          .then((res) => {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
            return res;
          })
          .catch(() => cached || caches.match("./index.html"));
        return cached || network;
      })
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
