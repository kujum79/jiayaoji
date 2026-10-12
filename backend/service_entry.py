# -*- coding: utf-8 -*-
"""
家肴记后端服务入口（方案A：开机自启 · 后台静默运行）

由 Windows 计划任务 "JiaYaoJi-Backend" 在用户登录时调用，
使用 pythonw.exe 运行（无控制台窗口），日志写入 backend_service.log。

手动启动仍然可用：双击项目根目录的 启动家肴记.bat（会自动跳过已运行实例）。
"""

import faulthandler
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from main import app

_LOG_PATH = Path(__file__).resolve().parent / "backend_service.log"

# pythonw 无控制台：sys.stdout/stderr 为 None，任何库写流都会直接崩进程。
# 统一重定向到文件，并用 faulthandler 抓原生崩溃堆栈。
_err = open(_LOG_PATH.with_suffix(".err.log"), "a", encoding="utf-8", buffering=1)
sys.stdout = _err
sys.stderr = _err
faulthandler.enable(_err)

# 日志落文件（最大 2MB，保留 2 个备份），不再依赖控制台
_handler = RotatingFileHandler(_LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])

if __name__ == "__main__":
    # log_config=None：不让 uvicorn 自行配置日志，让其 logger 传播到 root → 落文件
    uvicorn.run(app, host="127.0.0.1", port=8012, log_config=None)
