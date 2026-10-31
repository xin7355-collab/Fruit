"""
backtest.py —— 離線回測（不連網），用 repo 裡的真實批發行情回答四個問題：

  A. 新的「偏貴／偏便宜」（近 3 日量加權價 vs 往年同期）有沒有比舊的（單日價 vs 近 90 筆中位數 ±10%）準？
  B. 精準進貨「季節低價時多進一點」策略，比「每週固定進貨」省多少？（含運費、存放損耗）
  C. 採收預判 predict_price 準不準？跟兩個天真基準比：「價格不變」（＝買進持有的對應）、「去年同期價」。
  D. 搭配種法「挑預估每天淨利最高的菜」，實際收成時有沒有比「一直種小白菜」「種去年同期最賺的」好？

防偏誤（每一題都適用）：
  - 未來函數：每個決策只用「當天（含）以前」的資料；訊號出現後，用「下一個交易日」的價格成交。
  - 樣本外：判斷門檻（0.88／1.12、±10%）不是用這份資料調出來的；只在第 2 年（往年同期有資料時）評估。
  - 成本：沒有證交稅；運費、損耗、手續費都是參數（下方預設值是「假設」，請換成你的實際數字）。
  - 倖存者偏差：作物用當時有成交的；但市場清單是「現在還查得到的 19 個」，已關閉的市場不在資料裡（報告會註明）。

用法：python3 scripts/backtest.py [--app-dir .] [--out docs/backtest.md]
"""
import argparse
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
Y2_START = "2025-10-01"          # 第 2 年起：此後「往年同期」才有資料
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


def iso_add(iso, n):
    return (date.fromisoformat(iso) + timedelta(days=n)).isoformat()


def load_app(app_dir):
    ix = json.load(open(os.path.join(app_dir, "prices_index.json"), encoding="utf-8"))
    out = {}
    cwd = os.getcwd()
    os.chdir(app_dir)
    try:
        for m in ix["markets"]:
            data, _ = pc.read_market(m)
            if data:
                out[m["name"]] = {c: pc._valid(s) for c, s in data.items()}
    finally:
        os.chdir(cwd)
    return out, ix


def avg_near(es_by_date, iso, win=3):
    """目標日前後 win 天內的平均價（沒有成交回 None）。"""
    xs = [es_by_date[d] for d in (iso_add(iso, k) for k in range(-win, win + 1)) if d in es_by_date]
    return sum(xs) / len(xs) if xs else None


def fmt_pct(x):
    return "—" if x is None else f"{x * 100:+.1f}%"


# ───────────────────────── A. 貴賤判斷 ─────────────────────────
def eval_tags(markets):
    rows = []   # (rule, tag, fwd)
    basis_cnt = {"s": 0, "r": 0}
    for mk, crops in markets.items():
        for c, es in crops.items():
            if len(es) < MIN_DAYS:
                continue
            for i in range(0, len(es) - FWD_DAYS - 2, STEP):
                if es[i][0] < Y2_START:
                    continue
                pre = es[:i + 1]
                new_t, new_b = pc.season_signal(pre)
                old_t, _ = pc.old_signal(pre)
                entry = es[i + 1][1]["avg"]
                fut = statistics.mean(v["avg"] for _, v in es[i + 2:i + 2 + FWD_DAYS])
                fwd = fut / entry - 1
                rows.append(("舊", old_t, fwd))
                rows.append(("新", new_t, fwd))
                basis_cnt[new_b] += 1
    out = {}
    for rule in ("舊", "新"):
        rr = [r for r in rows if r[0] == rule]
        st = {}
        for t in ("dn", "mid", "up"):
            f = [r[2] for r in rr if r[1] == t]
            hit = (sum(1 for x in f if (x > 0 if t == "dn" else x < 0)) / len(f)) if f and t != "mid" else None
            st[t] = {"n": len(f), "share": len(f) / len(rr) if rr else 0,
                     "mean": statistics.mean(f) if f else None, "median": statistics.median(f) if f else None, "hit": hit}
        st["spread"] = (st["dn"]["mean"] - st["up"]["mean"]) if st["dn"]["mean"] is not None and st["up"]["mean"] is not None else None
        st["all_mean"] = statistics.mean(r[2] for r in rr) if rr else None
        out[rule] = st
    out["basis"] = basis_cnt
    return out


