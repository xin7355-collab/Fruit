"""
fetch_retail.py —— 抓「臺北市公有零售市場行情」（臺北市資料大平臺，每月各品項平均零售價，元/台斤）→ retail.json

為什麼：商人定零售價、消費者判斷有沒有買貴，都需要「零售大約是批發的幾倍」。
實測（2024-10～2025-12，27 種蔬菜）：零售 ÷ 台北一批發的中位數 2.8 倍，但甘藍、大白菜約 5 倍、
韭菜、空心菜約 1.7 倍 → 不能用單一倍數，要每種菜各算各的。

資料集與欄位（項目、平均零售價）參考 mina201199/veggie-price-tw tools/fetch_retail.py（MIT）的實際用法；
本檔程式與品名對照表為自行撰寫。資料集是否仍每月更新沒驗到（該專案資料停在 2025-12）→ 前端會標出資料月份。

輸出 retail.json：
  months: {"2025-12": {"甘藍": 68.0, ...}}           原始月均價（下次只補新月份，不重複下載）
  items:  {批發名: [{item, variety, latest:[月, 價], mult, lo, hi, n}]}   倍數＝零售(元/台斤) ÷ 台北一批發月均(元/台斤)
失敗時保留舊檔、不寫空檔（工作流亮黃燈，不擋行情更新）。
用法（在 merchant/ 下）：python3 scripts/fetch_retail.py [--out retail.json]
"""
import argparse
import csv
import io
import json
import os
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from http_retry import get_json, get_text, redact  # noqa: E402
import price_core  # noqa: E402

