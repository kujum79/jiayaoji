' 家肴记后端静默启动（方案A：登录自启 · 无窗口）
' 本文件由 安装脚本 复制到 Windows 启动文件夹；源文件保留在 backend 目录作为备份
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "d:\jiayaoji\backend"
sh.Run """C:\Users\HUAWEI\AppData\Local\Programs\Python\Python314\pythonw.exe"" ""d:\jiayaoji\backend\service_entry.py""", 0, False
