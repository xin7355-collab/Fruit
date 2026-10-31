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
from datetime import date, datetime, timedelta, timezone

import requests

from http_retry import get_json, redact   # 指數退避重試（同目錄 http_retry.py）
import price_core                          # 進口分開、量加權、往年同期判斷、檔案格式（同目錄 price_core.py）

FMT = 2          # 檔案格式：2＝進口不併母作物、上下價量加權、品種拆檔(_v)、看板摘要(sum)。舊格式會自動整個重抓一次
INCR_DAYS = 14   # 平常只重抓最近 14 天（含晚到的更正）併進舊資料；每月第一次執行、格式變更或 --full 才整段重抓 2 年
ARCHIVE_YEARS = 0   # 精準進貨不封存（同一批市場的 5 年資料在精準務農那邊；避免 repo／Pages 網站體積翻倍）
SEEN_KEEP = 30      # index.seen：記錄每個交易日的資料「最早幾點」出現在 API（量測農業部上線時間，決定排程時段）

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
    j = get_json(API, params=params, headers=UA, timeout=60, label=f"{market}/{crop}")
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
    j = get_json(API, params=params, headers=UA, timeout=60, label=f"probe {market}")
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


API_CAP = 9999   # 農業部 API 單次最多回 9999 筆：回滿＝被截斷（大市場 90 天約 2～4 萬筆，只留下最後 3～5 週）


