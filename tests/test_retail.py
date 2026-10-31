"""fetch_retail.py（精準進貨）：目錄解析、只補新月份、倍數逐菜計算、抓不到不覆蓋（不連網）。"""
import json
import os
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "merchant", "scripts"))
import fetch_retail as fr  # noqa: E402
import price_core as pc  # noqa: E402


class R:
    def __init__(self, payload=None, text=None):
        self.status_code, self._p = 200, payload
        self.content = (text or "").encode("utf-8-sig")

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def months_back(n):
    """近 n 個月（含本月）的 [(民國年, 月, 'YYYY-MM')]，舊到新；日期以今天為基準，測試不會過期。"""
    d, out = date.today().replace(day=1), []
    for _ in range(n):
        out.append((d.year - 1911, d.month, d.strftime("%Y-%m")))
        d = (d - timedelta(days=1)).replace(day=1)
    return out[::-1]


MONTHS = months_back(4)


def fake_getter(calls, price_of):
    def get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        if "dataset.view" in url:
            return R({"payload": {"resources": [{"name": f"{y}年{m}月份", "rid": f"r{ym}"} for y, m, ym in MONTHS]
                                  + [{"name": "說明文件", "rid": "x"}]}})
        ym = url.split("/resource/r")[1].split("/")[0]
        return R(text=f"項目,單位,平均零售價\n甘藍,元/台斤,{price_of(ym)}\n吳郭魚,元/台斤,90\n小白菜,元/台斤,-\n")
    return get


def seed_wholesale(tmp_path):
    os.makedirs(tmp_path / "prices", exist_ok=True)
    data = {"甘藍": {}}
    for _, _, ym in MONTHS:
        for day in range(1, 13):
            data["甘藍"][f"{ym}-{day:02d}"] = {"avg": 20.0, "qty": 1000}
    from datetime import datetime
    paths = pc.write_market("prices/01.json", "台北一", datetime.now(), data, {}, {})
    (tmp_path / "prices_index.json").write_text(json.dumps({"markets": [{"name": "台北一", **paths}]}, ensure_ascii=False),
                                                encoding="utf-8")


def test_ym_and_csv():
    assert fr.ym_of("114年12月份") == "2025-12" and fr.ym_of("說明") is None
    assert fr.parse_csv("項目,單位,平均零售價\n甘藍,元/台斤,68\n菠菜,元/台斤,-\n") == {"甘藍": 68.0}


def test_multiple_per_crop_and_incremental(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seed_wholesale(tmp_path)
    calls = []
    assert fr.main(["--out", "retail.json"], getter=fake_getter(calls, lambda ym: 60)) == 0
    j = json.load(open("retail.json", encoding="utf-8"))
    it = j["items"]["甘藍"][0]
    assert it["mult"] == 5.0, f"零售 60 元/台斤 ÷（批發 20 元/公斤×0.6）＝5 倍：{it}"
    assert it["latest"] == [MONTHS[-1][2], 60.0] and "吳郭魚" not in json.dumps(j["items"], ensure_ascii=False)
    calls2 = []
    fr.main(["--out", "retail.json"], getter=fake_getter(calls2, lambda ym: 66))
    downloads = [u for u in calls2 if "/resource/" in u]
    assert len(downloads) == 2, f"舊月份不重抓，只重抓最近兩個月：{downloads}"


def test_failure_keeps_old(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "retail.json").write_text('{"months":{}}', encoding="utf-8")

    def boom(*a, **k):
        raise fr.get_json.__globals__["requests"].ConnectionError("down")
    assert fr.main(["--out", "retail.json"], getter=boom, sleep=lambda s: None) == 1
    assert (tmp_path / "retail.json").read_text(encoding="utf-8") == '{"months":{}}'


def test_logs_catalog_and_warns_on_unknown_names(tmp_path, monkeypatch, capsys):
    """目錄有資料但月份名稱看不懂時要亮黃燈（分得出「資料集只到某月」和「格式改了」），舊資料照留。"""
    monkeypatch.chdir(tmp_path)
    seed_wholesale(tmp_path)
    assert fr.main(["--out", "retail.json"], getter=fake_getter([], lambda ym: 60)) == 0
    out = capsys.readouterr().out
    assert f"目錄 {len(MONTHS)} 個月份（{MONTHS[0][2]}～{MONTHS[-1][2]}），這次下載 {len(MONTHS)} 個" in out

    def renamed(url, params=None, headers=None, timeout=None):
        return R({"payload": {"resources": [{"name": "Retail price 2026-01", "rid": "z"}]}})
    assert fr.main(["--out", "retail.json"], getter=renamed) == 0
    cap = capsys.readouterr()
    assert "::warning::" in cap.err and "名稱格式可能改了" in cap.err
    assert len(json.load(open("retail.json", encoding="utf-8"))["months"]) == len(MONTHS)   # 舊月份沒被清掉


def test_variety_pick_is_deterministic():
    """名稱含關鍵字的品種有好幾個時：完全相同優先、其次交易天數最多（不能取 dict 第一個，順序每次執行可能不同）。"""
    base = {f"2025-01-{d:02d}": {"avg": 10.0} for d in range(1, 13)}
    big = {f"2025-01-{d:02d}": {"avg": 30.0} for d in range(1, 21)}       # 20 天
    small = {f"2025-01-{d:02d}": {"avg": 50.0} for d in range(1, 13)}     # 12 天
    for v in ({"油麥菜水耕": small, "油麥菜": big}, {"油麥菜": big, "油麥菜水耕": small}):
        assert fr.wholesale_monthly(base, "油麥", v) == {"2025-01": 30.0}
    assert fr.wholesale_monthly(base, "油麥菜水耕", {"油麥菜": big, "油麥菜水耕": small}) == {"2025-01": 50.0}
    assert fr.wholesale_monthly(base, "芥藍", {"油麥菜": big}) == {"2025-01": 10.0}   # 找不到就用母作物
