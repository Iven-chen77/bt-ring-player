@echo off
REM =========================================================================
REM BT-RingPlayer APK Build Launcher for Windows (via WSL)
REM Usage: Double-click this file, or run in cmd/PowerShell:
REM        build_apk_with_wsl.bat          -> build debug APK
REM        build_apk_with_wsl.bat release  -> build release APK
REM This script is 100% ASCII to avoid CMD encoding issues.
REM =========================================================================

setlocal

echo [INFO] Checking WSL installation...
where wsl >nul 2>nul
if errorlevel 1 (
    echo [ERROR] wsl command not found. Please install WSL2 first.
    echo.
    echo Open PowerShell as ADMINISTRATOR and run:
    echo     wsl --install
    echo Then restart your PC and try again.
    echo.
    pause
    exit /b 2
)

REM Important: "where wsl" finds the file, but WSL subsystem might not be enabled.
REM Check via wsl --status; it will print an error if WSL is not installed.
echo [INFO] Checking WSL subsystem status...
wsl --status > "%TEMP%\wsl_check.txt" 2>&1
set "WSL_CHECK_RC=%ERRORLEVEL%"
if NOT "%WSL_CHECK_RC%"=="0" (
    echo [ERROR] WSL subsystem is NOT installed on this machine.
    type "%TEMP%\wsl_check.txt"
    echo.
    echo ================================================================
    echo   HOW TO INSTALL WSL2 (one-time setup):
    echo   1. Right-click Start Menu -^> Windows PowerShell (Administrator)
    echo   2. Copy and run this command:
    echo        wsl --install -d Ubuntu
    echo   3. Wait for the download (~500MB), then RESTART your PC.
    echo   4. After restart, Ubuntu terminal opens automatically.
    echo      Create a Unix username and password (any, don't forget it!)
    echo   5. Come back and double-click build_apk_with_wsl.bat again.
    echo ================================================================
    echo.
    pause
    exit /b 2
)

set "BUILD_MODE=%1"
if "%BUILD_MODE%"=="" set "BUILD_MODE=debug"

echo [INFO] Build mode: %BUILD_MODE%
echo [INFO] Converting Windows path to WSL path...

REM Use WSL's own wslpath to convert the path (most reliable, no string hacks)
for /f "usebackq delims=" %%I in (`wsl wslpath -a "%~dp0"`) do set "WSL_DIR=%%I"
if errorlevel 1 (
    echo [ERROR] Failed to convert path via wslpath.
    pause
    exit /b 3
)

echo [INFO] Project (WSL path): %WSL_DIR%

REM Try Ubuntu distro first; fall back to default if not found
wsl -d Ubuntu -- bash -c "echo wsl_ok" >nul 2>nul
if errorlevel 1 (
    echo [WARN] WSL distro 'Ubuntu' not found, using default distro.
    set "WSL_DIST="
) else (
    echo [INFO] Using WSL distro: Ubuntu
    set "WSL_DIST=-d Ubuntu"
)

echo.
echo [INFO] Starting build inside WSL...
echo [INFO] First run downloads Android SDK/NDK (~4-6GB, takes 20-60 min).
echo ================================================================

wsl %WSL_DIST% -- bash -c "cd '%WSL_DIR%' && chmod +x build_apk.sh && bash build_apk.sh '%BUILD_MODE%'"
set "BUILD_RC=%ERRORLEVEL%"

echo ================================================================
echo.

if "%BUILD_RC%"=="0" (
    echo [SUCCESS] Build completed OK!
    echo APK file is in the bin\ folder of this project.
    echo.
    echo Install to phone via adb:
    echo   adb install -r "bin\btringplayer-1.0-arm64-v8a-debug.apk"
) else (
    echo [FAILED] Build failed, exit code: %BUILD_RC%
    echo.
    echo Please check 'build_apk.log' in this folder for the detailed error.
    echo.
    echo Common fixes:
    echo   1. Install missing dependencies in WSL Ubuntu (printed by build_apk.sh)
    echo   2. Clean and rebuild inside WSL:
    echo      cd "%WSL_DIR%"
    echo      rm -rf .buildozer .buildozer_cache ^&^& bash build_apk.sh
)

echo.
pause
endlocal
