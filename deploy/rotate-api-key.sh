#!/usr/bin/env bash
# ==============================================================================
# 家肴记 P0-4 · 生产环境 API Key 轮换 / 泄漏清理脚本
#
# 用途：
#   1) 扫描服务器上所有可能残留 DashScope(阿里云百炼) Key 的位置（systemd 单元、
#      EnvironmentFile、/etc/environment、仓库 .env 等），按 sha256 判断是否命中
#      「已泄漏 Key」（脚本只输出指纹，绝不打印 Key 原文）；
#   2) 一键把已泄漏 Key 清空（自动备份原文件）；
#   3) 把新 Key 写入仓库之外的 systemd drop-in
#      /etc/systemd/system/jiayaoji.service.d/20-api-keys.conf（600 权限），
#      重启服务后自动验证；
#   4) 验证旧 Key 是否 401、线上前端产物是否还有 sk- 明文。
#
# 用法（在服务器上、用 root 执行；建议先跑 --check 再动手）：
#   sudo bash deploy/rotate-api-key.sh --check
#   sudo bash deploy/rotate-api-key.sh --remove      # 清掉已泄漏 Key
#   sudo bash deploy/rotate-api-key.sh --set          # 交互输入新 Key（推荐，粘贴不回显）
#   sudo bash deploy/rotate-api-key.sh --set-file /tmp/newkey.txt
#   sudo bash deploy/rotate-api-key.sh --verify       # 只做验证，不改配置
#
# 可选环境变量：
#   SERVICE=jiayaoji         systemd 服务名
#   REPO=/var/www/jiayaoji   仓库路径
#   SITE=https://jiayaoji.cn 线上站点（验收断言前端无 sk- 明文）
# ==============================================================================

set -uo pipefail

SERVICE="${SERVICE:-jiayaoji}"
REPO="${REPO:-/var/www/jiayaoji}"
SITE="${SITE:-https://jiayaoji.cn}"
DROPIN_DIR="/etc/systemd/system/${SERVICE}.service.d"
DROPIN_FILE="${DROPIN_DIR}/20-api-keys.conf"

# 已泄漏的阿里云 DashScope Key 指纹（V331 前曾明文出现在前端；2026-11-02 实测已 401）
LEAKED_SHA256="0eb268714d6489b5e0204ba8060e3590e38676ce75a9e3b5c605696460ee4137"

MODE="--check"
case "${1:-}" in
    --check|--remove|--set|--verify) MODE="$1" ;;
    --set-file) MODE="--set-file"; NEWKEY_FILE="${2:-}" ;;
    -h|--help|"") MODE="--help" ;;
    *) echo "未知参数：$1（可用：--check / --remove / --set / --set-file <文件> / --verify）"; exit 2 ;;
esac

