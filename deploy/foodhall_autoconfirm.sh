#!/usr/bin/env bash
# V145 家肴记·食养堂保底自动确认
# 每天 06:00 由 systemd timer(jiayaoji-foodhall-confirm.timer) 调用：
# 把当天 publish_date 还是 pending 的备稿转为 confirmed，保证首页 6:00 后有内容。
set -euo pipefail

APP_DIR="${JIAYAOJI_DIR:-/var/www/jiayaoji}"
BASE_URL="${JIAYAOJI_BASE_URL:-http://127.0.0.1:8012}"
TOKEN_FILE="$APP_DIR/backend/data/.internal_token"

if [ ! -r "$TOKEN_FILE" ]; then
  echo "[foodhall-confirm] 令牌文件不存在：$TOKEN_FILE" >&2
  exit 2
fi
TOKEN="$(cat "$TOKEN_FILE")"

curl -fsS --max-time 30 -X POST "$BASE_URL/api/food-hall/auto-confirm" \
  -H "X-Internal-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  --data '{}'

echo
