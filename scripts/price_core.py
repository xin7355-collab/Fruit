"""
price_core.py —— 行情處理核心（精準務農 scripts/ 與精準進貨 merchant/scripts/ 用同一份內容，測試會比對）。

放「不連網、可單元測試」的邏輯，fetch_prices.py 只負責抓資料與流程：
- aggregate()       原始成交 → 每日序列。進口品不併進母作物（只留在品種裡）；上/下價改量加權；收集休市日。
- merge_series()    增量抓取：新抓到的日子覆蓋舊的、其他日子保留。
- signal()          看板摘要：偏貴/偏便宜（單日價 vs 近 90 筆中位數 ±10%，回測後保留舊法）、
                    比往年同期（補充資訊）、較上週、量少提示。
- summarize()       每個作物＋品種的看板摘要（前端先用摘要畫看板，不必等品種歷史）。
- write_market()    寫三個檔：NN.json（本月前母作物）、NN_v.json（本月前品種，點品種才載）、NN_recent.json（本月＋摘要）。

「往年同期」做法與門檻 0.88/1.12 參考 mina201199/veggie-price-tw（MIT），本檔程式為自行撰寫；
回測後未採用為標籤（見 signal_es 說明），只當補充資訊。
"""
import json
import os
import statistics
from datetime import date, datetime, timedelta

IMPORT_MARK = "進口"
TAG_LO, TAG_HI = 0.88, 1.12        # 往年同期基準：≤0.88 偏便宜、≥1.12 偏貴
OLD_TAG = 0.10                      # 舊法：90 筆中位數 ±10%（退回用，也是回測對照組）
SEASON_WEEKS = 2                    # 同週前後 2 週
SEASON_GAP_DAYS = 300               # 只用 300 天以前的資料當「往年」，避免把今年這一季算進基準
SEASON_MIN = 15                     # 往年同期至少 15 個交易日才判斷，否則退回近 90 筆
THIN_QTY = 300                      # 近 3 個交易日平均日量 < 300 公斤 → 標「量少，價格容易大起大落」
CLOSED_KEEP_DAYS = 120              # 休市日只保留近 120 天


def _d(iso):
    return date.fromisoformat(iso)


def week_of(iso):
    """與前端 weekOf 完全一致：doy＝距「1 月 0 日」的天數（1/1→1），再 //7。"""
    d = _d(iso)
    return ((d - date(d.year, 1, 1)).days + 1) // 7


def _week_dist(a, b):
    x = abs(a - b) % 52
    return min(x, 52 - x)


# ───────────────────────── 原始成交 → 每日序列 ─────────────────────────
def day_agg(rows):
    """同一天多筆（多品種/多次拍賣）→ 一筆。均價、中價、上價、下價都用交易量加權。
    上/下價以前取 max/min：台北一甘藍 10/30「最高 120」只來自 43 公斤甘藍心（當天 16.5 萬公斤），區間被冷門品種撐開。"""
    tq = sum(r[3] for r in rows)
    if tq > 0:
        w = [sum(r[i] * r[3] for r in rows) / tq for i in range(5)]
        avg, hi, lo, mid = w[0], w[1], w[2], w[4]
    else:
        n = len(rows)
        avg, mid = sum(r[0] for r in rows) / n, sum(r[4] for r in rows) / n
        hi, lo = max(r[1] for r in rows), min(r[2] for r in rows)
    return {"avg": round(avg, 1), "high": round(hi, 1), "mid": round(mid, 1), "low": round(lo, 1), "qty": round(tq)}


def variety_label(full):
    return (full.split("-", 1)[1].strip() if "-" in full else full) or "一般"