log()  { printf '%s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }
die()  { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

[ "$MODE" = "--help" ] && { sed -n '1,40p' "$0"; exit 0; }
[ "$(id -u)" -ne 0 ] && die "请用 root 执行：sudo bash $0 $MODE"

hash_of() { printf '%s' "$1" | sha256sum | awk '{print $1}'; }
is_leaked() { [ "$(hash_of "$1")" = "$LEAKED_SHA256" ]; }

# 收集候选文件：仓库 .env、systemd 主单元 + drop-in、EnvironmentFile、常见全局环境文件
collect_files() {
    local f
    for f in "$REPO/backend/.env" "$REPO/.env" "/etc/default/$SERVICE" "/etc/sysconfig/$SERVICE" \
             "/etc/environment" "/etc/profile" "/root/.bashrc" "/root/.profile"; do
        [ -f "$f" ] && printf '%s\n' "$f"
    done
    [ -d "$DROPIN_DIR" ] && find "$DROPIN_DIR" -type f -name '*.conf' -print
    # systemd 主单元路径（内联 Environment= 只会出现在这里）
    if command -v systemctl >/dev/null 2>&1; then
        systemctl show -p FragmentPath "$SERVICE" 2>/dev/null | cut -d= -f2- | tr -d '"' | while read -r p; do
            [ -n "$p" ] && [ -f "$p" ] && printf '%s\n' "$p"
        done
        # systemd 单元声明的 EnvironmentFile= 路径（去掉 systemd 的 "-" 前缀）
        systemctl cat "$SERVICE" 2>/dev/null | awk -F'=' '/^EnvironmentFile=/ {print $2}' | tr -d '"' | while read -r p; do
            p="${p#-}"
            [ -n "$p" ] && [ -f "$p" ] && printf '%s\n' "$p"
        done
    fi
}

# 扫描一个文件里的 sk- 令牌；输出 file:line fp=xxxxxx hit=LEAKED|other
scan_file() {
    local file="$1" found=0 leaked=0 line tok fp
    [ -f "$file" ] || return 0
    while IFS= read -r line; do
        for tok in $(printf '%s' "$line" | grep -oE 'sk-[A-Za-z0-9_-]{16,}' || true); do
            found=$((found + 1))
            fp="$(hash_of "$tok" | cut -c1-8)"
            if is_leaked "$tok"; then
                leaked=$((leaked + 1))
                log "  [$file] fp=$fp  <== 命中【已泄漏 Key】"
            else
                log "  [$file] fp=$fp  (其他服务商 Key，仅提示)"
            fi
        done
    done < "$file"
    [ "$found" -eq 0 ] && log "  [$file] 无 sk- 令牌"
    return 0
}

do_check() {
    log "=== 1) 扫描 Key 残留（只显示指纹，不显示原文）==="
    local files; files="$(collect_files | sort -u)"
    if [ -z "$files" ]; then warn "未找到任何候选文件，请确认 REPO / SERVICE 是否正确"; fi
    local f; while IFS= read -r f; do [ -n "$f" ] && scan_file "$f"; done <<< "$files"

    log ""
    log "=== 2) 当前服务是否带 DashScope Key（值已脱敏）==="
    if command -v systemctl >/dev/null 2>&1; then
        systemctl show -p Environment "$SERVICE" 2>/dev/null | tr ' ' '\n' \
            | grep -E '(DASHSCOPE|ALIYUN)_API_KEY=' \
            | sed -E 's/=sk-[A-Za-z0-9_-]+/=<已配置:sk-****>/; s/=(.*)/=<已配置>/' \
            || log "  （服务环境里未声明 DASHSCOPE/ALIYUN Key）"
    fi

    log ""
    log "=== 3) 线上前端产物是否还有 sk- 明文 ==="
    local c1 c2
    c1="$(curl -fsS --max-time 15 "$SITE/app" 2>/dev/null | grep -c 'sk-' || true)"
    c2="$(curl -fsS --max-time 15 "$SITE/index.html" 2>/dev/null | grep -c 'sk-' || true)"
    log "  $SITE/app        sk- 命中：${c1:-0}"
    log "  $SITE/index.html sk- 命中：${c2:-0}"
    [ "${c1:-0}" = "0" ] && [ "${c2:-0}" = "0" ] && log "  => 通过（无明文 Key）" || warn "线上产物疑似含 sk- 明文，请立即排查"

    log ""
    log "=== 4) 结论 ==="
    log "  若上面出现「命中【已泄漏 Key】」=> 执行：sudo bash $0 --remove"
    log "  需要启用百炼 => 先在阿里云控制台新建 Key，再执行：sudo bash $0 --set"
}

do_remove() {
    log "=== 清除已泄漏 Key（自动备份原文件）==="
    local files; files="$(collect_files | sort -u)"
    local f changed=0
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        local hits
        hits="$(grep -oE 'sk-[A-Za-z0-9_-]{16,}' "$f" 2>/dev/null | while read -r t; do is_leaked "$t" && echo "$t"; done || true)"
        if [ -n "$hits" ]; then
            local bak="${f}.bak-$(date +%Y%m%d%H%M%S)"
            cp -a "$f" "$bak" && log "  备份：$bak"
            while IFS= read -r t; do
                [ -n "$t" ] && sed -i "s|${t}||g" "$f"
            done <<< "$hits"
            log "  已清空泄漏 Key：$f（变量名保留、值置空）"
            changed=$((changed + 1))
        fi
    done <<< "$files"

    if [ "$changed" -eq 0 ]; then
        log "  未发现已泄漏 Key，无需清理"
    else
        command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload
        log "  已 daemon-reload；下面重启服务并验证"
        do_restart_and_verify
    fi
}

write_new_key() {
    local newkey="$1"
    [ -n "$newkey" ] || die "新 Key 为空"
    printf '%s' "$newkey" | grep -qE '^sk-[A-Za-z0-9_-]{16,}$' || die "新 Key 格式不合法（应以 sk- 开头且足够长）"
    is_leaked "$newkey" && die "这个 Key 就是已泄漏的那一把，请到阿里云控制台重新创建"

    install -d -m 755 "$DROPIN_DIR"
    umask 077
    # 直接写进 systemd drop-in 的 Environment=（优先级高于主单元里的同名空值），
    # 文件在 /etc 下、权限 600，不进仓库、也不散落在业务 .env 里
    cat > "$DROPIN_FILE" <<EOF
# 家肴记 P0-4：API Key 只放服务器本地 systemd drop-in，禁止进仓库
[Service]
Environment="DASHSCOPE_API_KEY=${newkey}"
Environment="ALIYUN_API_KEY=${newkey}"
EOF
    chmod 600 "$DROPIN_FILE"
    log "  新 Key 已写入 $DROPIN_FILE（权限 600，不在仓库内）"
}

read_new_key() {
    local k
    if [ -t 0 ]; then
        read -rsp "请粘贴新的 DashScope Key（回车确认，输入不回显）：" k; echo ""
    else
        read -r k
    fi
    printf '%s' "$k"
}

do_set() {
    local k
    if [ "$MODE" = "--set-file" ]; then
        [ -f "${NEWKEY_FILE:-}" ] || die "找不到 Key 文件：${NEWKEY_FILE:-}"
        k="$(tr -d ' \r\n\t' < "$NEWKEY_FILE")"
        rm -f "${NEWKEY_FILE:-}" && log "  已删除临时 Key 文件 ${NEWKEY_FILE:-}"
    else
        k="$(read_new_key)"
    fi
    write_new_key "$k"
    do_restart_and_verify
}

do_restart_and_verify() {
    local skip_restart="${1:-}"
    log ""
    log "=== 重启服务并验证 ==="
    if [ -z "$skip_restart" ]; then
        systemctl restart "$SERVICE" || die "systemctl restart $SERVICE 失败"
        sleep 3
    else
        log "  （--verify 模式：不重启，只检查当前状态）"
    fi

    log "  1) 本地健康检查："
    curl -fsS --max-time 10 http://127.0.0.1:8012/health && echo "" || warn "/health 无响应，请 systemctl status $SERVICE 查看日志"

    log "  2) 服务环境里的 Key（脱敏）："
    systemctl show -p Environment "$SERVICE" 2>/dev/null | tr ' ' '\n' | grep -E 'DASHSCOPE|ALIYUN' \
        | sed -E 's/=sk-[A-Za-z0-9_-]+/=<新 Key 已生效>/' || log "    （未读到，检查 $DROPIN_FILE）"

    log "  3) 新 Key 直连 DashScope（只看状态码）："
    local k2 status
    k2="$(grep -oE 'sk-[A-Za-z0-9_-]{16,}' "$DROPIN_FILE" 2>/dev/null | head -1 || true)"
    if [ -n "$k2" ]; then
        status="$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 \
            -H "Authorization: Bearer $k2" -H "Content-Type: application/json" \
            -d '{"model":"qwen-turbo","messages":[{"role":"user","content":"ping"}],"max_tokens":1}' \
            https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions)"
        log "    DashScope HTTP $status （200/201=可用；401=Key 无效）"
        [ "$status" = "401" ] && warn "新 Key 被判 401，请确认控制台里该 Key 已启用且属于百炼/DashScope"
    else
        log "    未配置新 Key（$DROPIN_FILE 不存在或为空）"
    fi

    log "  4) 旧泄漏 Key 复核（期望 401）："
    local oldtok
    oldtok="$(collect_files | sort -u | while read -r f; do grep -ohE 'sk-[A-Za-z0-9_-]{16,}' "$f" 2>/dev/null | while read -r t; do is_leaked "$t" && echo "$t"; done; done | head -1)"
    if [ -z "$oldtok" ]; then
        log "    服务器上已找不到旧 Key，跳过（此前实测已 401）"
    else
        status="$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 \
            -H "Authorization: Bearer $oldtok" -H "Content-Type: application/json" \
            -d '{"model":"qwen-turbo","messages":[{"role":"user","content":"ping"}],"max_tokens":1}' \
            https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions)"
        log "    旧 Key HTTP $status"$([ "$status" = "401" ] && echo "  => 已失效，符合预期" || echo "  => 仍可用！请立刻在阿里云控制台删除")
    fi

    log "  5) 线上前端无 sk- 明文："
    local c1 c2
    c1="$(curl -fsS --max-time 15 "$SITE/app" 2>/dev/null | grep -c 'sk-' || true)"
    c2="$(curl -fsS --max-time 15 "$SITE/index.html" 2>/dev/null | grep -c 'sk-' || true)"
    log "    $SITE/app=${c1:-0}  $SITE/index.html=${c2:-0}"

    log ""
    log "完成。若第 2 步没读到 Key，检查 $DROPIN_FILE 是否存在、"
    log "主单元里是否有同名的 Environment= 空值（有则删掉该行，再 daemon-reload + restart）。"
}

case "$MODE" in
    --check)  do_check ;;
    --remove) do_remove ;;
    --set|--set-file) do_set ;;
    --verify) do_restart_and_verify "skip-restart" ;;
esac
