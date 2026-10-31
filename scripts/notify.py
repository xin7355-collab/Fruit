#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notify.py  ——  精準務農的推播（讓農民、產銷班不必一直盯網頁）

兩種模式（管道都用環境變數；都沒設定 → dry-run 只印出訊息、exit 0）：
  1. 搶收（預設）：讀 harvest_advisory.json，颱風期間挑出「急迫」田區（立即搶收／deadline 逼近／PHI 兩難）。
     LINE 送 Flex 卡片（一田一列、顏色標決策、按鈕直接開「田園巡查」），webhook 送純文字。
  2. 行情（--prices）：讀 prices_index.json＋該市場本月檔的看板摘要（sum），關注品項「偏便宜／偏貴」時推播。
     關注清單沒設定 → 視為「還沒設定」，安靜略過（不是錯誤）。

管道：
  LINE Messaging API（push）  LINE_CHANNEL_ACCESS_TOKEN＋LINE_TO（user/group id；LINE Notify 已於 2025-03 停用）
  通用 webhook               NOTIFY_WEBHOOK_URL（送 {"text": ...}，Slack/Discord/Telegram 橋接皆可）

去重：
  搶收：「急迫田集合的簽章」沒變就不重送（避免每 30 分洗版）。
  行情：同一（市場, 品項, 方向）7 天內不重送，除非又再偏離 5 個百分點以上（做法參考 midssinglin/taiwan-price 的
        alerts.mjs 思路，程式自行撰寫）；回到「正常」就重置。

故意不重試：推播是 POST，逾時不代表沒送到，重試可能讓同一則通知送兩次。

用法：
  python3 scripts/notify.py --advisory harvest_advisory.json --state notify_state.json
  python3 scripts/notify.py --prices --state notify_price_state.json
環境變數：
  NOTIFY_DEADLINE_H     搶收：deadline 幾小時內視為急迫（預設 18）
  NOTIFY_PRICE_MARKET   行情：市場（預設 西螺）
  NOTIFY_PRICE_CROPS    行情：關注品項，逗號分隔（例：甘藍,小白菜,青江白菜）；空白＝不推行情
  APP_URL               App 網址（預設 https://xin7355-collab.github.io/Fruit/）
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
DEADLINE_H = float(os.environ.get("NOTIFY_DEADLINE_H", "18"))
APP_URL = os.environ.get("APP_URL", "https://xin7355-collab.github.io/Fruit/")
APP_NAME = "精準務農"
PRICE_RESEND_DAYS = 7
PRICE_RESEND_STEP = 0.05
PRICE_FRESH_DAYS = 4
DECISION_COLOR = {"HARVEST_NOW": "#E4572E", "HARVEST_ADVISED": "#F29E38"}
DECISION_ICON = {"HARVEST_NOW": "🔴", "HARVEST_ADVISED": "🟠"}


# ───────────────────────── 搶收 ─────────────────────────
def urgent_fields(adv, now=None):
    now = now or datetime.now(TZ)
    out = []
    for f in adv.get("fields", []):
        why = []
        if f.get("decision") == "HARVEST_NOW":
            why.append("立即搶收")
        dl = f.get("harvest_deadline")
        if dl:
            try:
                h = (datetime.fromisoformat(dl) - now).total_seconds() / 3600
                if 0 < h <= DEADLINE_H:
                    why.append(f"deadline {round(h)} 小時內")
            except ValueError:
                pass
        if f.get("dilemma"):
            why.append("PHI 兩難")
        if why:
            out.append((f, " · ".join(dict.fromkeys(why))))
    return out


def signature(ufs):
    key = ";".join(sorted(
        f"{f['field_id']}:{f['decision']}:{1 if f.get('dilemma') else 0}" for f, _ in ufs))
    return hashlib.sha1(key.encode()).hexdigest()


def _headline(adv):
    ty = adv.get("typhoon", {}) or {}
    prob = round((ty.get("invade_prob") or 0) * 100)
    region = ty.get("region") or ""        # 以前寫死「雲林」；全國使用者的田不一定在雲林
    return f"🌀 {ty.get('name', '颱風')}｜{region}侵襲機率 {prob}%｜{ty.get('eta_text', '')}".rstrip("｜")


