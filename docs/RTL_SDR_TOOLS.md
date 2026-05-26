# RTL-SDR Command-Line Tools — Install Guide

SOVEREIGN QRNG uses `rtl_sdr.exe` and `rtl_tcp.exe` directly as subprocesses.
These are **not Python packages** — they are native Windows binaries that must be
downloaded and placed where `config.py` expects them.

---

## What You Need

| Binary | Used by | Purpose |
|--------|---------|---------|
| `rtl_sdr.exe` | `near_entropy.py` | Captures raw IQ samples → stdout pipe |
| `rtl_tcp.exe` | `near_entropy.py` | TCP IQ stream server (fallback) |
| `rtl_test.exe` | You | Verifies dongle is working before starting the rig |

---

## Download

**Source:** Osmocom RTL-SDR Windows builds — the canonical pre-built binaries.

1. Go to: **https://osmocom.org/attachments/download/5394/RelWithDebInfo.zip**
   *(or search "osmocom rtl-sdr windows pre-built" for the current release page)*
2. Download the `.zip` — it contains a flat folder of `.exe` and `.dll` files.

> **Alternative:** https://github.com/osmocom/rtl-sdr/releases — grab the latest
> Windows release asset (`rtl-sdr-releases_*.zip`).

---

## Install Path

SOVEREIGN QRNG expects the binaries at:

```
%QRNG_POOL_DIR%\rtlsdr_tools\x64\rtl_sdr.exe
```

Default if `QRNG_POOL_DIR` is not set:

```
C:\QRNG_Pool\rtlsdr_tools\x64\rtl_sdr.exe
```

**Steps:**

```powershell
# 1. Create the folder
New-Item -ItemType Directory -Force "$env:QRNG_POOL_DIR\rtlsdr_tools\x64"

# 2. Extract the zip and copy all .exe and .dll files into that folder
#    (rtl_sdr.exe, rtl_tcp.exe, rtl_test.exe, librtlsdr.dll, libusb-1.0.dll, etc.)
```

### Override path (optional)

If you want to keep the binaries somewhere else, set the env var:

```powershell
# PowerShell (session)
$env:RTL_SDR_EXE = "C:\Tools\rtlsdr\rtl_sdr.exe"

# Permanent (machine-wide, run as Administrator)
[System.Environment]::SetEnvironmentVariable("RTL_SDR_EXE", "C:\Tools\rtlsdr\rtl_sdr.exe", "Machine")
```

---

## Verify the Dongle Works

Run this **before** starting the entropy rig:

```powershell
# Basic device detection — should print dongle model, tuner, and gain steps
.\rtl_test.exe

# Sample capture test — 1 second of IQ at 96.9 MHz, 256 KSPS
# Should run silently and exit cleanly (no "Failed to open" errors)
.\rtl_sdr.exe -f 96900000 -s 256000 -g 29 -n 256000 - | Out-Null
echo "Exit code: $LASTEXITCODE"   # 0 = success
```

Expected `rtl_test.exe` output (example):
```
Found 1 device(s):
  0:  Realtek, RTL2838UHIDIR, SN: 00000001

Using device 0: Generic RTL2832U OEM
Found Rafael Micro R820T tuner
...
```

If you see `usb_open error -3` or `Failed to open rtlsdr device`, run Zadig first —
see `docs/ZADIG_SETUP.md`.

---

## Two Dongles

SOVEREIGN QRNG supports up to 2 dongles simultaneously (`RTL_MAX_DEVICES = 2`).
When both are plugged in, `rtl_test.exe` should list them:

```
Found 2 device(s):
  0:  Nooelec, SMArt XTR v5, SN: 00000001
  1:  Realtek, RTL2838UHIDIR, SN: 00000002
```

Device index is assigned by USB enumeration order — plug dongles in the same USB
ports each session for consistent `[0]` / `[1]` assignment.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `usb_open error -3` | Wrong driver (default WinUSB → libusbK mismatch) | Run Zadig — see `docs/ZADIG_SETUP.md` |
| `Failed to open rtlsdr device #0` | Device in use by another app (SDR#, etc.) | Close other SDR software first |
| `No supported devices found` | Driver not installed at all | Install WinUSB via Zadig |
| Dongle works, then drops after 10s | USB selective suspend | Apply power management fix — see README |
| Second dongle not detected | HVCI + libusbK conflict | Both dongles must be on WinUSB |
