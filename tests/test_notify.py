"""notify.py：文案不再寫死雲林/舊名、LINE Flex 卡片結構、行情推播的觸發與去重（不連網）。"""
import json
import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import notify  # noqa: E402

ADV = {"typhoon": {"active": True, "name": "白海豚", "invade_prob": 0.42, "eta_text": "明晚"},
       "fields": [{"field_id": f"F{i}", "crop": "小白菜", "decision": "HARVEST_NOW", "risk_score": 80} for i in range(12)]}


def test_compose_no_hardcoded_region_or_old_name():
    ufs = notify.urgent_fields(ADV)
    text = notify.compose(ADV, ufs)
    assert "雲林" not in text and "二崙" not in text
    assert "精準務農" in text and notify.APP_URL + "#harvest" in text


def test_flex_card_structure():
    ufs = notify.urgent_fields(ADV)
    msg = notify.flex_harvest(ADV, ufs, notify.compose(ADV, ufs))
    json.dumps(msg, ensure_ascii=False)
    assert msg["type"] == "flex" and len(msg["altText"]) <= 400
    body = msg["contents"]["body"]["contents"]
    assert len(body) == 11 and "另有 2 田" in body[-1]["text"], "最多列 10 田，其餘合併一行"
    btn = msg["contents"]["footer"]["contents"][0]["action"]
    assert btn["type"] == "uri" and btn["uri"].startswith("https://") and btn["uri"].endswith("#harvest")


def write_market(tmp, summ):
    (tmp / "prices").mkdir(exist_ok=True)
    (tmp / "prices" / "14_recent.json").write_text(json.dumps({"sum": summ}, ensure_ascii=False), encoding="utf-8")
    (tmp / "prices_index.json").write_text(json.dumps({"markets": [{"name": "西螺", "recent": "prices/14_recent.json"}]},
                                                      ensure_ascii=False), encoding="utf-8")
    return str(tmp / "prices_index.json")


def sig(d, t, r, w=None):
    return {"d": d, "t": t, "r": r, "w": w, "l": {"avg": 20.0}}


def test_price_alerts_trigger_and_dedup(tmp_path):
    now = datetime(2026, 10, 31, 9, tzinfo=notify.TZ)
    idx = write_market(tmp_path, {"甘藍": sig("2026-10-30", "dn", 0.80, -9.0), "小白菜": sig("2026-10-30", "mid", 1.02),
                                  "菠菜": sig("2026-10-20", "up", 1.30)})
    msgs, st = notify.price_alerts(idx, "西螺", ["甘藍", "小白菜", "菠菜"], {}, now=now)
    assert len(msgs) == 1 and "甘藍" in msgs[0] and "低 20%" in msgs[0], "只有新鮮且偏離的才通知（菠菜是 10 天前的舊價）"
    msgs2, _ = notify.price_alerts(idx, "西螺", ["甘藍"], st, now=now + timedelta(days=2))
    assert msgs2 == [], "7 天內、沒有再跌 5 個百分點 → 不重送"
    write_market(tmp_path, {"甘藍": sig("2026-11-01", "dn", 0.74)})
    msgs3, _ = notify.price_alerts(idx, "西螺", ["甘藍"], st, now=now + timedelta(days=2))
    assert len(msgs3) == 1, "又再便宜 6 個百分點 → 重送"
    write_market(tmp_path, {"甘藍": sig("2026-11-01", "mid", 0.95)})
    _, st4 = notify.price_alerts(idx, "西螺", ["甘藍"], st, now=now + timedelta(days=2))
    assert "西螺|甘藍" not in st4, "回到正常 → 重置"


def test_price_mode_without_config_is_quiet(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("NOTIFY_PRICE_CROPS", raising=False)
    monkeypatch.chdir(tmp_path)
    sys.argv = ["notify.py", "--prices"]
    notify.main()
    assert "還沒設定關注品項" in capsys.readouterr().out
    assert not (tmp_path / "notify_price_state.json").exists()
