---
paths:
  - "index.html"
  - "merchant/index.html"
  - "core.js"
  - "merchant/core.js"
  - "sw.js"
  - "merchant/sw.js"
  - "scripts/**"
  - "merchant/scripts/**"
  - ".github/workflows/**"
  - "tests/**"
---

# 精準務農／精準進貨：專案說明與檔案結構（從 CLAUDE.md 搬來，內容未刪減）

> 原 CLAUDE.md 標題：精準務農（原「農商雷達／豐雨無阻／二崙葉菜行情室」）＋ 颱風搶收預判系統。給未來 Claude 工作階段的專案指引。

## 2026-10-31 新增的檔案（較新，以這段為準）

- `scripts/fetch_origin.py` → `origin.json`（務農詳細頁「產地價」）；`merchant/scripts/fetch_retail.py` → `merchant/retail.json`（進貨詳細頁「零售參考」）。
- `prices/archive/NN_YYYY.json`：5 年封存（務農），`price_core.add_to_archive`／`read_market(with_archive=True)`；手機不讀。
- `scripts/backtest.py` A～F（E 跨市場進貨、F 零售參考價）→ `docs/backtest.md`；測試 `tests/test_backtest.py`。
- 前端新函式：`digestHTML`（看板每日提示）、`closedNoteHTML`（今天休市）、`qrChip`（量能比）、`originHTML`（務農）、
  `retailHTML`／`mainVariety`／`freightPerKg`／`CMP_BT_NOTE`（進貨）。

## 這是什麼

雲林二崙／西螺葉菜農的兩件事，放在同一個純前端站台（GitHub Pages）：

1. **行情看板** — 讀西螺果菜市場批發行情，畫價量走勢與季節基準線。
2. **搶收預判** — 颱風/豪雨來襲前，整合 7 因子給「每塊田×每種菜」的搶收決策
   （照常／觀望／建議搶收／立即搶收）＋ deadline 倒數 ＋ 農藥安全期兩難警示。

七因子：①歷史天氣 ②歷史雨量 ③地勢高低 ④蔬菜收成時間 ⑤農藥滯留(PHI) ⑥颱風警報 ⑦颱風預判。

## ⚑ 已拆成兩支 App（2026-07 起）

原本一份程式碼用「蔬果農／商人」角色切換，已依用戶要求**拆成兩支獨立 App**：

- **精準務農（farmer）** — 這個 GitHub repo／Pages 就是它。行情看板＋田區搶收＋採收預判。
- **精準進貨（merchant）** — 另一支獨立 App（批發行情＋跨市場比價找便宜進貨），
  以打包 zip 交付、由用戶另外部署（不在此 repo）。

**⚑ 2026-07（v1.2.0）：本 repo 已「純化為 farmer 單一身分」，商人相關程式碼實體移除。**
依用戶要求（「這份是精準務農，把精準進貨相關資料刪除」），已從 `index.html` 刪除：跨市場比價分頁
（tab＋`view-compare`＋`renderCompare`/`cmp*` 全套）、設定的「角色」切換、首次啟動身分選擇介面（onboarding）、
商人「擺攤指數」天氣建議、`roleNoteHTML` 商人分支、`.cmprow`/`.onboard`/`.rolenote.merchant` 等 dead CSS；
`APP_NAME` 直接寫死「精準務農」、`S.role`/`S.onboarded` 已移除。`const APP="farmer"` 仍保留為標記。
→ **商人版若要維護，請以拆分前的歷史版本或另一支 zip 為準；不要再期望本 repo 兩身分同步。**
設定預設：字體 `vs_fs` 預設「中(m)」、果菜市場 `DEFAULT_MARKET="西螺"`、天氣地區 `vs_region` 預設「雲林縣」。
（字級 v1.9 整體下移一階：s=14/m=16/l=19/xl=23px；「中(m)」＝原「小」的 16px 為預設。）

