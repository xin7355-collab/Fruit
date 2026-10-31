"""backtest.py 的 E（跨市場進貨）與 F（零售參考價）：確認沒有偷看未來、運費與休市的算法對（不連網、用假資料）。"""
import json
import os
import sys

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
