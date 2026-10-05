@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==========================================
echo    Angel - Einrichtung
echo ==========================================
echo.
set "ANGEL_PY="
py -3 --version >nul 2>nul && set "ANGEL_PY=py -3"
if not defined ANGEL_PY python --version >nul 2>nul && set "ANGEL_PY=python"
if not defined ANGEL_PY (
  echo Python wurde nicht gefunden.
  echo Bitte installieren von https://www.python.org/downloads/
  echo WICHTIG: beim Installieren "Add python.exe to PATH" anhaken, danach installieren.bat erneut starten.
  start "" https://www.python.org/downloads/
  pause
  exit /b 1
)
echo Python gefunden.
where ollama >nul 2>nul
if errorlevel 1 (
  echo.
  echo Hinweis: Ollama wurde nicht gefunden. Ollama fuehrt die KI auf deinem PC aus.
  echo Bitte von https://ollama.com/download installieren.
  start "" https://ollama.com/download
)
echo.
set /p VOICE="Spracheingabe per Mikrofon einrichten? [j/n] "
if /i "%VOICE%"=="j" %ANGEL_PY% -m pip install --upgrade vosk sounddevice
echo.
set /p BROWSER="Browser-Steuerung einrichten (Playwright)? [j/n] "
if /i "%BROWSER%"=="j" (
  %ANGEL_PY% -m pip install --upgrade playwright
  %ANGEL_PY% -m playwright install chromium
)
echo.
set /p AUTO="Angel beim Anmelden automatisch starten? [j/n] "
if /i "%AUTO%"=="j" %ANGEL_PY% -m angel --autostart ein
echo.
echo Fertig! Angel startet jetzt. Spaeter einfach start.bat doppelklicken.
echo.
%ANGEL_PY% -m angel
if errorlevel 1 pause
