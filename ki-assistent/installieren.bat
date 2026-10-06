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

rem ---------- Passwort: ohne das richtige Passwort wird Angel NICHT eingerichtet ----------
echo.
echo Bitte das Angel-Passwort eingeben (nur fuer die Installation noetig).
%ANGEL_PY% -m angel --passwort-pruefen
if errorlevel 1 (
  echo.
  echo Installation abgebrochen.
  pause
  exit /b 1
)

rem ---------- Browser und Medien-Ordner abfragen ----------
echo.
echo Welchen Browser benutzt du? (Angel oeffnet damit Bilder/Videos/Seiten)
set "ANGEL_BROWSER="
set /p "ANGEL_BROWSER=  opera gx / firefox / edge / chrome / standard  [opera gx]: "
if not defined ANGEL_BROWSER set "ANGEL_BROWSER=opera gx"
%ANGEL_PY% -m angel --einstellung web_browser "%ANGEL_BROWSER%"
echo.
echo Wohin sollen gespeicherte Bilder/Videos? (voller Pfad, z.B. D:\Medien)
set "ANGEL_MEDIEN="
set /p "ANGEL_MEDIEN=  Ordner (leer = Standard unter Bilder\Angel-Medien): "
if defined ANGEL_MEDIEN %ANGEL_PY% -m angel --einstellung medien_ordner "%ANGEL_MEDIEN%"

rem ---------- Ollama finden, sonst per winget installieren ----------
call :find_ollama
if not defined ANGEL_OLLAMA (
  echo [2/5] Ollama wird installiert ...
  where winget >nul 2>nul && winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
  call :find_ollama
)
if not defined ANGEL_OLLAMA (
  echo.
  echo Ollama wurde installiert, ist in DIESEM Fenster aber noch nicht aktiv.
  echo Bitte dieses Fenster schliessen und installieren.bat noch einmal starten.
  echo Falls Ollama fehlt: https://ollama.com/download
  start "" https://ollama.com/download
  pause
  exit /b 1
)
echo [2/5] Ollama: OK
start "" /b "%ANGEL_OLLAMA%" serve >nul 2>nul

rem ---------- KI-Modell laden ----------
echo [3/5] KI-Modell wird geladen (einmalig, mehrere GB, bitte warten) ...
rem dem gerade gestarteten Ollama-Dienst ein paar Sekunden Zeit geben
timeout /t 5 /nobreak >nul 2>nul
"%ANGEL_OLLAMA%" pull hermes3:8b
if errorlevel 1 (
  echo Server war noch nicht bereit - neuer Versuch fuer das KI-Modell ...
  timeout /t 8 /nobreak >nul 2>nul
  "%ANGEL_OLLAMA%" pull hermes3:8b
)
echo     ... und das Seh-Modell fuer Bilder/Videos (llava) ...
"%ANGEL_OLLAMA%" pull llava

rem ---------- Sprache + Browser (Fehler hier sind nicht schlimm) ----------
echo [4/5] Sprache und Browser werden eingerichtet ...
%ANGEL_PY% -m pip install --disable-pip-version-check --quiet --upgrade vosk sounddevice playwright
%ANGEL_PY% -m playwright install chromium

rem ---------- Automatischer Start ----------
echo [5/5] Automatischer Start wird eingerichtet ...
%ANGEL_PY% -m angel --autostart ein

echo.
echo ============================================
echo    Fertig! Angel startet jetzt (ohne schwarzes Fenster).
echo    Spaeter: start.bat  -  Autostart aus: autostart-aus.bat
echo ============================================
echo.
rem Angel fensterlos und losgeloest starten, damit KEIN schwarzes Fenster offen bleibt
where pyw >nul 2>nul && ( start "" pyw -3 -m angel & exit /b 0 )
where pythonw >nul 2>nul && ( start "" pythonw -m angel & exit /b 0 )
start "" %ANGEL_PY% -m angel
exit /b 0

:find_py
set "ANGEL_PY="
py -3 --version >nul 2>nul && set "ANGEL_PY=py -3"
if not defined ANGEL_PY python --version >nul 2>nul && set "ANGEL_PY=python"
goto :eof

:find_ollama
rem Ollama ueber PATH suchen - und falls das Fenster den Befehl noch nicht kennt,
rem direkt an den ueblichen Installationsorten (winget legt es unter LOCALAPPDATA ab).
set "ANGEL_OLLAMA="
for /f "delims=" %%I in ('where ollama 2^>nul') do if not defined ANGEL_OLLAMA set "ANGEL_OLLAMA=%%I"
if not defined ANGEL_OLLAMA for %%P in (
  "%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
  "%ProgramFiles%\Ollama\ollama.exe"
  "%ProgramW6432%\Ollama\ollama.exe"
  "%LOCALAPPDATA%\Ollama\ollama.exe"
) do if not defined ANGEL_OLLAMA if exist "%%~P" set "ANGEL_OLLAMA=%%~P"
if defined ANGEL_OLLAMA for %%I in ("%ANGEL_OLLAMA%") do set "PATH=%%~dpI;%PATH%"
goto :eof