TZ = timezone(timedelta(hours=8))
DATASET = "54d9d492-1e2e-40d1-ae7b-fbce6f271bf1"
META = f"https://data.taipei/api/frontstage/tpeod/dataset.view?id={DATASET}"
DOWNLOAD = "https://data.taipei/api/dataset/" + DATASET + "/resource/{rid}/download"
REF_MARKET = "台北一"
JIN = 0.6               # 1 台斤＝0.6 公斤
MULT_MONTHS = 12        # 倍數取最近 12 個有資料的月份
# 零售品名 → (批發母作物, 品種關鍵字或 None)。只放我們批發資料裡有的作物；魚肉蛋不放。
RETAIL_MAP = {
    "冬瓜": ("冬瓜", None), "大芥菜": ("芥菜", None), "小白菜": ("小白菜", None), "扁蒲": ("扁蒲", None),
    "敏豆(四季豆)": ("敏豆", None), "木耳(黑色溼)": ("濕木耳", None), "本島萵苣": ("萵苣菜", None),
    "萵苣(油麥菜)": ("萵苣菜", "油麥"), "洋蔥(內銷)": ("洋蔥", None), "牛蒡": ("牛蒡", None), "甘藍": ("甘藍", None),
    "甜椒(青椒)": ("甜椒", None), "甜玉米": ("玉米", None), "甜豌豆": ("豌豆", "甜豌豆"), "豌豆": ("豌豆", None),
    "生薑(嫩薑)": ("薑", None), "番茄": ("番茄", None), "結球白菜": ("包心白", None), "絲瓜": ("絲瓜", None),
    "綠竹筍": ("竹筍", "綠竹"), "麻竹筍": ("竹筍", "麻竹"), "綠蘆筍": ("蘆筍", None), "胡瓜": ("胡瓜", None),
    "胡蘿蔔": ("胡蘿蔔", None), "芋頭": ("芋", None), "芥藍": ("芥藍菜", None), "花椰菜": ("花椰菜", None),
    "花胡瓜": ("花胡瓜", None), "芹菜(土芹菜)": ("芹菜", None), "苦瓜": ("苦瓜", None), "茄子": ("茄子", None),
    "茭白筍(帶殼)": ("茭白筍", None), "茼萵": ("茼蒿", None), "莧菜": ("莧菜", None), "菠菜": ("菠菜", None),
    "蒜頭": ("大蒜", None), "蕹菜(空心菜)": ("蕹菜", None), "蘿蔔": ("蘿蔔", None), "豇豆": ("菜豆", None),
    "青江白菜": ("青江白菜", None), "青花苔": ("青花苔", None), "青蔥": ("青蔥", None), "韭菜": ("韭菜", None),
    "香菇(太空包)鮮": ("濕香菇", None), "馬鈴薯": ("馬鈴薯", None),
    "小番茄(玉女)": ("小番茄", "玉女"), "小番茄(聖女)": ("小番茄", "聖女"), "巨峰葡萄": ("葡萄", "巨峰"),
    "文旦": ("柚子", "文旦"), "白柚": ("柚子", "白柚"), "新世紀梨": ("梨", "新世紀"), "新興梨": ("梨", "新興"),
    "橫山梨": ("梨", "橫山"), "木瓜": ("木瓜", None), "李(玉李)": ("李", "玉李"), "李(紅肉)": ("李", "紅肉"),
    "枇杷": ("枇杷", None), "柳橙": ("甜橙", None), "桶柑": ("桶柑", None), "棗子": ("棗子", None),
    "椪柑": ("椪柑", None), "楊桃": ("楊桃", None), "水蜜桃": ("桃子", "水蜜"), "甜桃": ("桃子", "甜桃"),
    "洋香瓜(秋蜜)": ("洋香瓜", None), "海梨柑": ("海梨柑", None), "火龍果": ("紅龍果", None),
    "甜柿": ("柿子", "甜柿"), "牛心柿": ("柿子", "牛心"), "番石榴(世紀)": ("番石榴", "世紀"),
    "番石榴(珍珠)": ("番石榴", "珍珠"), "番荔枝(釋迦)": ("釋迦", None), "芒果(在來)": ("芒果", "在來"),
    "芒果(愛文)": ("芒果", "愛文"), "芒果(金煌)": ("芒果", "金煌"), "茂谷柑": ("茂谷柑", None),
    "荔枝(玉荷)": ("荔枝", "玉荷"), "荔枝(黑葉)": ("荔枝", "黑葉"), "蓮霧": ("蓮霧", None),
    "西瓜(大粒)": ("西瓜", None), "香瓜(美濃瓜)": ("甜瓜", "美濃"), "香蕉(內銷)": ("香蕉", None),
    "鳳梨(17號)": ("鳳梨", None), "龍眼(大粒)": ("龍眼", None)}


def ym_of(name):
    m = re.match(r"\s*(\d+)年(\d+)月", name or "")
    return f"{int(m.group(1)) + 1911}-{int(m.group(2)):02d}" if m else None


def parse_csv(txt):
    rows = list(csv.DictReader(io.StringIO(txt)))
    if not rows:
        return {}
    col = next((k for k in rows[0] if k and "平均" in k), None)
    key = next((k for k in rows[0] if k and "項目" in k), None)
    if not col or not key:
        return {}
    out = {}
    for r in rows:
        try:
            v = float(str(r.get(col, "")).replace(",", "").strip())
        except ValueError:
            continue
        if v > 0:
            out[r[key].strip()] = v
    return out


def wholesale_monthly(series, variety=None, variants=None):
    """批發月均（元/公斤）：有品種關鍵字且找得到品種就用品種，否則用母作物。"""
    s = series
    if variety and variants:
        lab = next((k for k in variants if variety in k), None)
        if lab:
            s = variants[lab]
    by = {}
    for d, v in s.items():
        if v and (v.get("avg") or 0) > 0:
            by.setdefault(d[:7], []).append(v["avg"])
    return {ym: statistics.mean(xs) for ym, xs in by.items() if len(xs) >= 10}