def _shrink_days(recs, days_now):
    """被截斷時估下一次該抓幾天：依這次回來資料涵蓋的日曆天數估密度，抓它的 8 成，且至少砍半。"""
    ds = sorted({iso_from_roc(str(g(r, "交易日期", "TransDate") or "")) for r in recs} - {None})
    covered = (datetime.fromisoformat(ds[-1]) - datetime.fromisoformat(ds[0])).days + 1 if ds else 0
    est = int(covered * 0.8) if covered else days_now // 2
    return max(1, min(days_now // 2, est))


def fetch_market_all(market, start, end, getter=None, sleep=time.sleep):
    """逐市場、不帶作物名、分段抓 → 回傳該市場所有原始 records。
    區段預設 91 天；回滿 API_CAP 筆代表被截斷 → 這批整個不用（不賭 API 的排序），縮短區段重抓，
    學到的區段長度沿用到同市場後面的區段（小市場 91 天一段即可，台北一/台中等大市場約 2～3 週一段）。
    回傳 (records, failed)：failed＝重試後仍失敗（或單日就超過上限）的段數。>0 代表資料有缺口，
    呼叫端不可拿來覆蓋舊檔（否則 K 線會少一段、90 日中位數失真）。"""
    out, cur, failed, days = [], start, 0, 91
    while cur <= end:
        ce = min(cur + timedelta(days=days - 1), end)
        params = {"StartDate": roc(cur), "EndDate": roc(ce), "Market": market}
        try:
            j = get_json(API, params=params, headers=UA, timeout=180, getter=getter, sleep=sleep,
                         label=f"{market} {roc(cur)}~{roc(ce)}")
            recs = j if isinstance(j, list) else (j.get("data") or j.get("RS") or j.get("Data") or [])
        except Exception as e:
            failed += 1
            print(f"[fetch_prices] {market} {roc(cur)}~{roc(ce)} 重試後仍失敗：{redact(e)}", file=sys.stderr)
            cur = ce + timedelta(days=1)
            sleep(0.3)
            continue
        sleep(0.3)
        span = (ce - cur).days + 1
        if len(recs) >= API_CAP and span > 1:
            days = _shrink_days(recs, span)
            print(f"[fetch_prices] {market} {roc(cur)}~{roc(ce)} 回滿 {len(recs)} 筆（被截斷）→ 改每 {days} 天一段重抓",
                  file=sys.stderr)
            continue                      # 同一個起日、較短區段重抓；被截斷的這批不採用
        if len(recs) >= API_CAP:
            failed += 1                   # 單日就超過上限：無法再切，視為不完整
            print(f"::warning::[fetch_prices] {market} {roc(cur)} 單日回滿 {len(recs)} 筆，資料可能不完整", file=sys.stderr)
        out.extend(recs)
        cur = ce + timedelta(days=1)
    return out, failed



# ── 行情檔切分（防 repo 膨脹）────────────────────────────────────────────
# 舊做法每天整檔重寫 prices/NN.json（每檔數 MB、全部約 90MB）→ git 歷史每天暴增。
# 新做法：
#   prices/NN.json         ＝「本月 1 號以前」的歷史（整個月內內容固定、不含 updated → git 無變更，每月只換一次）
#   prices/NN_recent.json  ＝「本月」逐日資料（小檔，每天更新）
# 前端 loadMarket 會把兩檔合併；_recent 不存在時只用歷史檔（相容舊資料）。
# 檔號以既有 prices_index.json 的「市場名→檔名」對照為準，新市場才取下一個空號
# → 市場排序（依探索成交筆數）每天變動也不會讓檔號跳動、造成整批重寫。
def month_start(dt):
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def months_back_start(now, days):
    """抓取起日：往回 `days` 天後再對齊到該月 1 號 → 同一個月內起日固定，歷史檔才會穩定不變。"""
    return month_start(now - timedelta(days=days))


BREAKER_MARKETS = 3   # 連續 3 個市場都抓不完整 → 判定 API 故障、熔斷


def load_old_index(path="prices_index.json"):
    try:
        with open(path, encoding="utf-8") as f:
            j = json.load(f)
        return j if isinstance(j, dict) else {}
    except Exception:
        return {}


def file_map_of(old_index):
    return {m["name"]: m["file"] for m in old_index.get("markets", []) if m.get("name") and m.get("file")}


def carry_forward(old_index, failed_markets, index_markets, latest, latest_var, categories):
    """抓失敗的市場：沿用上次 index 的條目（檔案也沒被覆蓋），而不是從清單消失。回傳實際沿用的市場名。"""
    old = {m.get("name"): m for m in old_index.get("markets", [])}
    have = {m["name"] for m in index_markets}
    carried = []
    for mk in failed_markets:
        ent = old.get(mk)
        if not ent or mk in have or not os.path.exists(ent.get("file", "")):
            continue
        index_markets.append(ent)
        if mk in (old_index.get("latest") or {}):
            latest[mk] = old_index["latest"][mk]
        if mk in (old_index.get("latest_var") or {}):
            latest_var[mk] = old_index["latest_var"][mk]
        for c in (latest.get(mk) or {}):
            if c in (old_index.get("categories") or {}):
                categories.setdefault(c, old_index["categories"][c])
        carried.append(mk)
    return carried


def backfill_archive(index_markets, now, keep_from, budget_s, fetch=None):
    """把 2 年窗以前、5 年以內的資料分「市場×年份」補進封存；每做完一個就寫檔並標記完成（index.archive_done），
    時間預算用完就停、留到下一次（長工作不可整批做完才存，否則被逾時砍掉會全部白做）。
    回傳 (完成數, 剩餘數)。"""
    fetch = fetch or fetch_market_all
    t0 = time.time()
    start5 = months_back_start(now, ARCHIVE_YEARS * 365).date()
    end = date.fromisoformat(keep_from) - timedelta(days=1)
    units = [(e, y) for e in sorted(index_markets, key=lambda e: e["name"])
             for y in range(start5.year, end.year + 1) if str(y) not in (e.get("archive_done") or [])]
    done_n = 0
    for i, (ent, y) in enumerate(units):
        if time.time() - t0 > budget_s:
            print(f"[backfill] 時間預算用完：完成 {done_n}、剩 {len(units) - i} 個（市場×年份）留到下次", file=sys.stderr)
            return done_n, len(units) - i
        lo, hi = max(start5, date(y, 1, 1)), min(end, date(y, 12, 31))
        if lo <= hi:
            recs, bad = fetch(ent.get("query") or ent["name"], datetime(lo.year, lo.month, lo.day, tzinfo=TZ),
                              datetime(hi.year, hi.month, hi.day, tzinfo=TZ))
            if bad:
                print(f"::warning::[backfill] {ent['name']} {y}：{bad} 段失敗，下次再補", file=sys.stderr)
                continue
            mdata, _, _, _, _ = price_core.aggregate(recs, g=g, norm_crop=norm_crop, cat_by_code=CAT_BY_CODE,
                                                     skip_base=SKIP_BASE, iso_from_roc=iso_from_roc,
                                                     dom_prev=ent.get("dom", []))
            ent["archive"] = price_core.add_to_archive(ent, ent["file"], ent["name"], mdata)
            # 封存補進來後，看板摘要的「比往年同期」要重算（只改本月檔的 sum，其他不動）
            refresh_summary(ent)
            print(f"[backfill] {ent['name']} {y}：{len(mdata)} 種、{sum(len(s) for s in mdata.values())} 筆 → 封存")
        ent["archive_done"] = sorted(set(ent.get("archive_done") or []) | {str(y)})
        done_n += 1
    if units:
        print(f"[backfill] 本次完成 {done_n} 個（市場×年份），全部回填完成" if done_n == len(units) else
              f"[backfill] 本次完成 {done_n}/{len(units)}")
    return done_n, 0


def refresh_summary(ent):
    data, var = price_core.read_market(ent, with_archive=True)
    if data is None or not ent.get("recent"):
        return
    with open(ent["recent"], encoding="utf-8") as f:
        rec = json.load(f)
    rec["sum"] = price_core.summarize(data, var or {})
    price_core._dump(ent["recent"], rec)


def load_file_map():
    try:
        with open("prices_index.json", encoding="utf-8") as f:
            old = json.load(f)
        return {m["name"]: m["file"] for m in old.get("markets", []) if m.get("name") and m.get("file")}
    except Exception:
        return {}


def assign_file(mk, file_map, used):
    fname = file_map.get(mk)
    if fname and fname not in used:
        return fname
    n = 1
    taken = used | set(file_map.values())
    while f"prices/{n:02d}.json" in taken:
        n += 1
    return f"prices/{n:02d}.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=730)   # 近 2 年，讓走勢更穩
    ap.add_argument("--probe", default="", help="探測模式：指定市場短名（如 台北一），dump 原始欄位後結束")
    ap.add_argument("--full", action="store_true", help="不用增量，整段重抓（預設每月第一次執行時自動整段重抓）")
    ap.add_argument("--backfill-minutes", type=float, default=20,
                    help="封存回填的時間預算（分鐘）；用完就留到下一次，避免整個工作被逾時砍掉而白做")
    args = ap.parse_args()

    if args.probe:
        probe(args.probe)
        return

    now = datetime.now(TZ)
    start = months_back_start(now, args.days)   # 對齊月初：同月內歷史檔內容固定
    old_index = load_old_index()
    file_map, used_files = file_map_of(old_index), set()
    old_by_name = {m.get("name"): m for m in old_index.get("markets", [])}
    # 整段重抓：手動 --full、每月 1 日（順便校正 API 事後修正的舊資料）、或舊檔格式不同
    t_start = time.time()
    # 每月第一次執行才整段重抓（校正 API 事後修正的舊資料）；改成每小時排程後，不能再用「1 號」判斷，否則 1 號會整段重抓 8 次
    full_all = args.full or old_index.get("fmt") != FMT or str(old_index.get("last_full", ""))[:7] != now.strftime("%Y-%m")
    keep_from = start.strftime("%Y-%m-%d")
    print(f"[fetch_prices] 模式：{'整段重抓 ' + keep_from + ' 起' if full_all else f'增量（近 {INCR_DAYS} 天）'}")
    failed_markets, consecutive_fail = [], 0

    os.makedirs("prices", exist_ok=True)
    index_markets, latest, latest_var, total = [], {}, {}, 0
    categories = {} if full_all else dict(old_index.get("categories") or {})
    for mk in MARKETS:
        query = mk
        if consecutive_fail >= BREAKER_MARKETS:
            failed_markets.append(mk)       # 熔斷：API 看起來整個掛了，剩下的市場不再白等，沿用舊資料
            continue
        old_ent = old_by_name.get(mk)
        old_data = old_var = None
        if old_ent:
            old_data, old_var = price_core.read_market(old_ent)
        arch = dict((old_ent or {}).get("archive") or {})
        if ARCHIVE_YEARS and old_data and old_ent.get("file"):
            dropped = {c: {d: v for d, v in s.items() if d < keep_from} for c, s in old_data.items()}
            dropped = {c: s for c, s in dropped.items() if s}
            if dropped:
                arch = price_core.add_to_archive(old_ent, old_ent["file"], mk, dropped)
        if full_all:
            old_data = old_var = None
        incremental = old_data is not None
        fstart = (now - timedelta(days=INCR_DAYS - 1)).replace(hour=0, minute=0, second=0, microsecond=0) \
            if incremental else start
        recs, bad_chunks = fetch_market_all(query, fstart, now)
        if bad_chunks:
            consecutive_fail += 1
            failed_markets.append(mk)
            print(f"::warning::[fetch_prices] {mk}：{bad_chunks} 段重試後仍失敗 → 本次不覆蓋，沿用上次資料", file=sys.stderr)
            continue
        consecutive_fail = 0
        mdata_w, var_w, cat, closed_w, dom = price_core.aggregate(
            recs, g=g, norm_crop=norm_crop, cat_by_code=CAT_BY_CODE, skip_base=SKIP_BASE,
            iso_from_roc=iso_from_roc, dom_prev=(old_ent or {}).get("dom", []) if incremental else ())
        if not mdata_w:
            if mk in file_map:   # 上次有資料、這次 0 筆 ＝ API 異常（維護時常回空陣列），不是真的沒交易
                failed_markets.append(mk)
                print(f"::warning::[fetch_prices] {mk}：這次 0 筆但上次有資料 → 視為 API 異常，沿用上次資料", file=sys.stderr)
            else:
                print(f"[fetch_prices] {mk}：無資料，略過。", file=sys.stderr)
            continue
        if incremental:   # 新抓的日子覆蓋舊的（含 API 事後更正），其餘保留；超過 2 年窗的丟掉
            mdata = price_core.merge_series(old_data, mdata_w, keep_from)
            variants = price_core.merge_variants(old_var, var_w, keep_from)
            closed = set(closed_w) | set((old_ent or {}).get("closed", []))
        else:
            mdata, variants, closed = mdata_w, var_w, closed_w
        fname = assign_file(mk, file_map, used_files)
        used_files.add(fname)
        long_data = mdata
        if arch:   # 摘要的「比往年同期」用到 5 年封存（品種不封存，仍是 2 年）
            long_data = {}
            for y in sorted(arch):
                long_data = price_core.merge_series(long_data, price_core.read_archive(arch[y]))
            long_data = price_core.merge_series(long_data, mdata)
        paths = price_core.write_market(fname, mk, now, mdata, variants, price_core.summarize(long_data, variants))
        index_markets.append({"name": mk, **paths, "dom": sorted(dom), "closed": price_core.trim_closed(closed, now),
                              "query": query, "archive": arch,
                              "archive_done": sorted((old_ent or {}).get("archive_done", []))})
        latest[mk] = {c: _latest_valid(s) for c, s in mdata.items()}
        # 各品種最新價（供跨市場比價「品種細部」）：{作物:{品種:latest}}
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
        print(f"[fetch_prices] {mk}：{'增量 ' + str(len(mdata_w)) + ' 種／' if incremental else ''}"
              f"{len(mdata)} 種、{sum(len(s) for s in mdata.values())} 筆 → {fname}")

    carried = carry_forward(old_index, failed_markets, index_markets, latest, latest_var, categories)
    fetched = len(index_markets) - len(carried)
    if fetched == 0:
        print("::error::[fetch_prices] 本次沒有任何市場完整抓到（API 可能整個掛了）→ 不寫出任何檔案，保留上次資料。",
              file=sys.stderr)
        sys.exit(1)
    if failed_markets:
        print(f"::warning::[fetch_prices] 本次 {len(failed_markets)} 個市場未完整抓到：{'、'.join(failed_markets)}；"
              f"其中 {len(carried)} 個沿用上次資料：{'、'.join(carried) or '（無舊資料可沿用）'}", file=sys.stderr)

    if ARCHIVE_YEARS:
        budget = max(0.0, args.backfill_minutes) * 60 - (time.time() - t_start)
        backfill_archive(index_markets, now, keep_from, budget)

    crop_map = {c: c for c in categories}   # 展示名＝批發名＝母作物（API 驅動，免人工對照）
    latest_day = max((v.get("date", "") for m in latest.values() for v in m.values() if v), default="")
    seen = dict(old_index.get("seen") or {})
    if latest_day and latest_day not in seen:
        seen[latest_day] = now.strftime("%H:%M")      # 這個交易日的資料最早在這次執行出現
    seen = dict(sorted(seen.items())[-SEEN_KEEP:])
    index = {"updated": now.strftime("%Y-%m-%d %H:%M"),
             "fmt": FMT,
             "last_full": now.strftime("%Y-%m-%d") if full_all else old_index.get("last_full", ""),
             "seen": seen,
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
