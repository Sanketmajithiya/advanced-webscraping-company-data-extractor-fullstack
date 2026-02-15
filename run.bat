@echo off
echo ===========================================
echo    Surat Data Extractor - Frontend Mode
echo ===========================================
echo.
echo Attempting to start application...
py app.py

if %errorlevel% neq 0 (
    echo.
    echo ⚠️ Application failed to start!
    echo.
    echo It seems dependencies are missing or there was an error.
    echo Installing dependencies now...
    echo.
    py -m pip install -r requirements.txt
    echo.
    echo Dependencies installed. Retrying application...
    echo.
    py app.py
)

pause
