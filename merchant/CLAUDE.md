# CLAUDE.md — 精準進貨（全台蔬果批發行情＋跨市場比價，商人版）

給未來 Claude 工作階段的專案指引。

## ⚑ 部署位置（2026-10-31 起，repo `xin7355-collab/Fruit`）

本 repo 同時放兩支 App（由 2026-08-08 備份還原）：

- **根目錄＝精準務農（farmer）** → https://xin7355-collab.github.io/Fruit/
- **`merchant/`＝精準進貨（merchant）** → https://xin7355-collab.github.io/Fruit/merchant/
  - 排程在根目錄 `.github/workflows/merchant-*.yml`（`defaults.run.working-directory: merchant`，cron 與 farmer 錯開）。
  - `resolveGhBase()` 已改成取「目前頁面所在目錄」，否則會讀到根目錄 farmer 的 JSON。
  - 兩支同網域 → localStorage / Cache Storage 共用：merchant 改用 `jb_` 前綴（匯入舊 `vs_` 備份自動轉換）；
    兩個 `sw.js` 只清自己前綴的快取，farmer SW 不攔截 `/merchant/`。**不要改回去。**
  - **行情檔切分（2026-10-31）**：`prices/NN.json`＝本月 1 號前的歷史（同月內逐位元不變、不含 updated），
    `prices/NN_recent.json`＝本月逐日（小檔每日更新）；index 的 `markets[].recent` 指向當月檔，前端 `loadMarket`
    並行抓兩檔用 `mergeSeries` 合併。檔號沿用舊 index 的「市場名→檔名」不再依排序跳號。目的：止住 git 歷史每天暴增。
  - 天氣排程兩支都有「預報抓成空就還原、不覆蓋」守門（未設 `CWA_API_KEY` 時保護既有資料）。
  - **農業部 API 單次最多回 9999 筆**（回滿＝被截斷，API 不報錯）：`fetch_market_all` 回滿就整批丟棄、縮短區段重抓
    （2026-10-31 前大市場每 91 天只留最後 3～5 週，台中 2 年只剩 215 個交易日）。改抓法時務必保留這個偵測與測試。
  - 抓資料一律走 `scripts/http_retry.py`（2/4/8/16 秒指數退避；兩份內容須一致，測試會比對）。某市場抓不完整
    → 不覆蓋、沿用上次 index 條目；全部失敗 → 不寫檔 exit 1；連續 3 市場失敗熔斷。`notify.py` 刻意不重試（避免重複推播）。
  - 前端 JSON 一律 `cache:"no-cache"`（ETag 304，不要改回 no-store：每次開 App 會重抓 3～5MB）。
  - 精準進貨比價只採計最新成交日前 `CMP_FRESH_DAYS`(4) 天內的報價，舊價排最後標「舊價」。
- **2026-10-31 起共用**：`merchant/core.js`（＝根目錄 core.js，前端共用演算法）、`merchant/scripts/price_core.py`（＝scripts/price_core.py，
  行情三檔格式 NN.json／NN_v.json／NN_recent.json＋看板摘要 sum、增量抓取）。兩份內容須一致（測試會比對），改一邊就複製到另一邊。
  專案工作守則（授權、隱私、做事方式）以根目錄 `CLAUDE.md` 為準。
- **測試（改程式後必跑）**：`pip install -r requirements-dev.txt && python -m pytest -q tests/`（離線、兩支 App 都測）；
  `node tests/e2e/smoke.cjs`（iPhone 13 尺寸冒煙，截圖在 test-results/）。CI：`.github/workflows/test.yml`。
- **技能**：`.claude/skills/` 由 skl 的 app-bootstrap 安裝（清單見 `.claude/skills/.skl-vendor.json`）；
  更新：`python3 /tmp/skl/xin-toolkit/skills/app-bootstrap/scripts/bootstrap_app.py update --target .`。

## ⚑ 這個 repo 是「精準進貨」商人版專用

原本一份程式碼用「蔬果農／商人」角色切換，分成兩支獨立 App：

- **精準進貨（merchant）** — 就是這個 repo／Pages。批發行情看板＋跨市場比價找便宜進貨。
- **精準務農（farmer）** — 另一支獨立 App（行情＋田區搶收＋採收預判），在別的 repo。

**2026-07 起，本 repo 已把農夫版（田區搶收／採收預判）相關程式與資料整批移除**，成為商人版專用：

- 移除的檔案：`scripts/build_advisory.py`、`scripts/notify.py`、`scripts/fetch_yield.py`、
  `harvest_advisory.json`、`yield_index.json`、`veg_prices.json`、`data/`（田區登記表）。
