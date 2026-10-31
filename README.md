> **網址**：精準務農 https://xin7355-collab.github.io/Fruit/ ・ 精準進貨 https://xin7355-collab.github.io/Fruit/merchant/
> （精準進貨程式在 `merchant/` 子目錄；兩者共用本 repo 的 GitHub Pages 與 Actions 排程。）

# 二崙葉菜行情室 · 颱風搶收預判

雲林二崙／西螺葉菜農的行情看板與**颱風前搶收決策**系統。純前端（GitHub Pages）＋ 後端排程抓取，
把 7 項因子收斂成「每塊田 × 每種菜」的搶收建議。

| 分頁 | 內容 |
|---|---|
| **行情看板** | 西螺果菜市場批發行情、季節基準線、量能爆量燈號 |
| **搶收預判** | 颱風來襲前的搶收決策卡：紅黃綠燈、deadline 倒數、農藥安全期兩難、滅田風險 |

七因子：①歷史天氣 ②歷史雨量 ③地勢高低 ④蔬菜收成時間 ⑤農藥滯留(PHI) ⑥颱風警報 ⑦颱風預判。

## 快速開始

```bash
# 前端：直接用瀏覽器開 index.html（抓不到 JSON 會自動跑示範資料）
# 後端：
pip install -r scripts/requirements.txt

# 用示範颱風算一次搶收決策（不需金鑰）
python3 scripts/build_advisory.py --fields data/fields.example.json --demo-typhoon

# 抓真實 CWA 天氣/颱風（需授權碼）
cp .env.example .env    # 填入 CWA_API_KEY
CWA_API_KEY=CWA-xxxx python3 scripts/fetch_cwa.py
python3 scripts/build_advisory.py --fields data/fields.example.json --typhoon typhoon_status.json
```

## 資料流

```
CWA 開放資料 ──(scripts/fetch_cwa.py, 後端排程)──▶ typhoon_status.json
田區登記表 data/fields.json ─┐
                            ├─(scripts/build_advisory.py)──▶ harvest_advisory.json
typhoon_status.json ────────┘
西螺行情 miner ─────────────────────────────────▶ veg_prices.json（既有，另案）

index.html（前端）──讀──▶ veg_prices.json / harvest_advisory.json / typhoon_status.json
                        （任一抓不到 → 該分頁退回示範資料，不當機）
```

金鑰只放後端；前端只讀站台根的靜態 JSON。

## 資料來源（皆免費，需 CWA 授權碼）

| 因子 | 資料集 | 平臺 |
|---|---|---|
| 歷史天氣 | `O-A0003-001` / `O-A0001-001` | CWA 開放資料 |
| 歷史雨量 | `O-A0002-001` | CWA 開放資料 |
| 地勢高低 | DEM 20m (`data.gov.tw #35430`) + 淹水潛勢 (`#25766`) | 內政部 / 水利署 |
| 蔬菜收成時間 | 生育期參數表 | 農業知識入口網 |
| 農藥滯留 PHI | 安全採收期 | 植物保護資訊系統 / 農藥資訊服務網 |
| 颱風警報 | `W-C0034-001` / `W-C0033-001` | CWA 開放資料 |
| 颱風預判 | `W-C0034-005` / `W-C0034-003` | CWA 開放資料 |

## 部署（GitHub Pages）

1. 於 repo Settings → Secrets 新增 `CWA_API_KEY`（颱風/雨量資料）。選配：`LINE_CHANNEL_ACCESS_TOKEN`、`LINE_TO`、`NOTIFY_WEBHOOK_URL`（急迫搶收推播）。
2. 合併到 `main` → `.github/workflows/pages.yml` 自動部署 GitHub Pages（`configure-pages` 會在權限允許時自動啟用，免手動設定），拿到 `https://<owner>.github.io/StockAI-DB-New/`。
3. `.github/workflows/update-data.yml` 每 30 分排程：`fetch_cwa → build_advisory → notify`，把 `typhoon_status.json`、`harvest_advisory.json` 提交到站台根；前端 `resolveGhBase()` 自動讀取。
4. 颱風期間可到 Actions 手動 `workflow_dispatch`，或另建每 10 分的密集排程。

### 急迫搶收通知（讓農民不必盯網頁）
`notify.py` 會挑出「立即搶收 / deadline 逼近 / PHI 兩難」的田，推播到 **LINE 官方帳號（Messaging API push）** 或**通用 webhook**（Slack/Discord/Telegram）。以 state 檔去重、只在急迫集合變化時再送；未設任何管道則 dry-run 只印訊息。

> 真實田區登記表 `data/fields.json` 含農民個資，已 gitignore；workflow 若找不到會退用 `fields.example.json`。

## 落地路線

- **v1**：PHI/成熟度硬約束 + 時間軸 deadline + 滅田風險 R + 搶收卡片 UI + CWA 颱風接入。✅
- **v3**：接行情 `base_price` 與 `lampFor` 爆量壓價，做「早收落袋 vs 賭災後噴價」EV 期望值試算。✅
- **v4**：人力有限下跨多田的搶收排程／人力調度（EDF 貪婪，標出來不及救的田）。✅
- **v2（待辦）**：接雨量+DEM+淹水潛勢自動算 `W_terrain`；建歷史颱風損失表做風險類比校準。
- **未來**：即時通知推播（LINE/Web Push）、集合預報路徑不確定度、農民自助登錄介面。

## ⚠ 重要

**PHI（農藥安全採收期）是法規硬限制**。`fields.example.json` 內的 `phi_days` 僅為量級示意，
上線前務必逐藥×逐作物查「植物保護資訊系統」登記值，不可臆填——未達 PHI 採收上市即殘留超標，恐遭銷毀裁罰。
