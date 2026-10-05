@echo off
rem Angel starten (Doppelklick reicht). Angels Fenster oeffnet sich.
rem Dieses schwarze Fenster offen lassen - es zeigt den Status.
chcp 65001 >nul
cd /d "%~dp0"
set "ANGEL_PY="
py -3 --version >nul 2>nul && set "ANGEL_PY=py -3"
if not defined ANGEL_PY python --version >nul 2>nul && set "ANGEL_PY=python"
if not defined ANGEL_PY (
  echo Python wurde nicht gefunden.
  echo Bitte installieren: https://www.python.org/downloads/
  echo Wichtig: beim Installieren "Add python.exe to PATH" anhaken.
  pause
  exit /b 1
)
%ANGEL_PY% -m angel %*
if errorlevel 1 pause
