# -*- coding: utf-8 -*-
"""V143 Windows 定时任务入口：每天 22:30 调本机食养堂自动发布流水线。

等价于服务器端的 systemd timer（deploy/jiayaoji-foodhall.timer）。
仅用标准库，免装依赖：

    python auto_publish.py [城市]

注册 Windows 任务计划（每天 22:30，用户登录时运行即可）：
    schtasks /create /tn "JiaYaoJi-FoodHall" /f /sc daily /st 22:30 ^
      /tr "\"C:\\Users\\HUAWEI\\AppData\\Local\\Programs\\Python\\Python314\\python.exe\" \"d:\\jiayaoji\\backend\\auto_publish.py\" 杭州"

城市缺省取环境变量 JIAYAOJI_CITY，再缺省为"杭州"。
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("JIAYAOJI_BASE_URL", "http://127.0.0.1:8012").rstrip("/")
DATA_DIR = Path(__file__).resolve().parent / "data"
CITY = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("JIAYAOJI_CITY", "杭州")


def main() -> int:
    token_file = DATA_DIR / ".internal_token"
    if not token_file.exists():
        print(f"[foodhall] 令牌文件不存在（请先启动一次后端）：{token_file}", file=sys.stderr)
        return 2
    token = token_file.read_text(encoding="utf-8").strip()

    body = json.dumps({"city": CITY}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + "/api/food-hall/auto-publish",
        data=body,
        headers={"Content-Type": "application/json", "X-Internal-Token": token},
        method="POST",
    )
    try:
        # 流水线含多轮 AI 调用（生成 + 至多 3 篇弱信号复审），给足 9 分钟
        with urllib.request.urlopen(req, timeout=560) as resp:
            print(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[foodhall] 自动发布调用失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
