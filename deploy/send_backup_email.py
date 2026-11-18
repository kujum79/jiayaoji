#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V567: 把每日备份压缩包作为附件发送到邮箱（按发件域名自动选择 SMTP 主机，465 SSL）。

配置来源（优先级：环境变量 > /etc/jiayaoji/backup-email.env）：
    BACKUP_EMAIL_TO    收件邮箱
    BACKUP_EMAIL_USER  发件邮箱（同时作为 SMTP 账号，如 xxx@163.com / xxx@qq.com）
    BACKUP_EMAIL_PASS  发件邮箱的客户端授权码（不是登录密码）
    BACKUP_SMTP_HOST   可选，强制指定 SMTP 服务器（默认按域名自动识别）
cron 不继承 systemd 的 EnvironmentFile，因此独立 env 文件是主路径，
文件权限须为 600 且属主为运行备份的 admin 用户。

用法：send_backup_email.py [归档绝对路径]
本脚本任何失败只打印日志、恒以退出码 0 结束，绝不影响 backup.sh 的备份结果。
"""
import os
import smtplib
import sys
import time
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

ENV_FILE = "/etc/jiayaoji/backup-email.env"
# 按发件邮箱域名自动选择 SMTP 服务器（465 SSL）；可用 BACKUP_SMTP_HOST 强制覆盖
SMTP_HOSTS = {
    "qq.com": "smtp.qq.com",
    "foxmail.com": "smtp.qq.com",
    "163.com": "smtp.163.com",
    "126.com": "smtp.126.com",
    "yeah.net": "smtp.yeah.net",
}


def resolve_smtp(user):
    host = os.getenv("BACKUP_SMTP_HOST", "").strip()
    if not host:
        dom = user.rsplit("@", 1)[-1].lower()
        host = SMTP_HOSTS.get(dom) or ("smtp." + dom)
    try:
        port = int(os.getenv("BACKUP_SMTP_PORT", "465") or 465)
    except ValueError:
        port = 465
    return host, port


def log(msg):
    print("[%s] [backup-email] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))


def load_env_file():
    """env 文件中未设置的键从文件兜底加载（已存在的环境变量优先）。"""
    keys = ("BACKUP_EMAIL_TO", "BACKUP_EMAIL_USER", "BACKUP_EMAIL_PASS")
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if k in keys and not os.environ.get(k) and v:
                    os.environ[k] = v
    except FileNotFoundError:
        pass
    except Exception as e:
        log("读取 %s 失败: %s" % (ENV_FILE, e))


def main():
    archive = sys.argv[1] if len(sys.argv) > 1 else \
        "/var/backups/jiayaoji/jiayaoji_backup_%s.tar.gz" % time.strftime("%Y%m%d")

    load_env_file()
    to = os.getenv("BACKUP_EMAIL_TO", "").strip()
    user = os.getenv("BACKUP_EMAIL_USER", "").strip()
    password = os.getenv("BACKUP_EMAIL_PASS", "").strip()
    if not (to and user and password):
        log("BACKUP_EMAIL_TO/USER/PASS 未配置齐全，跳过邮件发送")
        return
    if not os.path.isfile(archive):
        log("归档不存在，跳过邮件发送: %s" % archive)
        return

    name = os.path.basename(archive)
    size_mb = os.path.getsize(archive) / 1048576.0
    msg = MIMEMultipart()
    msg["Subject"] = "家肴记每日备份 %s" % time.strftime("%Y-%m-%d")
    msg["From"] = formataddr(("家肴记备份", user))
    msg["To"] = to
    body = (
        "备份时间：%s\n"
        "归档文件：%s\n"
        "文件大小：%.2f MB\n"
        "服务器保留策略：最近 7 天\n\n"
        "附件为 jiayaoji.db 与 family_backups/ 的 gzip 打包，"
        "恢复方法：tar -xzf 归档 -C /var/www/jiayaoji/backend/data/\n"
        "—— 本邮件由 backup.sh 自动发送"
    ) % (time.strftime("%Y-%m-%d %H:%M:%S"), name, size_mb)
    msg.attach(MIMEText(body, "plain", "utf-8"))
    with open(archive, "rb") as f:
        part = MIMEApplication(f.read(), Name=name)
    part["Content-Disposition"] = 'attachment; filename="%s"' % name
    msg.attach(part)

    host, port = resolve_smtp(user)
    try:
        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            s.login(user, password)
            s.sendmail(user, [to], msg.as_string())
        log("邮件发送成功: %s → %s（%s:%d，附件 %.2f MB）" % (user, to, host, port, size_mb))
    except smtplib.SMTPAuthenticationError as e:
        log("SMTP 认证失败（请确认 BACKUP_EMAIL_PASS 是邮箱「客户端授权密码/授权码」而非登录密码）: %s" % e)
    except (smtplib.SMTPException, OSError) as e:
        log("SMTP 发送失败: %s: %s" % (type(e).__name__, e))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("邮件发送异常（不影响备份结果）: %s: %s" % (type(e).__name__, e))
    sys.exit(0)