- `index.html` 移除的功能：田區搶收分頁、採收預判分頁、我的田區設定、農藝參數（生育日數/PHI/產量係數/
  決策模型 computeField）、身分選擇 onboarding、角色切換。
- 保留並改為商人取向：颱風警戒卡＋颱風詳情 modal（侵襲率/路徑圖改依「所在縣市」，預設台中，不再寫死雲林）、
  擺攤指數天氣建議、進貨成本註記。

`index.html` 頂端仍有 `const APP="merchant"` 與 `APP_NAME="精準進貨"`（歷史沿革；已無 farmer 分支）。

## 這是什麼

全台蔬果商人的進貨決策站台（純前端、GitHub Pages）：

1. **行情看板** — 讀全台各果菜市場批發行情，畫價量走勢；附進貨成本估、擺攤天氣指數、颱風警戒。
2. **跨市場比價** — 同一品項比全台各市場批發價（低→高），標出最便宜的市場、方便找便宜進貨。

## 架構原則

- **純前端 + 後端排程快取**：`index.html` 不含任何金鑰，只讀後端排程產出的靜態 JSON。
  抓不到時一律退回示範資料、絕不白畫面（`loadIndex` / `loadMarket` / `loadForecast` /
  `loadWeather` / `loadEvents` 都有 shape 檢查 + try/catch，失敗回 demo 或空物件）。
- **CWA API 需授權金鑰、CORS 不保證** → 金鑰只放後端（`CWA_API_KEY` 環境變數 /
  GitHub Actions Secret），前端永遠不觸碰。
- **動態 `ghBase`**：`resolveGhBase()` 在 `*.github.io` 下解析出 repo 根，前端由此讀 JSON。

## 預設值（商人版）

`index.html` 的 `S`（localStorage 設定）預設：

- **字體小**（`S.fs` 預設 `"s"`；選項 s/m/l/xl）。
- **果菜市場台中**（`S.market` 預設 `"台中"`；啟動時若該名不在 `INDEX.markets` 才退回第一個市場）。
- **天氣預報地區台中市**（`S.region` 預設 `"台中市"`；供擺攤指數／颱風侵襲率，`activeCounty()` 讀它）。

皆可在右上 ⚙️ 設定更改；改了存 localStorage。

## 檔案結構

```
index.html                    單頁兩分頁：行情看板／跨市場比價(compare)。字體 s/m/l/xl、公斤/台斤(1台斤=0.6kg)。
                              行情看板為「大卡列表→點進去詳細頁」：詳細頁含 K線走勢(日/週/月·2年·查價線
                              ＋Y軸最高/最低刻度隨單位換算)＋「農產品交易行情」逐日明細卡(均價/上/中/下價/
                              漲跌/成交量/農曆)。看板頂有搜尋框(跨市場全部品項)＋市場標示；預設看板優先列
                              常見葉菜(COMMON_VEG)再補量大者。進貨成本註記=拍賣均價×(1+承銷手續費估6%)。
                              颱風警戒卡可點開「颱風詳情」modal：自氣象署逐時軌跡繪相對「所在縣市」的路徑圖
                              (暴風圈；REF/標籤用 activeCounty()＝S.region，預設台中)＋windy 雷達 iframe。
                              有品種的作物(如鳳梨)點卡片先下拉選品種再進 K線。品項圖示：蔬菜用 emoji、
                              水果用放大鏡(SEARCH_SVG)。點任一品項圖示開「說明」彈窗(挑選要訣＋Google 查圖)。
                              擺攤指數：weatherAdviceHTML() 依 weather_forecast.json 該縣市今明降雨機率給
                              佳/普通/不佳＋提醒。全站兩指縮放放大檢視、放開回彈(pageshow 清殘留 transform)。
                              設定分四類(顯示設定/行情看板/天氣/資料備份/關於)；我的品項蔬菜·水果各自
                              「全選/取消全選」＋計數。天氣預報地區為 22 縣市選單(S.region)。資料備份可匯出/
                              匯入所有 vs_* 為單一 .json(collectBackup/importData)。關於 App(意見回饋/FAQ/
                              隱私權/使用條款/分享)。PWA：manifest+sw+icon，可加到主畫面、離線可用。
                              讀 prices_index.json + prices/NN.json / typhoon_status.json /
                              weather_events.json / weather_forecast.json，全部可退回 demo。
privacy.html / terms.html     隱私權政策／使用條款靜態頁(上架必備)。誠實：無帳號、不收個資、
                              資料採政府公開資料、比價僅供參考。聯絡 xin7355@gmail.com。
scripts/
  fetch_prices.py             抓農業部「農產品交易行情」(免金鑰,近 2 年)→ prices_index.json
                              (markets/crop_map/categories/latest/latest_var)＋ prices/NN.json(data + variants)。
                              逐市場一次 query 拿回所有成交品項，自動分蔬菜/水果、濾花卉/休市/0 元。
                              作物名以 norm_crop() 收斂到母作物、量加權合併；品種逐日序列存 variants 供前端
                              「點品種看它自己 K線」。--probe <市場> 可 dump 原始欄位(prices_probe.json)。
  fetch_cwa.py                抓 CWA 颱風/路徑潛勢/侵襲機率/雨量/氣溫 → typhoon_status.json；逐日累積真實
                              天氣事件 → weather_events.json（颱風/大雨/低溫，K線標記用）；抓 F-C0032-001
                              (全國各縣市今明36小時)→ weather_forecast.json，供擺攤指數。
  requirements.txt            requests
.github/workflows/
  update-data.yml             天氣/颱風每 30 分：fetch_cwa → commit(typhoon_status/weather_events/
                              weather_forecast/cwa_debug)。輕量、快。
  update-prices.yml           批發行情每日 09/14 時(台灣)：fetch_prices → commit(prices_index/prices)。
                              抓 2 年較慢(~15 分)，故與天氣拆開、互不阻塞。
  ci.yml                      PR 檢查：py_compile + fetch_cwa 無金鑰安全狀態 + 前端關鍵元素(view-market/
                              view-compare/loadIndex/loadWeather/loadForecast)。
  probe.yml                   手動觸發：探測某市場行情 API 原始欄位(prices_probe.json)。
（部署）                        採 GitHub 內建「Deploy from a branch: main」(pages-build-deployment 自動觸發)。
.env.example                  CWA_API_KEY 範例
```

