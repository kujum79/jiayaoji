#!/bin/bash
# V566: 家肴记每日数据备份
# 备份 SQLite 主库 + 家庭备份快照目录，gzip 打包，保留 7 天
# crontab: 0 2 * * * /var/www/jiayaoji/deploy/backup.sh >> /var/log/jiayaoji_backup.log 2>&1
set -u

BACKUP_DIR="/var/backups/jiayaoji"
DATA_DIR="/var/www/jiayaoji/backend/data"
DATE=$(date +%Y%m%d)
ARCHIVE="$BACKUP_DIR/jiayaoji_backup_${DATE}.tar.gz"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

mkdir -p "$BACKUP_DIR"
log "开始备份 → $ARCHIVE"

# 若当天已有备份则覆盖（同一天多次手动执行时保持最新）
if tar -czf "$ARCHIVE" -C "$DATA_DIR" jiayaoji.db family_backups; then
    SIZE=$(du -h "$ARCHIVE" | cut -f1)
    log "备份成功: $ARCHIVE ($SIZE)"
else
    log "备份失败！请检查路径与磁盘空间（df -h）"
    exit 1
fi

# 完整性校验：列出归档条目数
COUNT=$(tar -tzf "$ARCHIVE" | wc -l)
log "归档包含 $COUNT 个文件条目"

# 清理 7 天前的旧备份
find "$BACKUP_DIR" -name 'jiayaoji_backup_*.tar.gz' -mtime +7 -print -delete | while read -r f; do
    log "已删除旧备份: $f"
done

# V568: 自动加载配置环境变量——cron 执行时无需任何手动 export。
# 依次尝试 api-keys.env（600 root，admin 不可读时静默跳过）与 backup-email.env（600 admin，邮件配置主来源）。
for _envf in /etc/jiayaoji/api-keys.env /etc/jiayaoji/backup-email.env; do
    if [ -r "$_envf" ]; then
        set -a; . "$_envf" 2>/dev/null || true; set +a
        log "已加载配置: $_envf"
    fi
done

# V567: 把归档作为附件发送到邮箱（脚本内部吞掉一切异常并恒返回 0，不影响备份结果）
python3 /var/www/jiayaoji/deploy/send_backup_email.py "$ARCHIVE" || log "邮件发送步骤异常（不影响备份）"

log "本次备份完成"