# ───────────────────────── B. 季節低價進貨 ─────────────────────────
def sim_buying(es, cap_weeks):
    """回傳 (策略每公斤成本, 基準每公斤成本, 策略多進貨次數)；只用第 2 年。"""
    idx = [i for i, (d, _) in enumerate(es) if d >= Y2_START]
    if len(idx) < 60:
        return None
    weeks = {}
    for i in idx:
        y, w, _ = date.fromisoformat(es[i][0]).isocalendar()
        weeks.setdefault((y, w), []).append(i)
    wk_keys = sorted(weeks)
    cap = WEEKLY_NEED * cap_weeks

    def run(strategy):
        inv, spent, trips, bought, cheap_buys = 0.0, 0.0, 0, 0.0, 0
        for k in wk_keys:
            days = weeks[k]
            first = days[0]
            if inv < WEEKLY_NEED:                                   # 週初不夠用：照當天價補足（被迫買）
                q = WEEKLY_NEED - inv
                spent += q * es[first][1]["avg"]
                trips += 1
                bought += q
                inv += q
            if strategy:
                for i in days:
                    if i + 1 >= len(es) or inv >= cap:
                        continue
                    st, sb = pc.season_signal(es[:i + 1])
                    if st == "dn" and sb == "s":                    # 往年同期基準下「偏便宜」→ 隔天多進到存量上限
                        q = cap - inv
                        spent += q * es[i + 1][1]["avg"]
                        trips += 1
                        bought += q
                        inv += q
                        cheap_buys += 1
            inv -= WEEKLY_NEED
            inv *= (1 - LOSS_PER_WEEK)                               # 存放損耗
        leftover_value = max(inv, 0) * es[-1][1]["avg"]
        used = WEEKLY_NEED * len(wk_keys)
        return (spent + trips * TRIP_COST - leftover_value) / used, cheap_buys
    s_cost, n_cheap = run(True)
    b_cost, _ = run(False)
    return s_cost, b_cost, n_cheap


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
            series = dict(es)
            by = {d: v["avg"] for d, v in es}
            last_day = es[-1][0]
            for i in range(0, len(es), STEP * 3):
                t = es[i][0]
                if t < Y2_START:
                    continue
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
                p = pc.predict_price(dict(crops[w]), hv, as_of=d)
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


# ───────────────────────── 報告 ─────────────────────────
def report(app_dir, out_path):
    t0 = time.time()
    markets, ix = load_app(app_dir)
    html = open(os.path.join(app_dir, "index.html"), encoding="utf-8").read()
    A, B, C, D = eval_tags(markets), eval_buying(markets), eval_predict(markets), eval_rotation(markets, html)
    L = [f"# 回測報告（{ix.get('updated', '')} 資料；{len(markets)} 個市場；第 2 年 {Y2_START} 起樣本外）", "",
         "> 每個決策只用當天以前的資料、隔一個交易日成交；運費、損耗、成本為**假設值**（見 scripts/backtest.py 頂部）。",
         "> 市場清單是「現在還查得到的市場」，已關閉的市場不在資料裡（倖存者偏差，影響小但存在）。", ""]
    L += ["## A. 偏貴／偏便宜：訊號後 10 個交易日的平均價變化", "",
          "「偏便宜」準＝之後價格上漲（現在買比較划算）；「偏貴」準＝之後價格下跌（等一下再買）。差距＝偏便宜後平均漲幅 − 偏貴後平均漲幅，越大越有用。", "",
          "| 判斷法 | 標偏便宜比例 | 偏便宜後平均 | 命中率 | 標偏貴比例 | 偏貴後平均 | 命中率 | 差距 |", "|---|---|---|---|---|---|---|---|"]
    for rule, name in (("舊", "舊：單日 vs 近 90 筆 ±10%"), ("新", "新：近 3 日 vs 往年同期 0.88/1.12")):
        s = A[rule]
        L.append(f"| {name} | {s['dn']['share']*100:.0f}% | {fmt_pct(s['dn']['mean'])} | {fmt_pct(s['dn']['hit']).lstrip('+')} | "
                 f"{s['up']['share']*100:.0f}% | {fmt_pct(s['up']['mean'])} | {fmt_pct(s['up']['hit']).lstrip('+')} | {fmt_pct(s['spread'])} |")
    tot = sum(A["basis"].values()) or 1
    L += ["", f"新判斷法用到「往年同期」基準的比例：{A['basis']['s']/tot*100:.0f}%（其餘樣本不足、退回近 90 筆）。",
          f"全部樣本的平均變化（基準線）：{fmt_pct(A['新']['all_mean'])}。", ""]
    L += ["## B. 季節低價進貨（耐放品項）vs 每週固定進貨", "",
          f"每週用 {WEEKLY_NEED} 公斤；運費每趟 {TRIP_COST} 元、存放損耗每週 {LOSS_PER_WEEK*100:.0f}%（假設）。策略：往年同期基準標「偏便宜」時，隔天進到可存放上限。", "",
          "| 市場 | 品項 | 策略每公斤 | 固定進貨每公斤 | 省下 | 多進貨次數 |", "|---|---|---|---|---|---|"]
    for r in B:
        L.append(f"| {r['market']} | {r['crop']} | {r['strategy']:.1f} | {r['baseline']:.1f} | {fmt_pct(r['save'])} | {r['cheap_buys']} |")
    if B:
        L += ["", f"平均省下：{fmt_pct(statistics.mean(r['save'] for r in B))}；中位數：{fmt_pct(statistics.median(r['save'] for r in B))}。", ""]
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
    L.append(f"_執行時間 {time.time() - t0:.0f} 秒；產生方式：`python3 scripts/backtest.py`。_")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return {"A": A, "B": B, "C": C, "D": D}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-dir", default=".")
    ap.add_argument("--out", default="docs/backtest.md")
    a = ap.parse_args()
    r = report(a.app_dir, a.out)
    print(open(a.out, encoding="utf-8").read())
    return r


if __name__ == "__main__":
    main()
