"""fetch_cwa.py（兩支）：金鑰走 HTTP 標頭、標頭被拒才改網址參數且錯誤訊息不露金鑰；颱風輸出已發生路徑／目前位置／70% 半徑。
不連網：用假的 requests.get。"""
import importlib.util
import os
from datetime import datetime, timedelta, timezone

import pytest
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS = {"farmer": os.path.join(ROOT, "scripts"), "merchant": os.path.join(ROOT, "merchant", "scripts")}
FAKE_KEY = "CWA-TEST-0000-FAKE"   # 假金鑰，只用來檢查有沒有被印出來  # guardrails: ignore


def load(app):
    spec = importlib.util.spec_from_file_location(f"fetch_cwa_{app}", os.path.join(APPS[app], "fetch_cwa.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class R:
    def __init__(self, status, payload=None):
        self.status_code, self._p = status, payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error for url: https://x/?Authorization={FAKE_KEY}",
                                     response=self)

    def json(self):
        return self._p


@pytest.fixture(params=["farmer", "merchant"])
def fc(request, monkeypatch):
    monkeypatch.setenv("CWA_API_KEY", FAKE_KEY)
    monkeypatch.syspath_prepend(APPS[request.param])   # cwa_get 呼叫時才 import http_retry
    return load(request.param)


def test_key_goes_in_header(fc, monkeypatch):
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append((dict(params or {}), dict(headers or {})))
        return R(200, {"records": {"ok": 1}})
    monkeypatch.setattr(requests, "get", get)
    assert fc.cwa_get("W-C0034-005") == {"ok": 1}
    assert len(calls) == 1
    params, headers = calls[0]
    assert headers.get("Authorization") == FAKE_KEY and "Authorization" not in params, "金鑰要放標頭、不放網址"


def test_header_rejected_falls_back_and_never_prints_key(fc, monkeypatch, capsys):
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append("url" if "Authorization" in (params or {}) else "header")
        return R(401) if "Authorization" in (headers or {}) else R(200, {"records": {"ok": 2}})
    monkeypatch.setattr(requests, "get", get)
    assert fc.cwa_get("A") == {"ok": 2}
    assert fc.cwa_get("B") == {"ok": 2}
    assert calls == ["header", "url", "url"], f"標頭被拒過一次，這次執行之後直接用網址參數：{calls}"
    out = capsys.readouterr()
    assert FAKE_KEY not in out.out + out.err


def test_other_errors_redacted(fc, monkeypatch, capsys):
    monkeypatch.setattr(requests, "get", lambda *a, **k: R(404))
    assert fc.cwa_get("X") is None
    out = capsys.readouterr()
    assert FAKE_KEY not in out.out + out.err and "抓取失敗" in out.err


def test_typhoon_past_now_r70(fc):
    tz = timezone(timedelta(hours=8))
    now = datetime(2026, 10, 31, 18, 0, tzinfo=tz)
    fix = lambda t, lat, lng, **k: {"DateTime": t, "CoordinateLatitude": str(lat), "CoordinateLongitude": str(lng),
                                    "MaxWindSpeed": "45", "MaxGustSpeed": "55", "Pressure": "940", **k}
    raw = {"TropicalCyclones": {"TropicalCyclone": [{
        "CwaTyphoonName": "測試",
        "AnalysisData": {"Fix": [fix("2026-10-20T08:00:00+08:00", 10, 160),          # 太久以前：不輸出
                                 fix("2026-10-30T14:00:00+08:00", 20.5, 143.5),
                                 fix("2026-10-31T14:00:00+08:00", 22.2, 142.9, MovingDirection="N", MovingSpeed="17",
                                     Circle15ms={"Radius": "220"}, Circle25ms={"Radius": "70"})]},
        "ForecastData": {"Fix": [{"InitialTime": "2026-10-31T14:00:00+08:00", "ForecastHour": "6",
                                  "CoordinateLatitude": "22.8", "CoordinateLongitude": "143.3", "MaxWindSpeed": "45",
                                  "MaxGustSpeed": "55", "Circle15ms": {"Radius": "220"}, "Circle25ms": {"Radius": "70"},
                                  "Radius70PercentProbability": "30"}]}}]}}
    t = fc.eval_typhoon(raw, now)
    assert [p["t"][:10] for p in t["past"]] == ["2026-10-30", "2026-10-31"], "只留最近 5 天"
    assert t["now"]["lat"] == 22.2 and t["now"]["r15"] == 220 and t["now"]["dir"] == "N"
    assert t["track"][0]["r70"] == 30 and t["track"][0]["wind"] == 45.0