def _field_line(f, why):
    line = f"{DECISION_ICON.get(f['decision'], '🟡')} {f['field_id']} {f['crop']}｜{why}｜風險 {f['risk_score']}"
    if f.get("dilemma"):
        line += f"（距安全採收期還差 {f['dilemma']['days_short']} 天，收了不可上市）"
    return line


def compose(adv, ufs):
    lines = [_headline(adv)] + [_field_line(f, why) for f, why in ufs]
    sch = adv.get("schedule")
    if sch and sch.get("summary"):
        lines.append("🗂 " + sch["summary"].replace("<b>", "").replace("</b>", ""))
    lines.append(f"— {APP_NAME}　{APP_URL}#harvest")
    return "\n".join(lines)


def flex_harvest(adv, ufs, text):
    """LINE Flex 卡片：一田一列（最多 10 列，其餘寫「另有 N 田」），按鈕直接開田園巡查。altText 用純文字版（上限 400 字）。"""
    rows = []
    for f, why in ufs[:10]:
        rows.append({"type": "box", "layout": "vertical", "margin": "md", "contents": [
            {"type": "text", "text": f"{f['field_id']} {f['crop']}", "weight": "bold", "size": "sm",
             "color": DECISION_COLOR.get(f["decision"], "#C9A227"), "wrap": True},
            {"type": "text", "text": f"{why}｜風險 {f['risk_score']}", "size": "xs", "color": "#666666", "wrap": True}]})
    if len(ufs) > 10:
        rows.append({"type": "text", "text": f"另有 {len(ufs) - 10} 田，請開 App 看完整清單", "size": "xs", "margin": "md"})
    bubble = {
        "type": "bubble",
        "header": {"type": "box", "layout": "vertical", "contents": [
            {"type": "text", "text": _headline(adv), "weight": "bold", "wrap": True, "size": "md"}]},
        "body": {"type": "box", "layout": "vertical", "contents": rows},
        "footer": {"type": "box", "layout": "vertical", "contents": [
            {"type": "button", "style": "primary", "color": "#5FB85A",
             "action": {"type": "uri", "label": "開啟田園巡查", "uri": APP_URL + "#harvest"}}]}}
    return {"type": "flex", "altText": text[:400], "contents": bubble}


# ───────────────────────── 行情 ─────────────────────────
def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def price_alerts(index_path, market, crops, state, now=None):
    """回傳 (要推的訊息列 list[str], 新 state dict)。只用後端算好的看板摘要，不重算。"""
    now = now or datetime.now(TZ)
    ix = _load_json(index_path) or {}
    ent = next((m for m in ix.get("markets", []) if m.get("name") == market), None)
    if not ent or not ent.get("recent"):
        return [], state
    base = os.path.dirname(os.path.abspath(index_path))
    summ = (_load_json(os.path.join(base, ent["recent"])) or {}).get("sum") or {}
    msgs, new_state = [], dict(state)
    for crop in crops:
        s = summ.get(crop)
        key = f"{market}|{crop}"
        if not s or s.get("r") is None:
            continue
        age = (now.date() - datetime.fromisoformat(s["d"]).date()).days
        if age > PRICE_FRESH_DAYS or s.get("t") not in ("up", "dn"):
            new_state.pop(key, None)                 # 回到正常或沒在交易 → 重置，下次偏離再通知
            continue
        dev = s["r"] - 1
        prev = state.get(key)
        if prev and prev.get("t") == s["t"]:
            days = (now - datetime.fromisoformat(prev["at"])).days
            further = abs(dev) - abs(prev.get("dev", 0))
            if days < PRICE_RESEND_DAYS and further < PRICE_RESEND_STEP:
                continue                              # 7 天內、沒有再偏離 5 個百分點 → 不重送
        word = "便宜" if s["t"] == "dn" else "貴"
        line = (f"{'📉' if s['t'] == 'dn' else '📈'} {market} {crop} {s['l']['avg']:.1f} 元/公斤（{s['d'][5:]}），"
                f"比近 90 日中位數{word} {abs(dev) * 100:.0f}%")
        if s.get("w") is not None:
            line += f"，較上週 {s['w']:+.0f}%"
        if s.get("q"):
            line += "（量少，價格容易大起大落）"
        msgs.append(line)
        new_state[key] = {"t": s["t"], "dev": dev, "at": now.isoformat()}
    return msgs, new_state


