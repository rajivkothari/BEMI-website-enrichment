@echo off
REM Bullseye website-enrichment web app launcher (Windows).
REM Double-click this file, or run  .\start-web  in PowerShell/cmd.
setlocal
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo [setup] Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo.
    echo ERROR: could not run "python". Install Python 3.11+ from
    echo https://www.python.org/downloads/ and check "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
  )
  echo [setup] Installing dependencies ^(first run only, may take a minute^)...
  "%PY%" -m pip install --upgrade pip >nul
  "%PY%" -m pip install -r requirements.txt
)

if not exist ".env" if exist ".env.example" (
  copy ".env.example" ".env" >nul
  echo [setup] Created .env - add your GOOGLE_MAPS_API_KEY to look up missing websites.
)

echo [run] Starting the Bullseye web app...  (press Ctrl+C to stop)
"%PY%" -m streamlit run app.py
