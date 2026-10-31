#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_advisory.py  ——  雲林葉菜「搶收決策」合成模型

輸入：
  data/fields.json         田區登記表（見 data/fields.example.json 格式）
  typhoon_status.json      fetch_cwa.py 產出的颱風/雨量現況（可缺；缺則視為無颱風）
  veg_prices.json          既有 miner 產出的行情（可缺；缺則用內建 fallback 價）

輸出：
  harvest_advisory.json    每田×每菜的決策物件陣列 + 全區搶收排程，供前端 index.html 讀取

實作範圍（對齊設計文件）：
  B 硬約束閘門：PHI（安全採收期）、成熟度                      ← v1
  D 時間軸求解：搶收窗 / deadline / partial                     ← v1
  C 滅田風險 R：P×I×W×V 物理估（歷史類比校準 analog 留 TODO）   ← v1
  E 市場 EV：早收落袋 vs 賭災後噴價（接 veg_prices + lampFor 壓價）← v3 ★本次
  F 全區搶收排程：人力有限下跨多田的最佳搶收順序/時程/來不及救的田 ← v4 ★本次

用法：
  python3 scripts/build_advisory.py --fields data/fields.example.json --demo-typhoon
  python3 scripts/build_advisory.py --fields data/fields.json --typhoon typhoon_status.json --teams 2
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))  # 台灣時間

# ─────────────── 可調參數（集中管理） ───────────────
SAFETY_BUFFER_H = 12
MATURE_SOFT = 0.9

GROWTH_DAYS = {  # (夏作, 冬作)；夏作=5–10 月。已依農業部農業知識入口網／各區農改場資料校準（估算）
    "青江菜": (33, 45), "青江白菜": (33, 45), "小白菜": (25, 35), "奶油白菜": (30, 40),
    "蚵白菜": (30, 40), "空心菜": (20, 32), "蕹菜": (20, 32), "莧菜": (35, 45),
    "菠菜": (38, 45), "芥藍菜": (50, 62), "油菜": (32, 42), "萵苣(A菜)": (30, 42),
    "茼蒿": (40, 50), "芥菜": (55, 65), "甘藍": (70, 90), "包心白菜": (60, 80),
}
MATURITY_MIN = {
    "青江菜": 0.75, "小白菜": 0.70, "奶油白菜": 0.72,
    "蚵白菜": 0.72, "菠菜": 0.75, "芥藍菜": 0.75,
}
V_CROP = {
    "青江菜": 0.95, "小白菜": 0.95, "奶油白菜": 0.90,
    "蚵白菜": 0.90, "菠菜": 0.85, "芥藍菜": 0.80,
}
FLOOD_ADVANCE_H = {1: 0, 2: 0, 3: 12, 4: 18, 5: 24}
WIND_ANCHOR_MS = 45.0
RAIN_ANCHOR_MM = 350.0

# ── v3 市場 EV 參數 ──
BASE_PRICE = {  # NT$/kg，veg_prices.json 抓不到時的 fallback（量級示意，上線以真實行情為準）
    "青江菜": 22, "小白菜": 20, "奶油白菜": 20, "蚵白菜": 20, "菠菜": 32, "芥藍菜": 30,
}
YIELD_KG_HA = {  # 每公頃可採收量（粗估）
    "青江菜": 20000, "小白菜": 22000, "奶油白菜": 20000,
    "蚵白菜": 20000, "菠菜": 15000, "芥藍菜": 12000,
}
HARVEST_COST_PER_HA = 18000    # 搶收人工+運搬粗估 NT$/ha
SALVAGE_RATE = 0.0             # 滅田殘值比例
PHI_MARKETABLE_RATIO = 0.15    # PHI 未到只能走加工/殘值通路的可售比例
SURGE_K = 1.5                  # 全區搶收比例→壓價代理係數（待接真實日成交量校準）

# ── v4 排程參數 ──
TEAMS_DEFAULT = 2              # 全區可同時作業的搶收人力隊數（region 人力上限）


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def mean(a):
    return sum(a) / len(a) if a else 0.0


def is_summer(d):
    return 5 <= d.month <= 10


def growth_days(crop, plant_dt):
    summer, winter = GROWTH_DAYS.get(crop, (35, 45))
    return summer if is_summer(plant_dt) else winter


def parse_date(s):
    if not s:
        return None
    try:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TZ)
        y, m, d = (int(x) for x in s.split("-"))
        return datetime(y, m, d, tzinfo=TZ)
    except Exception:
        return None


