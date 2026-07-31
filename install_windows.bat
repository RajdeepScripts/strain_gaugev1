@echo off
echo ============================================
echo  StrainGauge Installer for Windows
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found. Opening download page...
    start https://www.python.org/downloads/
    echo Please install Python 3.10 or newer, then re-run this script.
    pause
    exit /b 1
)

echo [1/3] Installing dependencies...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install pyinstaller >nul 2>&1
echo Done.

echo [2/3] Building executable...
pyinstaller --onefile --windowed --name "StrainGauge" gui_interface.py
if errorlevel 1 (
    echo Build failed. See errors above.
    pause
    exit /b 1
)

echo [3/3] Done!
echo.
echo Executable created at:  dist\StrainGauge.exe
echo.
pause
