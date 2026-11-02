"""
backtest.py —— 離線回測（不連網），用 repo 裡的真實批發行情回答四個問題：

  A. 新的「偏貴／偏便宜」（近 3 日量加權價 vs 往年同期）有沒有比舊的（單日價 vs 近 90 筆中位數 ±10%）準？
  B. 精準進貨「季節低價時多進一點」策略，比「每週固定進貨」省多少？（含運費、存放損耗）
  C. 採收預判 predict_price 準不準？跟兩個天真基準比：「價格不變」（＝買進持有的對應）、「去年同期價」。
  D. 搭配種法「挑預估每天淨利最高的菜」，實際收成時有沒有比「一直種小白菜」「種去年同期最賺的」好？
  E. 精準進貨跨市場比價：每天照 App「含運最便宜」去別的市場進貨，扣掉運費、路上損耗後，真的比在自己市場買便宜嗎？
  F. 零售參考價：「批發價 × 每種菜自己的倍數」估零售，準不準？跟「固定 2 倍」「上個月零售價」比。
  S1. 量價雙確認進貨（2026-11-02）：價格不高於往年同期（要 2 個往年）、一年百分位 ≤ 75%、到貨量比 ≥ 1.1 才多進，
      跟每週固定、每天買、B、事後最佳比。
  S2. 雜訊過濾（2026-11-02）：近 30 天價格變異係數 ≤ 0.3、量 ≥ 該市場門檻才發 A 的訊號，命中率有沒有變高。
  （A 另比「往年同期要求 2 個往年」：2026-11-02 試過，沒有比較好 → App 不採用。）

有 5 年封存（prices/archive/，排程回填後才有）時自動一起讀：A、B 用得到更長的「往年同期」；
C、D 仍只給模型看決策日前 2 年（手機上只有 2 年資料，回測不能比 App 實際看到的多）。

防偏誤（每一題都適用）：
  - 未來函數：每個決策只用「當天（含）以前」的資料；訊號出現後，用「下一個交易日」的價格成交。
  - 樣本外：判斷門檻（0.88／1.12、±10%）不是用這份資料調出來的；只在第 2 年（往年同期有資料時）評估。
  - 成本：沒有證交稅；運費、損耗、手續費都是參數（下方預設值是「假設」，請換成你的實際數字）。
  - 倖存者偏差：作物用當時有成交的；但市場清單是「現在還查得到的 19 個」，已關閉的市場不在資料裡（報告會註明）。

用法：python3 scripts/backtest.py [--app-dir .] [--out docs/backtest.md] [--retail merchant/retail.json]
"""
import argparse
import bisect
import importlib.util
import itertools
import json
import os
import re
import statistics
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import price_core as pc  # noqa: E402

# ── 可調參數（假設值，請換成實際數字） ──
Y2_START = "2025-10-01"          # 第 2 年起：此後「往年同期」才有資料（load_app 會依資料起點＋365 天重算）
APP_LOOKBACK_DAYS = 730          # C／D：手機上只有近 2 年資料 → 模型只准看決策日前 730 天
FWD_DAYS = 10                    # A：訊號後看幾個交易日的平均價
STEP = 3                         # A／C：每隔幾個交易日取一次樣本（加快速度）
MIN_DAYS = 400                   # 只評估 2 年內至少 400 個交易日的作物（太冷門的價格不可靠）
DURABLE = {"甘藍": 2, "洋蔥": 8, "大蒜": 12, "馬鈴薯": 6, "甘薯": 4, "南瓜": 8}   # B：可存放週數（假設）
WEEKLY_NEED = 100                # B：每週用量（公斤）
TRIP_COST = 300                  # B：每趟進貨運費（元，假設）
LOSS_PER_WEEK = 0.02             # B：存放每週損耗 2%（假設）
HORIZONS = (30, 45, 60)          # C：預測幾天後的價格
FEE = 0.06                       # D：拍賣手續費（App 現用估計值）
AREA_M2, USABLE = 293.4, 0.8     # D：1 分地、可用 80%
COST_PER_CYCLE = 2500 + 1800 + 5000 + 3500   # D：App 預設每期成本（肥料/農藥/人工/資材）
APP_TO_WHOLESALE = {"青江菜": "青江白菜", "空心菜": "蕹菜", "地瓜葉": "甘薯葉", "萵苣(A菜)": "萵苣菜"}
# E：跨市場進貨。只跟「同一區、開車當天來回得了」的市場比（台中商人不會為了便宜 3 元跑高雄）。
REGIONS = {"北部": ["台北一", "台北二", "三重", "板橋", "桃農", "宜蘭"],
           "中部": ["台中", "豐原", "東勢", "南投", "溪湖", "永靖", "西螺"],
           "南部": ["嘉義", "高雄", "鳳山", "屏東"]}
HOMES = {"台北一": "北部", "台中": "中部", "高雄": "南部"}
FREIGHTS = (0, 1, 2, 3, 5)       # 元/公斤＝App 比價頁「運費」欄位填的數字
TRANSIT_LOSS = 0.03              # 從別的市場載回來的路上損耗 3%（假設）
FRESH_DAYS = 4                   # 與前端 CMP_FRESH_DAYS 一致：超過 4 天沒成交的市場不參加比價
MIN_CMP_DAYS = 200               # 鄰近市場至少 200 個交易日有這項作物，才列入候選（偶爾才有貨的不算）
# F：零售參考價
RETAIL_FIXED = 2.0               # 對照組：常聽到的「零售約批發 1.5～2.5 倍」取中間 2 倍
RETAIL_LAGS = (1, 2, 3, 6)       # 零售資料若晚 1／2／3／6 個月才看得到，「直接用舊零售價」還準不準
# S1：量價雙確認進貨。三個門檻是事先給定的（任務需求），不是用這份資料調出來的 → 結果才算樣本外
S1_MAX_R = 1.0                   # 近 3 日價 ÷ 往年同期（至少 2 個往年）≤ 1.0
S1_MAX_PCT = 0.75                # 一年百分位 ≤ 75%（price_core.year_stats，跟 App 詳細頁同一套）
S1_MIN_SUP = 1.1                 # 到貨量比 ≥ 1.1
S1_MIN_YEARS = 2
S1_MIN_PRIOR_DAYS = 200          # 品項篩選：評估起點「以前」至少 200 個交易日（只用起點前的資料決定測不測，避免偷看未來）
# S2：雜訊過濾。想法參考 midssinglin/taiwan-price（沒有授權條款，只借想法，程式自行撰寫）；數字事先給定、沒有用這份資料調
S2_WIN_DAYS = 30                 # 看近 30 天（日曆天）
S2_MIN_WIN = 5                   # 近 30 天不到 5 個交易日：變異係數算不準 → 不發訊號
S2_CV_MAX = 0.3                  # 近 30 天均價的變異係數（標準差 ÷ 平均）上限
S2_VOL_PCTL = 0.25               # 量門檻＝該市場各品項「前 365 天平均每交易日成交量」的第 25 百分位；每月初只用之前的資料重算
S2_VOL_MIN_DAYS = 60             # 算門檻時，前 365 天至少 60 個交易日的品項才列入


def iso_add(iso, n):
    return (date.fromisoformat(iso) + timedelta(days=n)).isoformat()


