@echo off
rem Angel starten - OHNE schwarzes Fenster. Dieses Fenster schliesst sich sofort wieder;
rem Angel laeuft danach als eigenstaendiges Fenster weiter (das Schliessen beendet Angel nicht).
cd /d "%~dp0"
where pyw >nul 2>nul && ( start "" pyw -3 -m angel %* & exit /b )
where pythonw >nul 2>nul && ( start "" pythonw -m angel %* & exit /b )
rem Falls kein fensterloses Python da ist: normaler Start als Rueckfalloption
where py >nul 2>nul && ( start "" py -3 -m angel %* & exit /b )
start "" python -m angel %*
