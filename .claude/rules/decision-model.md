---
paths:
  - "scripts/**"
  - "merchant/scripts/**"
  - "index.html"
  - "data/**"
---

# 搶收決策模型與上線前必查（從 CLAUDE.md 搬來，內容未刪減）

## 決策模型 v1（build_advisory.py）

管線：`正規化 → B 硬約束閘門 → C 滅田風險R → D 時間軸求解 → A 決策合成`。

- **B 硬約束**：`PHI`（安全採收期，未到期不可上市，颱風逼近時凸顯「搶收超標 vs 不收滅田」兩難）、
  `成熟度`（未達門檻收了殘值過低 → 壓為觀望）。已實作。
- **C 風險 R**：`100 × P到達機率 × I風雨強度 × W淹水權重 × V作物脆弱`。已實作；
  **歷史類比校準（analog）留 v2 TODO**。
- **D 時間軸**：`deadline = 暴風到達 − 安全緩衝 − 淹水提前量 − 搶收工時`；工時裝不下 → `partial_pct`。已實作。
- **E 市場 EV（v3）**：早收落袋 vs 賭災後噴價期望值。接 `veg_prices.json` 取 `base_price`、
  以全區搶收比例套 lampFor 爆量壓價（`price_early_factor`）、以全區損失率套災後噴價（`spike_factor`）。已實作。
  （全區日成交量的精確 surge 校準留 TODO，目前用搶收田比例代理。）
- **F 全區搶收排程（v4）**：人力有限（`--teams` N 隊）下，跨多田以 EDF 貪婪排出搶收順序/時程，
  標出「來不及完收」的田並建議部分搶收/保田。輸出於 `harvest_advisory.json` 的 `schedule`。已實作。
  （嚴格最佳化排程／部分搶收價值最大化留 TODO。）

可調參數集中在 `build_advisory.py` 頂部（`GROWTH_DAYS` / `MATURITY_MIN` / `V_CROP` / `FLOOD_ADVANCE_H` 等）。

## ⚠ 上線前務必

- **PHI 逐藥×逐作物實查**：`data/fields.example.json` 的 `phi_days` 為量級示意，PHI 是「該藥×該作物」
  的登記值（同種菜不同藥天數不同），**不可臆填、法規硬限制**。前端田區表單已改為讓農友自填＋附官方查詢
  連結（田邊好幫手 m.moa.gov.tw/Pesticide、防檢署 pesticide.aphia.gov.tw），不內建假 PHI 常數。
- **生育日數/產量係數已校準**：`GROWTH`(index.html)／`GROWTH_DAYS`(build_advisory.py)／`YIELD_M2` 已依
  農業部農業知識入口網·各區農改場·農情調查資料校準（snippet 來源、med 信心，gov 站 403 無法直讀原表，
  仍屬估算）。要更精準可改由農情調查開放資料(每公頃單產×縣市)動態帶入。`fetch_cwa.py` 各 `parse_*`
  的 CWA JSON 欄位路徑仍須以真實 API 回應校準。
- **雲林測站站號別寫死**：以 `O-A0001-001` / `C-B0074-002` 回應過濾雲林縣站點取當前有效站號。
- **作物批發名校準**：`fetch_prices.py` 的 `VEG_MAP`/`FRUIT_MAP` 批發名須與農業部「農產品交易行情」
  實際「作物名稱」相符。已知 `地瓜葉→甘藷葉` 全台回 0（名稱不符，前端不顯示），水果批發名
  （如 火龍果→紅龍果、芭樂→番石榴）多數可對到但仍應逐一核對；對不到者跳過即可、不影響其他品項。

## 本機測試

```bash
pip install -r scripts/requirements.txt
python3 scripts/build_advisory.py --fields data/fields.example.json --demo-typhoon   # 注入示範颱風
python3 scripts/fetch_cwa.py            # 需 CWA_API_KEY，否則寫 active=false
# 前端：直接開 index.html（無 JSON 會自動跑 demo）
```