def load_app(app_dir):
    """讀全部市場的母作物序列（有 5 年封存就一起讀）。回傳 (markets, index, 第 2 年起點, 有沒有封存)。"""
    ix = json.load(open(os.path.join(app_dir, "prices_index.json"), encoding="utf-8"))
    out = {}
    cwd = os.getcwd()
    os.chdir(app_dir)
    try:
        for m in ix["markets"]:
            data, _ = pc.read_market(m, with_archive=True)
            if data:
                out[m["name"]] = {c: pc._valid(s) for c, s in data.items()}
    finally:
        os.chdir(cwd)
    has_arch = any(m.get("archive") for m in ix["markets"])
    firsts = sorted(es[0][0] for crops in out.values() for es in crops.values() if es)
    # 「往年同期」至少要有一整年歷史才算得出來 → 從資料起點（取第 10 百分位，避免單一怪資料）＋365 天開始評估
    y2 = iso_add(firsts[len(firsts) // 10], 365) if firsts else Y2_START
    return out, ix, y2, has_arch


def app_window(es, dates, as_of):
    """手機上看得到的資料：決策日前 APP_LOOKBACK_DAYS 天內（含當天）。dates＝es 的日期清單（已排序，用二分搜尋切片）。"""
    lo = iso_add(as_of, -APP_LOOKBACK_DAYS)
    return dict(es[bisect.bisect_left(dates, lo):bisect.bisect_right(dates, as_of)])


def avg_near(es_by_date, iso, win=3):
    """目標日前後 win 天內的平均價（沒有成交回 None）。"""
    xs = [es_by_date[d] for d in (iso_add(iso, k) for k in range(-win, win + 1)) if d in es_by_date]
    return sum(xs) / len(xs) if xs else None


def fmt_pct(x):
    return "—" if x is None else f"{x * 100:+.1f}%"


# ───────────────────────── A. 貴賤判斷（＋S2 雜訊過濾） ─────────────────────────
def s2_thresholds(crops):
    """S2 的量門檻 {YYYY-MM: 公斤/交易日}：每個月初，只用「那個月 1 號以前 365 天」的資料，
    取該市場各品項平均每交易日成交量的第 25 百分位（不偷看當月以後；品項太少的月份不給門檻＝不發訊號）。"""
    months = sorted({d[:7] for es in crops.values() for d, _ in es})
    idx = {c: ([d for d, _ in es], [0] + list(itertools.accumulate((v.get("qty") or 0) for _, v in es)))
           for c, es in crops.items()}
    out = {}
    for ym in months:
        m0 = ym + "-01"
        lo = iso_add(m0, -365)
        vals = []
        for ds, cum in idx.values():
            a, b = bisect.bisect_left(ds, lo), bisect.bisect_left(ds, m0)
            if b - a >= S2_VOL_MIN_DAYS:
                vals.append((cum[b] - cum[a]) / (b - a))
        if len(vals) >= 5:
            vals.sort()
            out[ym] = vals[int(len(vals) * S2_VOL_PCTL)]
    return out


def s2_check(es, i, thr):
    """第 i 天收市後能不能發訊號（只看第 i 天含以前）：回傳 (通過?, 原因 ok／thin／cv／vol)。"""
    lo = iso_add(es[i][0], -S2_WIN_DAYS)
    j = i
    while j >= 0 and es[j][0] > lo:
        j -= 1
    win = es[j + 1:i + 1]
    if len(win) < S2_MIN_WIN:
        return False, "thin"
    ps = [v["avg"] for _, v in win]
    m = sum(ps) / len(ps)
    cv = (sum((p - m) ** 2 for p in ps) / len(ps)) ** 0.5 / m     # 自己算標準差：statistics 用分數運算，40 萬次太慢
    if cv > S2_CV_MAX:
        return False, "cv"
    th = thr.get(es[i][0][:7])
    if th is None or sum((v.get("qty") or 0) for _, v in win) / len(win) < th:
        return False, "vol"
    return True, "ok"


def _tag_stats(rr):
    """rr＝[(t, 之後 10 個交易日平均漲跌)]：各標籤的比例、平均、命中率，以及差距（偏便宜後平均 − 偏貴後平均）。"""
    st = {}
    for t in ("dn", "mid", "up"):
        f = [x for tt, x in rr if tt == t]
        hit = (sum(1 for x in f if (x > 0 if t == "dn" else x < 0)) / len(f)) if f and t != "mid" else None
        st[t] = {"n": len(f), "share": len(f) / len(rr) if rr else 0,
                 "mean": statistics.mean(f) if f else None, "median": statistics.median(f) if f else None, "hit": hit}
    st["spread"] = (st["dn"]["mean"] - st["up"]["mean"]) if st["dn"]["mean"] is not None and st["up"]["mean"] is not None else None
    st["all_mean"] = statistics.mean(x for _, x in rr) if rr else None
    st["n"] = len(rr)
    return st


def eval_tags(markets):
    """同一批樣本比：舊法、往年同期（App 現用，1 個往年就算）、往年同期但要 2 個往年（F1 試驗）、
    往年同期＋S2 雜訊過濾（沒通過＝不發訊號，算成 mid）。"""
    rows = {"舊": [], "新": [], "新2年": [], "S2": [], "S2濾掉": []}
    basis_cnt = {"s": 0, "r": 0}
    only1 = {"新": [], "新2年": []}           # 只有 1 個往年可比的樣本：兩種規則唯一不同的地方
    why = {"thin": 0, "cv": 0, "vol": 0, "ok": 0}
    for mk, crops in markets.items():
        thr = s2_thresholds(crops)
        for c, es in crops.items():
            if len(es) < MIN_DAYS:
                continue
            for i in range(0, len(es) - FWD_DAYS - 2, STEP):
                if es[i][0] < Y2_START:
                    continue
                pre = es[:i + 1]
                smp = pc.season_sample(pre, pre[-1][0])      # 掃一次，兩種年數門檻共用
                new_t, new_b = pc.season_signal(pre, sample=smp)
                t2, b2 = pc.season_signal(pre, min_years=2, sample=smp)
                old_t, _ = pc.old_signal(pre)
                entry = es[i + 1][1]["avg"]
                fut = statistics.mean(v["avg"] for _, v in es[i + 2:i + 2 + FWD_DAYS])
                fwd = fut / entry - 1
                rows["舊"].append((old_t, fwd))
                rows["新"].append((new_t, fwd))
                rows["新2年"].append((t2, fwd))
                if new_b == "s" and b2 == "r":
                    only1["新"].append((new_t, fwd))
                    only1["新2年"].append((t2, fwd))
                ok, reason = s2_check(es, i, thr) if new_t != "mid" else (True, "ok")
                if new_t != "mid":
                    why[reason] += 1
                rows["S2"].append((new_t if ok else "mid", fwd))
                if not ok:
                    rows["S2濾掉"].append((new_t, fwd))
                basis_cnt[new_b] += 1
    out = {rule: _tag_stats(rr) for rule, rr in rows.items()}
    out["basis"] = basis_cnt
    out["only1"] = {k: _tag_stats(v) for k, v in only1.items()}
    out["s2_why"] = why
    return out


# ───────────────────────── B／S1. 進貨模擬（共用同一套帳） ─────────────────────────
def _weeks(es, start):
    """start 起每個 ISO 週的交易日索引（依週排序）。"""
    weeks = {}
    for i, (d, _) in enumerate(es):
        if d >= start:
            y, w, _ = date.fromisoformat(d).isocalendar()
            weeks.setdefault((y, w), []).append(i)
    return [weeks[k] for k in sorted(weeks)]


def _run_stock(es, wk_days, cap, trigger=None, loss=None):
    """每週固定用 WEEKLY_NEED 公斤：週初不夠就照當天價補足（被迫買）；trigger(i) 成立（第 i 天收市後的判斷，只准看 es[:i+1]）
    就隔一個交易日進到存量上限 cap。每趟運費 TRIP_COST、週末剩下的庫存損耗 LOSS_PER_WEEK；最後剩的庫存照最後一天價算回來。
    trigger=None＝每週固定進貨；loss 只給敏感度分析改（預設 LOSS_PER_WEEK）。回傳 (每公斤成本, 加碼次數)。"""
    loss = LOSS_PER_WEEK if loss is None else loss
    inv, spent, trips, extra = 0.0, 0.0, 0, 0
    for days in wk_days:
        if inv < WEEKLY_NEED:
            q = WEEKLY_NEED - inv
            spent += q * es[days[0]][1]["avg"]
            trips += 1
            inv += q
        if trigger:
            for i in days:
                if i + 1 >= len(es) or inv >= cap:
                    continue
                if trigger(i):
                    q = cap - inv
                    spent += q * es[i + 1][1]["avg"]
                    trips += 1
                    inv += q
                    extra += 1
        inv -= WEEKLY_NEED
        inv *= (1 - loss)
    leftover_value = max(inv, 0) * es[-1][1]["avg"]
    return (spent + trips * TRIP_COST - leftover_value) / (WEEKLY_NEED * len(wk_days)), extra


def _run_daily(es, wk_days):
    """每天買：每個交易日只進當天要用的量（這週用量 ÷ 這週交易日數），每天一趟運費、不囤貨所以沒有存放損耗。
    回傳 (含運費每公斤, 只算菜錢每公斤)——商人本來就天天跑市場時，運費可以當成沒有多花。"""
    spent, trips = 0.0, 0
    for days in wk_days:
        q = WEEKLY_NEED / len(days)
        for i in days:
            spent += q * es[i][1]["avg"]
            trips += 1
    used = WEEKLY_NEED * len(wk_days)
    return (spent + trips * TRIP_COST) / used, spent / used


def _hindsight(es, wk_days, cap_weeks, loss=None):
    """事後最佳（上限）：已經知道全部價格時，在同一套規則（每週初要有貨、最多囤 cap_weeks 週、週末損耗、每趟運費）下最便宜的進貨排程。
    動態規劃：f[k]＝前 k 週都備好貨的最低花費。一趟可以在第 j 週買下第 k～k+m−1 週的量（含損耗要多買的部分）：
    j＜k 時挑那週最便宜的一天；j＝k 只能是週初那天（那週要用的貨週初就要有）；最久放 cap_weeks 週（存量不會超過上限）。
    每週固定進貨是其中一種排法，所以結果一定不比它貴。"""
    K = len(wk_days)
    minp = [min(es[i][1]["avg"] for i in days) for days in wk_days]
    keep = 1 - (LOSS_PER_WEEK if loss is None else loss)
    f = [float("inf")] * (K + 1)
    f[0] = 0.0
    for k in range(K):
        for j in range(max(0, k - cap_weeks + 1), k + 1):
            p = minp[j] if j < k else es[wk_days[k][0]][1]["avg"]
            qty = 0.0
            for m in range(1, min(cap_weeks - (k - j), K - k) + 1):
                qty += WEEKLY_NEED / keep ** (k + m - 1 - j)
                c = f[k] + p * qty + TRIP_COST
                if c < f[k + m]:
                    f[k + m] = c
    return f[K] / (WEEKLY_NEED * K)


def _b_trigger(es, i):
    """B：往年同期基準下「偏便宜」（App 線上同一套規則）。"""
    st, sb = pc.season_signal(es[:i + 1])
    return st == "dn" and sb == "s"


def _s1_trigger(es, i):
    """S1 量價雙確認：近 3 日價 ÷ 往年同期（至少 2 個往年）≤ 1.0、一年百分位 ≤ 75%、到貨量比 ≥ 1.1。只看第 i 天（含）以前。"""
    pre = es[:i + 1]
    y = pc.year_stats(pre)                                   # 先算便宜的（只掃一年），不成立就不必掃 5 年找往年同期
    if not y or y["pct"] > S1_MAX_PCT or y["sup"] is None or y["sup"] < S1_MIN_SUP:
        return False
    base, _ = pc.season_baseline(pre, pre[-1][0], min_years=S1_MIN_YEARS)
    return base is not None and pc.recent_price(pre)[0] / base <= S1_MAX_R


def sim_buying(es, cap_weeks):
    """B：回傳 (策略每公斤成本, 基準每公斤成本, 策略多進貨次數)；只用第 2 年起。"""
    if sum(1 for d, _ in es if d >= Y2_START) < 60:
        return None
    wk, cap = _weeks(es, Y2_START), WEEKLY_NEED * cap_weeks
    s_cost, n_cheap = _run_stock(es, wk, cap, lambda i: _b_trigger(es, i))
    b_cost, _ = _run_stock(es, wk, cap)
    return s_cost, b_cost, n_cheap


def eval_s1(markets, start, loss=None):
    """S1 跟每週固定、每天買、B、事後最佳比；同一段期間（start 起）、同一批品項。
    品項只用 start 以前的資料篩（至少 S1_MIN_PRIOR_DAYS 個交易日），不看之後有沒有繼續交易。
    loss：存放損耗（敏感度分析用：損耗設 0 時贏不贏，分得出是「買得不夠便宜」還是「被損耗吃掉」）。"""
    res = []
    for mk in ("台北一", "台中", "西螺", "高雄"):
        for c, cap_weeks in DURABLE.items():
            es = markets.get(mk, {}).get(c)
            if not es or sum(1 for d, _ in es if d < start) < S1_MIN_PRIOR_DAYS:
                continue
            wk = _weeks(es, start)
            if len(wk) < 26:
                continue
            cap = WEEKLY_NEED * cap_weeks
            weekly, _ = _run_stock(es, wk, cap, loss=loss)
            daily, daily_px = _run_daily(es, wk)
            b, nb = _run_stock(es, wk, cap, lambda i: _b_trigger(es, i), loss=loss)
            s1, ns = _run_stock(es, wk, cap, lambda i: _s1_trigger(es, i), loss=loss)
            res.append({"market": mk, "crop": c, "weeks": len(wk), "weekly": weekly, "daily": daily, "daily_px": daily_px,
                        "b": b, "b_n": nb, "s1": s1, "s1_n": ns, "best": _hindsight(es, wk, cap_weeks, loss=loss)})
    return res


def eval_buying(markets):
    res = []
    for mk in ("台北一", "台中", "西螺", "高雄"):
        for c, weeks_cap in DURABLE.items():
            es = markets.get(mk, {}).get(c)
            if not es or len(es) < MIN_DAYS:
                continue
            r = sim_buying(es, weeks_cap)
            if r:
                res.append({"market": mk, "crop": c, "strategy": r[0], "baseline": r[1], "save": 1 - r[0] / r[1], "cheap_buys": r[2]})
    return res


# ───────────────────────── C. 採收預判 ─────────────────────────
def eval_predict(markets):
    out = {h: {"ape_m": [], "ape_last": [], "ape_ly": [], "cover": [], "same": 0, "n": 0} for h in HORIZONS}
    for mk, crops in markets.items():
        for c, es in crops.items():
            if len(es) < MIN_DAYS:
                continue
            dates = [d for d, _ in es]
            by = {d: v["avg"] for d, v in es}
            last_day = es[-1][0]
            for i in range(0, len(es), STEP * 3):
                t = es[i][0]
                if t < Y2_START:
                    continue
                series = app_window(es, dates, t)
                for h in HORIZONS:
                    tgt = iso_add(t, h)
                    if tgt > last_day:
                        continue
                    actual = avg_near(by, tgt)
                    ly = avg_near(by, iso_add(tgt, -365))
                    if actual is None or ly is None:
                        continue
                    p = pc.predict_price(series, tgt, as_of=t)
                    if not p:
                        continue
                    o = out[h]
                    o["n"] += 1
                    o["same"] += 1 if p["same"] else 0
                    o["ape_m"].append(abs(p["med"] - actual) / actual)
                    o["ape_last"].append(abs(es[i][1]["avg"] - actual) / actual)
                    o["ape_ly"].append(abs(ly - actual) / actual)
                    o["cover"].append(1 if p["lo"] <= actual <= p["hi"] else 0)
    summ = {}
    for h, o in out.items():
        if not o["n"]:
            continue
        summ[h] = {"n": o["n"], "same_share": o["same"] / o["n"],
                   "mape_model": statistics.mean(o["ape_m"]), "mape_last": statistics.mean(o["ape_last"]),
                   "mape_ly": statistics.mean(o["ape_ly"]),
                   "mdape_model": statistics.median(o["ape_m"]), "mdape_last": statistics.median(o["ape_last"]),
                   "mdape_ly": statistics.median(o["ape_ly"]),
                   "coverage": statistics.mean(o["cover"]),
                   "beat_last": statistics.mean(1 if a < b else 0 for a, b in zip(o["ape_m"], o["ape_last"])),
                   "beat_ly": statistics.mean(1 if a < b else 0 for a, b in zip(o["ape_m"], o["ape_ly"]))}
    return summ


# ───────────────────────── D. 搭配種法（單次選菜） ─────────────────────────
def parse_js_object(html, name):
    m = re.search(r"const " + name + r"=(\{.*?\});", html, re.S)
    if not m:
        return {}
    txt = re.sub(r'([{,]\s*)("?)([^"{},:\[\]\s]+)\2\s*:', r'\1"\3":', m.group(1))
    return json.loads(txt)


def eval_rotation(markets, html):
    growth, ym2, months = (parse_js_object(html, n) for n in ("GROWTH", "YIELD_M2", "PLANT_MONTHS"))
    res = {}
    for mk in ("西螺", "台北一"):
        crops = markets.get(mk, {})
        cand = {}
        for app_name in growth:
            w = APP_TO_WHOLESALE.get(app_name, app_name)
            if w in crops and len(crops[w]) >= MIN_DAYS and w not in cand.values():
                cand[app_name] = w
        if len(cand) < 3:
            continue
        last = max(es[-1][0] for es in crops.values())
        picks = {"模型（預估每天淨利最高）": [], "固定種小白菜": [], "種去年同期最賺的": [], "隨機選（候選平均）": [], "事後最佳（上限）": []}
        d = Y2_START
        while iso_add(d, 70) <= last:
            mth = int(d[5:7])
            summer = 5 <= mth <= 10
            evals = []
            for app_name, w in cand.items():
                if mth not in months.get(app_name, list(range(1, 13))):
                    continue
                g = growth[app_name]
                T = g[0] if summer else g[1]
                hv = iso_add(d, T)
                ykg = AREA_M2 * USABLE * ym2.get(app_name, 2.5) * (0.9 if summer else 1.1)
                by = {x: v["avg"] for x, v in crops[w]}
                actual = avg_near(by, hv)
                p = pc.predict_price(app_window(crops[w], [x for x, _ in crops[w]], d), hv, as_of=d)
                ly_price = avg_near(by, iso_add(hv, -365))
                if actual is None or p is None:
                    continue
                net = lambda price: (ykg * price * (1 - FEE) - COST_PER_CYCLE) / T
                evals.append({"crop": app_name, "pred": net(p["med"]), "real": net(actual),
                              "ly": net(ly_price) if ly_price is not None else None})
            if len(evals) >= 3:
                picks["模型（預估每天淨利最高）"].append(max(evals, key=lambda e: e["pred"])["real"])
                fixed = [e for e in evals if e["crop"] == "小白菜"]
                if fixed:
                    picks["固定種小白菜"].append(fixed[0]["real"])
                lyc = [e for e in evals if e["ly"] is not None]
                if lyc:
                    picks["種去年同期最賺的"].append(max(lyc, key=lambda e: e["ly"])["real"])
                picks["隨機選（候選平均）"].append(statistics.mean(e["real"] for e in evals))
                picks["事後最佳（上限）"].append(max(e["real"] for e in evals))
            d = iso_add(d, 14)
        res[mk] = {"candidates": list(cand.items()),
                   "result": {k: (statistics.mean(v) if v else None, len(v)) for k, v in picks.items()}}
    return res


# ───────────────────────── E. 跨市場進貨（精準進貨比價頁） ─────────────────────────
def _ordinal(iso):
    return date.fromisoformat(iso).toordinal()


def _simulate(home, h_es, others, acc, start=None):
    """一個比價單位（某作物或某品種）：每個交易日收市後照 App 比價頁的規則（近 4 天內有成交、含運價最低）挑市場，
    下一個交易日去那裡進貨；對照組＝一直在自己市場進貨。別的市場的成本＝當天價÷(1−路上損耗)＋運費；
    那天那個市場沒開就只能在自己市場買。另算「固定去平常最便宜的市場」（每月初用前 90 天中位數挑一次，不用每天看 App）。
    h_es：[(日期, 均價)]；others：{市場: [(日期, 均價)]}；acc：{運費: 累計}（就地累加）；start：從哪天開始評估（預設 Y2_START）。
    回傳這個單位的 {運費: (省下比例, 天數)}。"""
    start = start or Y2_START
    oth = {mk: ([_ordinal(d) for d, _ in es], [p for _, p in es], dict(es)) for mk, es in others.items()}
    unit = {f: [0.0, 0.0, 0] for f in FREIGHTS}            # strat, base, n
    static_pick, static_month = {f: home for f in FREIGHTS}, None
    for i in range(len(h_es) - 1):
        (t, pt), (t1, p_home1) = h_es[i], h_es[i + 1]
        if t < start:
            continue
        ot = _ordinal(t)
        if _ordinal(t1) - ot > FRESH_DAYS:                  # 長休市（過年）後不比：那時的「最新價」都是舊的
            continue
        seen = {home: pt}                                   # 當晚 App 上看得到的各市場最新價
        for mk, (ods, ps, _) in oth.items():
            j = bisect.bisect_right(ods, ot) - 1
            if j >= 0 and ot - ods[j] <= FRESH_DAYS:
                seen[mk] = ps[j]
        if t[:7] != static_month:                           # 每月初重挑一次「平常最便宜」的市場（只用之前 90 天）
            static_month = t[:7]
            lo = ot - 90
            meds = {home: statistics.median([p for d, p in h_es[max(0, i - 90):i + 1] if _ordinal(d) >= lo])}
            for mk, (ods, ps, _) in oth.items():
                a, b = bisect.bisect_left(ods, lo), bisect.bisect_right(ods, ot)
                if b - a >= 20:
                    meds[mk] = statistics.median(ps[a:b])
            for f in FREIGHTS:
                static_pick[f] = min(meds, key=lambda mk: meds[mk] + (0 if mk == home else f))
        raw1 = {mk: o[2].get(t1) for mk, o in oth.items()}   # 隔天各市場實際價（沒開市＝None）
        for f in FREIGHTS:
            def cost(mk):
                if mk == home or raw1.get(mk) is None:
                    return p_home1
                return raw1[mk] / (1 - TRANSIT_LOSS) + f
            pick = min(seen, key=lambda mk: seen[mk] + (0 if mk == home else f))   # App：含運最便宜
            naive = min(seen, key=lambda mk: seen[mk])                              # 沒填運費：只看最便宜
            T = acc[f]
            T["n"] += 1
            T["base"] += p_home1
            T["strat"] += cost(pick)
            T["naive"] += cost(naive)
            T["static"] += cost(static_pick[f])
            T["oracle"] += min([p_home1] + [cost(mk) for mk in oth])               # 事後才知道的最佳（上限）
            if pick != home:
                T["remote"] += 1
                if raw1.get(pick) is None:
                    T["closed"] += 1
                elif cost(pick) < p_home1:
                    T["hit"] += 1
            u = unit[f]
            u[0] += cost(pick)
            u[1] += p_home1
            u[2] += 1
    return {f: (1 - u[0] / u[1], u[2]) for f, u in unit.items() if u[2]}


def _avg_es(series):
    return [(d, v["avg"]) for d, v in pc._valid(series)]


def _var_ok(label):
    """「其他」「進口」是雜項／不同來源，各市場內容不一樣，不拿來跨市場比。"""
    return label and label != "其他" and "進口" not in label


def load_region_variants(app_dir, ix, names):
    """只讀某一區市場的品種序列（只留均價，省記憶體；全部市場的品種檔合計約 80 MB）。"""
    out = {}
    cwd = os.getcwd()
    os.chdir(app_dir)
    try:
        for m in ix["markets"]:
            if m["name"] in names:
                _, var = pc.read_market(m)
                out[m["name"]] = {c: {lab: _avg_es(s) for lab, s in vv.items() if _var_ok(lab)} for c, vv in (var or {}).items()}
    finally:
        os.chdir(cwd)
    return out


def eval_cross_market(markets, app_dir, ix):
    """兩種比法：①「全部綜合」（母作物均價；各市場品種組成不同）②同品種比（只比名稱相同的品種）。"""
    out = {}
    for home, region in HOMES.items():
        if home not in markets:
            continue
        names = [m for m in REGIONS[region] if m in markets]
        mvars = load_region_variants(app_dir, ix, names)
        # 評估期間固定在「2 年資料窗的第 2 年」：品種只存 2 年（不封存），母作物有 5 年封存；
        # 兩種比法要用同一段期間才能比，也跟比價頁說明（CMP_BT_NOTE）的期間一致
        vfirst = min((es[0][0] for cc in mvars.values() for vv in cc.values() for es in vv.values() if es), default=None)
        e_start = max(Y2_START, iso_add(vfirst, 365)) if vfirst else Y2_START
        res = {}
        for level in ("parent", "variety"):
            acc = {f: {k: 0.0 for k in ("strat", "naive", "static", "oracle", "base")} | {"n": 0, "remote": 0, "hit": 0, "closed": 0}
                   for f in FREIGHTS}
            per = {f: [] for f in FREIGHTS}
            units = 0
            for c, es in markets[home].items():
                hv = mvars.get(home, {}).get(c) or {}
                if level == "parent":
                    todo = [(c, [(d, v["avg"]) for d, v in es],
                             {mk: [(d, v["avg"]) for d, v in markets[mk][c]] for mk in names
                              if mk != home and len(markets[mk].get(c) or []) >= MIN_CMP_DAYS})]
                elif hv:                                        # 有品種：同名品種才比
                    todo = [(f"{c}·{lab}", h, {mk: mvars[mk][c][lab] for mk in names
                                               if mk != home and len((mvars.get(mk, {}).get(c) or {}).get(lab) or []) >= MIN_CMP_DAYS})
                            for lab, h in hv.items()]
                else:                                           # 沒有品種：只跟「也沒分品種」的市場比母作物
                    todo = [(c, [(d, v["avg"]) for d, v in es],
                             {mk: [(d, v["avg"]) for d, v in markets[mk][c]] for mk in names
                              if mk != home and not (mvars.get(mk, {}).get(c)) and len(markets[mk].get(c) or []) >= MIN_CMP_DAYS})]
                for name, h_es, others in todo:
                    if len(h_es) < MIN_DAYS or not others:
                        continue
                    units += 1
                    for f, (sv, n) in _simulate(home, h_es, others, acc, e_start).items():
                        if n >= 100:
                            per[f].append((name, sv, n))
            r = {}
            for f, T in acc.items():
                if not T["n"]:
                    continue
                r[f] = {"n": T["n"], "units": units, "save": 1 - T["strat"] / T["base"], "save_naive": 1 - T["naive"] / T["base"],
                        "save_static": 1 - T["static"] / T["base"], "save_oracle": 1 - T["oracle"] / T["base"],
                        "remote": T["remote"] / T["n"],
                        "hit": T["hit"] / (T["remote"] - T["closed"]) if T["remote"] > T["closed"] else None,
                        "best": sorted(per[f], key=lambda x: -x[1])[:5], "worst": sorted(per[f], key=lambda x: x[1])[:3],
                        "median_unit": statistics.median(x[1] for x in per[f]) if per[f] else None}
            res[level] = r
        del mvars
        out[home] = {"region": region, "others": [m for m in names if m != home], "res": res, "start": e_start}
    return out


# ───────────────────────── F. 零售參考價（精準進貨詳細頁） ─────────────────────────
def _retail_module():
    """載入 merchant/scripts/fetch_retail.py（品名對照表 RETAIL_MAP、批發月均算法跟線上同一份）。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "merchant", "scripts", "fetch_retail.py")
    spec = importlib.util.spec_from_file_location("fetch_retail", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_retail_months(path):
    """retail.json 的 months，或直接是 {月: {品名: 價}} 的檔；讀不到回 {}。"""
    try:
        with open(path, encoding="utf-8") as f:
            j = json.load(f)
    except Exception:
        return {}
    m = j.get("months") if isinstance(j, dict) and "months" in j else j
    return {k: v for k, v in (m or {}).items() if re.match(r"^\d{4}-\d{2}$", k) and isinstance(v, dict)}


def eval_retail(app_dir, ix, months):
    """依時間順序逐月：只用「這個月以前」的月份算每種菜的倍數，再用這個月的台北一批發月均估零售，跟實際零售比。"""
    if not months:
        return None
    fr = _retail_module()
    ent = next((m for m in ix["markets"] if m["name"] == fr.REF_MARKET), None)
    if not ent:
        return None
    cwd = os.getcwd()
    os.chdir(app_dir)
    try:
        data, variants = pc.read_market(ent)
    finally:
        os.chdir(cwd)
    if not data:
        return None
    yms = sorted(months)
    wm = {item: fr.wholesale_monthly(data[crop], var, (variants or {}).get(crop))
          for item, (crop, var) in fr.RETAIL_MAP.items() if crop in data}
    ratio = {item: {ym: months[ym][item] / (w[ym] * fr.JIN) for ym in yms if item in months[ym] and ym in w}
             for item, w in wm.items()}
    q = lambda xs, p: xs[min(len(xs) - 1, int(len(xs) * p))]
    rows = []
    for k, ym in enumerate(yms):
        prior_yms = yms[max(0, k - fr.MULT_MONTHS):k]
        pool = sorted(r[m] for r in ratio.values() for m in prior_yms if m in r)
        for item, r in ratio.items():
            if ym not in r:
                continue
            prior = sorted(r[m] for m in prior_yms if m in r)
            if len(prior) < 3 or len(pool) < 10:
                continue
            w = wm[item][ym] * fr.JIN
            actual = months[ym][item]
            last = next((months[m][item] for m in reversed(yms[:k]) if item in months[m]), None)
            ape = lambda pred: abs(pred - actual) / actual
            lag = {L: months[yms[k - L]].get(item) if k - L >= 0 else None for L in RETAIL_LAGS}   # 零售資料晚 L 個月才看得到
            rows.append({"item": item, "ym": ym, "model": ape(w * statistics.median(prior)),
                         "pool": ape(w * statistics.median(pool)), "fixed": ape(w * RETAIL_FIXED),
                         "last": ape(last) if last else None,
                         **{f"lag{L}": ape(v) if v else None for L, v in lag.items()},
                         "cover": 1 if w * q(prior, .25) <= actual <= w * q(prior, .75) else 0,
                         "in_rule": 1 if 1.5 <= r[ym] <= 2.5 else 0})
    if not rows:
        return None
    lasts = [x for x in rows if x["last"] is not None]
    lags = {}
    for L in RETAIL_LAGS:                                  # 同一批樣本比：那個落後月份有零售價的才算
        xs = [x for x in rows if x[f"lag{L}"] is not None]
        if len(xs) >= 30:
            lags[L] = {"n": len(xs), "lag": statistics.median(x[f"lag{L}"] for x in xs),
                       "model": statistics.median(x["model"] for x in xs)}
    by_item = {}
    for x in rows:
        by_item.setdefault(x["item"], []).append(x["model"])
    return {"n": len(rows), "items": len(by_item), "months": (rows[0]["ym"], rows[-1]["ym"]),
            "mape": {k: statistics.mean(x[k] for x in rows) for k in ("model", "pool", "fixed")} | {"last": statistics.mean(x["last"] for x in lasts) if lasts else None},
            "mdape": {k: statistics.median(x[k] for x in rows) for k in ("model", "pool", "fixed")} | {"last": statistics.median(x["last"] for x in lasts) if lasts else None},
            "within10": {k: statistics.mean(1 if x[k] <= 0.10 else 0 for x in rows) for k in ("model", "pool", "fixed")} | {"last": statistics.mean(1 if x["last"] <= 0.10 else 0 for x in lasts) if lasts else None},
            "beat_last": statistics.mean(1 if x["model"] < x["last"] else 0 for x in lasts) if lasts else None,
            "coverage": statistics.mean(x["cover"] for x in rows), "in_rule": statistics.mean(x["in_rule"] for x in rows),
            "lags": lags,
            "worst": sorted(((it, statistics.mean(v), len(v)) for it, v in by_item.items() if len(v) >= 3), key=lambda x: -x[1])[:5],
            "best": sorted(((it, statistics.mean(v), len(v)) for it, v in by_item.items() if len(v) >= 3), key=lambda x: x[1])[:5]}


# ───────────────────────── 報告 ─────────────────────────
def report(app_dir, out_path, retail_path=None):
    global Y2_START
    t0 = time.time()
    markets, ix, Y2_START, has_arch = load_app(app_dir)
    first = min(es[0][0] for crops in markets.values() for es in crops.values() if es)
    html = open(os.path.join(app_dir, "index.html"), encoding="utf-8").read()
    A, B, C, D = eval_tags(markets), eval_buying(markets), eval_predict(markets), eval_rotation(markets, html)
    s1_start = iso_add(Y2_START, 365)            # 往年同期要 2 個往年：資料起點＋2 年以後 S1 才可能成立
    S1 = eval_s1(markets, s1_start)
    S1_noloss = eval_s1(markets, s1_start, loss=0.0)    # 敏感度：存放完全不損耗時
    E = eval_cross_market(markets, app_dir, ix)
    months = load_retail_months(retail_path) if retail_path else {}
    F = eval_retail(app_dir, ix, months)
    L = [f"# 回測報告（{ix.get('updated', '')} 資料；{len(markets)} 個市場；資料自 {first} 起"
         f"{'（含 5 年封存）' if has_arch else ''}；{Y2_START} 起樣本外）", "",
         "> 每個決策只用當天以前的資料、隔一個交易日成交；運費、損耗、成本為**假設值**（見 scripts/backtest.py 頂部）。",
         "> 市場清單是「現在還查得到的市場」，已關閉的市場不在資料裡（倖存者偏差，影響小但存在）。", ""]
    L += ["## A. 偏貴／偏便宜：訊號後 10 個交易日的平均價變化", "",
          "「偏便宜」準＝之後價格上漲（現在買比較划算）；「偏貴」準＝之後價格下跌（等一下再買）。差距＝偏便宜後平均漲幅 − 偏貴後平均漲幅，越大越有用。", "",
          "| 判斷法 | 標偏便宜比例 | 偏便宜後平均 | 命中率 | 標偏貴比例 | 偏貴後平均 | 命中率 | 差距 |", "|---|---|---|---|---|---|---|---|"]
    hit = lambda x: fmt_pct(x).lstrip("+")
    for rule, name in (("舊", "舊：單日 vs 近 90 筆 ±10%"), ("新", "新：近 3 日 vs 往年同期 0.88/1.12（App 現用）"),
                       ("新2年", "同上，但往年同期要來自 2 個以上往年（試驗，未採用）")):
        s = A[rule]
        L.append(f"| {name} | {s['dn']['share']*100:.0f}% | {fmt_pct(s['dn']['mean'])} | {hit(s['dn']['hit'])} | "
                 f"{s['up']['share']*100:.0f}% | {fmt_pct(s['up']['mean'])} | {hit(s['up']['hit'])} | {fmt_pct(s['spread'])} |")
    tot = sum(A["basis"].values()) or 1
    o1, o2 = A["only1"]["新"], A["only1"]["新2年"]
    better2 = (A["新2年"]["dn"]["hit"] or 0) >= (A["新"]["dn"]["hit"] or 0) and (A["新2年"]["spread"] or 0) >= (A["新"]["spread"] or 0)
    L += ["", f"新判斷法用到「往年同期」基準的比例：{A['basis']['s']/tot*100:.0f}%（其餘樣本不足、退回近 90 筆）。",
          f"全部樣本的平均變化（基準線）：{fmt_pct(A['新']['all_mean'])}。", "",
          f"「要 2 個往年」（避免只有 1 年可比時被那年的颱風、寒流帶偏）只影響「只有 1 個往年可比」的 {o1['n']} 個樣本"
          f"（{o1['n']/tot*100:.0f}%，大多在樣本外第一年）：用那 1 年判斷，偏便宜命中 {hit(o1['dn']['hit'])}、差距 {fmt_pct(o1['spread'])}；"
          f"改成退回近 90 筆，偏便宜命中 {hit(o2['dn']['hit'])}、差距 {fmt_pct(o2['spread'])}。"
          + ("→ 要 2 個往年比較好。" if better2 else "→ 只有 1 年可比時，用那 1 年仍比退回舊法準；App 維持「1 個往年就判斷」，不採用。"), ""]
    cur, s2, cut = A["新"], A["S2"], A["S2濾掉"]
    why = A["s2_why"]
    nsig = sum(why.values()) or 1
    beats2 = (s2["dn"]["hit"] or 0) > (cur["dn"]["hit"] or 0) and (s2["up"]["hit"] or 0) > (cur["up"]["hit"] or 0) \
        and (s2["spread"] or 0) > (cur["spread"] or 0)
    L += ["## S2. 雜訊過濾：價格忽高忽低或成交量太小時，不發「偏貴／偏便宜」訊號", "",
          f"規則：近 {S2_WIN_DAYS} 天均價的變異係數（標準差÷平均）≤ {S2_CV_MAX:g}，而且近 {S2_WIN_DAYS} 天平均每交易日成交量 ≥ 該市場門檻才發訊號"
          f"（近 {S2_WIN_DAYS} 天不到 {S2_MIN_WIN} 個交易日也不發）。門檻＝每月 1 號只用之前 365 天算的「該市場各品項平均每交易日成交量」"
          f"第 {S2_VOL_PCTL*100:.0f} 百分位。數字事先給定、沒有用這份資料調；跟 A 同一批樣本。", "",
          "| 判斷法 | 偏便宜訊號數 | 偏便宜後平均 | 命中率 | 偏貴訊號數 | 偏貴後平均 | 命中率 | 差距 |", "|---|---|---|---|---|---|---|---|"]
    for s, name in ((cur, "現行：往年同期，不過濾"), (s2, "S2：過濾後"), (cut, "（被 S2 濾掉的那些訊號）")):
        L.append(f"| {name} | {s['dn']['n']:,} | {fmt_pct(s['dn']['mean'])} | {hit(s['dn']['hit'])} | "
                 f"{s['up']['n']:,} | {fmt_pct(s['up']['mean'])} | {hit(s['up']['hit'])} | {fmt_pct(s['spread'])} |")
    L += ["", f"{nsig:,} 個訊號被濾掉 {nsig - why['ok']:,} 個：價格變異太大 {why['cv']:,}、成交量低於門檻 {why['vol']:,}、"
          f"近 {S2_WIN_DAYS} 天交易日太少 {why['thin']:,}。",
          ("結論：S2 偏便宜、偏貴命中率與差距都比現行好。" if beats2 else
           f"結論：S2 沒有勝過現行（偏便宜命中 {hit(cur['dn']['hit'])}→{hit(s2['dn']['hit'])}、偏貴 {hit(cur['up']['hit'])}→{hit(s2['up']['hit'])}、"
           f"差距 {fmt_pct(cur['spread'])}→{fmt_pct(s2['spread'])}），訊號還少了 {(1 - s2['dn']['n'] / max(cur['dn']['n'], 1)) * 100:.0f}%；"
           f"被濾掉的訊號（價格忽高忽低或量小的品項）差距 {fmt_pct(cut['spread'])}，反而是之後變動最大、最有用的那些。"
           "不接進 LINE 推播（scripts/notify.py 維持現行）。"), ""]
    L += ["## B. 季節低價進貨（耐放品項）vs 每週固定進貨", "",
          f"每週用 {WEEKLY_NEED} 公斤；運費每趟 {TRIP_COST} 元、存放損耗每週 {LOSS_PER_WEEK*100:.0f}%（假設）。策略：往年同期基準標「偏便宜」時，隔天進到可存放上限。", "",
          "| 市場 | 品項 | 策略每公斤 | 固定進貨每公斤 | 省下 | 多進貨次數 |", "|---|---|---|---|---|---|"]
    for r in B:
        L.append(f"| {r['market']} | {r['crop']} | {r['strategy']:.1f} | {r['baseline']:.1f} | {fmt_pct(r['save'])} | {r['cheap_buys']} |")
    if B:
        L += ["", f"平均省下：{fmt_pct(statistics.mean(r['save'] for r in B))}；中位數：{fmt_pct(statistics.median(r['save'] for r in B))}。", ""]
    L += ["## S1. 量價雙確認進貨（耐放品項）vs 每週固定、每天買、B、事後最佳", "",
          f"規則：第 t 天收市後，近 3 日價 ÷ 往年同期（要 {S1_MIN_YEARS} 個以上往年）≤ {S1_MAX_R:g}、一年百分位 ≤ {S1_MAX_PCT*100:.0f}%、"
          f"到貨量比 ≥ {S1_MIN_SUP:g}（後兩個跟 App 詳細頁同一套 price_core.year_stats）→ 隔一個交易日照當天均價進到可存放上限；"
          "其他時候照每週固定進貨。三個門檻事先給定、沒有用這份資料調。",
          f"期間：{s1_start} 起（要 2 個往年，這天以後才可能成立）；品項只用這天以前的資料篩（至少 {S1_MIN_PRIOR_DAYS} 個交易日）。"
          f"每週用 {WEEKLY_NEED} 公斤、運費每趟 {TRIP_COST} 元、存放損耗每週 {LOSS_PER_WEEK*100:.0f}%（同 B）。"
          "每天買＝每個交易日只進當天用量、每天一趟運費；括號裡只算菜錢（本來就天天跑市場的人，運費不算多花）。"
          "事後最佳＝已經知道全部價格、同一套規則下最便宜的排法（上限，實際做不到）。表內是每公斤成本（元，含運費與損耗）。", "",
          "| 市場 | 品項 | 每週固定 | 每天買（只算菜錢） | B 季節低價 | S1 量價雙確認 | 事後最佳 | S1 比每週固定省 | S1 加碼次數 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in S1:
        L.append(f"| {r['market']} | {r['crop']} | {r['weekly']:.1f} | {r['daily']:.1f}（{r['daily_px']:.1f}） | {r['b']:.1f} | {r['s1']:.1f} | "
                 f"{r['best']:.1f} | {fmt_pct(1 - r['s1'] / r['weekly'])} | {r['s1_n']} |")
    if S1:
        sv = lambda k: [1 - r[k] / r["weekly"] for r in S1]
        ms = lambda k: f"{fmt_pct(statistics.mean(sv(k)))}（中位數 {fmt_pct(statistics.median(sv(k)))}）"
        s1_win = statistics.mean(sv("s1")) > 0 and statistics.median(sv("s1")) > 0
        nl = lambda k: statistics.mean(1 - r[k] / r["weekly"] for r in S1_noloss) if S1_noloss else None
        L += ["", f"比每週固定平均省下：每天買 {ms('daily')}、只算菜錢 {ms('daily_px')}；B {ms('b')}；S1 {ms('s1')}；事後最佳 {ms('best')}。"
              f"S1 比每週固定省到錢的品項：{sum(1 for x in sv('s1') if x > 0)}／{len(S1)}；S1 比 B 平均 {fmt_pct(statistics.mean(1 - r['s1'] / r['b'] for r in S1))}。",
              f"敏感度（只當參考）：假設存放完全不損耗，S1 {fmt_pct(nl('s1'))}、B {fmt_pct(nl('b'))}"
              + ("——買到的價錢確實比較低，是每週存放損耗把省下的吃掉；損耗比假設低很多的品項（例如冷藏）才可能划算。" if (nl("s1") or 0) > 0 else "。"),
              ("結論：S1 勝過每週固定進貨。" if s1_win else
               f"結論：S1 沒有勝過每週固定進貨（平均 {fmt_pct(statistics.mean(sv('s1')))}，B {fmt_pct(statistics.mean(sv('b')))}）。"
               f"事後最佳能省 {fmt_pct(statistics.mean(sv('best')))}，代表便宜的時機存在，但當天看得到的這三個數字抓不到。"
               "不接進 LINE 推播（scripts/notify.py 維持現行）。"), ""]
    L += ["## C. 採收預判（predict_price）準不準", "",
          "MAPE＝平均絕對誤差率（越低越好）；區間命中＝實際價落在預測 P25～P75 的比例（設計上應接近 50%）。", "",
          "| 幾天後 | 樣本 | 模型 MAPE | 價格不變 MAPE | 去年同期 MAPE | 模型贏「不變」 | 模型贏「去年」 | 區間命中 |", "|---|---|---|---|---|---|---|---|"]
    for h, s in C.items():
        L.append(f"| {h} 天 | {s['n']} | {s['mape_model']*100:.1f}% | {s['mape_last']*100:.1f}% | {s['mape_ly']*100:.1f}% | "
                 f"{s['beat_last']*100:.0f}% | {s['beat_ly']*100:.0f}% | {s['coverage']*100:.0f}% |")
    L += ["", "（中位數誤差：" + "；".join(f"{h} 天 模型 {s['mdape_model']*100:.0f}% / 不變 {s['mdape_last']*100:.0f}% / 去年 {s['mdape_ly']*100:.0f}%" for h, s in C.items()) + "）", ""]
    L += ["## D. 搭配種法：每兩週做一次「種哪一種」的決定，收成時的實際每天淨利（元/天/分地）", "",
          f"成本每期 {COST_PER_CYCLE} 元、手續費 {FEE*100:.0f}%、單產用 App 內建估算值（未用縣市統計）。", ""]
    for mk, r in D.items():
        L += [f"**{mk}**（候選：{'、'.join(a if a == w else f'{a}→{w}' for a, w in r['candidates'])}）", "",
              "| 選法 | 平均每天淨利 | 決策次數 |", "|---|---|---|"]
        for k, (v, n) in r["result"].items():
            L.append(f"| {k} | {'—' if v is None else f'{v:,.0f}'} | {n} |")
        L.append("")
    L += ["## E. 精準進貨跨市場比價：照「含運最便宜」去別的市場進貨，比一直在自己市場買省多少", "",
          f"每個交易日收市後看 App 比價頁（近 {FRESH_DAYS} 天內有成交的市場），隔一個交易日去「含運價最低」的市場買；"
          f"別的市場要加運費、路上損耗 {TRANSIT_LOSS*100:.0f}%（假設），那天沒開市就在自己市場買。"
          "只跟同一區、當天來回得了的市場比。省下＝跟一直在自己市場買相比（正＝省、負＝反而多花）。", "",
          "⚠ 兩種比法差很多：「全部綜合」是各市場所有品種混在一起的均價，各市場賣的品種、等級不一樣"
          "（例：高雄的韭菜混了很多韭菜黃，屏東多是白頭韭菜），看起來便宜很多其實是買到不同的東西。"
          "「同品種比」只比名稱相同的品種（排除「其他」「進口」），比較接近真的能省的錢；但同品種也可能等級不同，仍是上限。", ""]
    for home, r in E.items():
        L += [f"**{home}**（{r['start']} 起；同區比：{'、'.join(r['others'])}）", "",
              "| 比法 | 運費（元/公斤） | 去別的市場的天數 | 去了真的比較便宜 | 照 App 含運排名 | 不填運費只看最便宜 | 固定去平常最便宜的（每月挑一次） | 事後最佳（上限） |",
              "|---|---|---|---|---|---|---|---|"]
        for level, lname in (("parent", "全部綜合"), ("variety", "同品種")):
            for f, x in r["res"].get(level, {}).items():
                hit = "—" if x["hit"] is None else f"{x['hit'] * 100:.0f}%"
                L.append(f"| {lname} | {f} | {x['remote']*100:.0f}% | {hit} | {fmt_pct(x['save'])} | {fmt_pct(x['save_naive'])} | "
                         f"{fmt_pct(x['save_static'])} | {fmt_pct(x['save_oracle'])} |")
        v2 = r["res"].get("variety", {}).get(2)
        if v2 and v2["best"]:
            L += ["", f"同品種、運費 2 元/公斤（{v2['units']} 個品項·品種，每個至少 100 天）：省下的中位數 {fmt_pct(v2['median_unit'])}；"
                  "最值得跨市場買：" + "、".join(f"{c} {fmt_pct(v)}" for c, v, _ in v2["best"]) +
                  "；最不值得：" + "、".join(f"{c} {fmt_pct(v)}" for c, v, _ in v2["worst"]) + "。"]
        L.append("")
    L += ["## F. 零售參考價：批發 × 每種菜自己的倍數，估得準不準", ""]
    if not F:
        L += ["還沒有零售資料（等 merchant 排程抓到 retail.json 後重跑：`python3 scripts/backtest.py --retail merchant/retail.json`）。", ""]
    else:
        L += [f"臺北市公有零售市場月均價 vs 台北一批發月均，{F['months'][0]}～{F['months'][1]} 逐月、{F['items']} 個品項、{F['n']} 筆；"
              "每個月只用之前（最多 12 個月）的資料算倍數（樣本外）。誤差＝|估計−實際|÷實際。", "",
              "| 估法 | 平均誤差 | 誤差中位數 | 誤差在 10% 內 |", "|---|---|---|---|"]
        for k, name in (("model", "批發 × 每種菜自己的倍數（App 用的）"), ("pool", "批發 × 全部菜共用一個倍數"),
                        ("fixed", f"批發 × 固定 {RETAIL_FIXED:g} 倍"), ("last", "直接用上個月零售價")):
            if F["mape"][k] is None:
                continue
            L.append(f"| {name} | {F['mape'][k]*100:.0f}% | {F['mdape'][k]*100:.0f}% | {F['within10'][k]*100:.0f}% |")
        L += ["", f"App 顯示的「中間一半」倍數區間，實際零售落在裡面的比例：{F['coverage']*100:.0f}%（設計上約 50%）。",
              f"實際零售 ÷ 批發落在「1.5～2.5 倍」的比例：{F['in_rule']*100:.0f}%。"
              + (f"每種菜自己的倍數贏「上個月零售價」的比例：{F['beat_last']*100:.0f}%。" if F["beat_last"] is not None else ""),
              ""]
        if F["lags"]:
            L += ["零售資料越舊，「直接用舊零售價」越不準；這時改用「今天批發 × 倍數」：", "",
                  "| 零售資料晚幾個月 | 直接用舊零售價（誤差中位數） | 批發 × 倍數（誤差中位數） | 樣本 |", "|---|---|---|---|"]
            for lg, x in F["lags"].items():
                L.append(f"| {lg} 個月 | {x['lag']*100:.0f}% | {x['model']*100:.0f}% | {x['n']} |")
            L.append("")
        L += ["估得最準：" + "、".join(f"{it} {v*100:.0f}%" for it, v, _ in F["best"]) +
              "；最不準：" + "、".join(f"{it} {v*100:.0f}%" for it, v, _ in F["worst"]) + "。", ""]
    L.append(f"_執行時間 {time.time() - t0:.0f} 秒；產生方式：`python3 scripts/backtest.py"
             f"{' --retail ' + retail_path if retail_path else ''}`。_")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return {"A": A, "B": B, "S1": S1, "C": C, "D": D, "E": E, "F": F}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-dir", default=".")
    ap.add_argument("--out", default="docs/backtest.md")
    ap.add_argument("--retail", default="merchant/retail.json", help="零售月均價（retail.json 或 {月:{品名:價}}）")
    a = ap.parse_args()
    r = report(a.app_dir, a.out, a.retail)
    print(open(a.out, encoding="utf-8").read())
    return r


if __name__ == "__main__":
    main()
