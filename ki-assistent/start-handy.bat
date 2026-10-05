@echo off
rem Angel fuer PC UND Handy starten (Handy muss im selben WLAN sein). Fenster offen lassen.
chcp 65001 >nul
cd /d "%~dp0"
set "KAI_PY="
py -3 --version >nul 2>nul && set "KAI_PY=py -3"
if not defined KAI_PY python --version >nul 2>nul && set "KAI_PY=python"
if not defined KAI_PY (
  echo Python wurde nicht gefunden.
  echo Bitte installieren: https://www.python.org/downloads/
  echo Wichtig: beim Installieren "Add python.exe to PATH" anhaken.
  pause
  exit /b 1
)
%KAI_PY% -m kai --web --handy %*
if errorlevel 1 pause
