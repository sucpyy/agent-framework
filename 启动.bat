@echo off
title Agent Framework
chcp 65001 >nul 2>&1
cd /d "%~dp0"
if not defined AGENT_WORKSPACE_ROOT set "AGENT_WORKSPACE_ROOT=%~dp0"
echo.
echo   Agent Framework v3.0
echo.
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" app.py 8000
) else (
  echo   请先按 README 创建并安装项目虚拟环境。
)
pause
