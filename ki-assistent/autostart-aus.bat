@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "ANGEL_PY="
py -3 --version >nul 2>nul && set "ANGEL_PY=py -3"
if not defined ANGEL_PY python --version >nul 2>nul && set "ANGEL_PY=python"
if not defined ANGEL_PY ( echo Python nicht gefunden. & pause & exit /b 1 )
%ANGEL_PY% -m angel --autostart aus
pause
