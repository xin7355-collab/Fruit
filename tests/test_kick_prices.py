"""scripts/kick_prices.sh：行情排程補位。用假的 gh 指令（不連網）檢查什麼時候該補、什麼時候不該補。"""
import os
import subprocess
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "kick_prices.sh")


def ts(s):   # 台灣時間字串 → epoch
    return int(datetime.fromisoformat(s + "+08:00").timestamp())


def utc(s):
    return datetime.fromtimestamp(ts(s), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(tmp_path, now, last=None, list_fails=False):
    log = tmp_path / "gh.log"
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\n"
                  f'echo "$@" >> "{log}"\n'
                  'case "$1 $2" in\n'
                  f'  "run list") {"exit 1" if list_fails else "printf %s " + repr(last or "")} ;;\n'
                  '  "workflow run") exit 0 ;;\n'
                  'esac\n')
    gh.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "GITHUB_REPOSITORY": "o/r",
           "GH_TOKEN": "x", "KICK_NOW": str(ts(now))}
    r = subprocess.run(["bash", SCRIPT, "update-prices.yml"], env=env, capture_output=True, text=True)
    calls = log.read_text().splitlines() if log.exists() else []
    return r, [c for c in calls if c.startswith("workflow run")]


@pytest.mark.parametrize("now", ["2026-11-01T04:30:00", "2026-11-01T13:10:00", "2026-11-01T15:00:00", "2026-11-01T23:00:00"])
def test_off_hours_never_kick(tmp_path, now):
    r, kicks = run(tmp_path, now, "completed " + utc("2026-10-31T19:00:00"))
    assert r.returncode == 0 and not kicks and "略過" in r.stdout


def test_old_run_gets_kicked(tmp_path):
    r, kicks = run(tmp_path, "2026-11-01T06:40:00", "completed " + utc("2026-10-31T19:11:00"))
    assert r.returncode == 0 and kicks == ["workflow run update-prices.yml -R o/r --ref main"]


def test_recent_run_not_kicked(tmp_path):
    r, kicks = run(tmp_path, "2026-11-01T06:40:00", "completed " + utc("2026-11-01T06:07:00"))
    assert not kicks and "跑過" in r.stdout


@pytest.mark.parametrize("st", ["queued", "in_progress", "waiting"])
def test_running_not_kicked_again(tmp_path, st):
    r, kicks = run(tmp_path, "2026-11-01T09:00:00", f"{st} " + utc("2026-11-01T06:00:00"))
    assert not kicks and "不重複" in r.stdout


def test_no_history_kicks(tmp_path):
    r, kicks = run(tmp_path, "2026-11-01T05:20:00", "")
    assert len(kicks) == 1


def test_api_failure_warns_not_fails(tmp_path):
    r, kicks = run(tmp_path, "2026-11-01T06:40:00", list_fails=True)
    assert r.returncode == 0 and not kicks and "::warning::" in r.stdout
