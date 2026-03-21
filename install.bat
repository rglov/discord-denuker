@echo off
echo ============================================
echo   Denuker -- Discord Backup ^& Recovery Tool
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python not found.
  echo Download it from: https://www.python.org/downloads/
  echo Make sure to check "Add Python to PATH" during install.
  pause
  exit /b 1
)

echo Installing dependencies...
pip install -r requirements.txt

echo.
echo ============================================
echo   Installation complete!
echo   Run the app: python denuker.py
echo            or: run.bat
echo ============================================
pause
