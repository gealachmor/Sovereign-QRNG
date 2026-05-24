# TRUE ENTROPY — LAUNCHER
# Starts: webcam server → true entropy engine → dashboard API → browser (TRUE UI)
param([switch]$Force, [switch]$NoBrowser)
$ErrorActionPreference = "SilentlyContinue"

$RIG = "C:\WINDOWS\system32\hpclaude\entropy_rig"
$PY  = "python"

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║     SOVEREIGN QRNG — TRUE ENTROPY POOL              ║" -ForegroundColor Green
Write-Host "  ║     PHOENIX · USB Cam + Laser Shot Noise            ║" -ForegroundColor Green
Write-Host "  ╚══════════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""

function Port-Active($p) {
    $null -ne (Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue |
               Where-Object State -eq Listen)
}

function Kill-True {
    Write-Host "[!] Stopping TRUE entropy processes..." -ForegroundColor Red
    Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    Start-Sleep 2
}

if ($Force) { Kill-True }

$QPool = if ($env:QRNG_POOL_DIR) { $env:QRNG_POOL_DIR } else { "D:\STORAGE\QRNG_Pool" }
foreach ($dir in @($QPool, "$QPool\true_pool")) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Host "[+] Created $dir" -ForegroundColor DarkGray
    }
}

# ── 1. Webcam Server (shared — port 8090) ───────────────────────────────────
if (-not (Port-Active 8090)) {
    Write-Host "[+] Starting Webcam Server (port 8090)..." -ForegroundColor DarkGray
    Start-Process $PY -ArgumentList "$RIG\webcam_server.py" -WindowStyle Hidden
    Start-Sleep 3
} else {
    Write-Host "[*] Webcam Server already running." -ForegroundColor DarkGray
}

# ── 2. True Entropy Engine (port 8765) ──────────────────────────────────────
if (-not (Port-Active 8765)) {
    Write-Host "[+] Launching TRUE Entropy Engine (USB cam + laser)..." -ForegroundColor Green
    Start-Process powershell -ArgumentList @(
        "-NoExit",
        "-Command",
        "& $PY '$RIG\true_entropy.py'"
    ) -WindowStyle Normal
    Start-Sleep 1
} else {
    Write-Host "[*] TRUE Entropy Engine already running." -ForegroundColor Yellow
}

# ── 3. Dashboard API (port 8888) ─────────────────────────────────────────────
if (-not (Port-Active 8888)) {
    Write-Host "[+] Starting Dashboard API (port 8888)..." -ForegroundColor DarkGray
    Start-Process $PY -ArgumentList "$RIG\entropy_api.py" -WindowStyle Hidden
    Start-Sleep 1
} else {
    Write-Host "[*] Dashboard API already running." -ForegroundColor DarkGray
}

# ── 4. Open TRUE UI ──────────────────────────────────────────────────────────
if (-not $NoBrowser) {
    Write-Host "[+] Opening TRUE entropy dashboard..." -ForegroundColor Green
    Start-Sleep 1
    Start-Process "http://127.0.0.1:8888/true"
}

Write-Host ""
Write-Host "  [TRUE POOL ONLINE]" -ForegroundColor Green
Write-Host ""
Write-Host "  TRUE UI:         http://127.0.0.1:8888/true" -ForegroundColor Green
Write-Host "  TRUE Stats API:  http://127.0.0.1:8765/" -ForegroundColor Green
Write-Host "  Vault:           C:\QRNG_Pool\true_vault.bin  (10 GB)" -ForegroundColor DarkGreen
Write-Host ""
Write-Host "  Laser alignment: back USB cam off slightly — diffraction pattern fills sensor" -ForegroundColor DarkGray
Write-Host "  Stop TRUE only: Get-NetTCPConnection -LocalPort 8765 | % { Stop-Process -Id `$_.OwningProcess -Force }" -ForegroundColor DarkGray
Write-Host ""
