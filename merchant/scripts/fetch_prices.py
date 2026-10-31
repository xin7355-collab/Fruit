#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_prices.py  ——  抓農業部「農產品批發市場交易行情」公開資料 → prices_index.json + prices/NN.json

免金鑰的公開 API。輸出 schema 與前端 index.html 既有約定一致：
  { updated, markets:[...], crop_map:{展示名:批發名}, data:{市場:{批發名:{ISO日:{avg,high,mid,low,qty}}}} }

⚠ 本開發沙盒的網路政策擋 data.moa.gov.tw，無法本機實測；於 GitHub Actions（有外網）執行，
   再由 Action log 驗證每個品項抓到幾筆。抓不到的品項會安全略過，前端自動不顯示、退回示範資料。

用法：
  python3 scripts/fetch_prices.py --days 365
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

# 全台主要果菜批發市場（用短名做 API 過濾，較易匹配；對不到的自動略過，安全）。
MARKETS = ["台北一", "台北二", "三重", "板橋", "宜蘭", "桃園", "台中", "豐原",
           "南投", "溪湖", "永靖", "西螺", "北港", "嘉義", "高雄", "鳳山",
           "屏東", "台東", "花蓮"]

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=730)   # 近 2 年，讓走勢更穩
    ap.add_argument("--probe", default="", help="探測模式：指定市場短名（如 台北一），dump 原始欄位後結束")
    args = ap.parse_args()

    if args.probe:
        probe(args.probe)
        return

    now = datetime.now(TZ)
    start = now - timedelta(days=args.days)

    os.makedirs("prices", exist_ok=True)
    index_markets, latest, latest_var, categories, total = [], {}, {}, {}, 0
    for mk in MARKETS:
        recs = fetch_market_all(mk, start, now)
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