前端會讀取（不存在時退回 demo）：`prices_index.json` + `prices/NN.json`（行情，逐市場 lazy load）、
`typhoon_status.json`（颱風現況）、`weather_events.json`（K線天氣事件標記）、
`weather_forecast.json`（各縣市今明 36 小時，擺攤指數用），皆置於站台根。兩支排程都 push 到 main
（Pages 服務分支），push 前都有 fetch+rebase 重試，避免彼此/部署競爭 main 造成非快進被拒。

## ⚠ 上線前務必

- **作物批發名校準**：`fetch_prices.py` 的作物名須與農業部「農產品交易行情」實際「作物名稱」相符。
  已知 `地瓜葉→甘藷葉` 全台回 0（名稱不符，前端不顯示）；水果批發名（如 火龍果→紅龍果、
  芭樂→番石榴）多數可對到但仍應逐一核對；對不到者跳過即可、不影響其他品項。
- **`fetch_cwa.py` 各 `parse_*` 的 CWA JSON 欄位路徑**仍須以真實 API 回應校準。
- **雲林測站站號別寫死**：以回應過濾當前有效站號。

## 本機測試

```bash
pip install -r scripts/requirements.txt
python3 scripts/fetch_prices.py           # 抓批發行情（免金鑰）
CWA_API_KEY=CWA-xxxx python3 scripts/fetch_cwa.py   # 抓颱風/天氣（需 CWA 金鑰，否則寫 active=false）
python3 -m http.server 8080               # 開 http://localhost:8080 看真實快照
# 直接開 index.html（file://）會被瀏覽器擋本機 JSON → 自動跑 demo
```

## 分支慣例

- 資料 JSON 由 `update-data.yml`（天氣，30 分）與 `update-prices.yml`（行情，每日數次）
  兩支排程分別提交到 Pages 所服務的 main 分支根。
- 版本號在 `index.html` 的 `const VERSION`（頁尾顯示「精準進貨 vX.Y.Z」，淺灰置中）。
  標準 SemVer：修 bug 進 Z、加功能進 Y、大改版進 X。目前 `v1.6.0`。
  改前端有感內容時，記得同步把 `sw.js` 的 `CACHE` 版本號 +1（目前 `jingzhun-buy-v6`）讓舊快取失效。

## ⚑ 更新內容彈窗（What's New）

- `const CHANGELOG=[{v,date,title,items:[…]}, …]`（最新在最上）＝每版更新摘要，同時是「設定→關於 App→更新內容」清單。
- **每次改版第一次開啟自動跳一次彈窗**：`maybeShowWhatsNew()`（啟動時呼叫）比對 `CHANGELOG[0].v` 與
  `localStorage.vs_seenver`，不同就顯示本版並立刻寫入 `vs_seenver`（**看過或關掉都不再跳**）；要再看可到設定→關於 App→更新內容。
- **新版務必在 `CHANGELOG` 最上面加一筆** {v,date,title,items}，否則彈窗不會顯示新內容。
