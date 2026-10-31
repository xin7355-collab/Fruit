#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_cwa.py  ——  中央氣象署 (CWA) 開放資料抓取 → 快取 JSON（v1 skeleton）

從 CWA 開放資料平臺抓取颱風警報／路徑潛勢／侵襲機率／雨量，整理成前端需要的
typhoon_status.json、weather_events.json、weather_forecast.json。

⚠ 金鑰只放後端環境變數 CWA_API_KEY，前端永遠不觸碰。
⚠ 部分資料集的實際 JSON 欄位路徑需以真實回應驗證（見各 parse_* 的 TODO）；
   本檔為可運行骨架：抓不到 / 無金鑰 / 非颱風期間 → 寫出 active=false 的安全狀態，不中斷管線。

環境變數：
  CWA_API_KEY   CWA 授權碼（https://opendata.cwa.gov.tw 註冊免費取得）

用法：
  CWA_API_KEY=CWA-xxxx python3 scripts/fetch_cwa.py
  python3 scripts/fetch_cwa.py            # 無金鑰 → 寫出 active=false 狀態
"""

import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    requests = None

TZ = timezone(timedelta(hours=8))
BASE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
COUNTY = "雲林縣"
# 二崙／西螺參考點（田區包圍網中心）
REF_LAT, REF_LNG = 23.79, 120.44

DATASETS = {
    "typhoon_warning": "W-C0034-001",   # 颱風消息與警報
    "invasion_prob":   "W-C0034-003",   # 侵襲機率 / 72hr 暴風圈侵襲機率
    "path_potential":  "W-C0034-005",   # 颱風路徑潛勢預報
    "rainfall":        "O-A0002-001",   # 自動雨量站
    "temp_obs":        "O-A0001-001",   # 自動氣象站（含氣溫，供低溫/寒流事件）
    "county_fcst":     "F-C0032-001",   # 全國各縣市今明 36 小時天氣預報（全國、輕量、穩定）
}

# 全國各鄉鎮逐 3 小時預報（供「未來 24 小時」逐時）：資料集代碼常異動，逐一嘗試、取第一個可用。
# F-D0047-089＝臺灣各鄉鎮未來 2 天(逐 3 小時)；F-D0047-091＝未來 1 週。（-093 已不存在，會 404）
HOURLY_DATASETS = ["F-D0047-089", "F-D0047-091"]

# 天氣事件累積檔（K線標記用）；僅逐日「向前累積」真實觀測，不臆造歷史。
EVENTS_FILE = "weather_events.json"
EVENTS_KEEP_DAYS = 760          # 與行情約 2 年窗口相符
RAIN_HEAVY_MM = 80              # CWA 大雨特報：24hr 累積雨量達 80mm
RAIN_TORRENT_MM = 200          # CWA 豪雨：24hr 累積達 200mm
COLD_TEMP_C = 10               # 平地氣溫 ≤10°C 視為低溫/寒流量級


def cwa_get(dataid, timeout=30, **params):
    """呼叫 CWA REST API，回傳 records dict（失敗回 None）。timeout 可調（大資料集如全國逐時預報放寬）。"""
    key = os.environ.get("CWA_API_KEY")
    if not key or requests is None:
        return None
    url = f"{BASE}/{dataid}"
    params = {"Authorization": key, "format": "JSON", **params}
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        j = r.json()
        # CWA 慣例：{ success, records: {...} }
        return j.get("records")
    except Exception as e:
        print(f"[fetch_cwa] {dataid} 抓取失敗：{e}", file=sys.stderr)
        return None


def haversine_km(lat1, lng1, lat2, lng2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ─────────────── 颱風解析（W-C0034-005 路徑潛勢；已用巴威真實回應校準） ───────────────
def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _cyclone_list(path_records):
    """取出所有活動中熱帶氣旋（records.TropicalCyclones.TropicalCyclone[]）。"""
    if not isinstance(path_records, dict):
        return []
    cont = path_records.get("TropicalCyclones") or path_records.get("tropicalCyclones") or {}
    tcs = (cont.get("TropicalCyclone") or cont.get("tropicalCyclone")) if isinstance(cont, dict) else None
    if isinstance(tcs, dict):
        tcs = [tcs]
    return tcs or []


def _forecast_track(tc):
    """未來逐時預報點：InitialTime+ForecastHour 為時間，Circle15ms.Radius 為 7 級暴風圈半徑。"""
    fd = tc.get("ForecastData") or tc.get("forecastData") or {}
    fixes = (fd.get("Fix") or fd.get("fix")) if isinstance(fd, dict) else None
    if isinstance(fixes, dict):
        fixes = [fixes]
    out = []
    for fx in fixes or []:
        lat = _num(fx.get("CoordinateLatitude"))
        lng = _num(fx.get("CoordinateLongitude"))
        if lat is None or lng is None:
            continue
        dt = _parse_cwa_time(str(fx.get("InitialTime") or ""))
        fh = _num(fx.get("ForecastHour"))
        t = dt + timedelta(hours=fh) if (dt and fh is not None) else dt
        c15 = fx.get("Circle15ms") or {}
        c25 = fx.get("Circle25ms") or {}
        out.append({"t": t, "lat": lat, "lng": lng,
                    "r15": _num(c15.get("Radius")) or 0.0 if isinstance(c15, dict) else 0.0,
                    "r25": _num(c25.get("Radius")) or 0.0 if isinstance(c25, dict) else 0.0,
                    "gust": _num(fx.get("MaxGustSpeed"))})
    return out


def eval_typhoon(path_records, now, ref_lat=REF_LAT, ref_lng=REF_LNG):
    """用逐時預報位置 + 暴風圈半徑，算對雲林的侵襲機率/到達時間。回傳最具威脅的颱風 dict 或 None。"""
    best = None
    for tc in _cyclone_list(path_records):
        name = tc.get("CwaTyphoonName") or tc.get("TyphoonName") or "颱風"
        track = _forecast_track(tc)
        if not track:
            continue
        min_dist = 1e9
        eta_iso = eta_text = gust = None
        prob = 0.0
        for p in track:
            d = haversine_km(ref_lat, ref_lng, p["lat"], p["lng"])
            min_dist = min(min_dist, d)
            r15 = p["r15"]
            if r15 and d <= r15 and eta_iso is None and p["t"] and p["t"] > now:
                eta_iso = p["t"].isoformat()
                eta_text = f"約 {round((p['t'] - now).total_seconds() / 3600)} 小時後"
                gust = p["gust"]
            if p["r25"] and d <= p["r25"]:
                pr = 0.9
            elif r15 and d <= r15:
                pr = 0.65
            elif r15 and d <= r15 + 100:
                pr = 0.35
            elif r15 and d <= r15 + 300:
                pr = 0.12
            else:
                pr = 0.0
            prob = max(prob, pr)
        # 暴風圈未覆蓋雲林時，僅以距離給極小「接近度」（不足以觸發搶收，僅供顯示）
        if prob == 0.0 and min_dist < 400:
            prob = round(0.12 * (1 - min_dist / 400), 2)
        # 精簡逐時軌跡（供前端以「田區座標」自算各地侵襲機率）：時間 ISO + 位置 + 暴風圈半徑
        track_out = [{"t": p["t"].isoformat() if p["t"] else None,
                      "lat": round(p["lat"], 3), "lng": round(p["lng"], 3),
                      "r15": round(p["r15"] or 0), "r25": round(p["r25"] or 0),
                      "gust": p["gust"]} for p in track]
        cand = {"name": name, "min_dist": round(min_dist), "invade_prob": round(prob, 2),
                "eta_iso": eta_iso, "eta_text": eta_text, "gust": gust, "track": track_out}
        if best is None or cand["invade_prob"] > best["invade_prob"] or \
                (cand["invade_prob"] == best["invade_prob"] and cand["min_dist"] < best["min_dist"]):
            best = cand
    return best


def parse_warning(warn_records, now, county=COUNTY):
    """W-C0034-001：是否有生效中、涵蓋雲林的颱風警報（排除『解除』與過期）。"""
    if not isinstance(warn_records, dict):
        return {"land_warning": False, "text": None}
    infos = warn_records.get("info")
    if isinstance(infos, dict):
        infos = [infos]
    for info in infos or []:
        hl = str(info.get("headline") or "")
        if "解除" in hl:
            continue
        exp = _parse_cwa_time(str(info.get("expires") or ""))
        if exp and exp < now:
            continue
        areas = info.get("area")
        if isinstance(areas, list):
            covers = any(county in str(a.get("areaDesc", "")) or "雲林" in str(a) for a in areas)
        elif isinstance(areas, dict):
            covers = county in str(areas.get("areaDesc", ""))
        else:
            covers = True
        if covers:
            return {"land_warning": True, "text": hl or "颱風警報"}
    return {"land_warning": False, "text": None}


def _loc_of(o):
    """站點所屬縣市名。CWA v2 觀測 API（O-A000x）把縣市放在 GeoInfo.CountyName，
    舊格式則放在頂層 CountyName——兩處都查，避免比對不到而整批漏讀。"""
    gi = o.get("GeoInfo")
    return (o.get("CountyName") or o.get("countyName")
            or (gi.get("CountyName") or gi.get("countyName") if isinstance(gi, dict) else None)
            or "")


def parse_rain_24h(records, county=COUNTY):
    """回傳雲林縣各雨量站中最大 24hr 累積雨量 mm（找不到回 None）。"""
    if not records:
        return None
    best = None

    def walk(o):
        nonlocal best
        if isinstance(o, dict):
            loc = _loc_of(o)
            if county in str(loc) or "雲林" in str(loc):
                # TODO 驗證：24hr 累積常在 RainfallElement.Past24hr.Precipitation
                r = _dig(o, ["RainfallElement", "Past24hr", "Precipitation"])
                if r is None:
                    r = o.get("now") or o.get("past24hr")
                try:
                    v = float(r)
                    if best is None or v > best:
                        best = v
                except (TypeError, ValueError):
                    pass
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(records)
    return best


def parse_rain_hours(records, path, county=COUNTY):
    """回傳雲林各雨量站中某時段(由 path 指定，如 Past1hr)最大累積雨量 mm。"""
    if not records:
        return None
    best = None

    def walk(o):
        nonlocal best
        if isinstance(o, dict):
            loc = _loc_of(o)
            if county in str(loc) or "雲林" in str(loc):
                r = _dig(o, path)
                try:
                    v = float(r)
                    if best is None or v > best:
                        best = v
                except (TypeError, ValueError):
                    pass
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(records)
    return best


def parse_temp_min(records, county=COUNTY):
    """回傳雲林各自動氣象站當前氣溫的最小值（°C），供低溫/寒流事件判定。找不到回 None。
    ⚠ 欄位路徑（WeatherElement.AirTemperature）需以真實 O-A0001-001 回應校準；-99 等無效值排除。"""
    if not records:
        return None
    best = None

    def walk(o):
        nonlocal best
        if isinstance(o, dict):
            loc = _loc_of(o)
            if county in str(loc) or "雲林" in str(loc):
                t = _dig(o, ["WeatherElement", "AirTemperature"])
                if t is None:
                    t = o.get("TEMP") or o.get("AirTemperature")
                try:
                    v = float(t)
                    if v > -90 and (best is None or v < best):
                        best = v
                except (TypeError, ValueError):
                    pass
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(records)
    return best


def accumulate_events(status, temp_min, now):
    """把今天的真實天氣事件（颱風/大雨/低溫）併入 weather_events.json（逐日向前累積，去重、保留 2 年窗口）。
    回傳 (全部事件 list, 今日新增 list)。不臆造歷史——只記錄當下觀測到的事實。"""
    today = now.strftime("%Y-%m-%d")
    old = []
    try:
        with open(EVENTS_FILE, encoding="utf-8") as f:
            j = json.load(f)
            old = j if isinstance(j, list) else (j.get("events") or [])
    except Exception:
        old = []

    ev = {}   # key = "date|type" -> {date,type,label}
    for e in old:
        if isinstance(e, dict) and e.get("date") and e.get("type"):
            ev[f"{e['date']}|{e['type']}"] = {"date": e["date"], "type": e["type"], "label": e.get("label", "")}

    todays = []
    # ① 颱風：對雲林進入搶收模式或有陸上警報
    if status.get("active") or status.get("land_warning"):
        lbl = status.get("name") or "颱風"
        if status.get("land_warning") and status.get("warning"):
            lbl = f"{lbl}・{status['warning']}"
        todays.append({"date": today, "type": "typhoon", "label": lbl})
    # ② 大雨/豪雨：雲林 24hr 累積雨量達門檻
    r24 = status.get("rain_24h_mm")
    if isinstance(r24, (int, float)) and r24 >= RAIN_HEAVY_MM:
        kind = "豪雨" if r24 >= RAIN_TORRENT_MM else "大雨"
        todays.append({"date": today, "type": "rain", "label": f"{kind} {round(r24)}mm"})
    # ③ 低溫/寒流：雲林平地氣溫達門檻
    if isinstance(temp_min, (int, float)) and temp_min <= COLD_TEMP_C:
        todays.append({"date": today, "type": "cold", "label": f"低溫 {round(temp_min)}℃"})

    for e in todays:
        ev[f"{e['date']}|{e['type']}"] = e

    cutoff = (now - timedelta(days=EVENTS_KEEP_DAYS)).strftime("%Y-%m-%d")
    out = sorted((e for e in ev.values() if e.get("date", "") >= cutoff),
                 key=lambda e: (e["date"], e["type"]))
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out, todays


def _dig(o, path):
    for k in path:
        if isinstance(o, dict) and k in o:
            o = o[k]
        else:
            return None
    return o


def _parse_cwa_time(s):
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=TZ)
        except ValueError:
            continue
    return None


def _struct(o, depth=9):
    """回傳資料結構摘要（鍵名 + 型別 + 少量樣本），供校準欄位路徑用。"""
    if depth <= 0:
        return "…"
    if isinstance(o, dict):
        return {k: _struct(v, depth - 1) for k, v in list(o.items())[:25]}
    if isinstance(o, list):
        return {"__list_len__": len(o), "__item__": _struct(o[0], depth - 1)} if o else "[]"
    s = str(o)
    return f"{type(o).__name__}={s[:60]}"


def _first_local_station(records, county=COUNTY):
    """找出第一筆屬雲林的觀測站原始 dict（供 debug 校準真實雨量/氣溫欄位路徑）。找不到回 None。"""
    found = [None]

    def walk(o):
        if found[0] is not None:
            return
        if isinstance(o, dict):
            loc = _loc_of(o)
            if (county in str(loc) or "雲林" in str(loc)) and (
                    "RainfallElement" in o or "WeatherElement" in o):
                found[0] = o
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(records)
    return found[0]


def _hourly_series(elems, now):
    """由一組 WeatherElement 萃取逐時段序列 [{'t','pop','temp','wx'}]（只留現在起的未來時段）。"""
    series = {}
    for e in (elems or []):
        en = str(e.get("ElementName") or e.get("elementName") or "")
        for tm in (e.get("Time") or e.get("time") or []):
            t = str(tm.get("DataTime") or tm.get("StartTime")
                    or tm.get("dataTime") or tm.get("startTime") or "")
            if not t:
                continue
            ev = tm.get("ElementValue") or tm.get("elementValue") or []
            if isinstance(ev, dict):
                ev = [ev]
            v = ev[0] if ev else {}
            rec = series.setdefault(t, {})
            if "降雨機率" in en:
                raw = v.get("ProbabilityOfPrecipitation", v.get("降雨機率"))
                try:
                    rec["pop"] = int(float(raw))
                except (TypeError, ValueError):
                    pass
            elif "溫度" in en and "露點" not in en and "體感" not in en:
                raw = v.get("Temperature", v.get("溫度"))
                try:
                    rec["temp"] = int(round(float(raw)))
                except (TypeError, ValueError):
                    pass
            elif "天氣現象" in en:
                rec["wx"] = str(v.get("Weather") or v.get("天氣現象") or "")
    rows = []
    for t in sorted(series.keys()):
        dt = _parse_cwa_time(t)
        if dt and dt < now - timedelta(hours=1):   # 只留現在起的未來時段
            continue
        r = series[t]
        rows.append({"t": t, "pop": r.get("pop"), "temp": r.get("temp"), "wx": r.get("wx", "")})
    # F-D0047-089：溫度逐時、降雨機率/天氣現象逐 3 小時 → 只留「有天氣/降雨」的時段當逐 3 小時代表，
    # 避免整點列大量空白（溫度在這些時段也在，故仍完整）。若整份都沒天氣現象才退回逐時全量。
    complete = [r for r in rows if r.get("wx") or r.get("pop") is not None]
    return complete or rows


def parse_hourly_3h(recs, now):
    """從 F-D0047-089（臺灣各縣市未來 3 天，逐 3 小時）整理每縣市的逐 3 小時序列，
    供前端「未來 24 小時」逐時顯示（比 F-C0032-001 的 12 小時分段細）。
    回傳 {'台中市':[{'t':iso,'pop':int|None,'temp':int|None,'wx':str}], ...}（每縣市未來約 2 天）。
    實測結構：records.Locations[0]（LocationsName='台灣'）→ Location[]（22 縣市，LocationName＝縣市名）
             → WeatherElement[]。若某縣市層下還有子 Location（鄉鎮），取第一個代表。
    欄位大小寫/中英文皆容錯；抓不到或結構不符回 {}（前端自動退回 12 小時分段）。"""
    if not recs:
        return {}
    wrappers = recs.get("Locations") or recs.get("locations")
    if isinstance(wrappers, dict):
        wrappers = [wrappers]
    if not isinstance(wrappers, list):
        return {}

    out = {}
    for wrap in wrappers:
        if not isinstance(wrap, dict):
            continue
        areas = wrap.get("Location") or wrap.get("location") or []
        if not isinstance(areas, list):
            continue
        for a in areas:                       # 每個 area 即一縣市（LocationName＝縣市名）
            if not isinstance(a, dict):
                continue
            name = str(a.get("LocationName") or a.get("locationName") or "").strip().replace("臺", "台")
            if not name:
                continue
            # 若此層還有子鄉鎮 Location（含 WeatherElement），取第一個代表；否則本層即含 WeatherElement
            src = a
            sub = a.get("Location") or a.get("location")
            if isinstance(sub, list) and sub and isinstance(sub[0], dict) \
                    and (sub[0].get("WeatherElement") or sub[0].get("weatherElement")):
                src = sub[0]
            rows = _hourly_series(src.get("WeatherElement") or src.get("weatherElement") or [], now)
            if rows:
                out[name] = rows[:16]         # 約 2 天（16×3hr）
    return out


def _period_label(start_dt, now):
    """依起始時間相對今天，給友善標籤：今日白天/今晚/明日白天/明晚…"""
    if not start_dt:
        return ""
    day_diff = (start_dt.date() - now.date()).days
    daytime = 6 <= start_dt.hour < 18
    day = {0: "今", 1: "明", 2: "後"}.get(day_diff, start_dt.strftime("%m/%d"))
    if day_diff < 0:
        day = "今"
    return (day + "日白天") if daytime else (day + "晚")


def parse_nationwide_36h(recs, now):
    """從 F-C0032-001（全國各縣市今明 36 小時）整理每縣市的 3 時段預報。
    回傳 {'雲林縣': {'periods':[{'label','start','end','pop','minT','maxT','wx'}]}, ...}。
    欄位大小寫/中英文皆容錯（v1 lower / v2 Pascal）。"""
    if not recs:
        return {}

    def g(o, *keys):
        for k in keys:
            if isinstance(o, dict) and k in o and o[k] not in (None, ""):
                return o[k]
        return None

    locs = g(recs, "location", "Location")
    if not isinstance(locs, list):
        # 有些版本包一層 Locations
        wrap = g(recs, "locations", "Locations")
        if isinstance(wrap, list) and wrap:
            locs = g(wrap[0], "location", "Location")
    if not isinstance(locs, list):
        return {}

    out = {}
    for loc in locs:
        name = str(g(loc, "locationName", "LocationName") or "").strip()
        if not name:
            continue
        name = name.replace("臺", "台")
        elems = g(loc, "weatherElement", "WeatherElement") or []
        # 收集各元素逐時段 → 以 startTime 為鍵
        periods = {}
        for e in elems:
            en = str(g(e, "elementName", "ElementName") or "")
            for tm in (g(e, "time", "Time") or []):
                st = str(g(tm, "startTime", "StartTime", "dataTime", "DataTime") or "")
                et = str(g(tm, "endTime", "EndTime") or "")
                if not st:
                    continue
                par = g(tm, "parameter", "Parameter") or {}
                ev = g(tm, "elementValue", "ElementValue")
                if isinstance(ev, list) and ev:
                    par = ev[0] if not par else par
                pname = g(par, "parameterName", "ParameterName", "value", "Value")
                pval = g(par, "parameterValue", "ParameterValue")
                rec = periods.setdefault(st, {"start": st, "end": et})
                if en in ("Wx",):
                    rec["wx"] = str(pname or "")
                elif en in ("PoP", "PoP12h"):
                    try:
                        rec["pop"] = int(float(pname))
                    except (TypeError, ValueError):
                        pass
                elif en == "MinT":
                    try:
                        rec["minT"] = int(round(float(pname)))
                    except (TypeError, ValueError):
                        pass
                elif en == "MaxT":
                    try:
                        rec["maxT"] = int(round(float(pname)))
                    except (TypeError, ValueError):
                        pass
        rows = []
        for st in sorted(periods.keys()):
            dt = _parse_cwa_time(st)
            if dt and dt < now - timedelta(hours=12):
                continue
            r = periods[st]
            r["label"] = _period_label(dt, now)
            rows.append(r)
        if rows:
            out[name] = {"periods": rows[:4]}
    return out


def main():
    now = datetime.now(TZ)
    key_set = bool(os.environ.get("CWA_API_KEY"))

    warn = cwa_get(DATASETS["typhoon_warning"])
    inv = cwa_get(DATASETS["invasion_prob"])
    path = cwa_get(DATASETS["path_potential"])
    rain_recs = cwa_get(DATASETS["rainfall"], CountyName=COUNTY)
    temp_recs = cwa_get(DATASETS["temp_obs"], CountyName=COUNTY)
    county_recs = cwa_get(DATASETS["county_fcst"])           # 全國各縣市今明 36 小時（全國天氣預報）
    # 全國各鄉鎮逐 3 小時：逐一嘗試候選代碼，取第一個抓到的（大資料集放寬 timeout）
    hourly_recs, hourly_src = None, None
    for did in HOURLY_DATASETS:
        rec = cwa_get(did, timeout=60)
        if rec:
            hourly_recs, hourly_src = rec, did
            break

    # 未來天氣預報：counties = 12 小時分段（擺攤指數/何時下雨用）；hourly = 逐 3 小時（未來 24 小時逐時用）
    counties = parse_nationwide_36h(county_recs, now)
    hourly = parse_hourly_3h(hourly_recs, now)
    try:
        with open("weather_forecast.json", "w", encoding="utf-8") as f:
            json.dump({"updated": now.strftime("%Y-%m-%d %H:%M"),
                       "source": f"CWA F-C0032-001 + {hourly_src or '(逐時暫無)'}",
                       "counties": counties, "hourly": hourly}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[fetch_cwa] 預報寫出失敗：{e}", file=sys.stderr)

    # 除錯：把資料集真實結構 + 一筆雲林站原始樣本寫出，供校準 parse_*（雨量/氣溫/預報欄位路徑）
    try:
        with open("cwa_debug.json", "w", encoding="utf-8") as f:
            json.dump({"key_set": key_set, "generated": now.isoformat(),
                       "W-C0034-001_warning": _struct(warn),
                       "W-C0034-003_invasion": _struct(inv),
                       "W-C0034-005_path": _struct(path),
                       "O-A0002-001_rain": _struct(rain_recs),
                       "O-A0002-001_rain_yunlin_sample": _first_local_station(rain_recs),
                       "O-A0001-001_temp_yunlin_sample": _first_local_station(temp_recs),
                       "F-C0032-001_fcst": _struct(county_recs),
                       "F-C0032-001_counties_parsed": sorted(counties.keys()),
                       "hourly_source": hourly_src,
                       "hourly_struct": _struct(hourly_recs),
                       "hourly_counties_parsed": sorted(hourly.keys()),
                       "hourly_taichung_sample": hourly.get("台中市", [])[:4]},
                      f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[fetch_cwa] debug dump 失敗：{e}", file=sys.stderr)

    def _rp(key):
        return parse_rain_hours(rain_recs, ["RainfallElement", key, "Precipitation"])
    rain1 = _rp("Past1hr")
    rain24 = parse_rain_24h(rain_recs)
    # 多時段累積雨量（1/3/6/12/24hr、2/3 日）——比只看 24hr 更準。
    # 欄位名已用真實 O-A0002-001 校準：注意 6hr 是 Past6Hr（大寫 H），其餘為小寫 hr。
    rain_multi = {"h1": rain1, "h3": _rp("Past3hr"), "h6": _rp("Past6Hr"),
                  "h12": _rp("Past12hr"), "h24": rain24,
                  "d2": _rp("Past2days"), "d3": _rp("Past3days")}

    typ = eval_typhoon(path, now)
    warn_info = parse_warning(warn, now)

    if typ:
        # 對雲林有威脅（發布警報 或 侵襲機率≥10%）才進「搶收模式」；否則只「追蹤中」
        threatening = warn_info["land_warning"] or (typ["invade_prob"] or 0) >= 0.10
        status = {
            "updated": now.strftime("%Y-%m-%d %H:%M"),
            "source": "CWA opendata (W-C0034-005 路徑潛勢)",
            "active": bool(threatening),
            "tracking": (not threatening),
            "name": typ["name"],
            "warning": (warn_info["text"] if warn_info["land_warning"]
                        else ("颱風接近中" if threatening else "追蹤中，對雲林暫無直接威脅")),
            "land_warning": warn_info["land_warning"],
            "invade_prob": typ["invade_prob"],
            "eta_iso": typ["eta_iso"],
            "eta_text": typ["eta_text"] or "追蹤中",
            "min_dist_km": typ["min_dist"],
            "rain_24h_mm": rain24,
            "rain_1h_mm": rain1,
            "rain": rain_multi,
            "forecast_gust_ms": typ["gust"],
            "track": typ["track"],
        }
    else:
        status = {
            "updated": now.strftime("%Y-%m-%d %H:%M"), "source": "CWA opendata",
            "active": False, "tracking": False, "name": None, "warning": None,
            "land_warning": False, "invade_prob": None, "eta_iso": None, "eta_text": None,
            "min_dist_km": None, "rain_24h_mm": rain24, "rain_1h_mm": rain1, "rain": rain_multi,
            "forecast_gust_ms": None, "track": None,
        }

    with open("typhoon_status.json", "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

    # 累積真實天氣事件（K線標記）：颱風 + 大雨（已有 rain24）+ 低溫（temp_recs 已於上方抓取）
    temp_min = parse_temp_min(temp_recs)
    events, todays = accumulate_events(status, temp_min, now)

    print(f"[fetch_cwa] key_set={key_set} typhoon={status['name']} active={status['active']} "
          f"tracking={status.get('tracking')} invade={status['invade_prob']} eta={status['eta_text']} "
          f"dist={status.get('min_dist_km')}km rain24={rain24} rain1={rain1} temp_min={temp_min}")
    print(f"[fetch_cwa] 天氣事件累積：共 {len(events)} 筆，今日新增 {len(todays)} 筆"
          + (f"（{'、'.join(e['type'] for e in todays)}）" if todays else ""))
    print(f"[fetch_cwa] 逐時預報：來源={hourly_src or '無'} 縣市數={len(hourly)} "
          f"台中時段={len(hourly.get('台中市', []))}")
    if not key_set:
        print("[fetch_cwa] 未設 CWA_API_KEY → active=false 安全狀態。", file=sys.stderr)


if __name__ == "__main__":
    main()
