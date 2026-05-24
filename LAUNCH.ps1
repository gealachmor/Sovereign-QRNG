# SOVEREIGN ENTROPY RIG — LAUNCHER v2  (PHOENIX / HP)
# Starts: webcam server → true entropy → near entropy → dashboard API → browser
param([switch]$Force, [switch]$NoBrowser)
$ErrorActionPreference = "SilentlyContinue"

$RIG = "C:\WINDOWS\system32\hpclaude\entropy_rig"
$PY  = "python"

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║     SOVEREIGN QRNG — DUAL-POOL ENTROPY RIG          ║" -ForegroundColor Cyan
Write-Host "  ║     PHOENIX · TRUE + NEAR ENTROPY                   ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

function Port-Active($p) {
    $null -ne (Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue |
               Where-Object State -eq Listen)
}

function Kill-Rig {
    Write-Host "[!] Stopping existing entropy processes..." -ForegroundColor Red
    Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep 2
}

if ($Force) { Kill-Rig }

# ── Ensure QRNG_Pool directories exist ─────────────────────────────────
$QPool = if ($env:QRNG_POOL_DIR) { $env:QRNG_POOL_DIR } else { "D:\STORAGE\QRNG_Pool" }
foreach ($dir in @($QPool, "$QPool\true_pool", "$QPool\near_pool")) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Host "[+] Created $dir" -ForegroundColor DarkGray
    }
}

# ── 1. Webcam Server (port 8090) ────────────────────────────────────────
if (-not (Port-Active 8090)) {
    Write-Host "[+] Starting Webcam Server (port 8090)..." -ForegroundColor Green
    Start-Process $PY -ArgumentList "$RIG\webcam_server.py" -WindowStyle Hidden
    Start-Sleep 3
} else {
    Write-Host "[*] Webcam Server already running." -ForegroundColor Yellow
}

# ── 2. True Entropy Engine (port 8765) ──────────────────────────────────
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

# ── 3. Near Entropy Engine (port 8766) ──────────────────────────────────
if (-not (Port-Active 8766)) {
    Write-Host "[+] Launching NEAR Entropy Engine (built-in cam + LSB)..." -ForegroundColor DarkCyan
    Start-Process powershell -ArgumentList @(
        "-NoExit",
        "-Command",
        "& $PY '$RIG\near_entropy.py'"
    ) -WindowStyle Normal
    Start-Sleep 1
} else {
    Write-Host "[*] NEAR Entropy Engine already running." -ForegroundColor Yellow
}

# ── 4. Dashboard API (port 8888) ─────────────────────────────────────────
if (-not (Port-Active 8888)) {
    Write-Host "[+] Starting Dashboard API (port 8888)..." -ForegroundColor Magenta
    Start-Process $PY -ArgumentList "$RIG\entropy_api.py" -WindowStyle Hidden
    Start-Sleep 1
} else {
    Write-Host "[*] Dashboard API already running." -ForegroundColor Yellow
}

# ── 5. Entropy Market API (port 8889) ────────────────────────────────────
if (-not (Port-Active 8889)) {
    Write-Host "[+] Starting Entropy Market API (port 8889)..." -ForegroundColor Yellow
    Start-Process $PY -ArgumentList "$RIG\entropy_market_api.py" -WindowStyle Hidden
    Start-Sleep 1
} else {
    Write-Host "[*] Entropy Market API already running." -ForegroundColor Yellow
}

# ── 6. Open Dashboard ────────────────────────────────────────────────────
if (-not $NoBrowser) {
    Write-Host "[+] Opening dashboard..." -ForegroundColor Cyan
    Start-Sleep 1
    Start-Process "http://127.0.0.1:8888/"
}

Write-Host ""
Write-Host "  [RIG ONLINE]" -ForegroundColor Green
Write-Host ""
Write-Host "  Dashboard:       http://127.0.0.1:8888/" -ForegroundColor White
Write-Host "  Market API:      http://127.0.0.1:8889/v1/status" -ForegroundColor Yellow
Write-Host "  True Stats API:  http://127.0.0.1:8765/" -ForegroundColor Green
Write-Host "  Near Stats API:  http://127.0.0.1:8766/" -ForegroundColor Cyan
Write-Host "  Cam Server:      http://127.0.0.1:8090/" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Vaults:" -ForegroundColor DarkGray
Write-Host "    TRUE:   C:\QRNG_Pool\true_vault.bin   (10 GB)" -ForegroundColor DarkGreen
Write-Host "    NEAR:   C:\QRNG_Pool\near_vault.bin    (5 GB)" -ForegroundColor DarkCyan
Write-Host ""
Write-Host "  Market commands:" -ForegroundColor DarkGray
Write-Host "    python $RIG\key_manager.py status" -ForegroundColor DarkGray
Write-Host "    python $RIG\key_manager.py create --name buyer --tier pro --mb 100" -ForegroundColor DarkGray
Write-Host "    cloudflared tunnel --url http://127.0.0.1:8889" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  To stop all:   Get-Process python | Stop-Process -Force" -ForegroundColor DarkGray
Write-Host ""
