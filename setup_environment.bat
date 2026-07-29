@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found.
  echo Please install Python 3.11 first, then run this file again.
  pause
  exit /b 1
)
python -m venv .venv
if errorlevel 1 (
  echo Failed to create .venv
  pause
  exit /b 1
)
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
echo Environment setup finished.
echo You can now run start_dashboard.bat
pause
