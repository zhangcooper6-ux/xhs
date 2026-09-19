@echo off
chcp 65001 >nul
title 小红书克隆二创助手 - 控制台模式

cd /d "%~dp0"

:: 设置 Python 解释器路径（优先使用项目 venv）
if exist "%~dp0venv\Scripts\python.exe" (
    set "PY=%~dp0venv\Scripts\python.exe"
) else (
    set "PY=python"
)

:: 确保 temp 目录存在
if not exist "temp" mkdir "temp"

echo ============================================================
echo   小红书视频二创克隆工具 (CLI 命令行快捷启动)
echo ============================================================
echo.
echo 请输入需要处理的视频链接或本地视频绝对路径：
echo (示例: https://v.douyin.com/xxx 或 D:\videos\test.mp4)
echo.
set /p TARGET_URL="请输入: "

if "%TARGET_URL%"=="" (
    echo [提示] 输入为空，已取消。
    pause
    exit /b
)

echo.
echo ============================================================
echo   请选择处理模式:
echo   [1] 极速全自动二创 (生成40款非AI爆款封面+去重+二创文案+配音)
echo   [2] 开启 AI 生图封面模式 (--ai-cover)
echo   [3] 自定义高级参数运行
echo ============================================================
set /p MODE="请选择 [默认 1]: "

if "%MODE%"=="2" (
    echo.
    echo 正在启动 [AI 生图封面模式]...
    "%PY%" "%~dp0xhs_clone.py" %TARGET_URL% --ai-cover --hd-enhance --crf 18
) else if "%MODE%"=="3" (
    echo.
    echo 请输入附加参数 (如: --no-bgm --whisper):
    set /p EXTRA_ARGS="参数: "
    "%PY%" "%~dp0xhs_clone.py" %TARGET_URL% %EXTRA_ARGS%
) else (
    echo.
    echo 正在启动 [全自动二创模式]...
    "%PY%" "%~dp0xhs_clone.py" %TARGET_URL% --hd-enhance --crf 18
)

echo.
echo ============================================================
echo   处理完成！输出文件已保存在 output 目录下。
echo ============================================================
pause