def build_items(months, data, variants):
    items = {}
    for item, (crop, variety) in RETAIL_MAP.items():
        if crop not in data:
            continue
        wm = wholesale_monthly(data[crop], variety, (variants or {}).get(crop))
        pairs = [(ym, months[ym][item] / (wm[ym] * JIN)) for ym in sorted(months) if item in months[ym] and ym in wm]
        pairs = pairs[-MULT_MONTHS:]
        last = max((ym for ym in months if item in months[ym]), default=None)
        if len(pairs) < 3 or not last:
            continue
        ms = sorted(m for _, m in pairs)
        q = lambda p: ms[min(len(ms) - 1, int(len(ms) * p))]
        items.setdefault(crop, []).append({"item": item, "variety": variety, "latest": [last, months[last][item]],
                                           "mult": round(statistics.median(ms), 2), "lo": round(q(0.25), 2),
                                           "hi": round(q(0.75), 2), "n": len(ms)})
    return items


def main(argv=None, getter=None, sleep=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="retail.json")
    ap.add_argument("--index", default="prices_index.json")
    a = ap.parse_args(argv)
    now = datetime.now(TZ)
    kw = {"getter": getter, **({"sleep": sleep} if sleep else {})}   # 測試可注入，不真的等重試
    try:
        with open(a.out, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        old = {}
    months = dict(old.get("months") or {})
    try:
        meta = get_json(META, timeout=60, label="臺北市零售行情 目錄", **kw)
        raw = (meta.get("payload") or {}).get("resources", []) if isinstance(meta, dict) else []
        res = sorted((ym, rid) for ym, rid in ((ym_of(r.get("name")), r.get("rid")) for r in raw) if ym and rid)
        # 記清楚目錄裡有什麼：分得出「資料集本來就只到某月」和「名稱格式改了、程式看不懂」（不默默失敗）
        if raw and not res:
            print(f"::warning::[fetch_retail] 目錄有 {len(raw)} 筆資源，但月份名稱看不懂（例：{raw[-1].get('name')}）→ 名稱格式可能改了",
                  file=sys.stderr)
        recent = {ym for ym, _ in res[-2:]}                    # 最近兩個月可能被修正 → 每次重抓
        got_n = 0
        for ym, rid in res:
            if ym in months and ym not in recent:
                continue
            got = parse_csv(get_text(DOWNLOAD.format(rid=rid), timeout=60, label=f"零售 {ym}", **kw))
            if got:
                months[ym] = got
                got_n += 1
            else:
                print(f"::warning::[fetch_retail] {ym} 的 CSV 讀不到「項目／平均價」欄位 → 欄位名稱可能改了", file=sys.stderr)
        print(f"[fetch_retail] 目錄 {len(res)} 個月份（{res[0][0] if res else '—'}～{res[-1][0] if res else '—'}），這次下載 {got_n} 個")
    except Exception as e:
        print(f"::warning::[fetch_retail] 抓取失敗：{redact(e)}", file=sys.stderr)
    if not months:
        print("::warning::[fetch_retail] 沒有任何零售資料 → 保留舊檔、不覆蓋（下次排程再試）", file=sys.stderr)
        return 1
    try:
        ix = json.load(open(a.index, encoding="utf-8"))
        ent = next(m for m in ix["markets"] if m["name"] == REF_MARKET)
        data, variants = price_core.read_market(ent)
    except Exception as e:
        print(f"::warning::[fetch_retail] 讀不到 {REF_MARKET} 批發資料（{e}）→ 只更新零售原始月份", file=sys.stderr)
        data, variants = {}, {}
    out = {"updated": now.strftime("%Y-%m-%d %H:%M"), "source": "臺北市公有零售市場行情（臺北市資料大平臺，每月）",
           "unit": "元/台斤", "ref_market": REF_MARKET, "months": dict(sorted(months.items())),
           "items": build_items(months, data or {}, variants or {}) if data else (old.get("items") or {})}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    print(f"[fetch_retail] 零售月份 {len(months)} 個（最新 {max(months)}）、可算倍數 {sum(len(v) for v in out['items'].values())} 項 → {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
