@echo off
cd /d %~dp0
echo ============================================
echo      FiHay Farm Record Management System
echo          HOSTED PILOT CODEBASE - v2.0
echo ============================================
echo.
echo Checking required Python packages...
python -m pip install --retries 10 --timeout 120 -r requirements.txt
if errorlevel 1 (
  echo.
  echo Package installation did not complete. Internet is needed only for this installation step.
  echo Check the connection and run this file again.
  pause
  exit /b 1
)
echo.
echo Starting FiHay FRMS...
python run_fihay.py
pause
