# -*- coding: utf-8 -*-
# BT Ring Player - PowerShell Launcher (更可靠的双击启动方式)
# 右键此文件 -> "使用 PowerShell 运行" 即可

$ErrorActionPreference = 'Continue'
$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $BaseDir

$VenvPy = Join-Path $BaseDir 'venv\Scripts\python.exe'
$LogFile = Join-Path $BaseDir 'run_windows.log'
$ReqFile = Join-Path $BaseDir 'requirements.txt'
$AppFile = Join-Path $BaseDir 'main.py'

"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Start PS launcher" | Out-File $LogFile -Encoding utf8

Write-Host "========================================"  -ForegroundColor Cyan
Write-Host "  BT Ring Player - Windows Launcher"
Write-Host "  (log: $LogFile)"
Write-Host "========================================"
Write-Host ""

# 1) Python check
try {
    $pyver = & python --version 2>&1
    Write-Host "[OK] $pyver" -ForegroundColor Green
    "[OK] $pyver" | Out-File $LogFile -Append -Encoding utf8
} catch {
    Write-Host "[ERROR] Python not found in PATH. Install 3.8+." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# 2) venv
if (-not (Test-Path $VenvPy)) {
    Write-Host "[1/3] Creating venv..." -ForegroundColor Yellow
    "Creating venv" | Out-File $LogFile -Append -Encoding utf8
    & python -m venv (Join-Path $BaseDir 'venv') *>> $LogFile
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to create venv. See log." -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Host "      Done." -ForegroundColor Green
} else {
    Write-Host "[1/3] venv already exists." -ForegroundColor Gray
}

# 3) Dependencies
Write-Host "[2/3] Checking dependencies (Tsinghua mirror, first run takes minutes)..." -ForegroundColor Yellow
"Installing requirements" | Out-File $LogFile -Append -Encoding utf8
& $VenvPy -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple *>> $LogFile
& $VenvPy -m pip install -r $ReqFile -i https://pypi.tuna.tsinghua.edu.cn/simple *>> $LogFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] pip install failed. See tail of $LogFile" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "      Dependencies OK." -ForegroundColor Green

# 4) Music dir
$musicDir = Join-Path $BaseDir 'music'
if (-not (Test-Path $musicDir)) { New-Item -ItemType Directory $musicDir | Out-Null }

Write-Host ""
Write-Host "[3/3] Launching app (Kivy window pops up shortly)..." -ForegroundColor Cyan
"Launching main.py" | Out-File $LogFile -Append -Encoding utf8
Write-Host "  Tips:"
Write-Host "    - Bluetooth = SIMULATION mode on Windows"
Write-Host "    - Close Kivy window to exit"
Write-Host ""

& $VenvPy $AppFile *>> $LogFile
$exitcode = $LASTEXITCODE

if ($exitcode -ne 0) {
    Write-Host ""
    Write-Host "[ERROR] App exited with code $exitcode. See end of $LogFile" -ForegroundColor Red
} else {
    Write-Host ""
    Write-Host "[Done] App closed normally." -ForegroundColor Green
}
Read-Host "Press Enter to exit"
