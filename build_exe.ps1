# Build standalone Windows executable for stock_watcher.py
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    Write-Error "pyinstaller is not installed. Run: pip install -r requirements-build.txt"
}

pyinstaller --clean --noconfirm --onefile --name stock-watcher stock_watcher.py

Write-Host "Build complete. EXE path: $Root\\dist\\stock-watcher.exe"
Write-Host "Copy config.json next to the EXE before running."
