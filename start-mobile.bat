@echo off
REM Start the server for phones, tablets and classroom devices (HTTPS on the local network).
REM (start.bat alone already serves phones too: https://<this-pc-ip>:8443 while the app window is open)
call "%~dp0start.bat" --lan
