#!/usr/bin/env bash
# V404 家肴记·食养堂「晨读」到点发布（饮食文章）
# 每天 06:30 由 systemd timer(jiayaoji-foodhall-confirm.timer) 调用：
#   - 已确认：跳过（幂等，不覆盖超管选择）
#   - 有候选池：审核后择优转 confirmed
#   - 没有候选：现场 AI 生成 3 篇再择优（AI 自动兜底发布）
# 只处理 diet_article，与 16:30 的食谱轨道互不干扰。
set -euo pipefail

APP_DIR="${JIAYAOJI_DIR:-/var/www/jiayaoji}"
BASE_URL="${JIAYAOJI_BASE_URL:-http://127.0.0.1:8000}"
TOKEN_FILE="$APP_DIR/backend/data/.internal_token"

if [ ! -r "$TOKEN_FILE" ]; then
  echo "[foodhall-confirm] 令牌文件不存在：$TOKEN_FILE" >&2
  exit 2
fi
TOKEN="$(cat "$TOKEN_FILE")"

# 无候选时会现场生成 3 篇（含 AI 调用），最长允许 9 分钟
curl -fsS --max-time 560 -X POST "$BASE_URL/api/food-hall/publish-due" \
  -H "X-Internal-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"contentType":"diet_article"}'

echo