# ─────────────── 行情 base_price（v3 需要） ───────────────
def load_base_prices(veg_path):
    """由 veg_prices.json 取每個作物最新均價（NT$/kg）。抓不到回 {}。"""
    if not veg_path or not os.path.exists(veg_path):
        return {}
    try:
        with open(veg_path, encoding="utf-8") as f:
            j = json.load(f)
        markets = j.get("markets") or []
        crop_map = j.get("crop_map") or {}
        data = j.get("data") or {}
        if not markets:
            return {}
        market = markets[0]
        out = {}
        for disp_crop, wholesale in crop_map.items():
            series = (data.get(market) or {}).get(wholesale) or {}
            if not series:
                continue
            last_iso = max(series.keys())
            avg = series[last_iso].get("avg")
            if avg is not None:
                out[disp_crop] = float(avg)
        return out
    except Exception as e:
        print(f"[build_advisory] 讀 veg_prices 失敗（用 fallback 價）：{e}", file=sys.stderr)
        return {}


def price_early_factor(surge):
    """全區搶收造成的供給暴增→壓價倍率（接 lampFor 爆量邏輯）。"""
    if surge < 1.2:
        return 0.95
    if surge < 1.8:
        return 0.75
    if surge < 2.5:
        return 0.55
    return 0.40


def spike_factor(loss_rate):
    """區域減產→災後噴價倍率。"""
    if loss_rate < 0.3:
        return 1.2
    if loss_rate < 0.5:
        return 1.4
    if loss_rate < 0.7:
        return 1.9
    return 2.6


# ─────────────── B. 硬約束閘門 ───────────────
def phi_gate(field, now):
    latest, latest_chem = None, None
    for c in field.get("chemicals", []) or []:
        sd = parse_date(c.get("spray_date"))
        phi = c.get("phi_days")
        if sd is None or phi is None:
            continue
        expiry = sd + timedelta(days=int(phi))
        if latest is None or expiry > latest:
            latest, latest_chem = expiry, c.get("name", "?")
    if latest is None:
        return None, 0, None
    days_short = math.ceil((latest - now).total_seconds() / 86400)
    return latest, max(0, days_short), latest_chem


def maturity_of(field, now):
    plant = parse_date(field.get("planting_date"))
    if plant is None:
        return None, None
    T = growth_days(field["crop"], plant)
    return round((now - plant).total_seconds() / 86400 / T, 3), T


# ─────────────── C. 滅田風險 R ───────────────
def risk_score(field, typhoon):
    if not typhoon or not typhoon.get("active"):
        return 0, {}
    p_arrival = float(typhoon.get("invade_prob") or 0.0)
    rain = float(typhoon.get("rain_24h_mm") or 0.0)
    gust = typhoon.get("forecast_gust_ms")
    rain_norm = clamp(rain / RAIN_ANCHOR_MM, 0, 1)
    if gust is not None:
        i_norm = clamp(0.5 * clamp(float(gust) / WIND_ANCHOR_MS, 0, 1) + 0.5 * rain_norm, 0, 1)
    else:
        i_norm = rain_norm  # TODO v2：併入陣風預測 F-C0034-005
    flood = int(field.get("flood_potential_level") or 1)
    elev = float(field.get("elevation_m") or 8)
    w_terrain = clamp(1.0 + 0.10 * (flood - 1) - 0.05 * clamp((elev - 5) / 10, -1, 1), 0.3, 1.3)
    v_crop = V_CROP.get(field["crop"], 0.9)
    r_raw = 100 * p_arrival * i_norm * w_terrain * v_crop
    # TODO v2：analog_loss_rate 歷史類比校準
    return round(clamp(r_raw, 0, 100)), {
        "P_arrival": round(p_arrival, 2), "I_norm": round(i_norm, 2),
        "W_terrain": round(w_terrain, 2), "V_crop": v_crop, "R_raw": round(r_raw),
    }


