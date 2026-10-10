@echo off
setlocal
cd /d "%~dp0"
py -3.13 main.py
if errorlevel 1 pause
endlocal
