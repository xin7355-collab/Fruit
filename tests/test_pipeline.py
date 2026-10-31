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
from datetime import date, datetime, timedelta

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
    s = "fail https://a/b?format=JSON&Authorization=CWA-AB12-CD34&api_key=zzz&token=t0k"   # 假金鑰，測遮蔽用 guardrails: ignore
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


# ───────────────────────── API 9999 筆截斷 ─────────────────────────
def fake_capped_api(fp, per_day, newest_first=True, cap=9999):
    """模擬農業部 API：區間內每天 per_day 筆，超過 cap 只回 cap 筆（截掉一端）。"""
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        s = datetime.strptime(fp.iso_from_roc(params["StartDate"]), "%Y-%m-%d")
        e = datetime.strptime(fp.iso_from_roc(params["EndDate"]), "%Y-%m-%d")
        calls.append((s, e))
        rows, d = [], s
        while d <= e:
            rows += [{"交易日期": fp.roc(d), "作物名稱": f"菜{i}", "種類代碼": "N04", "平均價": 10, "交易量": 1}
                     for i in range(per_day)]
            d += timedelta(days=1)
        if newest_first:
            rows.reverse()
        return FakeResp(200, rows[:cap])
    get.calls = calls
    return get


@pytest.mark.parametrize("newest_first", [True, False])
def test_capped_chunks_are_refetched_completely(fp, newest_first):
    start = datetime(2024, 10, 1, tzinfo=fp.TZ)
    end = datetime(2026, 9, 30, tzinfo=fp.TZ)
    get = fake_capped_api(fp, per_day=400, newest_first=newest_first)   # 91 天 ≈ 3.6 萬筆 → 一定被截斷
    recs, failed = fp.fetch_market_all("台北一", start, end, getter=get, sleep=lambda s: None)
    assert failed == 0
    from collections import Counter
    per_day = Counter(r["交易日期"] for r in recs)
    total_days = (end - start).days + 1
    assert len(per_day) == total_days, "每一天都要抓到（不能只剩每段最後幾週）"
    assert set(per_day.values()) == {400}, "每天的筆數要完整、不能重複或殘缺"
    assert len(get.calls) < 60, f"請求數要合理（實際 {len(get.calls)} 次）"


def test_single_day_over_cap_marked_incomplete(fp):
    day = datetime(2026, 10, 1, tzinfo=fp.TZ)
    get = fake_capped_api(fp, per_day=12000)
    recs, failed = fp.fetch_market_all("台北一", day, day, getter=get, sleep=lambda s: None)
    assert failed == 1


# ───────────────────────── price_core：彙整、檔案、判斷 ─────────────────────────
@pytest.fixture(params=["farmer", "merchant"])
def pc(request):
    return load(request.param, "price_core")


def test_price_core_two_copies_identical():
    a = open(os.path.join(APPS["farmer"], "price_core.py"), "rb").read()
    b = open(os.path.join(APPS["merchant"], "price_core.py"), "rb").read()
    assert a == b, "scripts/price_core.py 與 merchant/scripts/price_core.py 必須保持一致"


def agg(fp, pc, recs, dom_prev=()):
    return pc.aggregate(recs, g=fp.g, norm_crop=fp.norm_crop, cat_by_code=fp.CAT_BY_CODE,
                        skip_base=fp.SKIP_BASE, iso_from_roc=fp.iso_from_roc, dom_prev=dom_prev)


def r2(fp, d, name, avg, qty, hi=None, lo=None, code="N04"):
    return {"種類代碼": code, "作物名稱": name, "交易日期": fp.roc(d), "平均價": avg,
            "上價": hi or avg, "中價": avg, "下價": lo or avg, "交易量": qty}


def test_import_not_merged_into_domestic_parent(fp, pc):
    d = datetime(2026, 10, 30)
    mdata, variants, _, _, dom = agg(fp, pc, [
        r2(fp, d, "草莓-豐香", 60, 1000), r2(fp, d, "草莓-進口", 190, 50),          # 國產＋進口
        r2(fp, d + timedelta(days=1), "草莓-進口", 195, 40),                          # 只有進口的那天
        r2(fp, d, "奇異果-進口", 80, 500, code="N05")])                               # 純進口作物
    assert mdata["草莓"]["2026-10-30"]["avg"] == 60, "母作物只算國產"
    assert "2026-10-31" not in mdata["草莓"], "國產作物不可用進口價補空"
    assert set(variants["草莓"]) == {"豐香", "進口"}, "進口留在品種裡"
    assert mdata["奇異果"]["2026-10-30"]["avg"] == 80, "純進口作物照樣顯示"
    assert "草莓" in dom and "奇異果" not in dom