def aggregate(recs, *, g, norm_crop, cat_by_code, skip_base, iso_from_roc, dom_prev=()):
    """回傳 (mdata, variants, cat, closed, dom_crops)。
    dom_crops：曾有國產成交的作物。母作物只用國產（進口留在品種裡）；從沒有國產的作物（多數進口水果）才用進口價。
    （台北二「草莓」最新價 192.6 元其實是進口；國產 3 月就下市了。）"""
    dom_rows, imp_rows, cat, varser, closed = {}, {}, {}, {}, set()
    dom_crops = set(dom_prev)
    for rec in recs:
        full = str(g(rec, "作物名稱", "CropName") or "").strip()
        iso = iso_from_roc(str(g(rec, "交易日期", "TransDate") or ""))
        if not iso:
            continue
        if full == "休市" or norm_crop(full) == "休市":     # API 以「休市」列（價量 0）標示休市日
            closed.add(iso)
            continue
        c = cat_by_code.get(str(g(rec, "種類代碼", "TcType") or ""))
        if not c:
            continue
        base = norm_crop(full)
        if not base or base in skip_base or base.startswith("其他"):
            continue
        try:
            a = float(g(rec, "平均價", "Avg_Price"))
        except (TypeError, ValueError):
            continue
        if a <= 0:
            continue
        row = (a, float(g(rec, "上價", "Upper_Price") or a), float(g(rec, "下價", "Lower_Price") or a),
               float(g(rec, "交易量", "Trans_Quantity") or 0), float(g(rec, "中價", "Middle_Price") or a))
        imported = IMPORT_MARK in full
        (imp_rows if imported else dom_rows).setdefault(base, {}).setdefault(iso, []).append(row)
        if not imported:
            dom_crops.add(base)
        cat[base] = c
        varser.setdefault(base, {}).setdefault(full, {}).setdefault(iso, []).append(row)

    mdata = {}
    for base in set(dom_rows) | set(imp_rows):
        src = dom_rows.get(base) if base in dom_crops else imp_rows.get(base)
        if src:
            mdata[base] = {iso: day_agg(rows) for iso, rows in src.items()}
    variants = {}
    for base, fulls in varser.items():
        if len(fulls) < 2:
            continue
        vv = {}
        for full, days in fulls.items():
            vv.setdefault(variety_label(full), {}).update({iso: day_agg(rows) for iso, rows in days.items()})
        variants[base] = vv
    return mdata, variants, cat, closed, dom_crops


# ───────────────────────── 增量合併 ─────────────────────────
def merge_series(old, new, keep_from=None):
    """{作物:{日期:值}}：new 的日子覆蓋 old；keep_from（ISO）之前的日子丟掉（維持約 2 年窗）。"""
    out = {}
    for c in sorted(set(old) | set(new)):     # 排序：記憶體裡的順序每次執行都一樣（set 的順序會隨機變）
        s = dict(old.get(c, {}))
        s.update(new.get(c, {}))
        if keep_from:
            s = {d: v for d, v in s.items() if d >= keep_from}
        if s:
            out[c] = s
    return out


def merge_variants(old, new, keep_from=None):
    out = {}
    for c in sorted(set(old) | set(new)):
        m = merge_series(old.get(c, {}), new.get(c, {}), keep_from)
        if m:
            out[c] = m
    return out


# ───────────────────────── 貴賤判斷 ─────────────────────────
def _valid(series):
    return sorted((d, v) for d, v in series.items() if v and (v.get("avg") or 0) > 0)


def _vw(entries):
    q = sum(v.get("qty") or 0 for _, v in entries)
    if q > 0:
        return sum(v["avg"] * (v.get("qty") or 0) for _, v in entries) / q
    return sum(v["avg"] for _, v in entries) / len(entries)


def season_baseline(entries, as_of):
    """往年同期中位數：同週前後 2 週、且在 as_of 300 天以前的交易日。樣本不足回 None。"""
    wk, cut = week_of(as_of), (_d(as_of) - timedelta(days=SEASON_GAP_DAYS)).isoformat()
    xs = [v["avg"] for d, v in entries if d <= cut and _week_dist(week_of(d), wk) <= SEASON_WEEKS]
    return (statistics.median(xs), len(xs)) if len(xs) >= SEASON_MIN else (None, len(xs))


def recent_price(entries):
    """最近一個交易日往前 7 天內、最多 3 個交易日的量加權均價（比單日價穩，不會被一天的爆量/冷門拉走）。"""
    last = _d(entries[-1][0])
    tail = [e for e in entries[-3:] if (last - _d(e[0])).days <= 6]
    return _vw(tail), tail


def week_change(entries):
    """較上週：最新價 vs 7～11 天前那段時間最後一個交易日的價。冷門品項那段沒成交就回 None，不拿幾週前的舊價硬比。"""
    last = _d(entries[-1][0])
    prev = [e for e in entries if 7 <= (last - _d(e[0])).days <= 11]
    if not prev:
        return None
    p = prev[-1][1]["avg"]
    return round((entries[-1][1]["avg"] - p) / p * 100, 1) if p else None


def old_signal(entries):
    """舊法（回測對照組）：單日價 vs 近 90 筆中位數 ±10%。"""
    base = statistics.median([v["avg"] for _, v in entries[-90:]])
    r = entries[-1][1]["avg"] / base if base else 1.0
    return "up" if r > 1 + OLD_TAG else "dn" if r < 1 - OLD_TAG else "mid", r


