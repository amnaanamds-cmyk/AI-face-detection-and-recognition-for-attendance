@echo off
REM One-click start on Windows. Add --lan to serve classroom devices over HTTPS.
cd /d "%~dp0"
if not exist .venv (
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\pip install -r requirements.txt
)
.venv\Scripts\python run.py %*
pause