## ⚑ App 簡介（可直接用於商店介紹）

> **精準務農 — 全台蔬果行情 × 颱風搶收預判**
>
> 給蔬果農的免費工具，打開就用、免帳號、可加到主畫面離線看。
> ・**行情看板**：全台 19 個果菜批發市場的蔬菜／水果均價、漲跌與「偏貴／偏便宜」標籤，可切公斤／台斤；
>   點品項看近 2 年 K 線與逐日行情，多品種還能各別看走勢。
> ・**採收預判**：蔬菜依種植日推算採收期並帶入同期行情、附本季推薦種植；水果依果樹採收月份估產期行情。
> ・**田園巡查**：颱風／豪雨來襲前，整合天氣、地勢、成熟度、農藥安全期等因子，給每塊田每種菜
>   「照常／觀望／建議搶收／立即搶收」決策與倒數。
> ・**天氣颱風**：全國各縣市天氣預報、颱風動態與路徑／雷達圖。
> ・**貼心設計**：品項依類型分類、可逐品種關注；設定一鍵備份匯出／匯入，換手機不流失。
>
> 資料採農業部「農產品交易行情」與中央氣象署公開資料；建議與係數僅供參考，實際請依現場判斷。
> 不收集個人資料、無廣告、無需登入。聯絡：xin7355@gmail.com

## 檔案結構

