"""JS（core.js，App 實際在跑的）與 Python（price_core.py，回測用的）採收預判必須算出一樣的結果，
否則回測數字不代表 App 的表現。用 repo 裡的真實行情（西螺）抽樣比對。需要 node（CI 的 ubuntu 有內建）。"""
import json
import os
import random
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import price_core as pc  # noqa: E402


@pytest.mark.skipif(shutil.which("node") is None, reason="沒有 node")
def test_predict_price_js_python_identical(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    ix = json.load(open("prices_index.json", encoding="utf-8"))
    ent = next(m for m in ix["markets"] if m["name"] == "西螺")
    data, _ = pc.read_market(ent)
    rnd = random.Random(20261031)
    crops = sorted(c for c, s in data.items() if len(s) >= 30)
    cases = []
    for c in rnd.sample(crops, min(12, len(crops))):
        for _ in range(5):
            cases.append({"crop": c, "harvest": f"2026-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"})
    inp = tmp_path / "cases.json"
    inp.write_text(json.dumps({"data": {c["crop"]: data[c["crop"]] for c in cases}, "cases": cases}, ensure_ascii=False),
                   encoding="utf-8")
    js = ("const C=require(process.argv[1]);const j=require(process.argv[2]);"
          "console.log(JSON.stringify(j.cases.map(k=>C.predictPrice(j.data[k.crop],k.harvest))));")
    out = subprocess.run(["node", "-e", js, os.path.join(ROOT, "core.js"), str(inp)],
                         capture_output=True, text=True, check=True).stdout
    for case, got in zip(cases, json.loads(out)):
        want = pc.predict_price(data[case["crop"]], case["harvest"])
        assert (got is None) == (want is None), case
        if got:
            for k in ("med", "lo", "hi", "n", "same"):
                assert got[k] == pytest.approx(want[k]), (case, k, got, want)


@pytest.mark.skipif(shutil.which("node") is None, reason="沒有 node")
def test_year_stats_js_python_identical(tmp_path, monkeypatch):
    """詳細頁的「一年百分位／到貨量比」（core.js yearStats）跟回測 S1 用的 price_core.year_stats 必須一樣，
    否則回測測的不是 App 顯示的數字。真實行情（西螺，母作物＋品種）在隨機日期截斷後比對。"""
    monkeypatch.chdir(ROOT)
    ix = json.load(open("prices_index.json", encoding="utf-8"))
    ent = next(m for m in ix["markets"] if m["name"] == "西螺")
    data, var = pc.read_market(ent)
    pool = {c: s for c, s in data.items() if len(s) >= 100}
    pool.update({f"{c}·{lab}": s for c, vv in (var or {}).items() for lab, s in vv.items() if len(s) >= 100})
    rnd = random.Random(20261102)
    cases = []
    for name in rnd.sample(sorted(pool), min(20, len(pool))):
        days = sorted(pool[name])
        for _ in range(4):
            cases.append({"name": name, "cut": days[rnd.randint(len(days) // 3, len(days) - 1)]})
    cases.append({"name": cases[0]["name"], "cut": sorted(pool[cases[0]["name"]])[10]})   # 資料太少 → 兩邊都回 null
    series = {k["name"] + "|" + k["cut"]: {d: v for d, v in pool[k["name"]].items() if d <= k["cut"]} for k in cases}
    inp = tmp_path / "ys.json"
    inp.write_text(json.dumps({"series": series, "keys": list(series)}, ensure_ascii=False), encoding="utf-8")
    js = ("const C=require(process.argv[1]);const j=require(process.argv[2]);"
          "console.log(JSON.stringify(j.keys.map(k=>C.yearStats(j.series[k]))));")
    out = subprocess.run(["node", "-e", js, os.path.join(ROOT, "core.js"), str(inp)],
                         capture_output=True, text=True, check=True).stdout
    got_all = json.loads(out)
    assert any(g is None for g in got_all) and sum(g is not None for g in got_all) >= 40, "要同時測到「不夠算」與「算得出來」"
    for key, got in zip(series, got_all):
        want = pc.year_stats(pc._valid(series[key]))
        assert (got is None) == (want is None), key
        if got:
            assert (got["last"], got["n"], got["nRecent"]) == (want["last"], want["n"], want["n_recent"]), key
            assert got["pct"] == want["pct"], (key, got, want)            # 同一個整數比例
            assert (got["sup"] is None) == (want["sup"] is None), key
            if got["sup"] is not None:
                assert got["sup"] == pytest.approx(want["sup"], rel=1e-12), (key, got, want)
