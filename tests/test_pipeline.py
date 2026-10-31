"""
資料管線離線測試（不連網）：精準務農 scripts/ 與精準進貨 merchant/scripts/ 兩份都跑一次。

涵蓋：
- http_retry：暫時性錯誤指數退避重試、Retry-After、4xx 不重試、放棄時遮蔽金鑰、兩份內容一致
- fetch_prices：分段失敗計數、行情檔切分（歷史檔同月不變、可無損合併）、檔號穩定
- 資料守門：單一市場失敗沿用舊資料、上次有資料這次 0 筆視為異常、全部失敗不寫檔、連續失敗熔斷

執行：pip install -r requirements-dev.txt && python -m pytest -q
"""
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta

import pytest
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS = {"farmer": os.path.join(ROOT, "scripts"), "merchant": os.path.join(ROOT, "merchant", "scripts")}


def load(app, name):
    d = APPS[app]
    if d not in sys.path:
        sys.path.insert(0, d)
    spec = importlib.util.spec_from_file_location(f"{app}_{name}", os.path.join(d, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ───────────────────────── http_retry ─────────────────────────
class FakeResp:
    def __init__(self, status=200, payload=None, headers=None, bad_json=False):
        self.status_code, self._p, self.headers, self._bad = status, payload, headers or {}, bad_json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error for url: https://x/api?Authorization=CWA-SECRET-1234")

    def json(self):
        if self._bad:
            raise ValueError("Expecting value")
        return self._p


def scripted(*steps):
    """依序回傳/拋出 steps 的假 getter，並記錄呼叫次數。"""
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        s = steps[min(len(calls), len(steps)) - 1]
        if isinstance(s, Exception):
            raise s
        return s
    get.calls = calls
    return get


@pytest.fixture(params=["farmer", "merchant"])
def hr(request):
    return load(request.param, "http_retry")


def test_http_retry_two_copies_identical():
    a = open(os.path.join(APPS["farmer"], "http_retry.py"), "rb").read()
    b = open(os.path.join(APPS["merchant"], "http_retry.py"), "rb").read()
    assert a == b, "scripts/http_retry.py 與 merchant/scripts/http_retry.py 必須保持一致"


def test_retry_then_success(hr):
    sleeps = []
    get = scripted(FakeResp(503), requests.Timeout("slow"), FakeResp(200, {"ok": 1}))
    assert hr.get_json("https://x/api", getter=get, sleep=sleeps.append) == {"ok": 1}
    assert len(get.calls) == 3
    assert 2 <= sleeps[0] <= 2.5 and 4 <= sleeps[1] <= 5   # 2s、4s ＋ 最多 25% 抖動


def test_retry_after_header_respected_and_capped(hr):
    sleeps = []
    get = scripted(FakeResp(429, headers={"Retry-After": "7"}), FakeResp(429, headers={"Retry-After": "999"}),
                   FakeResp(200, [1]))
    assert hr.get_json("https://x/api", getter=get, sleep=sleeps.append) == [1]
    assert sleeps == [7.0, 60.0]


def test_4xx_not_retried_and_redacted(hr):
    get = scripted(FakeResp(404))
    with pytest.raises(requests.HTTPError) as e:
        hr.get_json("https://x/api", getter=get, sleep=lambda s: None)
    assert len(get.calls) == 1
    assert "CWA-SECRET" not in str(e.value) and "Authorization=***" in str(e.value)


def test_gives_up_after_4_retries(hr):
    get = scripted(requests.ConnectionError("down https://x?Authorization=CWA-SECRET-1234"))
    with pytest.raises(RuntimeError) as e:
        hr.get_json("https://x/api", getter=get, sleep=lambda s: None)
    assert len(get.calls) == 5                      # 1 次＋重試 4 次
    assert "CWA-SECRET" not in str(e.value)


def test_non_json_200_is_retried(hr):
    get = scripted(FakeResp(200, bad_json=True), FakeResp(200, {"a": 1}))
    assert hr.get_json("https://x/api", getter=get, sleep=lambda s: None) == {"a": 1}


def test_redact(hr):
    s = "fail https://a/b?format=JSON&Authorization=CWA-AB12-CD34&api_key=zzz&token=t0k"
    out = hr.redact(s)
    assert "CWA-AB12" not in out and "zzz" not in out and "t0k" not in out and "format=JSON" in out


# ───────────────────────── fetch_prices ─────────────────────────
@pytest.fixture(params=["farmer", "merchant"])
def fp(request):
    return load(request.param, "fetch_prices")


def rec(fp, day, crop="甘藍-初秋", avg=20.0, qty=100):
    return {"種類代碼": "N04", "作物名稱": crop, "交易日期": fp.roc(day), "平均價": avg,
            "上價": avg * 1.5, "中價": avg, "下價": avg * 0.5, "交易量": qty}


def test_fetch_market_all_counts_failed_chunks(fp):
    now = datetime(2026, 10, 31, tzinfo=fp.TZ)
    start = now - timedelta(days=200)               # 3 段（每段 91 天）
    n = {"i": 0}

    def get(url, params=None, headers=None, timeout=None):
        n["i"] += 1
        if params["StartDate"] == fp.roc(start + timedelta(days=91)):   # 第 2 段永遠失敗
            raise requests.ConnectionError("boom")
        return FakeResp(200, [rec(fp, start)])
    recs, failed = fp.fetch_market_all("西螺", start, now, getter=get, sleep=lambda s: None)
    assert failed == 1 and len(recs) == 2
    assert n["i"] == 1 + 5 + 1                       # 第 2 段重試 4 次


def test_write_split_stable_within_month_and_lossless(fp, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("prices")
    data = {"甘藍": {"2026-09-29": {"avg": 1}, "2026-10-02": {"avg": 2}},
            "芒果": {"2026-08-01": {"avg": 3}}}
    variants = {"甘藍": {"初秋": {"2026-09-29": {"avg": 1}, "2026-10-02": {"avg": 2}}}}
    t1 = datetime(2026, 10, 3, 9, tzinfo=fp.TZ)
    t2 = datetime(2026, 10, 30, 15, tzinfo=fp.TZ)
    recent = fp.write_split("prices/01.json", "西螺", t1, data, variants)
    h1 = open("prices/01.json", "rb").read()
    fp.write_split("prices/01.json", "西螺", t2, data, variants)
    assert open("prices/01.json", "rb").read() == h1, "同月內歷史檔必須逐位元不變（否則 git 每天又整檔重寫）"
    hist, rc = json.load(open("prices/01.json")), json.load(open(recent))
    assert rc["since"] == "2026-10-01" and "updated" not in hist
    merged = {c: {**hist["data"].get(c, {}), **rc["data"].get(c, {})} for c in data}
    assert merged == data
    assert {**hist["variants"]["甘藍"]["初秋"], **rc["variants"]["甘藍"]["初秋"]} == variants["甘藍"]["初秋"]


def test_assign_file_keeps_numbers(fp):
    fm = {"西螺": "prices/14.json", "台北一": "prices/01.json"}
    used = set()
    assert fp.assign_file("西螺", fm, used) == "prices/14.json"
    used.add("prices/14.json")
    assert fp.assign_file("新市場", fm, used) == "prices/02.json"   # 取第一個沒被占用的號碼


def test_months_back_start_aligned(fp):
    now = datetime(2026, 10, 31, 9, tzinfo=fp.TZ)
    s = fp.months_back_start(now, 730)
    assert (s.day, s.hour, s.minute) == (1, 0, 0) and s <= now - timedelta(days=730)


# ───────────────────────── 資料守門（整合） ─────────────────────────
def run_main(fp, tmp_path, monkeypatch, markets, behavior):
    """behavior[市場] = "ok" | "fail" | "empty"；回傳 (exit_code, index or None, fetch 次數)。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fp, "MARKETS", list(markets))
    calls = []
    now = datetime.now(fp.TZ)

    def fake_fetch(market, start, end, getter=None, sleep=None):
        calls.append(market)
        b = behavior[market]
        if b == "fail":
            return [], 2
        if b == "empty":
            return [], 0
        return [rec(fp, now - timedelta(days=40)), rec(fp, now)], 0
    monkeypatch.setattr(fp, "fetch_market_all", fake_fetch)
    argv = ["fetch_prices.py", "--days", "60"] + (["--no-discover"] if hasattr(fp, "resolve_markets") else [])
    monkeypatch.setattr(sys, "argv", argv)
    code = 0
    try:
        fp.main()
    except SystemExit as e:
        code = e.code
    ix = json.load(open("prices_index.json")) if os.path.exists("prices_index.json") else None
    return code, ix, calls


def seed_old(tmp_path, names):
    os.makedirs(tmp_path / "prices", exist_ok=True)
    markets = []
    for i, nm in enumerate(names, 1):
        f, r = f"prices/{i:02d}.json", f"prices/{i:02d}_recent.json"
        (tmp_path / f).write_text('{"data":{"舊菜":{"2026-01-01":{"avg":9}}}}', encoding="utf-8")
        (tmp_path / r).write_text('{"data":{}}', encoding="utf-8")
        markets.append({"name": nm, "file": f, "recent": r})
    old = {"updated": "old", "markets": markets, "categories": {"舊菜": "蔬菜"},
           "latest": {nm: {"舊菜": {"avg": 9, "date": "2026-01-01"}} for nm in names}, "latest_var": {}}
    (tmp_path / "prices_index.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    return old


def test_failed_market_is_carried_forward(fp, tmp_path, monkeypatch):
    old = seed_old(tmp_path, ["台北一", "西螺"])
    before = (tmp_path / "prices/02.json").read_bytes()
    code, ix, _ = run_main(fp, tmp_path, monkeypatch, ["台北一", "西螺"], {"台北一": "ok", "西螺": "fail"})
    assert code in (0, None)
    names = [m["name"] for m in ix["markets"]]
    assert set(names) == {"台北一", "西螺"}, "抓失敗的市場不能從清單消失"
    assert ix["latest"]["西螺"] == old["latest"]["西螺"]
    assert (tmp_path / "prices/02.json").read_bytes() == before, "失敗市場的舊檔不能被覆蓋"
    assert "甘藍" in ix["latest"]["台北一"]


def test_empty_result_for_known_market_is_carried(fp, tmp_path, monkeypatch):
    seed_old(tmp_path, ["台北一", "西螺"])
    _, ix, _ = run_main(fp, tmp_path, monkeypatch, ["台北一", "西螺"], {"台北一": "ok", "西螺": "empty"})
    assert {m["name"] for m in ix["markets"]} == {"台北一", "西螺"}


def test_all_failed_writes_nothing(fp, tmp_path, monkeypatch):
    seed_old(tmp_path, ["台北一", "西螺"])
    before = (tmp_path / "prices_index.json").read_bytes()
    code, _, _ = run_main(fp, tmp_path, monkeypatch, ["台北一", "西螺"], {"台北一": "fail", "西螺": "fail"})
    assert code == 1
    assert (tmp_path / "prices_index.json").read_bytes() == before, "全部失敗時不可覆蓋索引（否則前端退回示範資料）"


def test_breaker_stops_after_3_consecutive_failures(fp, tmp_path, monkeypatch):
    names = ["A", "B", "C", "D", "E"]
    seed_old(tmp_path, names)
    code, _, calls = run_main(fp, tmp_path, monkeypatch, names, {n: "fail" for n in names})
    assert calls == ["A", "B", "C"] and code == 1
