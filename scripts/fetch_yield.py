#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_yield.py  ——  農業部「生產概況」開放資料 → yield_index.json（各縣市每公頃單產）

抓農業部農業資料開放平臺（免金鑰）：
  - 蔬菜生產概況  UnitId=113
  - 果品生產概況  UnitId=135
取各縣市 × 各作物「每公頃平均產量(公斤/公頃)」，輸出前端可用的：
  yield_index.json = {
    "updated": "...", "source": "MOA UnitId 113/135",
    "national": {作物: kg_per_ha, ...},              # 全國(各縣市平均)
    "counties": {縣市: {作物: kg_per_ha, ...}, ...}   # 逐縣市
  }
前端 yieldPerM2() 以「所在縣市」優先取用，查不到退回內建估算值 → 全國化的產量試算。

⚠ 政府站對機器抓取偶回 403 / CORS 不保證 → 只放後端排程；抓不到就寫空物件，前端自動退回估算值。
資料授權：政府資料開放授權條款第 1 版（需標示來源）。
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    requests = None

TZ = timezone(timedelta(hours=8))
BASE = "https://data.moa.gov.tw/Service/OpenData/DataFileService.aspx"
DATASETS = {"veg": "113", "fruit": "135"}

# 排除的「地區別」彙總列（非單一縣市）
AGG_REGIONS = {"臺灣省", "台灣省", "臺灣地區", "台灣地區", "合計", "總計", "全省", "全國",
               "臺灣", "台灣", "農糧署", ""}


def moa_get(unit_id):
    """抓 MOA OpenData JSON（回傳 list[dict]；失敗回 None）。"""
    if requests is None:
        return None
    url = f"{BASE}?UnitId={unit_id}&IsTransData=1"
    try:
        r = requests.get(url, timeout=40, headers={"User-Agent": "Mozilla/5.0 nongshang-radar"})
        r.raise_for_status()
        j = r.json()
        if isinstance(j, dict):                      # 有些包一層
            for v in j.values():
                if isinstance(v, list):
                    return v
            return None
        return j if isinstance(j, list) else None
    except Exception as e:
        print(f"[fetch_yield] UnitId={unit_id} 抓取失敗：{e}", file=sys.stderr)
        return None


def _norm_county(nm):
    return str(nm or "").strip().replace("臺", "台")


def _find_key(row, *needles):
    """找出 dict 中「包含所有 needles 子字串」的第一個鍵（容忍 _公斤 等後綴、大小寫欄名差異）。"""
    for k in row.keys():
        ks = str(k)
        if all(n in ks for n in needles):
            return k
    return None


def parse_overview(rows, cat_key_needle):
    """解析生產概況：回傳 {縣市: {作物: kg_per_ha}}，只留最新年度。
    cat_key_needle：'蔬菜類別' 或 '果品類別' 的辨識子字串（'類別'）。"""
    if not rows or not isinstance(rows, list) or not isinstance(rows[0], dict):
        return {}
    sample = rows[0]
    k_region = _find_key(sample, "地區")
    k_cat = _find_key(sample, cat_key_needle) or _find_key(sample, "類別") or _find_key(sample, "作物")
    k_yield = _find_key(sample, "每公頃")            # 每公頃平均產量(_公斤)
    k_year = _find_key(sample, "年")
    if not (k_region and k_cat and k_yield):
        print(f"[fetch_yield] 欄位對不到：region={k_region} cat={k_cat} yield={k_yield}", file=sys.stderr)
        return {}
    # 找最新年度
    years = []
    for r in rows:
        try:
            years.append(int(str(r.get(k_year, "")).strip()))
        except (TypeError, ValueError):
            pass
    latest_year = max(years) if years else None

    out = {}
    for r in rows:
        if latest_year is not None and k_year:
            try:
                if int(str(r.get(k_year, "")).strip()) != latest_year:
                    continue
            except (TypeError, ValueError):
                continue
        region = _norm_county(r.get(k_region))
        if region in AGG_REGIONS or "區農糧" in region:
            continue
        crop = str(r.get(k_cat, "")).strip()
        if not crop:
            continue
        try:
            kgha = float(str(r.get(k_yield, "")).replace(",", "").strip())
        except (TypeError, ValueError):
            continue
        if kgha <= 0:
            continue
        out.setdefault(region, {})[crop] = round(kgha, 1)
    return out


def merge_counties(*dicts):
    m = {}
    for d in dicts:
        for county, crops in (d or {}).items():
            m.setdefault(county, {}).update(crops)
    return m


def national_avg(counties):
    """各作物取縣市平均（簡單平均，供無縣市對應時的全國退回值）。"""
    acc = {}
    for crops in counties.values():
        for crop, v in crops.items():
            acc.setdefault(crop, []).append(v)
    return {c: round(sum(v) / len(v), 1) for c, v in acc.items() if v}


def main():
    now = datetime.now(TZ)
    veg_rows = moa_get(DATASETS["veg"])
    fruit_rows = moa_get(DATASETS["fruit"])
    veg = parse_overview(veg_rows, "蔬菜")
    fruit = parse_overview(fruit_rows, "果品")
    counties = merge_counties(veg, fruit)
    national = national_avg(counties)

    out = {"updated": now.strftime("%Y-%m-%d %H:%M"),
           "source": "MOA 生產概況 UnitId 113/135（政府資料開放授權條款）",
           "national": national, "counties": counties}
    with open("yield_index.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)

    # 除錯：原始結構 + 縣市/作物摘要，供校準欄位名
    try:
        def head(rows):
            return {"__len__": len(rows), "__keys__": list(rows[0].keys()) if rows and isinstance(rows[0], dict) else None,
                    "__sample__": rows[0] if rows else None} if isinstance(rows, list) else str(type(rows))
        with open("yield_debug.json", "w", encoding="utf-8") as f:
            json.dump({"generated": now.isoformat(), "veg_raw": head(veg_rows), "fruit_raw": head(fruit_rows),
                       "counties": sorted(counties.keys()),
                       "veg_crops_sample": sorted({c for cr in veg.values() for c in cr})[:60],
                       "fruit_crops_sample": sorted({c for cr in fruit.values() for c in cr})[:40]},
                      f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[fetch_yield] debug dump 失敗：{e}", file=sys.stderr)

    print(f"[fetch_yield] 縣市 {len(counties)}、全國作物 {len(national)} 種 → yield_index.json")


if __name__ == "__main__":
    main()
