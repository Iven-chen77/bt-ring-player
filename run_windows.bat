@echo off
title BT Ring Player Launcher
setlocal

pushd "%~dp0"
set "BASEDIR=%CD%"
set "VENV=%BASEDIR%\venv\Scripts\python.exe"
set "LOG=%BASEDIR%\run_windows.log"
set "REQ=%BASEDIR%\requirements.txt"
set "APP=%BASEDIR%\main.py"

echo [%date% %time%] Start > "%LOG%"
echo ========================================
echo   BT Ring Player - Windows Launcher
echo   (log: run_windows.log)
echo ========================================
echo.

REM === 1. Check Python ===
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python command not found.
    echo   Install Python 3.8+ and add it to PATH.
    goto :err
)
for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo [OK] %PYVER%
>> "%LOG%" echo [OK] %PYVER%

REM === 2. venv ===
if not exist "%VENV%" (
    echo [1/3] Creating venv ...
    >> "%LOG%" echo Creating venv
    python -m venv "%BASEDIR%\venv" >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo [ERROR] Failed to create venv. See run_windows.log.
        goto :err
    )
    echo       Done.
) else (
    echo [1/3] venv found, skipped creation.
    >> "%LOG%" echo venv already exists
)

REM === 3. Install deps ===
echo [2/3] Checking dependencies (use Tsinghua mirror; first run takes 3-10 min) ...
>> "%LOG%" echo Installing requirements
call "%VENV%" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple >> "%LOG%" 2>&1
call "%VENV%" -m pip install -r "%REQ%" -i https://pypi.tuna.tsinghua.edu.cn/simple >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] pip install failed. See tail of run_windows.log.
    echo   (network timeout / firewall / VC++ runtime)
    goto :err
)
echo       Dependencies OK.
>> "%LOG%" echo Requirements OK

REM === 4. Music folder ===
if not exist "%BASEDIR%\music" mkdir "%BASEDIR%\music" 2>nul

echo.
echo [3/3] Launching app ... (Kivy window will open separately)
>> "%LOG%" echo Launching main.py
echo   Tips:
echo     - Bluetooth on Windows = SIMULATION mode (fake devices)
echo     - Real BT works only on Android (build APK with WSL2)
echo     - Close the Kivy window to exit this launcher.
echo.

call "%VENV%" "%APP%" >> "%LOG%" 2>&1
set EXITCODE=%errorlevel%

if not "%EXITCODE%"=="0" (
    echo.
    echo [ERROR] App exited abnormally (code=%EXITCODE%). See end of run_windows.log.
    goto :err
)

echo.
echo [Done] App closed normally.
pause
popd
exit /b 0

:err
echo.
echo ===== FAILED =====
echo Open run_windows.log in Notepad for details.
echo.
pause
popd
exit /b 1
