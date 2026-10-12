@echo off
rem JiaYaoJi one-click launcher: start backend (if not running) and open the app
rem V138: port 8012 (8011 retired - Simple Browser stubborn cache, same as 8010 before)
cd /d d:\jiayaoji\backend
netstat -ano | find "127.0.0.1:8012" | find "LISTENING" >nul
if errorlevel 1 (
  start "JiayaoJi-Backend" /min "C:\Users\HUAWEI\AppData\Local\Programs\Python\Python314\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8012
  timeout /t 4 /nobreak >nul
)
start "" http://127.0.0.1:8012/app
