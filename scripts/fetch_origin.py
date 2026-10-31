"""
fetch_origin.py —— 抓農業部「農產品產地價格」（各地農會查報的全台當日平均價）→ origin.json

為什麼：農友要判斷「賣給販仔（產地價）」還是「自己送拍賣（批發價扣手續費）」比較划算，
App 以前只有批發價。資料集涵蓋約 25 種大宗品項（多為水果，另有甘藍、蒜頭、甘藷等），葉菜很少。

資料來源（免金鑰）：https://data.moa.gov.tw/Service/OpenData/TransService.aspx?UnitId=WVOiWSdDjWxx
欄位依 midssinglin/taiwan-price 的 scripts/update.mjs 實際使用的 YEAR/MONTH/PERIOD/PRODUCTNAME/ORGNAME/AVGPRICE
（該專案無授權，只參考欄位名稱、程式自行撰寫）；單位由數值推斷為元/公斤（官方欄位說明沒驗到）。

輸出：origin.json = {updated, source, unit, items: {批發名: [{name, latest:[日期, 價], series:{日期: 價}}]}}
失敗時保留舊檔、不寫空檔（工作流會亮黃燈警告，不擋行情更新）。
用法：python3 scripts/fetch_origin.py [--out origin.json]
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from http_retry import get_json, redact  # noqa: E402

TZ = timezone(timedelta(hours=8))
API = "https://data.moa.gov.tw/Service/OpenData/TransService.aspx"
UNIT_ID = "WVOiWSdDjWxx"
AVG_NAME = "當日平均價"
KEEP_DAYS = 120
# 產地價格品名 → 批發行情的母作物名（對不到的照原名保留，前端只顯示批發資料裡有的作物）
ALIAS = {"青香蕉": "香蕉", "蒜頭": "大蒜", "甘藷": "甘薯", "柳橙": "甜橙", "番荔枝": "釋迦", "鳳梨釋迦": "釋迦",
         "甜柿": "柿子", "紅柿": "柿子", "西洋梨": "梨", "金柑": "柑橘"}


def crop_of(product):
    base = re.sub(r"[(（].*?[)）]", "", product or "").strip()
    return ALIAS.get(base, base)


def iso_of(rec):
    try:
        y, m, d = int(rec.get("YEAR")), int(rec.get("MONTH")), int(rec.get("PERIOD"))
    except (TypeError, ValueError):
        return None
    if y < 1911:
        y += 1911            # 防：若哪天改回民國年
    try:
        return datetime(y, m, d).strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse(rows):
    """回傳 {品名: {日期: 價}}（只取「當日平均價」那一列）。"""
    out = {}
    for r in rows or []:
        if not isinstance(r, dict) or r.get("ORGNAME") != AVG_NAME:
            continue
        iso, name = iso_of(r), (r.get("PRODUCTNAME") or "").strip()
        try:
            v = float(r.get("AVGPRICE"))
        except (TypeError, ValueError):
            continue
        if iso and name and v > 0:
            out.setdefault(name, {})[iso] = round(v, 2)
    return out


def merge(old, fresh, now):
    """old/fresh：{品名: {日期: 價}}；保留近 KEEP_DAYS 天。"""
    cut = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    out = {}
    for name in set(old) | set(fresh):
        s = dict(old.get(name, {}))
        s.update(fresh.get(name, {}))
        s = {d: v for d, v in s.items() if d >= cut}
        if s:
            out[name] = s
    return out


def build(series_by_name, now):
    items = {}
    for name, s in sorted(series_by_name.items()):
        d = max(s)
        items.setdefault(crop_of(name), []).append({"name": name, "latest": [d, s[d]], "series": dict(sorted(s.items()))})
    return {"updated": now.strftime("%Y-%m-%d %H:%M"), "source": "農業部 農產品產地價格（各地農會查報的全台當日平均價）",
            "unit": "元/公斤", "items": items}


def load_old(path):
    try:
        with open(path, encoding="utf-8") as f:
            j = json.load(f)
        return {x["name"]: x.get("series", {}) for its in j.get("items", {}).values() for x in its}
    except Exception:
        return {}


def main(argv=None, getter=None, sleep=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="origin.json")
    a = ap.parse_args(argv)
    now = datetime.now(TZ)
    kw = {"getter": getter, **({"sleep": sleep} if sleep else {})}   # 測試可注入，不真的等重試
    fresh = {}
    for params in ({"UnitId": UNIT_ID, "IsTransData": 1, "ORGNAME": AVG_NAME}, {"UnitId": UNIT_ID, "IsTransData": 1}):
        label = f"產地價格 {params.get('ORGNAME', '全部')}"
        try:
            rows = get_json(API, params=params, timeout=60, label=label, **kw)
            rows = rows if isinstance(rows, list) else []
            got = parse(rows)
            for name, s in got.items():
                fresh.setdefault(name, {}).update(s)
            # 記下收到幾列、認得幾列：回了資料卻一列都認不得＝欄位名稱改了，要亮黃燈
            print(f"[fetch_origin] {label}：收到 {len(rows)} 列，認得 {sum(len(s) for s in got.values())} 筆"
                  f"（{len(got)} 個品名）")
            if rows and not got:
                print(f"::warning::[fetch_origin] {label} 有回資料但一筆都認不得（欄位：{sorted(rows[0])[:8] if isinstance(rows[0], dict) else type(rows[0]).__name__}）"
                      "→ 欄位名稱可能改了", file=sys.stderr)
        except Exception as e:
            print(f"::warning::[fetch_origin] 抓取失敗：{redact(e)}", file=sys.stderr)
    if not fresh:
        print("::warning::[fetch_origin] 這次沒抓到任何產地價格 → 保留舊檔、不覆蓋（下次排程再試）", file=sys.stderr)
        return 1
    out = build(merge(load_old(a.out), fresh, now), now)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    n = sum(len(v) for v in out["items"].values())
    newest = max((x["latest"][0] for v in out["items"].values() for x in v), default="—")
    print(f"[fetch_origin] {n} 個品名（{len(out['items'])} 種作物，最新 {newest}）→ {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
