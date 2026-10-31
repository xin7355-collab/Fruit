#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_prices.py  ——  抓農業部「農產品批發市場交易行情」公開資料 → veg_prices.json

免金鑰的公開 API。輸出 schema 與前端 index.html / build_advisory.py 既有約定一致：
  { updated, markets:[...], crop_map:{展示名:批發名}, data:{市場:{批發名:{ISO日:{avg,high,mid,low,qty}}}} }

⚠ 本開發沙盒的網路政策擋 data.moa.gov.tw，無法本機實測；於 GitHub Actions（有外網）執行，
   再由 Action log 驗證每個品項抓到幾筆。抓不到的品項會安全略過，前端自動不顯示、退回示範資料。

市場清單改「動態探索」：不再只抓寫死的短名清單，而是先不帶 Market 逐日撈近 10 天全台成交，
   收集 API 實際回報的『所有』市場（含合作社／農會／青果運銷社經營的批發市場），再與靜態
   MARKETS 聯集後逐一抓 2 年歷史。解決過去寫死短名對不到（如桃園、北港）而整區漏掉的問題。
   探索失敗（API 需帶 Market 或無外網）時安全退回靜態清單。

用法：
  python3 scripts/fetch_prices.py --days 365        # 動態探索市場 + 抓行情
  python3 scripts/fetch_prices.py --discover        # 只列出 API 有哪些市場（含合作社），不抓行情
  python3 scripts/fetch_prices.py --no-discover     # 舊行為：只抓靜態 MARKETS 清單
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

TZ = timezone(timedelta(hours=8))
API = "https://data.moa.gov.tw/Service/OpenData/FromM/FarmTransData.aspx"
UA = {"User-Agent": "Mozilla/5.0 (compatible; erlun-veg-advisory/1.0)"}

# 種類代碼（由 --probe 探測台北一得知）：N04=蔬菜、N05=水果；其餘(花卉等)略過。
CAT_BY_CODE = {"N04": "蔬菜", "N05": "水果"}
_SPLIT = re.compile(r"[-\s（(]")
SKIP_BASE = {"休市", "其他", "其它", ""}


def norm_crop(name):
    """作物名稱 → 母作物（去品種/產地尾綴）：花椰菜-青梗→花椰菜、紅龍果-白肉→紅龍果、
    小番茄-聖女→小番茄、南瓜-栗子 小黑→南瓜。"""
    return _SPLIT.split((name or "").strip())[0]

# 保底靜態清單：僅在「動態探索」完全失敗時才用。內容＝2026-07 實跑確認「查得到 2 年資料」的市場名
# （用 API 實際回報名，桃園農產＝『桃農』、含農會型『東勢』）。⚑ 正常情況一律以 discover_markets
# 探索到的即時清單為準，本清單只是離線/API 異常時的退路，避免整站空掉。
MARKETS = ["台北一", "台北二", "三重", "板橋", "宜蘭", "桃農", "台中", "豐原", "東勢",
           "南投", "溪湖", "永靖", "西螺", "嘉義", "高雄", "鳳山", "屏東", "台東", "花蓮"]
# 沙盒擋 data.moa.gov.tw，市場名以 GitHub Actions 實跑驗證（workflow_dispatch / --discover）。
# 用戶指定要「強制確認」的候選市場：即使近 14 天動態探索沒看到（可能休市/季節性沒交易），
# 也用 2 年區間單獨試抓一次；有歷史成交才會留下、否則安全略過。
EXTRA_MARKETS = []   # 「水里」實測：此 API 對 Market=水里 無回應/無資料（查詢逾時），不強制試抓以免拖慢每日更新
DISCOVER_DAYS = 14   # 動態探索市場清單時往回看幾天的成交（逐日查；14 天確保各市場至少有一個交易日被看到）
# API 會回一些「縣市彙總」名（如『台北市場／彰化市場』），無單一市場明細、以 Market 再查得不到 → 濾掉。
ROLLUP_MARKET = re.compile(r"市場$|彙總|合計|小計")

