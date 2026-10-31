"""
http_retry.py —— 抓外部開放資料用的「指數退避重試」小工具（精準務農／精準進貨共用同一份內容）。

為什麼需要：農業部、氣象署 API 在早上開盤、颱風期間很常逾時或回 5xx。以前一段失敗就直接略過，
結果把「少一季資料」的檔案當成正確資料寫出去（K 線缺一段、90 日中位數失真）。

規則：
- 逾時、連線中斷、回應被截斷、429、5xx → 等 2/4/8/16 秒（加一點隨機抖動）再試，最多重試 4 次；
  伺服器有給 Retry-After 就照它（上限 60 秒）。
- 其他 4xx（參數錯、權限錯）重試也不會好 → 直接拋出，讓呼叫端記錄失敗。
- 200 但內容不是 JSON（例如維護頁 HTML、傳到一半斷掉）也視為暫時性錯誤重試。
- 錯誤訊息一律遮蔽網址裡的 Authorization／key／token 參數，避免金鑰出現在公開的 Actions 記錄。
"""
import random
import re
import sys
import time

import requests

RETRY_STATUS = {429, 500, 502, 503, 504}
DELAYS = (2, 4, 8, 16)
MAX_RETRY_AFTER = 60

_SECRET_QS = re.compile(r"((?:Authorization|api[_-]?key|key|token|access_token)=)[^&\s'\"]+", re.I)


def redact(text):
    """把網址查詢字串中的金鑰換成 ***（錯誤訊息常帶完整網址）。"""
    return _SECRET_QS.sub(r"\1***", str(text))


class RetryableHTTPError(Exception):
    """429/5xx 或內容不是 JSON：值得重試的錯誤。"""


def _retry_after(resp):
    try:
        v = float(resp.headers.get("Retry-After", ""))
        return max(0.0, min(v, MAX_RETRY_AFTER))
    except (TypeError, ValueError):
        return None


def get_json(url, *, params=None, headers=None, timeout=60, delays=DELAYS,
             sleep=time.sleep, getter=None, label=""):
    """GET 並解析 JSON；暫時性錯誤依 delays 重試。全部失敗時拋出最後一個錯誤（訊息已遮蔽金鑰）。"""
    get = getter or requests.get
    name = label or redact(url)
    last = None
    for attempt in range(len(delays) + 1):
        wait = None
        try:
            r = get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code in RETRY_STATUS:
                wait = _retry_after(r)
                raise RetryableHTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()          # 其他 4xx：不重試，直接往外丟
            try:
                return r.json()
            except ValueError as e:       # 200 但不是 JSON（維護頁／截斷）
                raise RetryableHTTPError(f"回應不是 JSON：{e}") from e
        except (requests.Timeout, requests.ConnectionError,
                requests.exceptions.ChunkedEncodingError, RetryableHTTPError) as e:
            last = e
        except requests.HTTPError as e:
            raise requests.HTTPError(redact(e), response=getattr(e, "response", None)) from None
        if attempt == len(delays):
            break
        d = wait if wait is not None else delays[attempt] * (1 + random.uniform(0, 0.25))
        print(f"[retry] {name} 第 {attempt + 1} 次失敗（{redact(last)}），{d:.0f} 秒後重試",
              file=sys.stderr)
        sleep(d)
    raise RuntimeError(f"{name} 重試 {len(delays)} 次仍失敗：{redact(last)}")
