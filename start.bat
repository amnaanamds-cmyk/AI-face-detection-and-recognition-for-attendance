@echo off
REM One-click start on Windows. Add --lan to serve classroom devices over HTTPS.
setlocal
cd /d "%~dp0"
title Smart Classroom Attendance

REM Setup is complete only when this marker exists (a half-finished .venv is rebuilt).
if exist ".venv\setup-complete.txt" goto run

echo ============================================================
echo  First-time setup (needs internet, takes 3-10 minutes)
echo ============================================================
echo.

set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY goto nopython

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 goto oldpython
echo Using:
%PY% --version
echo.

if exist ".venv" (
  echo Removing an incomplete setup from an earlier attempt...
  rmdir /s /q ".venv"
)
echo Creating virtual environment...
%PY% -m venv .venv
if not exist ".venv\Scripts\python.exe" goto venvfail

echo Installing libraries...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto pipfail
echo ok> ".venv\setup-complete.txt"
echo.
echo Setup finished.
echo.

:run
".venv\Scripts\python.exe" run.py %*
echo.
echo The server has stopped. If you see an error above, copy it and ask for help.
pause
exit /b 0

:nopython
echo ERROR: Python was not found.
echo.
echo  1. Download Python 3.11 from https://www.python.org/downloads/
echo  2. In the installer, TICK "Add python.exe to PATH" (bottom of the first screen)
echo  3. Close this window and double-click start.bat again
echo.
echo If typing "python" opens the Microsoft Store: open Windows Settings,
echo search "App execution aliases" and turn OFF the two "python" entries.
pause
exit /b 1

:oldpython
echo ERROR: Python 3.10 or newer is required. Found:
%PY% --version
echo Install Python 3.11 from https://www.python.org/downloads/ (tick "Add python.exe to PATH").
pause
exit /b 1

:venvfail
echo ERROR: could not create the virtual environment (.venv).
echo Move this folder to a simple path such as C:\attendance and try again.
pause
exit /b 1

:pipfail
echo.
echo ERROR: installing the libraries failed (see the messages above).
echo Check your internet connection and run start.bat again.
pause
exit /b 1
