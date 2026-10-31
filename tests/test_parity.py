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