def test_domestic_history_keeps_import_out_in_incremental_window(fp, pc):
    d = datetime(2026, 10, 30)   # 增量窗內只有進口，但歷史上有國產 → 不可把進口價當母作物
    mdata, _, _, _, _ = agg(fp, pc, [r2(fp, d, "草莓-進口", 190, 50)], dom_prev=["草莓"])
    assert "草莓" not in mdata


def test_high_low_are_volume_weighted(fp, pc):
    d = datetime(2026, 10, 30)
    mdata, _, _, _, _ = agg(fp, pc, [r2(fp, d, "甘藍-改良種", 28, 100000, hi=34, lo=20),
                                     r2(fp, d, "甘藍-甘藍心", 120, 43, hi=120, lo=120)])
    day = mdata["甘藍"]["2026-10-30"]
    assert day["high"] < 40, f"冷門小品種不可把上價撐到 120（{day}）"
    assert day["low"] <= day["mid"] <= day["high"]


def test_closed_days_collected(fp, pc):
    d = datetime(2026, 10, 26)
    _, _, _, closed, _ = agg(fp, pc, [{"作物名稱": "休市", "交易日期": fp.roc(d), "平均價": 0, "交易量": 0},
                                      r2(fp, d + timedelta(days=1), "甘藍-改良種", 28, 100)])
    assert closed == {"2026-10-26"}


def test_merge_series_overwrites_window_and_trims(pc):
    old = {"甘藍": {"2024-09-01": {"avg": 1}, "2026-10-20": {"avg": 2}, "2026-10-29": {"avg": 3}}}
    new = {"甘藍": {"2026-10-29": {"avg": 30}, "2026-10-30": {"avg": 31}}, "蘿蔔": {"2026-10-30": {"avg": 9}}}
    m = pc.merge_series(old, new, keep_from="2024-10-01")
    assert m["甘藍"] == {"2026-10-20": {"avg": 2}, "2026-10-29": {"avg": 30}, "2026-10-30": {"avg": 31}}
    assert m["蘿蔔"] == {"2026-10-30": {"avg": 9}}


