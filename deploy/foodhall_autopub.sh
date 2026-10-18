#!/usr/bin/env bash
# V143/V145 家肴记·食养堂自动备稿流水线
# 每天 22:30 由 systemd timer(jiayaoji-foodhall.timer) 调用：
# 生成明天的 3 篇候选 -> 合规审核 -> 打分选 1 篇 -> 入库为 pending，
# 次日 6:00 由 jiayaoji-foodhall-confirm.timer 保底转 confirmed，超管可在中间手动审。
# V145: 选题全国通用，不再传 city/weather。
set -euo pipefail

APP_DIR="${JIAYAOJI_DIR:-/var/www/jiayaoji}"
BASE_URL="${JIAYAOJI_BASE_URL:-http://127.0.0.1:8000}"
TOKEN_FILE="$APP_DIR/backend/data/.internal_token"

if [ ! -r "$TOKEN_FILE" ]; then
  echo "[foodhall] 令牌文件不存在：$TOKEN_FILE（后端启动时会自动生成，请先确认 jiayaoji 服务已重启）" >&2
  exit 2
fi
TOKEN="$(cat "$TOKEN_FILE")"

# 整个流水线含多轮 AI 调用，最长允许 9 分钟；失败时 curl 非零退出，systemd 记失败
curl -fsS --max-time 560 -X POST "$BASE_URL/api/food-hall/auto-publish" \
  -H "X-Internal-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  --data '{}'

echo
