# SOVEREIGN QRNG — NEAR QUANTUM LAUNCHER v4
# ==========================================
# ONE consolidated window for all services.
# Window size: 110 columns × 35 rows (laptop-friendly).
# Ctrl-C in the process manager window stops all services cleanly.
#
# Desktop shortcut: NEAR QUANTUM.lnk → powershell -NoExit -File LAUNCH_NEAR.ps1
param([switch]$Force, [switch]$NoBrowser, [switch]$NoPayment, [switch]$SkipInstall)
$ErrorActionPreference = "SilentlyContinue"

$RIG = "C:\WINDOWS\system32\hpclaude\entropy_rig"
$PY  = "python"

# ── Resize THIS window to laptop-friendly size ───────────────────────────
$w = $host.UI.RawUI.WindowSize
$w.Width  = 110
$w.Height = 35
$host.UI.RawUI.WindowSize = $w
$b = $host.UI.RawUI.BufferSize
$b.Width  = 110
$b.Height = 3000
$host.UI.RawUI.BufferSize = $b
$host.UI.RawUI.WindowTitle = "SOVEREIGN QRNG — NEAR QUANTUM"

# ── Banner ───────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║   SOVEREIGN QRNG — NEAR QUANTUM  v4                     ║" -ForegroundColor Cyan
Write-Host "  ║   PHOENIX · Cam + RTL-SDR x2 + CPU Jitter + Audio       ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── Helper functions ─────────────────────────────────────────────────────
function Step($msg) { Write-Host "  --> $msg" -ForegroundColor DarkCyan }
function Ok($msg)   { Write-Host "  [+] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  [!] $msg" -ForegroundColor Yellow }

function Pip-Install($pkg) {
    $installed = & $PY -m pip show $pkg 2>$null
    if (-not $installed) {
        Write-Host "  [pip] Installing $pkg..." -ForegroundColor DarkGray
        & $PY -m pip install $pkg --quiet 2>$null
        if ($LASTEXITCODE -eq 0) { Ok "Installed $pkg" } else { Warn "Could not install $pkg (non-critical)" }
    }
}

# ── Kill leftover processes (Force mode or clean start) ──────────────────
if ($Force) {
    Write-Host "  [!] Force-stopping any existing NEAR processes..." -ForegroundColor Red
    foreach ($p in @(8090, 8766, 8888, 8889, 8890)) {
        Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue |
            ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    }
    Start-Sleep 2
    Ok "Old processes cleared"
}

# ── Ensure QRNG directories ───────────────────────────────────────────────
foreach ($dir in @("C:\QRNG_Pool", "C:\QRNG_Pool\near_pool", "C:\QRNG_Pool\true_pool")) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Ok "Created $dir"
    }
}

# ── Python dependency check ───────────────────────────────────────────────
if (-not $SkipInstall) {
    Step "Checking dependencies (one-time)..."
    Pip-Install "numpy"
    Pip-Install "Pillow"
    Pip-Install "opencv-python"
    Pip-Install "pyrtlsdr"
    Pip-Install "pyrtlsdrlib"
    Pip-Install "sounddevice"
    Pip-Install "cryptography"
    Pip-Install "flask"
    Pip-Install "flask-cors"
    Ok "Dependencies ready"
}

# ── Build process manager arguments ──────────────────────────────────────
$pmArgs = "$RIG\process_manager.py"
if ($NoBrowser)  { $pmArgs += " --no-browser" }
if ($NoPayment)  { $pmArgs += " --no-payment" }

# ── Launch process manager in THIS window ────────────────────────────────
Step "Launching Process Manager (all services in this window)..."
Write-Host ""
Write-Host "  Dashboard:   http://127.0.0.1:8888/near" -ForegroundColor Cyan
Write-Host "  Ctrl-C here to stop all services" -ForegroundColor DarkGray
Write-Host ""

& $PY $pmArgs