# ─────────────── D. 時間軸求解 ───────────────
def solve_timeline(field, typhoon, now):
    if not typhoon or not typhoon.get("active"):
        return {"deadline": None, "slack_h": None, "partial_pct": None, "t_safe": None, "dur_h": None}
    arrival = parse_date(typhoon.get("eta_iso"))
    if arrival is None:
        return {"deadline": None, "slack_h": None, "partial_pct": None, "t_safe": None, "dur_h": None}
    flood = int(field.get("flood_potential_level") or 1)
    t_safe = arrival - timedelta(hours=SAFETY_BUFFER_H + FLOOD_ADVANCE_H.get(flood, 0))
    dur_h = (field.get("area_ha", 0.5) * field.get("labor_h_per_ha", 60)
             / max(1, field.get("crew_size", 3)))
    deadline = t_safe - timedelta(hours=dur_h)
    slack_h = (deadline - now).total_seconds() / 3600
    avail_h = (t_safe - now).total_seconds() / 3600
    partial = int(round(avail_h / dur_h * 100)) if dur_h > avail_h and avail_h > 0 else None
    return {"deadline": deadline, "slack_h": slack_h, "partial_pct": partial,
            "t_safe": t_safe, "dur_h": dur_h}


# ─────────────── A. 決策合成（回傳 公開物件, 內部欄位） ───────────────
def decide(field, typhoon, now):
    crop = field["crop"]
    mat, _ = maturity_of(field, now)
    phi_ok, days_short, phi_chem = phi_gate(field, now)
    R, rb = risk_score(field, typhoon)
    tl = solve_timeline(field, typhoon, now)
    typhoon_active = bool(typhoon and typhoon.get("active"))

    reasons = []
    immature = mat is not None and mat < MATURITY_MIN.get(crop, 0.75)
    slack_h = tl["slack_h"]

    if R >= 65 and slack_h is not None and slack_h <= (tl.get("dur_h", 6) * 1.2 + SAFETY_BUFFER_H):
        decision = "HARVEST_NOW"
    elif R >= 50:
        decision = "HARVEST_ADVISED"
    elif R >= 25:
        decision = "WATCH"
    else:
        decision = "NORMAL"

    if immature and decision in ("HARVEST_NOW", "HARVEST_ADVISED"):
        decision = "WATCH"
        reasons.append(f"成熟度僅 {round(mat*100)}%，未達 {round(MATURITY_MIN.get(crop,0.75)*100)}%，搶收殘值過低")

    if typhoon_active:
        reasons.insert(0, f"雲林暴風圈侵襲機率 {round(rb.get('P_arrival',0)*100)}%，"
                          f"預估 24hr 雨量 {typhoon.get('rain_24h_mm','?')}mm，滅田風險 {R}/100")
    flood = int(field.get("flood_potential_level") or 1)
    if flood >= 3:
        reasons.append(f"淹水潛勢等級 {flood}（{'低窪' if flood>=4 else '偏低'}），泡水 48hr 起爛根，需提前搶收")
    if crop == "芥藍菜":
        reasons.append("中長期作物（60–80 天），颱風曝險期最長")

    if tl["deadline"] is not None and decision in ("HARVEST_NOW", "HARVEST_ADVISED"):
        dl = tl["deadline"]
        reasons.append(f"最晚 {dl.month}/{dl.day} {dl.hour:02d}:{dl.minute:02d} 前須開始搶收")
    if tl["partial_pct"] is not None:
        reasons.append(f"暴風前可作業時間不足，人手僅能收約 {tl['partial_pct']}% 面積 → 依風險先收")

    dilemma = None
    if days_short > 0:
        reasons.append(f"⚠ 不可上市：距安全採收期還差 {days_short} 天（{phi_chem}）")
        if typhoon_active and decision in ("HARVEST_NOW", "HARVEST_ADVISED", "WATCH"):
            dilemma = {"days_short": days_short, "chemical": phi_chem}
            if R >= 50:
                reasons.append("兩難：現在收→農藥殘留超標恐遭銷毀；不收→泡水滅田。"
                               "建議加強排水保田或申報天災救助")
    elif phi_ok is not None:
        reasons.append("PHI 已過，可合法上市")

    if decision == "NORMAL" and not reasons:
        reasons.append("無迫近颱風時窗、風險分數低，按原計畫管理")

    conf = 0.6
    if typhoon_active:
        conf += 0.1
    if all(field.get(k) is not None for k in ("planting_date", "flood_potential_level")):
        conf += 0.1
    if field.get("chemicals"):
        conf += 0.1
    conf = round(clamp(conf, 0.35, 0.95), 2)

    public = {
        "field_id": field["field_id"], "crop": crop, "decision": decision,
        "confidence": conf, "risk_score": R, "maturity": mat,
        "flood_potential_level": field.get("flood_potential_level"),
        "harvest_deadline": tl["deadline"].isoformat() if tl["deadline"] else None,
        "dilemma": dilemma, "partial_pct": tl["partial_pct"], "immature": immature,
        "ev": None,  # v3 於 region 階段填入（需全區壓價/損失率）
        "risk_breakdown": rb, "reasons": reasons,
    }
    internal = {"t_safe": tl["t_safe"], "dur_h": tl["dur_h"], "area_ha": field.get("area_ha", 0.5)}
    return public, internal


