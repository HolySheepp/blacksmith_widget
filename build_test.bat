@echo off
echo ========================================
echo  Build EXE - NO UPX (AV test build)
echo  Output: dist\BlacksmithWidget_noUPX.exe
echo ========================================
echo.

taskkill /f /im BlacksmithWidget_noUPX.exe >nul 2>&1

pip install --upgrade pyinstaller

echo.
echo Building...
echo.

pyinstaller BlacksmithWidget_noUPX.spec

echo.
if %ERRORLEVEL% == 0 (
    echo SUCCESS: dist\BlacksmithWidget_noUPX.exe
) else (
    echo BUILD FAILED - check errors above
)
echo.
pause
