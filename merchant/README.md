# 精準進貨 · 全台蔬果批發行情比價

給蔬果**商人／擺攤／進貨**用的純前端 App（GitHub Pages）。看全台各果菜市場批發行情、
跨市場比價找最便宜的市場進貨，附擺攤天氣指數與颱風警戒。

> 此 repo 已為**商人版專用**：農夫版「精準務農」的田區搶收／採收預判相關程式與資料
> （`build_advisory.py`、`notify.py`、`fetch_yield.py`、`harvest_advisory.json`、`yield_index.json`、
> `veg_prices.json`、`data/` 等）已移除。前端 `index.html` 只保留行情看板＋跨市場比價。
>
> 預設值：字體**小**、果菜市場**台中**、天氣預報地區**台中市**（皆可在右上 ⚙️ 更改）。

| 分頁 | 內容 |
|---|---|
| **行情看板** | 全台各市場批發行情、K 線走勢、逐日明細、擺攤天氣指數、颱風警戒 |
| **跨市場比價** | 同一品項比全台各市場批發價（低→高，適合進貨），標出最便宜市場 |

價格＝農業部「農產品交易行情」公開資料；天氣／颱風＝中央氣象署資料。抓不到時自動顯示示範資料、絕不白畫面。

---

## 一、直接用（最快）

* 這個資料夾**本身就是完整的 App**，已附一份當日行情快照（`prices_index.json`、`prices/`、
  `weather_forecast.json`、`typhoon_status.json` 等）。
* 用一個簡單的本機伺服器打開就會看到真實資料：

```bash
cd 精準進貨
python3 -m http.server 8080
# 瀏覽器開 http://localhost:8080
```

> 直接用 `file://` 雙擊 `index.html` 也能開，但瀏覽器會擋本機 JSON，
> 因此會顯示「示範資料」。要看快照裡的真實行情，請用上面的本機伺服器或部署到網路。

## 二、上線到 GitHub Pages（做成可安裝 App）

1. 在 GitHub 開一個新的空 repo（例如 `jingzhun-buy`），把這個資料夾裡的檔案全部推上去。
2. repo → **Settings → Pages → Build and deployment → Deploy from a branch → `main` / `(root)`**。
3. 等 1–2 分鐘，開 `https://<你的帳號>.github.io/jingzhun-buy/`。
4. 手機瀏覽器開該網址 →「加入主畫面」，就是一支可離線的 App（PWA）。

## 三、讓資料自動更新（可選）

已附兩支排程（`.github/workflows/`）與抓取程式（`scripts/`）：

```bash
pip install -r scripts/requirements.txt
python3 scripts/fetch_prices.py           # 抓批發行情 → prices_index.json + prices/NN.json（免金鑰）
CWA_API_KEY=CWA-xxxx python3 scripts/fetch_cwa.py   # 抓颱風/天氣（需中央氣象署授權碼）
```

* `CWA_API_KEY` 到中央氣象署開放資料平臺免費申請，設成 repo 的 **Actions Secret**。
* Workflow 會定時抓資料並 commit 到 `main`，Pages 自動重新部署：
  `update-prices.yml`（批發行情，每日數次）＋ `update-data.yml`（天氣/颱風，每 30 分）。
* `scripts/` 只剩商人版需要的 `fetch_prices.py`（行情）與 `fetch_cwa.py`（天氣/颱風/預報）。

## 四、換 App 圖示

`icon-512.png`、`icon-192.png`、`apple-touch-icon.png`、`icon.svg` 目前沿用「精準務農」的雷達徽章。
要做商人版專屬圖示，換掉這四個檔即可（正方形、建議 512／192／180；SVG 為向量原稿）。

## 五、法遵

`privacy.html`（隱私權政策）、`terms.html`（使用條款）已改寫為商人版文案：無帳號、不收個資、
所有設定只存在本機、資料採政府公開資料、比價僅供參考。上架前請再核對一次聯絡信箱與條文。

---

版本：`index.html` 的 `const VERSION`（頁尾顯示「精準進貨 vX.Y.Z」）。
