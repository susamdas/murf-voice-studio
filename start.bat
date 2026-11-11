@echo off
title Murf Voice Studio
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo First start: setting up, please wait...
  python -m venv .venv
  if errorlevel 1 (
    echo.
    echo Python was not found. Install Python 3.10 or newer from https://www.python.org
    echo and tick "Add python.exe to PATH" during setup. Then run start.bat again.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo Could not install the required packages. Check your internet connection.
  pause
  exit /b 1
)

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo.
  echo A new .env file was created. Open it in Notepad, paste your Murf API key,
  echo save it, then refresh the page in your browser.
  start notepad ".env"
)

".venv\Scripts\python.exe" app.py
pause