# 展示名 → 農業部作物名稱（批發端）。名稱須與 API「作物名稱」相符，
# 不符者該品項抓不到即安全略過。上線時可對照「農產品交易行情」實際名稱微調。
VEG_MAP = {
    # 葉菜（雲林農主力）
    "青江菜": "青江白菜", "小白菜": "小白菜", "芥藍菜": "芥藍菜", "菠菜": "菠菜",
    "空心菜": "蕹菜", "地瓜葉": "地瓜葉", "莧菜": "莧菜", "茼蒿": "茼蒿",
    "油菜": "油菜", "萵苣(A菜)": "萵苣菜", "芥菜": "芥菜",
    # 葉菜（進階）
    "山蘇": "山蘇", "龍鬚菜": "龍鬚菜", "過貓": "過溝菜蕨",
    # 一般/瓜果蔬菜（大市場常見，各地依季節/供應自然不同）
    "高麗菜": "甘藍", "大白菜": "包心白菜", "花椰菜": "花椰菜", "青花菜": "綠花椰菜",
    "白蘿蔔": "蘿蔔", "洋蔥": "洋蔥", "番茄": "番茄", "苦瓜": "苦瓜",
    "絲瓜": "絲瓜", "冬瓜": "冬瓜", "南瓜": "南瓜", "茄子": "茄子",
    "青椒": "青椒", "玉米": "玉米", "芹菜": "芹菜", "青蔥": "青蔥",
    # 根莖菜
    "紅蘿蔔": "胡蘿蔔", "馬鈴薯": "馬鈴薯", "地瓜": "甘藷", "芋頭": "芋頭",
    "牛蒡": "牛蒡", "蓮藕": "蓮藕", "薑": "生薑", "大蒜": "蒜頭",
    # 豆菜
    "四季豆": "敏豆", "毛豆": "毛豆", "豌豆": "豌豆", "菜豆": "菜豆",
    # 筍/菇/其他
    "茭白筍": "茭白筍", "綠竹筍": "綠竹筍", "桂竹筍": "桂竹筍", "麻竹筍": "麻竹筍",
    "蘆筍": "蘆筍", "秋葵": "秋葵", "金針菇": "金針菇", "香菇": "香菇",
    "杏鮑菇": "杏鮑菇", "木耳": "木耳",
}
# 水果（展示名 → 批發名）。⚠ 各批發名須以「農產品交易行情」實際作物名校準；
# 不符者抓不到即安全略過，前端不顯示。各地市場實際有交易的品項才會出現（達成「各地種類不同」）。
FRUIT_MAP = {
    "香蕉": "香蕉", "鳳梨": "鳳梨", "西瓜": "大西瓜", "木瓜": "木瓜",
    "蓮霧": "蓮霧", "芭樂": "番石榴", "葡萄": "葡萄", "椪柑": "椪柑",
    "柳丁": "柳橙", "火龍果": "紅龍果",
    # 季節/南部水果（商人採購常見）
    "芒果": "芒果", "荔枝": "荔枝", "龍眼": "龍眼", "釋迦": "釋迦",
    "榴槤": "榴槤", "柚子": "文旦", "梨": "梨", "棗子": "棗",
    "楊桃": "楊桃", "百香果": "百香果", "甜柿": "甜柿", "橘子": "桶柑",
    "檸檬": "檸檬", "洋香瓜": "洋香瓜", "香瓜": "香瓜", "酪梨": "酪梨",
    "草莓": "草莓", "李子": "李", "水蜜桃": "水蜜桃", "葡萄柚": "葡萄柚", "文旦柚": "白柚",
}
CROP_MAP = {**VEG_MAP, **FRUIT_MAP}                    # 抓取用（蔬菜+水果）
CATEGORIES = {**{d: "蔬菜" for d in VEG_MAP}, **{d: "水果" for d in FRUIT_MAP}}


def roc(dt):
    """2026-07-08 → '115.07.08'（民國）"""
    return f"{dt.year - 1911:03d}.{dt.month:02d}.{dt.day:02d}"


def iso_from_roc(s):
    """'115.07.08' → '2026-07-08'"""
    try:
        y, m, d = s.split(".")
        return f"{int(y) + 1911:04d}-{int(m):02d}-{int(d):02d}"
    except Exception:
        return None


def g(rec, *keys):
    for k in keys:
        v = rec.get(k) if isinstance(rec, dict) else None
        if v not in (None, "", "--"):
            return v
    return None


