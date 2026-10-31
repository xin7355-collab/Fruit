# 專案工作守則（給 Claude Code）

## 1. 溝通
- 一律用繁體中文（台灣用語）回覆、寫註解、寫 commit 訊息。
- 我主要用 iPhone 操作，沒有電腦。步驟以手機能完成為準；要我去設定頁時直接給網址。
- 回報要誠實：哪些驗過、哪些沒驗到，分開講清楚。不確定的事實先查證再講。
- 我說「只是先問問題」時，只回答，不動手改東西。

## 2. 長期授權：以下不用再問我
- 做完直接合併到 main 並推上去，不需要我同意：
  在指定分支開發 → rebase 到最新 main → fast-forward 合併 → push main → 分支也同步推。
  網路失敗重試最多 4 次（間隔 2/4/8/16 秒）；遠端有新 commit 就 fetch + rebase 再推。
- 部署用的分支（例如 gh-pages）也可以直接推。
- 改工作流、加套件、新增檔案、重構——直接做。
- 不要開 Pull Request，除非我明確要求。

## 3. 連這些也直接做，不用先問
- 刪除：Issue、Release、分支、檔案；force push；改寫 git 歷史。
- 花錢：開付費方案、呼叫付費 API。
- 改公開程度：repo 公開／私人切換、網站權限。
- 做了不可逆的事（刪除、force push、會產生費用的動作），完成後在回覆裡明確列出來。

## 4. 隱私與安全
- 我說過「不要上傳 GitHub」的東西永遠不 commit，做好打包給我下載。
- 絕對不要把金鑰、Token 印出來（log、commit、回覆都不行）。金鑰放 repo Secrets；
  程式用 HTTP 標頭傳，不放網址（網址會出現在錯誤訊息和記錄裡）。
- repo 保持公開（公開 repo 的 Actions 分鐘數無上限；私人免費只有 2000 分鐘／月）。所以：
  - README 精簡，不詳述這個軟體在做什麼；詳細說明放 docs/。
  - 用 GitHub Issue 送的通知，標題和內文只寫數量，不寫內容——公開 Issue 會被搜尋引擎收錄。
  - 網站加 `<meta name="robots" content="noindex, nofollow">`。
- 用到付費 API 時提醒我設預算上限。

## 5. 做事的方式
- 先重現再修：修 bug 前用實際資料或真的瀏覽器（Playwright）重現，修完用同樣方法驗證。
- 新功能附離線可跑的自我測試（例如 `python xxx.py selftest`），涵蓋邊界情況。
- 不要默默失敗：
  - 全部失敗時不交出空檔案；部分失敗要講幾個、哪幾個。
  - 分清楚「還沒設定」（安靜等、不發失敗通知）和「真的壞掉」（紅燈、通知我）。
  - 錯誤訊息要說我能做什麼，不是只丟 HTTP 代碼。
- 註解寫「為什麼」與避開了什麼失敗情境，不重述程式在做什麼。
- commit 訊息寫：問題、根本原因、怎麼修、怎麼驗證。
- 不寫死外部 API 的模型名稱（常改名下架）；能自動挑就自動挑，也能用環境變數指定。
  呼叫 AI 時用 skl 的 `ai-model-router`（只寫等級 fast／smart／best／auto，下架自動換、不合格自動升級）。
- 範圍外的問題簡短提出，不擅自擴大範圍。
- 裝了新技能（app-bootstrap）之後不要停在「裝好了」：先列出執行方案（每個新技能要拿來做什麼、會改哪些檔案、
  技能需要的套件寫進哪個設定檔、怎麼驗證、有沒有費用），列完就直接做，不用等我回覆。需要我提供的東西
  （金鑰、帳號、付款）或本守則沒授權的動作才先問我；我說「只裝不做」時才停。
- 不留死碼；改名、搬檔後全專案搜一次舊名稱的殘留參照。
- CLAUDE.md 每次對話都整份載入，控制在 200 行內：只跟某些檔案有關的細節放 `.claude/rules/`（開頭寫 `paths:`），
  讀或改到那些檔案才載入。

