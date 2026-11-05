@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 找不到项目运行环境 .venv，请查看使用说明。
    pause
    exit /b 1
)
if not exist "logs" mkdir "logs"
set "PYTHONUTF8=1"
echo 正在启动小说讲书工作台，请保留此窗口。
echo 如果启动失败，详细信息将保存在 logs\startup.log。
".venv\Scripts\python.exe" -u "main.py" >"logs\startup.log" 2>&1
if errorlevel 1 (
    echo.
    echo 启动失败，错误信息如下：
    type "logs\startup.log"
    echo.
    pause
    exit /b 1
)
exit /b 0
