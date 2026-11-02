#!/usr/bin/env bash
# 家肴记 P0-5 · 一键发布（双推 → 服务器拉取 → 重启 → 路由冒烟）
#
# 为什么必须双推：生产服务器拉的是 Gitee（gitee.com/jiayaoji/jiayaoji.git），
#   GitHub（github.com/kujum79/jiayaoji）只是备份远端。只推 GitHub 时线上不更新
#   （V344/V345 曾因只推 GitHub，导致生产滞留 V343，git pull 一直 Already up to date）。
#
# 用法：
#   bash deploy/release.sh                       # 只做本地双推 + 提示服务器命令
#   SRV=admin@47.97.178.39 bash deploy/release.sh   # 双推后自动 ssh 拉取并重启，再做冒烟
#   SKIP_SMOKE=1 bash deploy/release.sh          # 跳过冒烟（不建议）
#
# 环境变量：SRV（ssh 目标，空则不动服务器）、REPO（服务器仓库路径，默认 /var/www/jiayaoji）、
#           BRANCH（默认 main）、BASE（冒烟地址，默认 https://jiayaoji.cn）
set -euo pipefail

BRANCH="${BRANCH:-main}"
REPO="${REPO:-/var/www/jiayaoji}"
SRV="${SRV:-}"
BASE="${BASE:-https://jiayaoji.cn}"

cd "$(dirname "$0")/.."

ver=$(grep -oE "APP_VER *= *'V[0-9]+-[0-9]+'" frontend/index.html | head -1 | grep -oE "V[0-9]+-[0-9]+" || true)
echo "== 1/4 本地版本：${ver:-未识别} =="

if [ -n "$(git status --porcelain)" ]; then
    echo "工作区有未提交改动，发布必须先提交（git add / git commit），已中止。"
    git status --short
    exit 1
fi

echo "== 2/4 双推（origin + gitee） =="
git push origin "$BRANCH"
git push gitee "$BRANCH"

if [ -z "$SRV" ]; then
    echo "== 3/4 未设置 SRV，跳过服务器更新。请手动在服务器执行： =="
    echo "    cd $REPO && git pull --ff-only && sudo systemctl restart jiayaoji"
    echo "    systemctl is-active jiayaoji && curl -sS localhost:8000/health"
else
    echo "== 3/4 服务器更新（$SRV） =="
    ssh "$SRV" "cd $REPO && git pull --ff-only && sudo systemctl restart jiayaoji && sleep 2 && systemctl is-active jiayaoji && curl -sS -m 5 http://127.0.0.1:8000/health"
fi

if [ -z "${SKIP_SMOKE:-}" ]; then
    echo "== 4/4 路由冒烟（$BASE） =="
    BASE="$BASE" bash deploy/smoke-routes.sh
fi

echo "发布完成：线上版本应显示 ${ver:-未知}（浏览器刷新 /app 可核对）"
