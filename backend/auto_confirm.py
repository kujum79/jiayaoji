# -*- coding: utf-8 -*-
"""V145 Windows 定时任务入口：每天 06:00 调本机食养堂保底自动确认。

等价于服务器端的 systemd timer（deploy/jiayaoji-foodhall-confirm.timer）。
仅用标准库，免装依赖：

    python auto_confirm.py

注册 Windows 任务计划（每天 06:00，用户登录时运行即可）：
    schtasks /create /tn "JiaYaoJi-FoodHallConfirm" /f /sc daily /st 06:00 ^
      /tr "\"C:\\Users\\HUAWEI\\AppData\\Local\\Programs\\Python\\Python314\\python.exe\" \"d:\\jiayaoji\\backend\\auto_confirm.py\""
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("JIAYAOJI_BASE_URL", "http://127.0.0.1:8012").rstrip("/")
DATA_DIR = Path(__file__).resolve().parent / "data"


def main() -> int:
    token_file = DATA_DIR / ".internal_token"
    if not token_file.exists():
        print(f"[foodhall-confirm] 令牌文件不存在（请先启动一次后端）：{token_file}", file=sys.stderr)
        return 2
    token = token_file.read_text(encoding="utf-8").strip()

    body = json.dumps({}).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + "/api/food-hall/auto-confirm",
        data=body,
        headers={"Content-Type": "application/json", "X-Internal-Token": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[foodhall-confirm] 保底确认调用失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
