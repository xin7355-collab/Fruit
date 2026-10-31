#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notify.py  ——  急迫搶收通知（讓農民不必一直盯網頁）

讀 harvest_advisory.json，挑出「急迫」田區（立即搶收 / deadline 逼近 / PHI 兩難），
組成訊息，透過已設定的管道推播。管道皆用環境變數；都沒設定 → dry-run 只印出訊息、exit 0。

支援管道（擇一或多）：
  LINE Messaging API（push）  需 LINE_CHANNEL_ACCESS_TOKEN + LINE_TO（user/group id）
                             ※ LINE Notify 已於 2025-03 停用，故用 Messaging API。
  通用 webhook               NOTIFY_WEBHOOK_URL（送 {"text": ...}，通吃 Slack/Discord/Telegram 橋接）

去重：以「急迫田集合的簽章」存於 state 檔，未變化則不重複推播（避免每 30 分洗版）；
      有新田轉急迫或決策升級才再通知。--force 可強制送出。

用法：
  python3 scripts/notify.py --advisory harvest_advisory.json --state notify_state.json
環境變數：
  NOTIFY_DEADLINE_H   deadline 幾小時內視為急迫（預設 18）
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


def urgent_fields(adv):
    now = datetime.now(TZ)
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


def compose(adv, ufs):
    ty = adv.get("typhoon", {}) or {}
    prob = round((ty.get("invade_prob") or 0) * 100)
    lines = [f"🌀 {ty.get('name','颱風')}｜雲林侵襲機率 {prob}%｜{ty.get('eta_text','')}".rstrip("｜")]
    for f, why in ufs:
        tag = {"HARVEST_NOW": "🔴", "HARVEST_ADVISED": "🟠"}.get(f["decision"], "🟡")
        line = f"{tag} {f['field_id']} {f['crop']}｜{why}｜風險 {f['risk_score']}"
        if f.get("dilemma"):
            d = f["dilemma"]
            line += f"（距安全採收期還差 {d['days_short']} 天，收了不可上市）"
        lines.append(line)
    sch = adv.get("schedule")
    if sch and sch.get("summary"):
        lines.append("🗂 " + sch["summary"].replace("<b>", "").replace("</b>", ""))
    lines.append("— 二崙葉菜搶收預判室")
    return "\n".join(lines)


def _post(url, payload, headers):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def send_line(token, to, text):
    return _post("https://api.line.me/v2/bot/message/push",
                 {"to": to, "messages": [{"type": "text", "text": text}]},
                 {"Authorization": f"Bearer {token}"})


def send_webhook(url, text):
    return _post(url, {"text": text}, {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--advisory", default="harvest_advisory.json")
    ap.add_argument("--state", default="notify_state.json")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.advisory):
        print(f"[notify] 找不到 {args.advisory}，略過。", file=sys.stderr)
        return
    with open(args.advisory, encoding="utf-8") as f:
        adv = json.load(f)

    if not (adv.get("typhoon") or {}).get("active"):
        print("[notify] 無颱風警報，略過通知。")
        return

    ufs = urgent_fields(adv)
    if not ufs:
        print("[notify] 無急迫田區，略過通知。")
        return

    sig = signature(ufs)
    prev = None
    if os.path.exists(args.state):
        try:
            prev = json.load(open(args.state, encoding="utf-8")).get("sig")
        except Exception:
            prev = None
    if sig == prev and not args.force:
        print(f"[notify] 急迫田集合未變化（{len(ufs)} 田），略過重複通知。")
        return

    text = compose(adv, ufs)

    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    to = os.environ.get("LINE_TO")
    webhook = os.environ.get("NOTIFY_WEBHOOK_URL")
    sent = []
    try:
        if token and to:
            send_line(token, to, text)
            sent.append("LINE")
        if webhook:
            send_webhook(webhook, text)
            sent.append("webhook")
    except Exception as e:
        print(f"[notify] 推播失敗：{e}", file=sys.stderr)
        sys.exit(1)

    if sent:
        print(f"[notify] 已推播（{'/'.join(sent)}），{len(ufs)} 個急迫田。")
    else:
        print("[notify] 未設定任何通知管道（dry-run），訊息內容如下：\n" + "-" * 40 + f"\n{text}\n" + "-" * 40)

    with open(args.state, "w", encoding="utf-8") as f:
        json.dump({"sig": sig, "updated": datetime.now(TZ).isoformat(),
                   "urgent_count": len(ufs)}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
