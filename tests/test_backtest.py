"""backtest.py 的 E（跨市場進貨）、F（零售參考價）、S1（量價雙確認進貨）、S2（雜訊過濾）：
確認沒有偷看未來、運費／損耗／休市的算法對（不連網、用假資料）。"""
import json
import os
import sys
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import backtest as bt  # noqa: E402


@pytest.fixture(autouse=True)
def _all_days(monkeypatch):
    # 假資料從 2025-01 開始，讓每一天都算「樣本外」
    monkeypatch.setattr(bt, "Y2_START", "2000-01-01")
    monkeypatch.setattr(bt, "FREIGHTS", (0, 5))


def days(n, start="2025-01-01"):
    return [bt.iso_add(start, i) for i in range(n)]


def test_cross_market_pays_freight_and_loss():
    d = days(6)
    home = [(x, 100.0) for x in d]
    others = {"A": [(x, 80.0) for x in d]}
    acc = {f: {k: 0.0 for k in ("strat", "naive", "static", "oracle", "base")} | {"n": 0, "remote": 0, "hit": 0, "closed": 0}
           for f in bt.FREIGHTS}
    unit = bt._simulate("H", home, others, acc)
    cost = 80 / (1 - bt.TRANSIT_LOSS) + 5
    assert acc[5]["n"] == 5                                   # 6 天 → 5 次「隔天進貨」
    assert acc[5]["strat"] == pytest.approx(cost * 5)         # 別的市場：價÷(1−損耗)＋運費
    assert unit[5][0] == pytest.approx(1 - cost / 100)
    assert acc[5]["hit"] == 5 and acc[5]["remote"] == 5


def test_cross_market_no_lookahead():
    """B 只有「隔天」才便宜：當晚看不到 → App 規則不會去 B；只有事後最佳（上限）拿得到。"""
    d = days(3)
    home = [(x, 100.0) for x in d]
    others = {"B": [(d[0], 120.0), (d[1], 50.0), (d[2], 120.0)]}
    acc = {f: {k: 0.0 for k in ("strat", "naive", "static", "oracle", "base")} | {"n": 0, "remote": 0, "hit": 0, "closed": 0}
           for f in bt.FREIGHTS}
    bt._simulate("H", home, others, acc)
    # 第 1 晚（d0）B 看起來 120 比較貴 → 隔天在自己市場買 100；第 2 晚（d1）B 看起來 50 → 隔天 d2 去 B 買到 120（虧）
    assert acc[0]["strat"] == pytest.approx(100 + 120 / (1 - bt.TRANSIT_LOSS))
    assert acc[0]["oracle"] == pytest.approx(50 / (1 - bt.TRANSIT_LOSS) + 100)   # 事後最佳：d1 在 B 買
    assert acc[0]["hit"] == 0


def test_cross_market_closed_day_falls_back_home():
    d = days(3)
    home = [(x, 100.0) for x in d]
    others = {"A": [(d[0], 60.0), (d[1], 60.0)]}             # A 在 d2 沒開市
    acc = {f: {k: 0.0 for k in ("strat", "naive", "static", "oracle", "base")} | {"n": 0, "remote": 0, "hit": 0, "closed": 0}
           for f in bt.FREIGHTS}
    bt._simulate("H", home, others, acc)
    assert acc[0]["closed"] == 1                              # d1 晚上選 A、d2 A 沒開 → 只能在自己市場買
    assert acc[0]["strat"] == pytest.approx(60 / (1 - bt.TRANSIT_LOSS) + 100)


def test_cross_market_skips_after_long_closure():
    d = ["2025-01-01", "2025-01-02", "2025-01-10", "2025-01-11"]   # 中間休市 8 天（像過年）
    home = [(x, 100.0) for x in d]
    others = {"A": [(x, 50.0) for x in d]}
    acc = {f: {k: 0.0 for k in ("strat", "naive", "static", "oracle", "base")} | {"n": 0, "remote": 0, "hit": 0, "closed": 0}
           for f in bt.FREIGHTS}
    bt._simulate("H", home, others, acc)
    assert acc[0]["n"] == 2                                   # 01-02→01-10 那一次不比（最新價都是舊的）


