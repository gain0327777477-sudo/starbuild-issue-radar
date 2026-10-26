@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [!] .venv not found. See README_KO.md - setup.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m collector.main
pause
