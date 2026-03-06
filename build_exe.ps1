param(
    [switch]$GuiOnly,
    [switch]$CliOnly
)

# Build standalone Windows executable(s)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    Write-Error "pyinstaller is not installed. Run: pip install -r requirements-build.txt"
}

if (-not $GuiOnly) {
    pyinstaller --clean --noconfirm --onefile --name stock-watcher stock_watcher.py
    Write-Host "Built CLI EXE: $Root\\dist\\stock-watcher.exe"
}

if (-not $CliOnly) {
    pyinstaller --clean --noconfirm --onefile --windowed --name stock-watcher-gui stock_watcher_gui.py
    Write-Host "Built GUI EXE: $Root\\dist\\stock-watcher-gui.exe"
}

Write-Host "Copy config.json next to the EXE before running."