def _latest_valid(series):
    """回傳最近一個 avg>0 的日期資料（附 date）；全為 0 時退回最新日。"""
    for dt in sorted(series, reverse=True):
        if (series[dt].get("avg") or 0) > 0:
            return {**series[dt], "date": dt}
    dt = max(series)
    return {**series[dt], "date": dt}


def fetch(market, crop, start, end):
    params = {"StartDate": roc(start), "EndDate": roc(end), "Market": market, "Crop": crop}
    r = requests.get(API, params=params, headers=UA, timeout=60)
    r.raise_for_status()
    try:
        j = r.json()
    except Exception:
        return []
    # 可能是 list，或包在 {"data":[...]} / {"RS":[...]}
    if isinstance(j, list):
        return j
    if isinstance(j, dict):
        return j.get("data") or j.get("RS") or j.get("Data") or []
    return []


def probe(market):
    """探測：不帶作物名，抓一個市場近 14 天的原始回應，dump 欄位與樣本 → prices_probe.json。
    用來確認 (1) API 支不支援『不帶 Crop』(2) 每筆有沒有『種類/分類』欄位，決定分類走法。"""
    now = datetime.now(TZ)
    params = {"StartDate": roc(now - timedelta(days=14)), "EndDate": roc(now), "Market": market}
    r = requests.get(API, params=params, headers=UA, timeout=60)
    r.raise_for_status()
    j = r.json()
    recs = j if isinstance(j, list) else (j.get("data") or j.get("RS") or j.get("Data") or [])
    crops = sorted({str(g(x, "作物名稱", "CropName") or "") for x in recs})
    by_code = {}
    for x in recs:
        c = str(g(x, "種類代碼", "TcType") or "?")
        nm = str(g(x, "作物名稱", "CropName") or "")
        by_code.setdefault(c, [])
        if nm not in by_code[c] and len(by_code[c]) < 12:
            by_code[c].append(nm)
    out = {
        "market": market, "count": len(recs),
        "sample_keys": sorted(recs[0].keys()) if recs else [],
        "by_category_code": by_code,     # ← 種類代碼 → 例作物名，用來解碼分類
        "distinct_crop_count": len(crops),
    }
    with open("prices_probe.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[probe] {market}：{len(recs)} 筆、{len(crops)} 種作物；欄位={out['sample_keys']}")


def fetch_market_all(market, start, end):
    """逐市場、不帶作物名、分季抓（避免單次回應過大）→ 回傳該市場所有原始 records。
    17 市場 × 8 季 ≈ 136 次請求（vs 逐品項 1400+），快且完全不漏品項。"""
    out, cur = [], start
    while cur <= end:
        ce = min(cur + timedelta(days=90), end)
        params = {"StartDate": roc(cur), "EndDate": roc(ce), "Market": market}
        try:
            r = requests.get(API, params=params, headers=UA, timeout=180)
            r.raise_for_status()
            j = r.json()
            recs = j if isinstance(j, list) else (j.get("data") or j.get("RS") or j.get("Data") or [])
            out.extend(recs)
        except Exception as e:
            print(f"[fetch_prices] {market} {roc(cur)}~{roc(ce)} 失敗：{e}", file=sys.stderr)
        cur = ce + timedelta(days=1)
        time.sleep(0.3)
    return out


def clean_market_name(s):
    """市場名稱正規化：去掉可能的代號前綴與前後空白（如 '648 西螺鎮' → '西螺鎮'）。"""
    return re.sub(r"^\s*\d+\s*", "", (s or "").strip()).strip()


def discover_markets(end, days=DISCOVER_DAYS):
    """不帶 Market/Crop，逐日撈近 `days` 天全台成交，收集 API 實際回報的所有市場名稱
    （含合作社／農會／青果運銷社經營的批發市場）→ 回傳 {市場名稱: 出現筆數}。
    逐日查（而非一次長區間）以避免單次回應過大或被截斷而漏掉市場。"""
    found = {}
    for i in range(days):
        day = end - timedelta(days=i)
        params = {"StartDate": roc(day), "EndDate": roc(day)}   # 關鍵：不帶 Market → 回全台所有市場
        try:
            r = requests.get(API, params=params, headers=UA, timeout=120)
            r.raise_for_status()
            j = r.json()
            recs = j if isinstance(j, list) else (j.get("data") or j.get("RS") or j.get("Data") or [])
        except Exception as e:
            print(f"[discover] {roc(day)} 失敗：{e}", file=sys.stderr)
            recs = []
        for x in recs:
            nm = clean_market_name(str(g(x, "市場名稱", "MarketName", "Market") or ""))
            if nm:
                found[nm] = found.get(nm, 0) + 1
        time.sleep(0.3)
    return found