## 6. 選用：手機 PWA + GitHub Actions 類型的專案
（新專案是同一種架構才需要這段，不是的話整段刪掉）
- 前端每次改版都更新版本號（例如 `APP_VER`），手機上的舊頁面偵測到會自動重載。
- iPhone 下載檔案用 `navigator.share({files:[f]})`，不要同時帶 `title`
  （iOS 會只送出 title 文字、檔案被丟掉）；不支援再退回 `<a download>`。
- 大檔上傳直接把 File 當 body 送（XHR 有進度條），不要先讀成 ArrayBuffer——1GB 在 iPhone 上必當。
- 版面：一個分頁盡量一張卡；說明收進 `<details>`；選項多用下拉，不用一排排按鈕。
- GitHub Actions：
  - 長工作每做完一個就存進度，不要整批做完才存（被逾時砍掉會全部白做、無限重來）。
  - 發佈結果的步驟加 `if: always()`。
  - 設時間預算，估不下就留到下一輪。
  - 「抓下來」和「處理成功」分兩段記：成功才標記完成，失敗的下一輪自動重試。
  - 預設 shell 是 `bash -e -o pipefail`：`tee` 到的資料夾要先 `mkdir -p`。
- 大檔暫存用 GitHub Release 附件（單檔上限 2 GiB、不進 git 歷史），repo 只放小標記檔觸發工作流。

## 7. 本專案資訊
- **用途**：台灣蔬果批發行情 App 兩支，純前端 PWA、免帳號、可離線。
  - 精準務農（根目錄，farmer）：行情看板、採收預判、田園巡查（颱風搶收）、全國天氣。
  - 精準進貨（`merchant/`）：行情看板、跨市場比價、天氣。
- **部署在哪**：GitHub Pages（Deploy from a branch：main / root）→ https://xin7355-collab.github.io/Fruit/ 與 /Fruit/merchant/。
  資料由 GitHub Actions 排程抓進 main（`update-prices.yml` 行情、`update-data.yml` 天氣，merchant-* 同理）。
- **需要的 Secrets**：`CWA_API_KEY`（天氣颱風）；選配 `LINE_CHANNEL_ACCESS_TOKEN`、`LINE_TO`、`NOTIFY_WEBHOOK_URL`。
  **Variables**（選配，行情推播）：`NOTIFY_PRICE_CROPS`（例：甘藍,小白菜）、`NOTIFY_PRICE_MARKET`（預設 西螺）。
- **主要檔案**：`index.html`／`merchant/index.html`（單檔 App）、`core.js`（兩支共用演算法，兩份須一致）、
  `scripts/fetch_prices.py`＋`scripts/price_core.py`＋`scripts/http_retry.py`（行情管線，merchant/scripts 有同名副本）、
  `scripts/fetch_cwa.py`、`scripts/notify.py`、`scripts/backtest.py`（回測，結果在 `docs/backtest.md`）、
  `scripts/fetch_origin.py`（產地價→`origin.json`，務農）、`merchant/scripts/fetch_retail.py`（臺北市零售→`merchant/retail.json`）。
  細節：`.claude/rules/structure.md`（檔案結構）、`decision-model.md`（搶收模型）、`history.md`（版本沿革），改到相關檔案時自動載入。