def signal(series):
    """回傳 dict 或 None：d 最後成交日、p 最近價、l 最後一天完整資料、r 對基準比值、b 基準（s 往年同期／r 近 90 筆）、
    t up/dn/mid、w 較上週%、q 量少旗標、n 基準樣本數。"""
    es = _valid(series)
    return signal_es(es) if es else None


def signal_es(es):
    """signal() 的本體：吃「已排序、avg>0」的 [(日期, 值)]。回測逐日截斷資料時直接呼叫（與線上同一段程式）。

    ⚑ 2026-10-31 回測（scripts/backtest.py、docs/backtest.md）：「往年同期 0.88/1.12」沒有比舊法準
      （命中率 57% vs 57%、偏便宜後−偏貴後差距 11.0% vs 11.4%），而且把 42% 的日子標成偏便宜
      （去年同期剛好被颱風墊高、只有一年可比）。所以標籤 t 沿用舊法（單日價 vs 近 90 筆中位數 ±10%），
      往年同期只當補充資訊 sr（比往年同期的比值），不拿來下貴賤結論。"""
    old_t, old_r = old_signal(es)
    cur, tail = recent_price(es)
    base, n = season_baseline(es, es[-1][0])
    out = {"d": es[-1][0], "p": round(cur, 1), "l": es[-1][1], "r": round(old_r, 3), "t": old_t,
           "sr": round(cur / base, 3) if base else None, "n": n, "w": week_change(es)}
    # 量能比：近 3 個交易日平均量 ÷ 近 90 筆日量中位數。回測（docs/backtest.md）顯示量縮不能預測之後會跌，
    # 所以前端只拿來「說明」這個價錢是缺貨型還是盛產型，不下買賣結論。
    qmed = statistics.median([v.get("qty") or 0 for _, v in es[-90:]])
    if qmed > 0:
        out["qr"] = round(sum(v.get("qty") or 0 for _, v in tail) / len(tail) / qmed, 2)
    if sum(v.get("qty") or 0 for _, v in tail) / len(tail) < THIN_QTY:
        out["q"] = 1
    return out


def season_signal(es):
    """回測用：往年同期判斷法（未採用為標籤，保留供比較）。"""
    cur, _ = recent_price(es)
    base, _ = season_baseline(es, es[-1][0])
    if base is None:
        return old_signal(es)[0], "r"
    r = cur / base
    return ("up" if r >= TAG_HI else "dn" if r <= TAG_LO else "mid"), "s"


def summarize(mdata, variants):
    """{作物: signal＋v:{品種: signal}}，前端看板直接用。"""
    out = {}
    for c, s in mdata.items():
        sg = signal(s)
        if sg:
            out[c] = sg
    for c, vv in variants.items():
        vs = {lab: sg for lab, sg in ((lab, signal(s)) for lab, s in vv.items()) if sg}
        if vs:
            out.setdefault(c, {})["v"] = vs
    return out


# ───────────────────────── 採收預判（前端 predictPrice 的 Python 版，回測用） ─────────────────────────
def predict_price(series, harvest_iso, as_of=None):
    """與 index.html predictPrice 同邏輯：同週前後 2 週（含跨年）的中位數與 25/75 百分位，不足 5 筆退回近 90 筆。
    as_of：回測時只准用這天（含）以前的資料，避免偷看未來。"""
    es = [(d, v) for d, v in _valid(series) if as_of is None or d <= as_of]
    if not es:
        return None
    hw = week_of(harvest_iso)
    near = [v["avg"] for d, v in es if (lambda x: x <= 2 or x >= 50)(abs(week_of(d) - hw))]

    def pct(a, p):
        s = sorted(a)
        return s[min(len(s) - 1, int(len(s) * p))]
    if len(near) >= 5:
        return {"med": statistics.median(near), "lo": pct(near, .25), "hi": pct(near, .75), "n": len(near), "same": True}
    rec = [v["avg"] for _, v in es[-90:]]
    if len(rec) >= 5:
        return {"med": statistics.median(rec), "lo": pct(rec, .25), "hi": pct(rec, .75), "n": len(rec), "same": False}
    return None


# ───────────────────────── 檔案 ─────────────────────────
def month_start_iso(now):
    return now.replace(day=1).strftime("%Y-%m-%d")


def _split(series_map, cutoff):
    h, r = {}, {}
    for c, s in series_map.items():
        hs = {d: v for d, v in s.items() if d < cutoff}
        rs = {d: v for d, v in s.items() if d >= cutoff}
        if hs:
            h[c] = hs
        if rs:
            r[c] = rs
    return h, r


