@echo off
echo ========================================
echo  Blacksmith Widget - Build EXE
echo ========================================
echo.

:: Kill running exe to avoid PermissionError
taskkill /f /im BlacksmithWidget.exe >nul 2>&1

:: Install/upgrade PyInstaller first
pip install --upgrade pyinstaller

echo.
echo Building...
echo.

pyinstaller ^
    --noconsole ^
    --onefile ^
    --name BlacksmithWidget ^
    --hidden-import pynput.keyboard._win32 ^
    --hidden-import pynput.mouse._win32 ^
    --hidden-import pynput.keyboard._base ^
    --collect-all pynput ^
    main.py

echo.
if %ERRORLEVEL% == 0 (
    echo SUCCESS: dist\BlacksmithWidget.exe
    echo The save file will appear next to the exe after first run.
) else (
    echo BUILD FAILED - check errors above
)
echo.
pause
