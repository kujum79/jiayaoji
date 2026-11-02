#!/usr/bin/env bash
# 家肴记 P0-5 · 路由冒烟（10 条「无 /api 前缀」的后端路由）
#
# 背景：D6-08 曾因 nginx 未代理这些路径，导致线上 POST 全部 405、GET 全部落回 index.html；
#       修复只在服务器上做过，换环境/重装即丢。本脚本用于每次发布后一键断言。
#
# 用法（在服务器或本地都可跑，只需要 curl）：
#   bash deploy/smoke-routes.sh                          # 默认打生产 https://jiayaoji.cn
#   BASE=https://jiayaoji.cn bash deploy/smoke-routes.sh
#   BASE=http://127.0.0.1:8000 bash deploy/smoke-routes.sh   # 绕开 nginx，只验后端
#
# 退出码：0 全部通过；1 有失败项（发布应视为失败并回滚）。
set -u

BASE="${BASE:-https://jiayaoji.cn}"
TIMEOUT="${TIMEOUT:-20}"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

pass=0
fail=0

# check <名称> <方法> <路径> [curl 额外参数...]
# 断言：① 不是 404（说明 nginx 没代理）；② 响应不是 HTML（说明落回了 index.html）；
#       ③ Content-Type 是 JSON / 图片（后端真的处理了）
check() {
    local name="$1" method="$2" path="$3"
    shift 3
    local hdr="$TMPDIR/h" body="$TMPDIR/b"
    local code ct first

    # shellcheck disable=SC2086
    code=$(curl -sS -m "$TIMEOUT" -o "$body" -D "$hdr" -w '%{http_code}' -X "$method" "$@" "$BASE$path" 2>/dev/null) || code="000"
    ct=$(tr -d '\r' <"$hdr" 2>/dev/null | awk 'BEGIN{IGNORECASE=1}/^content-type:/{print $2; exit}')
    ct="${ct:--}"

    local ok=1 why=""
    if [ "$code" = "000" ]; then ok=0; why="连接失败/超时"
    elif [ "$code" = "404" ]; then ok=0; why="404：nginx 未代理该路径"
    elif [ "$code" = "405" ] && [ "${ct#text/html}" != "$ct" ]; then ok=0; why="405 + HTML：落回静态页"
    elif grep -qi '<html' "$body" 2>/dev/null; then ok=0; why="响应是 HTML（落回 index.html）"
    elif ! echo "$ct" | grep -qiE 'json|image/|audio/'; then ok=0; why="Content-Type 非 JSON/图片（$ct）"
    fi

    if [ "$ok" = "1" ]; then
        pass=$((pass + 1))
        printf '  [PASS] %-24s %-4s %-22s HTTP %s  %s\n' "$name" "$method" "$path" "$code" "$ct"
    else
        fail=$((fail + 1))
        printf '  [FAIL] %-24s %-4s %-22s HTTP %s  %s\n' "$name" "$method" "$path" "$code" "$why"
    fi
}

echo "路由冒烟：$BASE（单次超时 ${TIMEOUT}s）"

# 用「空参 / 缺参」触发 FastAPI 的 422 JSON 校验错误——既能证明路由存在且被代理，
# 又不会真的消耗 AI 额度（/plan、/recommend-dishes 等真实调用不在此脚本内）。
check "/plan"                 POST /plan
check "/recommend-dishes"     POST /recommend-dishes
check "/recommend-restaurants" POST /recommend-restaurants
check "/parse-menu-image"     POST /parse-menu-image
check "/parse-menu-url"       POST /parse-menu-url
check "/recognize_ingredients" POST /recognize_ingredients
# P0-4: 前端配图已改走后端 /api/chef-image（后端不存在 /chef-image，GET 会落回 index.html）
check "/api/chef-image"       POST /api/chef-image
check "/ai/usage"             GET  "/ai/usage"
check "/ai/budget"            GET  "/ai/budget"
check "/ai/vision"            POST /ai/vision

# 附带检查：前端入口可达，并打印线上版本号（版本没刷新时一眼能看出来）
app_code=$(curl -sS -m "$TIMEOUT" -o "$TMPDIR/app" -w '%{http_code}' "$BASE/app" 2>/dev/null) || app_code="000"
app_ver=$(grep -oE "APP_VER *= *'V[0-9]+-[0-9]+'" "$TMPDIR/app" 2>/dev/null | head -1 | grep -oE "V[0-9]+-[0-9]+")
if [ "$app_code" = "200" ] && [ -n "$app_ver" ]; then
    printf '  [PASS] %-24s %-4s %-22s HTTP %s  %s\n' "/app" "GET" "/app" "$app_code" "$app_ver"
    pass=$((pass + 1))
else
    printf '  [FAIL] %-24s %-4s %-22s HTTP %s  %s\n' "/app" "GET" "/app" "$app_code" "入口不可达或读不到版本号"
    fail=$((fail + 1))
fi

echo "结果：通过 $pass / 失败 $fail"
[ "$fail" = "0" ] || exit 1