def test_retail_months_shapes(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps({"months": {"2025-01": {"甘藍": 60}}, "items": {}}), encoding="utf-8")
    b.write_text(json.dumps({"2025-01": {"甘藍": 60}, "bad": 1}), encoding="utf-8")
    assert bt.load_retail_months(str(a)) == {"2025-01": {"甘藍": 60}}
    assert bt.load_retail_months(str(b)) == {"2025-01": {"甘藍": 60}}
    assert bt.load_retail_months(str(tmp_path / "none.json")) == {}


def test_retail_uses_only_past_months(tmp_path):
    """零售倍數只能用「這個月以前」的月份算：最後一個月倍數突然變 2 倍，估計要用舊倍數（所以會估錯）。"""
    fr = bt._retail_module()
    os.makedirs(tmp_path / "prices")
    yms = [f"2025-{m:02d}" for m in range(1, 8)]
    series = {f"{ym}-{d:02d}": {"avg": 20.0, "qty": 100} for ym in yms for d in range(1, 13)}
    data = {c: series for c in ("甘藍", "小白菜", "胡瓜", "茄子")}
    (tmp_path / "prices" / "01.json").write_text(json.dumps({"data": data, "variants": {}}), encoding="utf-8")
    ix = {"markets": [{"name": fr.REF_MARKET, "file": "prices/01.json"}]}
    w = 20.0 * fr.JIN
    months = {ym: {"甘藍": w * 3, "小白菜": w * 2, "胡瓜": w * 2, "茄子": w * 2} for ym in yms}
    months[yms[-1]]["甘藍"] = w * 6                           # 最後一個月甘藍零售突然變 2 倍
    F = bt.eval_retail(str(tmp_path), ix, months)
    assert F and F["months"][1] == yms[-1]
    # 前 6 個月倍數都準（誤差 0），只有最後一個月甘藍估 3 倍、實際 6 倍 → 誤差 50%
    assert F["worst"][0][0] == "甘藍" and F["worst"][0][1] > 0
    assert F["mdape"]["model"] == pytest.approx(0)


# ───────────────────────── S1（量價雙確認進貨）、S2（雜訊過濾）──2026-11-02 ─────────────────────────
def daily_es(start, n, price=lambda d: 20.0, qty=lambda d: 100):
    return [(d, {"avg": price(d), "qty": qty(d)}) for d in days(n, start)]


def test_weekly_daily_hindsight_costs_on_flat_prices(monkeypatch):
    es = [(d, {"avg": 10.0, "qty": 1}) for d in days(70, "2025-01-06")]   # 2025-01-06 是週一：剛好 10 週
    wk = bt._weeks(es, "2025-01-06")
    assert len(wk) == 10 and all(len(w) == 7 for w in wk)
    assert bt._run_stock(es, wk, cap=400)[0] == pytest.approx(10 + bt.TRIP_COST / bt.WEEKLY_NEED)   # 每週一趟
    daily, px = bt._run_daily(es, wk)
    assert px == pytest.approx(10) and daily == pytest.approx(10 + 7 * bt.TRIP_COST / bt.WEEKLY_NEED)   # 每天一趟
    monkeypatch.setattr(bt, "LOSS_PER_WEEK", 0.0)
    # 不損耗、價格不變：最省是每趟買滿 4 週 → 10 週要 3 趟（4＋4＋2）
    assert bt._hindsight(es, wk, 4) == pytest.approx(10 + 3 * bt.TRIP_COST / (bt.WEEKLY_NEED * 10))


def test_hindsight_never_worse_than_weekly():
    import random
    rnd = random.Random(7)
    es = [(d, {"avg": rnd.uniform(5, 50), "qty": 1}) for d in days(200, "2025-01-06")]
    wk = bt._weeks(es, "2025-01-06")
    for cap_weeks in (1, 2, 4, 8):
        weekly = bt._run_stock(es, wk, bt.WEEKLY_NEED * cap_weeks)[0]
        assert bt._hindsight(es, wk, cap_weeks) <= weekly + 1e-9, cap_weeks   # 每週固定也是 DP 的一種排法
    assert bt._hindsight(es, wk, 1) == pytest.approx(bt._run_stock(es, wk, bt.WEEKLY_NEED)[0]), "只能放 1 週＝只能每週買"