def test_write_market_three_files_stable_and_lossless(fp, pc, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("prices")
    data = {"甘藍": {"2026-09-29": {"avg": 1, "qty": 1}, "2026-10-02": {"avg": 2, "qty": 1}}}
    variants = {"甘藍": {"初秋": {"2026-09-29": {"avg": 1}, "2026-10-02": {"avg": 2}}}}
    t1, t2 = datetime(2026, 10, 3, 9, tzinfo=fp.TZ), datetime(2026, 10, 30, 15, tzinfo=fp.TZ)
    p = pc.write_market("prices/01.json", "西螺", t1, data, variants, pc.summarize(data, variants))
    h1, v1 = open(p["file"], "rb").read(), open(p["vfile"], "rb").read()
    pc.write_market("prices/01.json", "西螺", t2, data, variants, pc.summarize(data, variants))
    assert open(p["file"], "rb").read() == h1 and open(p["vfile"], "rb").read() == v1, "同月內歷史檔必須逐位元不變"
    hist = json.load(open(p["file"]))
    assert "variants" not in hist and "updated" not in hist, "歷史檔不放品種（品種另檔、點了才載）"
    d2, v2 = pc.read_market({"name": "西螺", **p})
    assert d2 == data and v2 == variants
    assert "sum" in json.load(open(p["recent"]))


def series_from(pairs):
    return {d: {"avg": a, "qty": q} for d, a, q in pairs}


def test_signal_tag_old_rule_with_season_ratio(pc):
    # 每年 10 月便宜（20 元）、其他月份 40 元；今年 10 月 21 元 → 對往年同期是「正常」，舊法（近 90 筆）卻會說「偏便宜」
    pairs, d = [], date(2024, 10, 1)
    while d <= date(2026, 10, 30):
        a = 21 if d.year == 2026 and d.month == 10 else (20 if d.month in (10, 11) else 40)
        pairs.append((d.isoformat(), a, 1000))
        d += timedelta(days=1)
    s = pc.signal(series_from(pairs))
    es = sorted(series_from(pairs).items())
    assert s["t"] == pc.old_signal(es)[0] == "dn", "看板標籤沿用舊法（回測後未改）"
    assert abs(s["sr"] - 1.05) < 0.01, f"比往年同期＝21/20（補充資訊）：{s}"
    assert pc.season_signal(es) == ("mid", "s"), "往年同期判斷法（回測對照用）說「正常」"


def test_signal_falls_back_when_no_history(pc):
    pairs = [((date(2026, 8, 1) + timedelta(days=i)).isoformat(), 30 if i < 80 else 45, 1000) for i in range(90)]
    s = pc.signal(series_from(pairs))
    assert s["t"] == "up" and s["sr"] is None
    assert pc.season_signal(sorted(series_from(pairs).items())) == ("up", "r")


def test_week_change_and_thin(pc):
    s = pc.signal(series_from([("2026-10-01", 10, 100), ("2026-10-20", 12, 100), ("2026-10-30", 15, 100)]))
    assert s["w"] == 25.0 and s["q"] == 1        # 10/20 落在 7～11 天前；量 100 公斤 < 300 → 量少
    s2 = pc.signal(series_from([("2026-09-01", 10, 5000), ("2026-10-30", 15, 5000)]))
    assert s2["w"] is None and "q" not in s2     # 7～11 天前沒成交 → 不硬比


def test_predict_price_respects_as_of(pc):
    s = series_from([((date(2025, 1, 1) + timedelta(days=i)).isoformat(), 10 + (i > 400) * 90, 1) for i in range(640)])
    # 2026-02-06 起價格跳到 100；as_of=2/1 時只准看到 10 元的資料
    p = pc.predict_price(s, "2026-03-01", as_of="2026-02-01")
    assert p["med"] == 10 and p["hi"] == 10, f"偷看到未來資料：{p}"
    leak = pc.predict_price(s, "2026-03-01")
    assert leak["hi"] == 100, "對照：不設 as_of 就會用到 2 月後的 100 元"


# ───────────────────────── 增量抓取（整合） ─────────────────────────
class FixedNow:
    def __init__(self, fp, iso):
        self.real, self.now_v = fp.datetime, datetime.fromisoformat(iso).replace(tzinfo=fp.TZ)

    def install(self, monkeypatch, fp):
        real, nv = self.real, self.now_v

        class DT(real):
            @classmethod
            def now(cls, tz=None):
                return nv
        monkeypatch.setattr(fp, "datetime", DT)


def test_incremental_fetch_merges_window(fp, pc, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("prices")
    FixedNow(fp, "2026-10-30T09:00:00").install(monkeypatch, fp)
    old = {"甘藍": {"2025-06-01": {"avg": 11.0, "high": 11.0, "mid": 11.0, "low": 11.0, "qty": 10}}}
    paths = pc.write_market("prices/01.json", "台北一", datetime(2026, 10, 1, tzinfo=fp.TZ), old, {}, {})
    json.dump({"fmt": fp.FMT, "markets": [{"name": "台北一", **paths, "dom": ["甘藍"], "closed": []}],
               "categories": {"甘藍": "蔬菜"}, "latest": {}, "latest_var": {}},
              open("prices_index.json", "w"), ensure_ascii=False)
    monkeypatch.setattr(fp, "MARKETS", ["台北一"])
    seen = []

    def fake_fetch(market, start, end, getter=None, sleep=None):
        seen.append((start.date().isoformat(), end.date().isoformat()))
        return [r2(fp, datetime(2026, 10, 29), "甘藍-改良種", 25, 1000)], 0
    monkeypatch.setattr(fp, "fetch_market_all", fake_fetch)
    monkeypatch.setattr(sys, "argv", ["fetch_prices.py", "--days", "730"] +
                        (["--no-discover"] if hasattr(fp, "resolve_markets") else []))
    fp.main()
    assert seen == [("2026-10-17", "2026-10-30")], f"平常只抓近 14 天：{seen}"
    ix = json.load(open("prices_index.json"))
    d, _ = pc.read_market(ix["markets"][0])
    assert set(d["甘藍"]) == {"2025-06-01", "2026-10-29"}, "新抓的日子要併進舊資料、舊的保留"
    assert ix["fmt"] == fp.FMT and ix["markets"][0]["vfile"].endswith("_v.json")