- **特別注意（踩過的坑，不要改回去）**：
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
    - **比價預設比「主要品種」**（`mainVariety`），不要改回「全部綜合」：各市場品種組成不同（高雄韭菜混韭菜黃），
      回測 E 顯示會把「買到不同東西」當成便宜。價差 2 倍以上標「可能等級／包裝不同」；運費欄 `jb_freight`（元/公斤）。
    - **行情排程**：台灣 05–12 時每小時＋14 時（merchant 晚 30 分）；每月第一次（或格式變、`--full`）整段重抓 2 年，
      其餘只抓近 14 天（index `last_full`）；`seen` 記每個交易日第一次抓到的時間。
    - **5 年封存** `prices/archive/NN_YYYY.json`（只有務農，`ARCHIVE_YEARS=5`）：滑出 2 年窗的資料搬進去；回填有時間預算
      （手動觸發 update-prices.yml 的 `backfill_minutes`），每個「市場×年」做完就存進度。只給摘要與回測用，手機不下載。
    - 產地價、零售抓不到 → 保留舊檔、不寫空檔、黃燈警告，不擋行情更新。
  - **技能**：`.claude/skills/` 由 skl 的 app-bootstrap 安裝（清單見 `.claude/skills/.skl-vendor.json`）；
    更新：`python3 /tmp/skl/xin-toolkit/skills/app-bootstrap/scripts/bootstrap_app.py update --target .`。
- **測試**：`pip install -r requirements-dev.txt && python -m pytest -q tests/`；`node --test tests/core.test.cjs`；
  `node tests/e2e/smoke.cjs`（iPhone 13 尺寸；`E2E_ROOT=<資料副本> E2E_SPLIT=1` 可驗新格式與品種延後載入）。CI：`.github/workflows/test.yml`。
- **回測結論（2026-10，docs/backtest.md）**：往年同期判斷沒有勝過舊法 → 看板標籤維持「近 90 筆 ±10%」，往年同期只當補充；
  季節低價多進貨平均多花 5% → 不做；採收預判 30 天誤差 ≈「價格不變」、區間命中 31% → App 上有誠實說明；搭配種法選菜勝過固定單作。
  跨市場進貨（同品種）：中部運費 2 元/公斤只省 0.3%、不填運費反虧；北部/高雄看似省 2 成但多半是等級不同 → 比價頁附說明。
  零售：批發×倍數誤差中位數 21%，直接用 1～6 個月前的零售價只有 8～15% → 零售卡先給零售價，倍數只當參考。

### 版本號規則
對外版本自 **v1.0.0** 起，之後**每次改版最後一碼 +1、逢 9 進位**：
`v1.0.0 → v1.0.1 → … → v1.0.9 → v1.1.0 → v1.1.1 → … → v1.1.9 → v1.2.0 → …`
（即單純的流水號，不再用「修 bug 進 Z、加功能進 Y」的 SemVer 語意。）
改版流程：`index.html` 的 `const VERSION` 進一碼、`CHANGELOG` 陣列最上面加一筆該版「更新內容」、
`sw.js` 的 `CACHE` 版本號 +1（強制舊快取失效）。

### 更新內容彈窗（What's New）
- `const CHANGELOG=[{v,date,title,items:[…]}, …]`（最新在最上）＝每版更新摘要，同時是「設定→關於 App→更新內容」清單。
- **每次改版第一次開啟自動跳一次彈窗**：`maybeShowWhatsNew()`（啟動時呼叫）比對 `CHANGELOG[0].v` 與
  `localStorage.vs_seenver`，不同就 `openChangelog()` 並立刻寫入 `vs_seenver`（**看過或關掉都不再跳**）。
  所以「這版改了什麼」只提醒一次；要再看可到設定→關於 App→更新內容。
- **新版要記得在 `CHANGELOG` 最上面加一筆**（版本號、日期、items 簡短列出改動），否則彈窗不會顯示新內容。

### 架構原則
- **純前端 + 後端排程快取**：`index.html` 不含任何金鑰，只讀後端排程產出的靜態 JSON。
  抓不到時一律退回示範資料、絕不白畫面（`loadIndex` / `loadMarket` / `loadWeather` /
  `loadEvents` 都有 shape 檢查 + try/catch，失敗回 demo 或空物件）。
- **CWA / 水利署 API 需授權金鑰、CORS 不保證** → 金鑰只放後端（`CWA_API_KEY` 環境變數 /
  GitHub Actions Secret），前端永遠不觸碰。
- **動態 `ghBase`**：`resolveGhBase()` 在 `*.github.io` 下解析出 repo 根，前端由此讀 JSON。