def s1_series(first="2023-01-01", end_price=15.0, surge=True):
    """每天 20 元、100 公斤；最後 3 天降到 end_price，最後 14 天到貨量翻倍（surge）。"""
    es = daily_es(first, (date(2026, 3, 31) - date.fromisoformat(first)).days + 1)
    last = es[-1][0]
    out = []
    for d, v in es:
        age = (date.fromisoformat(last) - date.fromisoformat(d)).days
        out.append((d, {"avg": end_price if age < 3 else 20.0, "qty": 200 if (surge and age < 14) else 100}))
    return out


def test_s1_trigger_needs_all_three_conditions():
    es = s1_series()
    i = len(es) - 1
    assert bt._s1_trigger(es, i), "比往年同期低、一年最低價位、到貨量翻倍 → 成立"
    assert not bt._s1_trigger(s1_series(surge=False), i), "到貨量沒變多 → 不成立"
    assert not bt._s1_trigger(s1_series(end_price=25.0), i), "價格在一年最高的位置 → 不成立"
    short = s1_series(first="2025-01-01")
    assert not bt._s1_trigger(short, len(short) - 1), "只有 1 個往年可比 → 不成立（S1 要 2 個往年）"


def test_s1_trigger_does_not_peek_at_future():
    es = s1_series()
    i = len(es) - 30                       # 30 天前：那時價格、量都正常 → 不成立
    fut = es[:i + 1] + [(d, {"avg": 1.0, "qty": 99999}) for d, _ in es[i + 1:]]   # 之後改成超便宜、爆量
    assert bt._s1_trigger(es, i) == bt._s1_trigger(fut, i) is False
    assert bt._s1_trigger(es, len(es) - 1) == bt._s1_trigger(es[:len(es)], len(es) - 1)


def test_s2_thresholds_use_only_data_before_the_month():
    crops = {f"菜{k}": daily_es("2024-01-01", 500, qty=lambda d, k=k: 100 * (k + 1)) for k in range(8)}
    thr = bt.s2_thresholds(crops)
    later = {c: [(d, {"avg": v["avg"], "qty": (v["qty"] * 50 if d >= "2025-03-01" else v["qty"])}) for d, v in es]
             for c, es in crops.items()}
    thr2 = bt.s2_thresholds(later)
    assert thr["2025-03"] == thr2["2025-03"], "3 月的門檻只看 3 月 1 號以前：之後爆量不能影響"
    assert thr["2025-03"] == 300, "8 個品項 100～800 公斤：第 25 百分位取排序後第 int(8×0.25)＝2 個 → 300"
    assert "2024-01" not in thr, "第一個月之前沒有資料 → 沒有門檻（不發訊號），不是 0"


def test_s2_check_filters_noisy_thin_and_small():
    thr = {"2025-03": 150.0}
    es = daily_es("2025-02-01", 40)                                    # 每天 20 元、100 公斤
    i = len(es) - 1                                                    # 2025-03-12
    assert bt.s2_check(es, i, thr) == (False, "vol"), "量 100 < 門檻 150"
    assert bt.s2_check(es, i, {"2025-03": 100.0}) == (True, "ok")
    jumpy = [(d, {"avg": (10.0 if k % 2 else 40.0), "qty": 500}) for k, (d, _) in enumerate(es)]   # 10、40 元交替：變異係數 0.6
    assert bt.s2_check(jumpy, i, thr) == (False, "cv")
    sparse = [e for k, e in enumerate(es) if k % 9 == 0]                 # 近 30 天只剩 3～4 筆
    assert bt.s2_check(sparse, len(sparse) - 1, {"2025-03": 1.0}) == (False, "thin")
    assert bt.s2_check(es, i, {}) == (False, "vol"), "那個月沒有門檻（之前資料不夠）→ 不發訊號"