# ───────────────────────── 送出 ─────────────────────────
def _post(url, payload, headers):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def send_line(token, to, messages):
    return _post("https://api.line.me/v2/bot/message/push", {"to": to, "messages": messages},
                 {"Authorization": f"Bearer {token}"})


def send_webhook(url, text):
    return _post(url, {"text": text}, {})


def deliver(text, line_messages):
    """回傳已送出的管道；都沒設定回 []（dry-run）。失敗 exit 1（真的壞掉才紅燈）。"""
    token, to, webhook = (os.environ.get(k) for k in ("LINE_CHANNEL_ACCESS_TOKEN", "LINE_TO", "NOTIFY_WEBHOOK_URL"))
    sent = []
    try:
        if token and to:
            send_line(token, to, line_messages)
            sent.append("LINE")
        if webhook:
            send_webhook(webhook, text)
            sent.append("webhook")
    except Exception as e:
        print(f"[notify] 推播失敗：{e}（請到 GitHub → Settings → Secrets 確認 LINE／webhook 設定）", file=sys.stderr)
        sys.exit(1)
    return sent


def run_harvest(args):
    if not os.path.exists(args.advisory):
        print(f"[notify] 找不到 {args.advisory}，略過。", file=sys.stderr)
        return
    adv = _load_json(args.advisory) or {}
    if not (adv.get("typhoon") or {}).get("active"):
        print("[notify] 無颱風警報，略過通知。")
        return
    ufs = urgent_fields(adv)
    if not ufs:
        print("[notify] 無急迫田區，略過通知。")
        return
    sig = signature(ufs)
    prev = (_load_json(args.state) or {}).get("sig")
    if sig == prev and not args.force:
        print(f"[notify] 急迫田集合未變化（{len(ufs)} 田），略過重複通知。")
        return
    text = compose(adv, ufs)
    sent = deliver(text, [flex_harvest(adv, ufs, text)])
    print(f"[notify] 已推播（{'/'.join(sent)}），{len(ufs)} 個急迫田。" if sent else
          "[notify] 未設定任何通知管道（dry-run），訊息內容如下：\n" + "-" * 40 + f"\n{text}\n" + "-" * 40)
    with open(args.state, "w", encoding="utf-8") as f:
        json.dump({"sig": sig, "updated": datetime.now(TZ).isoformat(), "urgent_count": len(ufs)}, f,
                  ensure_ascii=False, indent=2)


def run_prices(args):
    crops = [c.strip() for c in (os.environ.get("NOTIFY_PRICE_CROPS") or "").split(",") if c.strip()]
    market = (os.environ.get("NOTIFY_PRICE_MARKET") or "西螺").strip()
    if not crops:
        print("[notify] 行情推播：還沒設定關注品項（NOTIFY_PRICE_CROPS），略過。")
        return
    state = _load_json(args.state) or {}
    msgs, new_state = price_alerts(args.index, market, crops, state)
    if msgs:
        text = "\n".join([f"🥬 {APP_NAME}｜行情提醒（{market}）"] + msgs + [f"— {APP_URL}"])
        sent = deliver(text, [{"type": "text", "text": text[:5000]}])
        print(f"[notify] 行情已推播（{'/'.join(sent)}），{len(msgs)} 則。" if sent else
              "[notify] 未設定任何通知管道（dry-run），行情訊息如下：\n" + text)
    else:
        print(f"[notify] 行情：{market} 關注的 {len(crops)} 個品項沒有新的偏貴／偏便宜，略過。")
    with open(args.state, "w", encoding="utf-8") as f:
        json.dump(new_state, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--advisory", default="harvest_advisory.json")
    ap.add_argument("--state", default=None)
    ap.add_argument("--prices", action="store_true", help="行情推播模式")
    ap.add_argument("--index", default="prices_index.json")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if args.prices:
        args.state = args.state or "notify_price_state.json"
        run_prices(args)
    else:
        args.state = args.state or "notify_state.json"
        run_harvest(args)


if __name__ == "__main__":
    main()