```
index.html                    單頁多分頁：行情看板／採收預判／田園巡查（farmer 單一身分，無角色切換、無比價分頁）。
                              （分頁「田園巡查」＝田(菜)＋園(果)通用；原名「田區搶收」→「田區巡查」→「田園巡查」，
                              內部 data-t 仍為 harvest，決策標籤仍用「立即/建議搶收」。）
                              採收預判分「蔬菜／水果」切換(FC_CAT)：蔬菜＝種植日→生育天數→採收＋同期行情推估、含「本季推薦種植」；
                              水果＝選果樹→採收期月份(FRUIT_HARVEST)＋各品種採收月(vars)＋產區/地區差異(南部多較早)＋盛產期行情，
                              切水果時隱藏種植日與推薦種植、用語改「園區」。FRUIT_HARVEST/採收期為參考估算值。
                              ⚑ v1.0.2 當季判斷(PLANT_MONTHS/inPlantSeason，平地建議播種月)：作物下拉標「(非當季)」並把當季排前面、
                              選到非當季時 renderForecast 出提醒；「本季推薦種植」只列該種植日當季可種者。
                              ⚑ v1.0.2 成本淨利試算(renderPlantAdvisor)：可填每分地 肥料/農藥/人工/資材成本(vs_cost_*，COST_DEF 預設、
                              1 分=293.4㎡)，每張卡算 預估收入/淨利(收入−成本)/保本價(成本÷產量)、負值標「恐不敷成本」。
                              ⚑ v1.0.3 加「每天淨利」＝淨利÷生育天數(把採收天數也算進去)，排序可切每天淨利/整期總淨利(vs_pa_sort，預設每天淨利)。
                              ⚑ v1.0.4 一年「搭配種法」(planRotation/rotationHTML)：從種植日起貪婪逐期挑每天淨利最高且當季可種者(用
                              predictPrice 歷史同期行情=季節性)、收成隔 10 天再種、不連種同一種(輪作)，排約一年→序列＋累計淨利。
                              ⚑ v1.0.5 搭配種法可調(都存 vs_rot_*、隨備份)：輪作依據品項/科別(CROP_FAMILY，vs_rot_family)、
                              「只用我常種的」(vs_rot_mode=mine + vs_rot_crops，＝慣性施作、受限池排不開時允許重複)、
                              每期下拉鎖定作物(vs_rot_locks)。chips/鎖定只列此市場查得到行情者；無可種時往後 15 天(休耕)再看。
                              ⚑ v1.0.3 縮放：移除自寫回彈式 transform:scale(會切掉右側/卡住「縮放異常」)，改用瀏覽器原生縮放(viewport maximum-scale=5)。
                              字體 s/m/l/xl（預設 m 中＝16px；s=14/m=16/l=19/xl=23）。
                              設定「我的品項」分類(蔬菜/水果)與每個母作物的品種預設**折疊**(PICK_CATOPEN/PICK_EXP)，
                              點分類展開、點「▸品種」展開該作物品種；展開後**蔬菜與水果都**依類型小標分組(.picktype)：
                              蔬菜 VEG_TYPES/vegType(甘藍花菜/根莖/蔥蒜薑/瓜果菜/豆菜/菇筍/香辛香草/葉菜，LEAF_FIRST 先把
                              甘薯葉等食葉者歸葉菜避免被根莖誤收)；水果 FRUIT_TYPES/fruitType(柑橘/瓜/熱帶/漿果/核果/仁果/柿)。
                              分類正則已用線上實際作物名(蔬菜123+水果63)校準到 0 誤分。
                              左緣右滑或手機返回鍵＝上一頁(goBack 關上層彈窗/K線/清搜尋)。
                              行情看板為「大卡列表」：每卡顯示品項名＋均價＋漲跌＋偏貴/偏便宜標籤，全站可切
                              公斤/台斤(1台斤=0.6kg，S.unit)。看板頂有搜尋框(跨市場全部品項、不分蔬果)＋市場標示；
                              預設看板精簡：常見葉菜(COMMON_VEG)＋量大者，veg≤10＋fruit≤6(defaultWatch)，避免一次列太多。
                              ⚑ 2026-07（v1.5.0）品項互動：**點卡片＝進 K線價格走勢**(openDetail)；卡片另有「ⓘ 說明」鈕
                              (mc-info→openCropInfo：挑選要訣＋Google 查圖)。單一品項→點卡進 K線；多品項→展開各品種
                              (無「全部綜合」)、點品種名進該品種 K線、品種列 ⓘ 開說明。品項圖示仍全面移除(無 emoji/放大鏡)。
                              (v1.3 曾改成「點卡片開說明、無 K線入口」，v1.5 依用戶要求改回點卡進 K線＋獨立說明鈕。)
                              搜尋框輸入即跳**自動完成下拉**(renderSug，開頭符合優先、點一項直接看該品項)。
                              設定「我的品項」勾選表**依細類型排序**(subtypeRank 葉菜/花果/根莖/菇筍)、**有品種者列出細部品種**(chipvar)。
                              K線詳細頁 openDetail/renderDetail(日/週/月·2年 走勢＋逐日明細＋農曆)；查價線改「橫滑才顯示」
                              (attachCross 不預設畫，修正「切台斤跑出橫豎線」的觀感)。cropIcon(emoji) 僅保留給採收預判推薦清單。
                              颱風警戒卡可點開「颱風詳情」modal：自氣象署逐時軌跡繪相對雲林的路徑圖(暴風圈)
                              ＋windy 雷達 iframe＋NCDR/windy 連結。地圖 CHINA 多邊形內陸封口拉遠(緯46/lng100)
                              →海岸線永遠不被畫面切成一半。農曆用內建 solar→lunar(2020-2035 表，已對節氣校驗)。
                              頁首：實際·示範 badge 靠左、其右接更新日期、⚙️最右。App 名農商雷達見 PWA/頁尾。
                              App 圖示 icon.svg/PNG 為自繪「雷達準星弧＋雙葉幼苗(漸層)＋扇形田壟」綠徽(依用戶圖例)。
                              縮放用瀏覽器原生雙指縮放(viewport maximum-scale=5；v1.0.3 移除舊的回彈式 transform:scale——會切右側/卡住)。
                              全國定位：颱風侵襲機率依「所在縣市」中心點×颱風軌跡計算(regionInvasion，不寫死雲林)；
                              文案改「全台蔬果」。設定順序(v1.0.1)：顯示設定(字體)→天氣預報(地區)→行情看板(果菜市場/我的品項)
                              →田區(我的田區)→資料備份→關於。更新彈窗只顯示本版(renderChangelog(true))。我的品項
                              蔬菜·水果各自「全選/取消全選」＋計數；田區表單附「Google Earth 量田」教學＋地勢等級海拔參考。
                              設定「資料備份／換手機」可匯出/匯入所有 vs_* 為單一 .json(collectBackup/importData，
                              navigator.share 檔案優先→退回 a[download]；安卓/iOS 皆可)。
                              （原「蔬果農/商人」首次啟動身分選擇介面已移除，farmer 直接進入。）
                              天氣為全國：設定有「天氣預報地區」縣市選單(S.region，預設雲林縣)；農夫若有填
                              座標的田自動以田所在縣市為準(activeCounty/countyFromLatLng，COUNTY_LL 22縣市中心)。
                              預報以「今日白天/今晚/明日白天」時段卡呈現(天氣emoji＋降雨機率＋高低溫)。
                              設定內「關於 App」(上架用、可摺疊)：意見回饋(mailto)/更新內容(clModal 讀 CHANGELOG 陣列)/
                              常見問題(FAQ modal)/隱私權政策(privacy.html)/使用條款(terms.html)/分享App(navigator.share→剪貼簿退回)。
                              App 圖示(icon.svg/PNG)為自繪「雷達準星＋幼苗＋田壟」綠色徽章，扣合「農商雷達」。
                              PWA：manifest.json+sw.js+icon，可加到主畫面、離線可用。
                              讀 prices_index.json + prices/NN.json / typhoon_status.json /
                              weather_events.json / weather_forecast.json / harvest_advisory.json，全部可退回 demo。
privacy.html / terms.html     隱私權政策／使用條款靜態頁(上架必備)。誠實：無帳號、不收個資、GPS 僅本機、
                              資料採政府公開資料；建議/PHI/產量係數僅供參考，勿臆填。聯絡 xin7355@gmail.com。
scripts/
  fetch_prices.py             抓農業部「農產品交易行情」（免金鑰，近 2 年）→ prices_index.json
                              （markets/crop_map/categories/latest）+ prices/NN.json（data + variants）。
                              **市場清單動態探索**(discover_markets/resolve_markets)：先不帶 Market 逐日撈近
                              10 天全台成交，收集 API 實際回報的「所有」市場(含合作社/農會/青果運銷社經營的批發市場)，
                              與靜態 MARKETS 聯集後逐一抓。解決過去寫死短名對不到(桃園→桃農、北港)整區漏掉；
                              顯示名盡量對回既有短名(維持「西螺」等不變、不動用戶設定)，查詢用 API 實際名稱最可靠。
                              以探索清單為準、濾掉「縣市彙總」名(ROLLUP_MARKET，如『台北市場/彰化市場』無單一明細)，
                              探索失敗才退回靜態清單(19 個實跑確認查得到者、含桃農/東勢)。實跑：探索到 24→濾彙總後 20→
                              19 市場有 2 年資料(桃農＝桃園農產，過去寫死『桃園』一直漏)。`--discover` 只列市場(markets_discovered.json)、
                              `--no-discover` 走舊行為。⚑ 註：本 API 僅涵蓋政府指定的批發市場(~20+，含合作社經營者)；
                              未設批發市場的縣市(產地小型合作社/農會直銷)不在此資料，需另接「產地價格」資料集才有。
                              **逐市場抓全部**：每市場一次 query(不帶作物名)拿回所有成交品項(取代逐品項的上千次)。
                              用回應的「種類代碼」自動分蔬菜/水果、濾掉花卉/休市/0 元。
                              作物名以 norm_crop() 收斂到母作物(鳳梨-金鑽→鳳梨)、量加權合併；同時把各品種
                              的「完整逐日序列」存到 variants[母作物]={品種:{iso:{avg..}}}供前端「點品種看它自己 K線」。
                              crop_map 為 identity。每日 series 存 avg/high(上價)/mid(中價,取 API「中價」量加權)/low(下價)/qty。
                              index 另存 latest_var[市場][作物][品種]=最新價，供比價分頁「品種細部」跨市場排序。
                              各市場僅出實際有交易者(葉菜市場只有葉菜、大市場整排水果+品種)。
                              --probe <市場> 可 dump 原始欄位(prices_probe.json)。latest 取最近 avg>0。
  fetch_cwa.py                抓 CWA 颱風/路徑潛勢/侵襲機率/雨量/氣溫 → typhoon_status.json；
                              並逐日「向前累積」真實天氣事件 → weather_events.json
                              （颱風/大雨≥80mm/低溫≤10°C，去重、保留 ~2 年，K線標記用）。
                              另抓 F-C0032-001（全國各縣市今明36小時）→ weather_forecast.json
                              ={counties:{縣市:{periods:[{label,pop,minT,maxT,wx}]}}}，供農夫農事建議。
                              parse_nationwide_36h 大小寫/中英文欄位容錯、縣市名「臺→台」。
                              颱風狀態 typhoon_status.json 另含 `departing` 旗標（暴風圈最近點已過、距離拉開＝遠離中），
                              修正「颱風離開後仍顯示颱風接近中／仍催搶收」：eval_typhoon 以時間排序預報點判斷，
                              前端 typhoonDeparted() 另有 eta 過期>3hr 的保險退路。
  fetch_yield.py              抓農業部「生產概況」(UnitId=113蔬菜/135果品，免金鑰) → yield_index.json
                              ={national:{作物:kg/ha}, counties:{縣市:{作物:kg/ha}}}。取最新年度、排除彙總列
                              (臺灣省/合計)、臺→台；欄名容錯(每公頃平均產量_公斤 有無後綴皆可)。前端 yieldInfo()
                              以所在縣市優先(÷10000→kg/㎡)、退全國、再退內建估算(YIELD_ALIAS 對照概況作物名)。
  build_advisory.py           田區登記表 + 天氣 → 決策模型 → harvest_advisory.json（含 EV、schedule）。
                              註：前端 index.html 另有自帶的 client 版決策，這支為後端/通知用。
  notify.py                   挑急迫田 → LINE Messaging API / webhook 推播（去重；未設則 dry-run）
  requirements.txt            requests
data/
  fields.example.json         田區登記表範例（真實檔為 data/fields.json，已 gitignore）
.github/workflows/
  update-data.yml             天氣/颱風每 30 分：fetch_cwa → build_advisory → notify → commit
                              (typhoon_status/weather_events/harvest_advisory/cwa_debug)。輕量、快。
  update-prices.yml           批發行情每日 08/13/17 時(台灣)：fetch_prices → commit
                              (prices_index/prices)。抓 2 年較慢(~15 分)，故與天氣拆開、互不阻塞。
  ci.yml                      PR 檢查：py_compile + 決策 demo + notify dry-run + 前端關鍵元素
（部署）                        採 GitHub 內建「Deploy from a branch: main」(pages-build-deployment
                              自動觸發)；不用 Actions 版 pages.yml（權限受限會失敗，已移除）。
.env.example                  CWA_API_KEY / LINE / webhook 範例
```

前端會讀取（不存在時退回 demo）：`prices_index.json` + `prices/NN.json`（行情，逐市場 lazy load）、
`typhoon_status.json`（颱風現況）、`weather_events.json`（K線天氣事件標記）、
`harvest_advisory.json`（後端決策），皆置於站台根。兩支排程都 push 到 main（Pages 服務分支），
push 前都有 fetch+rebase 重試，避免彼此/部署競爭 main 造成非快進被拒。