def _dump(path, obj):
    # sort_keys＋不放 updated：同月內歷史檔逐位元不變，git 不會每天整檔重寫
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def market_paths(fname):
    stem = fname[:-5]
    return {"file": fname, "vfile": stem + "_v.json", "recent": stem + "_recent.json"}


def write_market(fname, mk, now, mdata, variants, summary):
    """NN.json（本月前母作物）／NN_v.json（本月前品種）／NN_recent.json（本月母作物＋品種＋看板摘要）。回傳路徑 dict。"""
    p = market_paths(fname)
    cutoff = month_start_iso(now)
    hd, rd = _split(mdata, cutoff)
    hv, rv = {}, {}
    for c, vv in variants.items():
        a, b = _split(vv, cutoff)
        if a:
            hv[c] = a
        if b:
            rv[c] = b
    _dump(p["file"], {"market": mk, "until": cutoff, "data": hd})
    _dump(p["vfile"], {"market": mk, "until": cutoff, "variants": hv})
    _dump(p["recent"], {"market": mk, "updated": now.strftime("%Y-%m-%d %H:%M"), "since": cutoff,
                        "data": rd, "variants": rv, "sum": summary})
    return p


def read_market(entry, with_archive=False):
    """讀回一個市場的完整序列（歷史＋當月；相容舊格式：品種放在 NN.json 裡）。讀不到回 (None, None)。
    with_archive＝True 時連 5 年封存的母作物一起讀（摘要的「比往年同期」與回測用；品種不封存）。"""
    def load(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    h = load(entry.get("file", ""))
    if h is None:
        return None, None
    data, var = dict(h.get("data") or {}), {}
    var = merge_variants(var, h.get("variants") or {})
    if entry.get("vfile"):
        v = load(entry["vfile"])
        if v is None:
            return None, None
        var = merge_variants(var, v.get("variants") or {})
    if entry.get("recent"):
        r = load(entry["recent"])
        if r is None:
            return None, None
        data = merge_series(data, r.get("data") or {})
        var = merge_variants(var, r.get("variants") or {})
    if with_archive:
        for path in sorted((entry.get("archive") or {}).values()):
            data = merge_series(read_archive(path), data)    # 主檔（較新）優先
    return data, var


# ───────────────────────── 5 年封存（只給後端摘要與回測用，手機不下載） ─────────────────────────
ARCHIVE_DIR = "prices/archive"
ARCH_FIELDS = ["avg", "high", "mid", "low", "qty"]


def archive_path(fname, year):
    """prices/14.json → prices/archive/14_2022.json（一個市場一年一檔；年份過完就不再變，git 不會反覆重寫）"""
    stem = fname.rsplit("/", 1)[-1][:-5]
    return f"{ARCHIVE_DIR}/{stem}_{year}.json"


def pack(series_map):
    """{作物:{日期:{avg..}}} → {作物:[[日期,avg,high,mid,low,qty],…]}：比物件格式小約一半。"""
    return {c: [[d] + [v.get(k) for k in ARCH_FIELDS] for d, v in sorted(s.items())] for c, s in series_map.items() if s}


def unpack(packed):
    return {c: {r[0]: dict(zip(ARCH_FIELDS, r[1:])) for r in rows} for c, rows in (packed or {}).items()}


def read_archive(path):
    try:
        with open(path, encoding="utf-8") as f:
            return unpack(json.load(f).get("data"))
    except Exception:
        return {}


def write_archive(path, mk, year, series_map):
    os.makedirs(path.rsplit("/", 1)[0], exist_ok=True)
    _dump(path, {"market": mk, "year": int(year), "fields": ARCH_FIELDS, "data": pack(series_map)})


def split_by_year(series_map):
    out = {}
    for c, s in series_map.items():
        for d, v in s.items():
            out.setdefault(d[:4], {}).setdefault(c, {})[d] = v
    return out


def add_to_archive(entry, fname, mk, series_map):
    """把資料（例如滑出 2 年窗的那個月）併進對應年份的封存檔；回傳更新後的 {年: 路徑}。"""
    arch = dict(entry.get("archive") or {})
    for y, part in split_by_year(series_map).items():
        path = arch.get(y) or archive_path(fname, y)
        write_archive(path, mk, y, merge_series(read_archive(path), part))
        arch[y] = path
    return arch


def trim_closed(days, now):
    cut = (now - timedelta(days=CLOSED_KEEP_DAYS)).strftime("%Y-%m-%d")
    return sorted(d for d in set(days) if d >= cut)