# ─────────────── E. 市場 EV（v3；需全區 surge / loss_rate） ───────────────
def compute_ev(pub, field_area, base_price, region):
    crop = pub["crop"]
    mat = pub["maturity"] if pub["maturity"] is not None else 0.8
    R = pub["risk_score"]
    yield_full = field_area * YIELD_KG_HA.get(crop, 18000)
    yield_now = yield_full * min(1.0, mat)
    phi_violated = pub["dilemma"] is not None
    marketable = PHI_MARKETABLE_RATIO if phi_violated else 1.0
    imm_disc = 0.5 if mat < 0.6 else (0.8 if mat < 0.8 else 1.0)

    price_early = base_price * price_early_factor(region["surge"])
    price_spike = base_price * spike_factor(region["loss_rate"])
    cost = HARVEST_COST_PER_HA * field_area

    ev_early = yield_now * marketable * price_early * imm_disc - cost
    p_survive = clamp(1 - R / 100.0, 0.0, 1.0)
    ev_gamble = p_survive * (yield_full * price_spike) + (1 - p_survive) * (yield_full * SALVAGE_RATE) - cost

    if phi_violated:
        hint = "PHI 未到，早收只能走加工通路（殘值），數字僅供參考"
    elif ev_early > ev_gamble * 1.15:
        hint = "數字上早收較穩"
    elif ev_gamble > ev_early * 1.15:
        hint = "數字上值得賭，但風險自負"
    else:
        hint = "兩者接近，看您的風險承受度"

    return {
        "early": round(ev_early), "gamble": round(ev_gamble),
        "p_survive": round(p_survive, 2),
        "price_early_factor": round(price_early_factor(region["surge"]), 2),
        "price_spike_factor": round(spike_factor(region["loss_rate"]), 2),
        "base_price": round(base_price, 1), "hint": hint,
    }


# ─────────────── F. 全區搶收排程（v4；人力有限下的最佳順序） ───────────────
def build_schedule(publics, internals, teams, now):
    """貪婪 EDF（最早 deadline 優先）+ N 隊平行人力。
    可行的田排入時程並佔用人力；來不及的田標為『來不及救』，不佔人力（讓人手去救得了的田）。
    註：這是啟發式；嚴格最佳排程（含部分搶收價值最大化）留未來迭代。"""
    cands = []
    for pub, intern in zip(publics, internals):
        if pub["decision"] in ("HARVEST_NOW", "HARVEST_ADVISED") \
           and intern.get("t_safe") and intern.get("dur_h"):
            cands.append((pub, intern))
    if not cands:
        return None
    # EDF：t_safe 早者優先，同時間風險高者優先
    cands.sort(key=lambda x: (x[1]["t_safe"], -x[0]["risk_score"]))
    team_free = [now] * max(1, teams)
    items, unsaveable = [], []
    for pub, intern in cands:
        ti = min(range(len(team_free)), key=lambda i: team_free[i])
        start = max(team_free[ti], now)
        finish = start + timedelta(hours=intern["dur_h"])
        feasible = finish <= intern["t_safe"]
        if feasible:
            team_free[ti] = finish
            note = ""
        else:
            unsaveable.append(pub["field_id"])
            note = "人力/時間不足，來不及完收 → 建議部分搶收高價區或加強排水保田"
        items.append({
            "field_id": pub["field_id"], "crop": pub["crop"],
            "risk_score": pub["risk_score"], "team": ti + 1 if feasible else None,
            "start": start.isoformat() if feasible else None,
            "finish": finish.isoformat() if feasible else None,
            "dur_h": round(intern["dur_h"], 1),
            "feasible": feasible, "note": note,
        })
    saveable = [i for i in items if i["feasible"]]
    if unsaveable:
        summary = (f"{teams} 隊人力：可救 {len(saveable)} 塊；"
                   f"{('、'.join(unsaveable))} 來不及完收，建議部分搶收/保田。")
    else:
        summary = f"{teams} 隊人力：{len(saveable)} 塊田皆可在暴風前完收。"
    return {"teams": teams, "items": items, "unsaveable": unsaveable,
            "saveable_count": len(saveable), "summary": summary}


