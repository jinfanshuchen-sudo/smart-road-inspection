@echo off
cd /d "%~dp0"
if not exist ".\.venv\Scripts\python.exe" (
  echo Cannot find .venv\Scripts\python.exe
  echo Please run setup_environment.bat first, or create the Python environment manually.
  pause
  exit /b 1
)
start "" "http://127.0.0.1:5055/"
.\.venv\Scripts\python.exe .\drone_mission_service.py
pause