def resolve_markets(end):
    """決定要抓的市場清單 → [(顯示名, 查詢用名)]。
    以動態探索為準（含合作社/農會/青果社），濾掉縣市彙總名；探索完全失敗才退回靜態 MARKETS。
    查詢一律用 API 實際回報名（最可靠，解決『桃園』其實叫『桃農』對不到而漏掉整區的老問題）；
    顯示名盡量對回既有短名以維持用戶設定/『西螺』等不變。"""
    disc = discover_markets(end)
    real = {nm: c for nm, c in disc.items() if nm and not ROLLUP_MARKET.search(nm)}   # 濾掉『台北市場』等彙總
    if not real:
        print("[fetch_prices] ⚠ 市場探索無結果（API 可能需帶 Market，或當下無外網）→ 退回靜態清單。", file=sys.stderr)
        base = [(m, m) for m in MARKETS]
    else:
        out, used = [], set()
        for nm, _cnt in sorted(real.items(), key=lambda kv: (-kv[1], kv[0])):
            short = next((s for s in MARKETS if s in nm), None)   # 能對回既有短名就沿用（維持顯示名/用戶設定不變）
            display = short or nm
            if display in used:      # 顯示名撞名 → 改用 API 完整名區隔
                display = nm
            if display in used:
                continue
            used.add(display)
            out.append((display, nm))
        print(f"[fetch_prices] 市場清單：探索到 {len(disc)} 個（濾彙總後 {len(real)} 個）、要抓 {len(out)} 個"
              f"（含合作社/農會等）：{'、'.join(d for d, _ in out)}")
        base = out
    # 強制候選市場：探索沒看到的（如水里）也單獨以 2 年區間試抓一次，確認是否有歷史資料。
    have = {q for _, q in base} | {d for d, _ in base}
    for m in EXTRA_MARKETS:
        if m in have or any(m in q for _, q in base):
            continue
        base.append((m, m))
        print(f"[fetch_prices] 強制候選市場（探索未見）：{m} → 以 2 年區間試抓確認", file=sys.stderr)
    return base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=730)   # 近 2 年，讓採收預判/走勢更穩
    ap.add_argument("--out", default="veg_prices.json")
    ap.add_argument("--probe", default="", help="探測模式：指定市場短名（如 台北一），dump 原始欄位後結束")
    ap.add_argument("--discover", action="store_true",
                    help="只做市場探索：印出 API 實際回報的所有市場（含合作社/農會）後結束，不抓行情")
    ap.add_argument("--no-discover", action="store_true",
                    help="關閉動態探索，只抓靜態 MARKETS 清單（舊行為）")
    args = ap.parse_args()

    if args.probe:
        probe(args.probe)
        return

    now = datetime.now(TZ)

    if args.discover:
        disc = discover_markets(now)
        out = {"updated": now.strftime("%Y-%m-%d %H:%M"), "days": DISCOVER_DAYS,
               "count": len(disc), "markets": disc}
        with open("markets_discovered.json", "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"[discover] 近 {DISCOVER_DAYS} 天共探索到 {len(disc)} 個市場：")
        for nm, cnt in sorted(disc.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"   {nm}  ({cnt} 筆)")
        return

    start = now - timedelta(days=args.days)
    market_list = [(m, m) for m in MARKETS] if args.no_discover else resolve_markets(now)

    os.makedirs("prices", exist_ok=True)
    index_markets, latest, latest_var, categories, total = [], {}, {}, {}, 0
    for mk, query in market_list:
        recs = fetch_market_all(query, start, now)
        # 母作物 → 日 → 同日多品種明細；用種類代碼自動分類、跳過花卉/休市/0 元
        # varser：保留「每個品種」的完整歷史序列（供前端點品種看它自己的 K 線）
        agg, cat, varser = {}, {}, {}
        for rec in recs:
            c = CAT_BY_CODE.get(str(g(rec, "種類代碼", "TcType") or ""))
            if not c:
                continue
            full = str(g(rec, "作物名稱", "CropName") or "")
            base = norm_crop(full)
            if not base or base in SKIP_BASE or base.startswith("其他"):
                continue
            iso = iso_from_roc(str(g(rec, "交易日期", "TransDate") or ""))
            av = g(rec, "平均價", "Avg_Price")
            if not iso or av is None:
                continue
            try:
                a = float(av)
            except (TypeError, ValueError):
                continue
            if a <= 0:
                continue
            hi = float(g(rec, "上價", "Upper_Price") or a)
            md = float(g(rec, "中價", "Middle_Price") or a)
            lo = float(g(rec, "下價", "Lower_Price") or a)
            qty = float(g(rec, "交易量", "Trans_Quantity") or 0)
            agg.setdefault(base, {}).setdefault(iso, []).append((a, hi, lo, qty, md))
            cat[base] = c
            varser.setdefault(base, {}).setdefault(full, {}).setdefault(iso, []).append((a, hi, lo, qty, md))

        def _day(lst):
            tq = sum(x[3] for x in lst)
            avg = (sum(x[0] * x[3] for x in lst) / tq) if tq > 0 else (sum(x[0] for x in lst) / len(lst))
            mid = (sum(x[4] * x[3] for x in lst) / tq) if tq > 0 else (sum(x[4] for x in lst) / len(lst))
            return {"avg": round(avg, 1), "high": round(max(x[1] for x in lst), 1),
                    "mid": round(mid, 1), "low": round(min(x[2] for x in lst), 1), "qty": round(tq)}

        # 收斂：同日多品種 → 量加權均/中價、量加總、上價取 max、下價取 min
        mdata = {crop: {iso: _day(lst) for iso, lst in days.items()} for crop, days in agg.items()}
        # 品種明細（≥2 個品種才存；標籤取「-」後的品種名）；每品種存完整逐日序列供 K 線
        variants = {}
        for crop, fulls in varser.items():
            if len(fulls) < 2:
                continue
            vv = {}
            for full, days in fulls.items():
                label = (full.split("-", 1)[1].strip() if "-" in full else full) or "一般"
                vv[label] = {iso: _day(lst) for iso, lst in days.items()}
            variants[crop] = vv
        if not mdata:
            print(f"[fetch_prices] {mk}：無資料，略過。", file=sys.stderr)
            continue
        fname = f"prices/{len(index_markets) + 1:02d}.json"
        with open(fname, "w", encoding="utf-8") as f:
            json.dump({"market": mk, "updated": now.strftime("%Y-%m-%d %H:%M"),
                       "data": mdata, "variants": variants}, f, ensure_ascii=False)
        index_markets.append({"name": mk, "file": fname})
        latest[mk] = {c: _latest_valid(s) for c, s in mdata.items()}
        # 各品種最新價（供跨市場比價「品種細部」）：{作物:{品種:latest}}
        if variants:
            lv = {}
            for crop, vv in variants.items():
                d = {lab: _latest_valid(s) for lab, s in vv.items()}
                d = {lab: v for lab, v in d.items() if v}
                if d:
                    lv[crop] = d
            if lv:
                latest_var[mk] = lv
        categories.update(cat)
        total += sum(len(s) for s in mdata.values())
        print(f"[fetch_prices] {mk}：{len(mdata)} 種、{sum(len(s) for s in mdata.values())} 筆 → {fname}")

    crop_map = {c: c for c in categories}   # 展示名＝批發名＝母作物（API 驅動，免人工對照）
    index = {"updated": now.strftime("%Y-%m-%d %H:%M"),
             "years_back": round(args.days / 365, 1),
             "crop_map": crop_map,
             "categories": categories,   # {作物: 蔬菜|水果}（種類代碼自動分類）
             "markets": index_markets,
             "latest": latest,
             "latest_var": latest_var}
    with open("prices_index.json", "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)

    print(f"[fetch_prices] 寫出 prices_index.json + {len(index_markets)} 市場、{len(categories)} 種作物、共 {total} 筆")
    if not index_markets:
        print("[fetch_prices] ⚠ 全部市場 0 筆 —— 請確認 API/市場名（前端會退回示範資料）。", file=sys.stderr)


if __name__ == "__main__":
    main()
