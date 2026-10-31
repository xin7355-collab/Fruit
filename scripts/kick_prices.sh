#!/usr/bin/env bash
# 行情排程補位：GitHub 的 schedule 常被延遲或整個丟掉（2026-10-31 實測：天氣每 30 分的排程 10 小時只跑 5 次，
# 早上行情 05:07／05:37 都沒跑）。天氣工作流比較常被排到，就由它在行情時段檢查一下：
# 行情工作流超過 55 分鐘沒跑、也沒有正在跑或排隊 → 用 workflow_dispatch 補一次。
# GITHUB_TOKEN 觸發 workflow_dispatch 是 GitHub 允許的例外，不需要另外建權杖。
# 用法：kick_prices.sh <工作流檔名>；需要環境變數 GH_TOKEN、GITHUB_REPOSITORY。補位失敗只警告，不讓天氣更新變紅燈。
set -u
WF="${1:?要給工作流檔名，例如 update-prices.yml}"
NOW="${KICK_NOW:-$(date +%s)}"                     # 測試用：可指定「現在」
h=$(TZ=Asia/Taipei date -d "@$NOW" +%-H)
if [ "$h" -lt 5 ] || [ "$h" -gt 14 ] || [ "$h" -eq 13 ]; then
  echo "台灣 ${h} 時不是行情更新時段（05–12、14 時），略過補位"; exit 0
fi
if ! last=$(gh run list -R "$GITHUB_REPOSITORY" -w "$WF" -L 1 --json status,createdAt -q '.[0] | "\(.status) \(.createdAt)"'); then
  echo "::warning::查不到 $WF 的執行紀錄（GitHub API 失敗），這次不補位，下次天氣排程再查"; exit 0
fi
status="${last%% *}"; created="${last#* }"
case "$status" in
  queued|in_progress|waiting|pending|requested)
    echo "$WF 正在跑或排隊中（$status），不重複觸發"; exit 0 ;;
esac
age=$(( NOW - $(date -d "${created:-1970-01-01T00:00:00Z}" +%s) ))
if [ "$age" -lt 3300 ]; then
  echo "$WF $((age / 60)) 分鐘前跑過，不用補"; exit 0
fi
echo "$WF 已 $((age / 60)) 分鐘沒跑（GitHub 排程漏掉），補觸發一次"
gh workflow run "$WF" -R "$GITHUB_REPOSITORY" --ref main \
  || echo "::warning::補觸發 $WF 失敗：請到 Actions 頁手動按 Run workflow，或檢查工作流權限是否有 actions: write"
exit 0
