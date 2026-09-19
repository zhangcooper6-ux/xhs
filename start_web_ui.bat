@echo off
chcp 65001 >nul
title 小红书克隆二创助手 (XHS Clone Web UI)

echo ============================================================
echo   正在启动 小红书二创克隆工具 Web UI ...
echo ============================================================

:: 切换到当前脚本所在目录
cd /d "%~dp0"

:: 检查并创建临时目录（避免占用 C 盘）
if not exist "temp" mkdir "temp"

:: 检查 Python 虚拟环境
if exist "%~dp0venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0venv\Scripts\python.exe"
) else (
    echo [警告] 未找到项目专属虚拟环境 venv，尝试使用系统 Python...
    set "PYTHON_EXE=python"
)

:: 启动浏览器并运行 Web 服务
echo.
echo [1/2] 正在启动后台服务...
echo [2/2] 浏览器服务地址: http://127.0.0.1:5000
echo.

:: 延时 1.5 秒后自动在默认浏览器中打开页面
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:5000"

:: 运行 Web UI 主程序
"%PYTHON_EXE%" "%~dp0web_ui.py"

if errorlevel 1 (
    echo.
    echo [错误] 程序异常退出，请检查上方报错信息。
    pause
)
