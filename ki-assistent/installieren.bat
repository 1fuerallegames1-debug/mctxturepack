:: Angel - alles automatisch einrichten (ein Doppelklick genuegt)
@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==========================================
echo    Angel - automatische Einrichtung
echo ==========================================
echo.
echo Dieser eine Schritt richtet ALLES ein: Python, Ollama, KI-Modell,
echo Sprache, Browser und den automatischen Start.
echo Beim ersten Mal werden mehrere GB geladen (das KI-Modell) - das kann
echo eine Weile dauern. Bitte dieses Fenster offen lassen.
echo.

rem ---------- Python finden, sonst per winget installieren ----------
call :find_py
if not defined ANGEL_PY (
  echo [1/5] Python wird installiert ...
  where winget >nul 2>nul && winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  call :find_py
)
if not defined ANGEL_PY (
  echo.
  echo Python ist jetzt installiert, aber in DIESEM Fenster noch nicht aktiv.
  echo Bitte dieses Fenster schliessen und installieren.bat noch einmal starten.
  echo Falls Python fehlt: https://www.python.org/downloads/ ^(dort "Add python.exe to PATH" anhaken^).
  start "" https://www.python.org/downloads/
  pause
  exit /b 1
)
echo [1/5] Python: OK

rem ---------- Ollama finden, sonst per winget installieren ----------
where ollama >nul 2>nul
if errorlevel 1 (
  echo [2/5] Ollama wird installiert ...
  where winget >nul 2>nul && winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
)
where ollama >nul 2>nul
if errorlevel 1 (
  echo.
  echo Ollama wurde noch nicht gefunden. Bitte von https://ollama.com/download installieren
  echo und danach installieren.bat erneut starten.
  start "" https://ollama.com/download
  pause
  exit /b 1
)
echo [2/5] Ollama: OK
start "" /b ollama serve >nul 2>nul

rem ---------- KI-Modell laden ----------
echo [3/5] KI-Modell wird geladen (einmalig, mehrere GB, bitte warten) ...
ollama pull qwen3:8b

rem ---------- Sprache + Browser (Fehler hier sind nicht schlimm) ----------
echo [4/5] Sprache und Browser werden eingerichtet ...
%ANGEL_PY% -m pip install --disable-pip-version-check --quiet --upgrade vosk sounddevice playwright
%ANGEL_PY% -m playwright install chromium

rem ---------- Automatischer Start ----------
echo [5/5] Automatischer Start wird eingerichtet ...
%ANGEL_PY% -m angel --autostart ein

echo.
echo ============================================
echo    Fertig! Angel startet jetzt.
echo    Spaeter: start.bat  -  Autostart aus: autostart-aus.bat
echo ============================================
echo.
%ANGEL_PY% -m angel
if errorlevel 1 pause
exit /b 0

:find_py
set "ANGEL_PY="
py -3 --version >nul 2>nul && set "ANGEL_PY=py -3"
if not defined ANGEL_PY python --version >nul 2>nul && set "ANGEL_PY=python"
goto :eof