def demo_typhoon(now):
    return {
        "active": True, "name": "克蘿莎 KROSA", "warning": "海上陸上颱風警報中",
        "invade_prob": 0.70, "eta_iso": (now + timedelta(hours=40)).isoformat(),
        "eta_text": "約 40 小時後", "rain_24h_mm": 300, "forecast_gust_ms": 40,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fields", default="data/fields.json")
    ap.add_argument("--typhoon", default="typhoon_status.json")
    ap.add_argument("--prices", default="veg_prices.json")
    ap.add_argument("--demo-typhoon", action="store_true")
    ap.add_argument("--teams", type=int, default=TEAMS_DEFAULT)
    ap.add_argument("--out", default="harvest_advisory.json")
    args = ap.parse_args()

    now = datetime.now(TZ)

    with open(args.fields, encoding="utf-8") as f:
        reg = json.load(f)
    fields = reg.get("fields", reg if isinstance(reg, list) else [])

    typhoon = None
    # --demo-typhoon 優先於既有檔：確保 demo/CI 冒煙測試不受 repo 內（可能過期 eta）的
    # typhoon_status.json 影響而變不確定；正式排程走 --typhoon（見 update-data.yml），不受影響。
    if args.demo_typhoon:
        typhoon = demo_typhoon(now)
    elif os.path.exists(args.typhoon):
        with open(args.typhoon, encoding="utf-8") as f:
            typhoon = json.load(f)
    typhoon_active = bool(typhoon and typhoon.get("active"))

    base_prices = load_base_prices(args.prices)

    # 1) 逐田決策
    publics, internals = [], []
    for fd in fields:
        pub, intern = decide(fd, typhoon, now)
        publics.append(pub)
        internals.append(intern)

    # 2) 全區聚合（surge 壓價 / loss_rate 災後噴價）
    harvesting = [p for p in publics if p["decision"] in ("HARVEST_NOW", "HARVEST_ADVISED")]
    frac = len(harvesting) / max(1, len(publics))
    region = {
        "surge": 1 + frac * SURGE_K,                       # TODO：接真實日成交量校準
        "loss_rate": mean([p["risk_score"] / 100.0 for p in publics]) if publics else 0.0,
    }

    # 3) v3 EV（僅颱風期間、且該田有搶收/觀望決策時才算）
    if typhoon_active:
        for pub, intern in zip(publics, internals):
            if pub["decision"] in ("HARVEST_NOW", "HARVEST_ADVISED", "WATCH"):
                bp = base_prices.get(pub["crop"], BASE_PRICE.get(pub["crop"], 20))
                pub["ev"] = compute_ev(pub, intern["area_ha"], bp, region)

    # 4) v4 全區排程
    schedule = build_schedule(publics, internals, args.teams, now) if typhoon_active else None

    out = {
        "updated": now.strftime("%Y-%m-%d %H:%M"),
        "typhoon": {
            "name": (typhoon or {}).get("name"), "warning": (typhoon or {}).get("warning"),
            "invade_prob": (typhoon or {}).get("invade_prob"),
            "eta_text": (typhoon or {}).get("eta_text"),
            "rain_24h_mm": (typhoon or {}).get("rain_24h_mm"),
            "active": typhoon_active,
        },
        "region": {"surge_ratio": round(region["surge"], 2),
                   "loss_rate": round(region["loss_rate"], 2),
                   "price_source": "veg_prices.json" if base_prices else "fallback"},
        "schedule": schedule,
        "fields": publics,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[build_advisory] 寫出 {args.out}：{len(publics)} 塊田，"
          f"颱風={'有' if typhoon_active else '無'}，"
          f"價來源={out['region']['price_source']}")
    for a in publics:
        ev = a.get("ev")
        evs = f"EV早{ev['early']//10000}萬/賭{ev['gamble']//10000}萬" if ev else ""
        print(f"  {a['field_id']:<8} {a['crop']:<5} {a['decision']:<16} "
              f"R={a['risk_score']:>3} {'⚠PHI' if a['dilemma'] else '    '} {evs}")
    if schedule:
        print(f"[排程] {schedule['summary']}")


if __name__ == "__main__":
    main()
