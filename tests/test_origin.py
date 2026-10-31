"""fetch_origin.py：解析、品名對應、合併保留舊資料、抓不到時不覆蓋（不連網）。"""
import json
import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import fetch_origin as fo  # noqa: E402


class R:
    def __init__(self, payload, status=200):
        self.status_code, self._p = status, payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


# 日期以「今天」為基準：程式只保留近 120 天，寫死日期的測試過幾個月會自己壞掉
TODAY = datetime.now(fo.TZ)
D1, D2 = (TODAY - timedelta(days=1)), (TODAY - timedelta(days=2))
ISO1, ISO2 = D1.strftime("%Y-%m-%d"), D2.strftime("%Y-%m-%d")


def row(d, name, org, price, roc=False):
    return {"YEAR": str(d.year - 1911 if roc else d.year), "MONTH": str(d.month), "PERIOD": str(d.day),
            "PRODUCTNAME": name, "ORGNAME": org, "AVGPRICE": str(price)}


ROWS = [row(D1, "番石榴(珍珠)", "當日平均價", 49.33), row(D1, "番石榴(珍珠)", "某鄉農會", 60),
        row(D2, "青香蕉(內銷)", "當日平均價", 27), row(D1, "蒜頭(蒜球)", "當日平均價", 90, roc=True),
        {"YEAR": "2026", "MONTH": "13", "PERIOD": "1", "PRODUCTNAME": "壞資料", "ORGNAME": "當日平均價", "AVGPRICE": "5"}]


def test_parse_and_alias():
    s = fo.parse(ROWS)
    assert s["番石榴(珍珠)"] == {ISO1: 49.33}, "只取「當日平均價」那一列"
    assert s["蒜頭(蒜球)"] == {ISO1: 90.0}, "民國年也能轉"
    assert "壞資料" not in s
    assert fo.crop_of("青香蕉(內銷)") == "香蕉" and fo.crop_of("蒜頭(蒜球)") == "大蒜" and fo.crop_of("木瓜") == "木瓜"


def test_main_merges_and_keeps_old(tmp_path):
    out = tmp_path / "origin.json"
    old_day = (TODAY - timedelta(days=10)).strftime("%Y-%m-%d")
    old = {"items": {"木瓜": [{"name": "木瓜", "series": {old_day: 55.0}}]}}
    out.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    assert fo.main(["--out", str(out)], getter=lambda *a, **k: R(ROWS), sleep=lambda s: None) == 0
    j = json.loads(out.read_text(encoding="utf-8"))
    assert j["items"]["番石榴"][0]["latest"] == [ISO1, 49.33]
    assert j["items"]["香蕉"][0]["name"] == "青香蕉(內銷)"
    assert j["items"]["木瓜"][0]["series"] == {old_day: 55.0}, "這次沒抓到的品項保留舊資料"


def test_no_data_keeps_old_file(tmp_path):
    out = tmp_path / "origin.json"
    out.write_text('{"items":{"木瓜":[]}}', encoding="utf-8")
    assert fo.main(["--out", str(out)], getter=lambda *a, **k: R([]), sleep=lambda s: None) == 1
    assert out.read_text(encoding="utf-8") == '{"items":{"木瓜":[]}}', "抓不到就不覆蓋"
