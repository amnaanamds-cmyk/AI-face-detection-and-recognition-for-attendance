@echo off
REM One-click start on Windows: opens the FaceAttend desktop app (no console window stays open).
REM   start.bat --server   console server only (http://127.0.0.1:8000)
setlocal
cd /d "%~dp0"
title FaceAttend setup

REM Setup is complete only when a marker exists (a half-finished .venv is rebuilt).
if exist ".venv\setup-complete.txt" goto run_venv
if exist "setup-system-python.txt" goto run_system

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
if exist ".venv\Scripts\python.exe" goto install_venv

REM Some Python installs (e.g. 3.14 from the new "Python install manager") lack the
REM files the built-in venv needs. Try the virtualenv tool instead.
echo.
echo The built-in venv failed - trying "virtualenv" instead...
if exist ".venv" rmdir /s /q ".venv"
%PY% -m pip install --user --quiet --upgrade virtualenv
%PY% -m virtualenv .venv
if exist ".venv\Scripts\python.exe" goto install_venv

REM Last resort: install the libraries for this Windows user and run without a venv.
echo.
echo virtualenv also failed - installing the libraries directly for this user instead...
if exist ".venv" rmdir /s /q ".venv"
%PY% -m pip install --user --upgrade pip
%PY% -m pip install --user -r requirements.txt
if errorlevel 1 goto pipfail
echo %PY%> "setup-system-python.txt"
echo.
echo Setup finished.
goto run_system

:install_venv
echo Installing libraries...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto pipfail
echo ok> ".venv\setup-complete.txt"
echo.
echo Setup finished.
echo.

:run_venv
if "%~1"=="" (
  REM desktop app: own window, no console - this window closes by itself
  start "" ".venv\Scripts\pythonw.exe" desktop.py
  exit /b 0
)
".venv\Scripts\python.exe" run.py %*
goto stopped

:run_system
set /p PY=<"setup-system-python.txt"
if not "%~1"=="" goto run_system_server
REM desktop app via pythonw.exe (no console), started detached so this window can close
%PY% -c "import os,subprocess,sys; w=os.path.join(os.path.dirname(sys.executable),'pythonw.exe'); subprocess.Popen([w if os.path.exists(w) else sys.executable,'desktop.py'],creationflags=8)"
exit /b 0

:run_system_server
%PY% run.py %*
goto stopped

:stopped
echo.
echo The server has stopped. If you see an error above, copy it and ask for help.
pause
exit /b 0

:nopython
echo ERROR: Python was not found.
echo.
echo  1. Download Python 3.12 from https://www.python.org/downloads/windows/
echo     ("Windows installer (64-bit)")
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
echo Install Python 3.12 from https://www.python.org/downloads/windows/ (tick "Add python.exe to PATH").
pause
exit /b 1

:pipfail
echo.
echo ERROR: installing the libraries failed (see the messages above).
echo  - Check your internet connection and run start.bat again.
echo  - If the messages mention "Microsoft Visual C++" or "building wheel", your Python
echo    version is too new for some library: install Python 3.12 from
echo    https://www.python.org/downloads/windows/ and delete the .venv folder.
pause
exit /b 1
