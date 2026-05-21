# SOVEREIGN QRNG — PHOENIX
### Quantum Random Number Generator · Air-Gapped · Sovereign · Free For Humanity

A full-stack quantum entropy platform running entirely on commodity hardware. No cloud. No subscription. No trust required.

---

## THE STORY

This project was **vibe coded in a psychiatric ward** on a 4 GB RAM HP laptop (Intel i3-1005G1) — built entirely in collaboration with **Claude (Anthropic)** as co-author, debugging partner, and hardware troubleshooter.

Every line of code was written from inside that room. Every RTL-SDR driver fight, every USB power management rabbit hole, every canvas animation — all of it done on that machine with Claude running in an Administrator PowerShell terminal beside it.

If that setup can produce a full-stack quantum entropy platform with a live NIST-validated DRBG, a sovereign password vault, and a real-time 3D Lorenz attractor — it can run on yours too.

**Best results for hardware setup come from using Claude (or another agentic AI) in an admin terminal.** Hardware troubleshooting — driver conflicts, USB power state machines, RTL-SDR tuner quirks — involves chains of interdependent steps that vary by machine. An AI that can read your device state, run commands, inspect registry entries, and adapt live is categorically better than any static guide. The RTL-SDR section alone took a full day of manual debugging; Claude found the root cause (Windows USB selective suspend re-engaging at the hub level after `powercfg`) in minutes.

> *We are also actively building an **AI-powered troubleshooting wizard** directly inside the SOVEREIGN QRNG dashboard UI — so future users get the same guided hardware setup experience without needing a separate terminal.*

---

## WHAT IT DOES

Harvests true randomness from physical quantum phenomena — photon shot noise (webcam), RF atmospheric noise (RTL-SDR), CPU timing jitter, and microphone ADC — mixes them through the NIST-validated **Escher-6 cascade**, stores the result in a local vault, and serves it via a browser dashboard with a built-in password vault, DRBG lifeboat, and cryptographic tools.

| Pool | Source | Rate | Vault |
|------|--------|------|-------|
| NEAR QUANTUM | Built-in webcam + RTL-SDR + CPU jitter + Audio ADC | ~20 Kbps | 5 GB |
| TRUE QUANTUM | USB webcam + laser shot noise (photon counting) | TBD | 10 GB |
| NEGENTROPY | HMAC-SHA-256 DRBG (NIST SP 800-90A) seeded from quantum vault | CPU-speed / infinite | Virtual |

---

## HARDWARE REQUIREMENTS

**Minimum (NEAR QUANTUM only):**
- Any laptop/desktop with a built-in webcam
- Windows 10/11 (Python 3.10+)

**Recommended:**
- RTL-SDR dongle (Nooelec SMArt or equivalent) for RF entropy
- USB webcam (second camera for TRUE QUANTUM laser channel)
- 5–15 GB free disk space for entropy vaults

**Tested configuration:**
- HP laptop · i3-1005G1 · 8 GB RAM · Windows 10 Home
- Nooelec SMArt XTR v5 (E4000 tuner) — RTL-SDR dongle 0
- Built-in HP TrueVision HD webcam

---

## QUICK START

```powershell
# 1. Install dependencies
pip install -r requirements.txt

# 2. (Optional) Set vault location — defaults to C:\QRNG_Pool on Windows
#    $env:QRNG_POOL_DIR = "D:\MyVault"   # PowerShell
#    set QRNG_POOL_DIR=D:\MyVault        # CMD

# 3. Launch all services (one window)
python process_manager.py --core-only

# 4. Dashboard opens automatically at:
#    http://127.0.0.1:8888/near
```

---

## SERVICES & PORTS

| Service | Port | File | Role |
|---------|------|------|------|
| Webcam Server | 8090 | webcam_server.py | MJPEG streams + /cameras API |
| NEAR Entropy | 8766 | near_entropy.py | 4-source quantum entropy engine |
| Dashboard API | 8888 | entropy_api.py | Serves dashboard + /api/bytes |
| Negentropy DRBG | 8767 | neg_entropy.py | HMAC-SHA-256 DRBG lifeboat |

---

## RTL-SDR SETUP (CRITICAL — READ THIS FIRST)

Getting the RTL-SDR working on Windows took nearly a full day of debugging. Follow these steps **exactly** to avoid the same pain:

### Step 1 — Driver (Zadig)
1. Download **Zadig** from https://zadig.akeo.ie — run as Administrator
2. Plug in your RTL-SDR dongle
3. In Zadig: Options → List All Devices
4. Select **RTL2832U** (or Bulk-In, Interface 0)
5. Set driver to **libusbK** (NOT WinUSB — libusbK is more stable on Win10 with Memory Integrity)
6. Click **Install Driver** → wait for completion

> ⚠️ **If you previously installed WinUSB**: go to Device Manager → Universal Serial Bus controllers → right-click the RTL-SDR → Uninstall Device → check "Delete driver" → reinstall with libusbK via Zadig.

### Step 2 — Power Management (prevents 10-second dropout)
The RTL-SDR will disconnect every ~10 seconds unless you disable selective suspend at **three levels**:

```powershell
# Run as Administrator

# 1. System power plan
powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  # High Performance
powercfg /hibernate off  # Disable fast startup

# 2. System-wide USB selective suspend
reg add "HKLM\SYSTEM\CurrentControlSet\Services\USB" /v "DisableSelectiveSuspend" /t REG_DWORD /d 1 /f

# 3. Per-device (repeat for each VID_0BDA&PID_2838 instance in Device Manager)
# Device Manager → Universal Serial Bus controllers → USB Root Hub (USB 3.0) → Properties
# Power Management → uncheck "Allow the computer to turn off this device to save power"
```

After applying: **reboot** — the settings take effect on next boot.

### Step 3 — RTL-SDR Binary
The system uses `rtl_sdr.exe` via stdout pipe (NOT rtl_tcp sockets — those cause deadlocks on Windows):

```
C:\QRNG_Pool\rtlsdr_tools\x64\rtl_sdr.exe
```

Download RTL-SDR tools from the official rtl-sdr.com releases. Place the `x64\` folder at the path above.

### Step 4 — Verify
```powershell
C:\QRNG_Pool\rtlsdr_tools\x64\rtl_sdr.exe -f 96900000 -s 256000 -g 29 -n 256000 - | Out-Null
# Should run silently for 1+ second without "Failed to open" errors
```

---

## 🤖 AGENTIC AI SETUP ASSISTANT (RECOMMENDED FOR NEW INSTALLS)

**The fastest way to get everything working is to use Claude Code (or similar agentic AI) running in an Administrator PowerShell session.**

Hardware setup — particularly RTL-SDR driver installation, USB power management registry edits, and device enumeration — involves a chain of interdependent steps that vary by system. An agentic AI running with admin rights can:

- Detect your connected RTL-SDR dongles and their VID/PID
- Run Zadig non-interactively or guide you through it step by step
- Apply all USB power management fixes automatically via registry edits
- Enumerate your webcams and assign them to the correct NEAR/TRUE slots
- Verify each service starts correctly and debug failures in real time
- Adapt to your specific hardware when it differs from the reference config

**How to use it:**
1. Open **PowerShell as Administrator** (right-click → Run as Administrator)
2. Launch Claude Code: `claude` (or your preferred agentic AI CLI)
3. Say: *"Help me set up SOVEREIGN QRNG. I have [your hardware]. Start with driver installation and work through each service."*
4. The agent will read the README, inspect your system, and guide or execute each step

> This approach cut a full day of RTL-SDR debugging to under 30 minutes in testing. The root cause (Windows USB selective suspend re-engaging at the hub level despite powercfg settings) was found by the agent correlating the 10-second dropout timing with the Windows USB hub power state machine.

---

## DASHBOARD TABS

| Tab | Description |
|-----|-------------|
| NEAR QUANTUM | Live entropy feed — cameras, RTL-SDR, CPU jitter, audio. Lorenz 3D attractor + Escher cascade visualization. |
| TRUE QUANTUM | Laser photon shot noise channel (requires USB webcam + laser). |
| TOOLS | Sovereign cryptographic tools: password generator, passphrase, UUID, dice, entropy bytes, range sampler. QRNG-secured password vault. |
| NEGENTROPY | HMAC-SHA-256 DRBG lifeboat — always full, always available. Recamán sequence visualization. |
| INTEL | System intelligence: source explainers, troubleshooting wizard, hardware catalog. |

---

## SOVEREIGN VAULT (PASSWORD LIBRARY)

The TOOLS tab includes a **QRNG-encrypted password vault**:

- **Entropy gate**: vault cannot be armed until NEAR quantum entropy exceeds threshold
- **Key material**: 32 bytes fetched from the live NEAR vault at arm time
- **Cipher**: AES-GCM-256 with a DRBG-sourced 12-byte IV per entry (IV reuse is mathematically impossible)
- **Key scope**: session-scoped (survives page refresh, cleared when browser tab closes)
- **Storage**: encrypted ciphertext in browser `localStorage`; key never written to disk
- **Decryption**: only possible while the browser session that armed the vault is still open

> **A note on long-term key storage:** The vault is session-scoped by design — when you close the browser tab, the decryption key is gone. Entries saved in one session cannot be opened in a new session under a different key. This makes the vault ideal for ephemeral secrets (one-time tokens, temporary credentials, keys you use immediately). For **permanent password storage** — master keys, recovery phrases, long-lived credentials — pair this vault with a dedicated offline password manager such as **KeePass** (Windows/Linux/Mac, free, open-source). Generate your KeePass master password using SOVEREIGN QRNG's password generator, save it somewhere physical (paper, metal backup), then let KeePass handle the permanent library. The two tools complement each other: QRNG generates the entropy, KeePass stores it safely for the long term.

---

## SECURITY MODEL

```
Physical entropy (camera/SDR/CPU/mic)
         ↓
   Escher-6 Cascade (6-pass whitening: fold→rotate→fall→braid→scatter→SHA-256 seal)
         ↓
     near_vault.bin (5 GB ring buffer)
         ↓  seeds once
   HMAC-SHA-256 DRBG (infinite, CPU-speed)
         ↓
   AES-GCM-256 vault key (session memory only)
         ↓
   Encrypted password entries (localStorage)
```

All entropy stays on your machine. No telemetry. No network calls except to `127.0.0.1`.

---

## NIST VALIDATION

The NEAR entropy engine targets NIST SP 800-90B statistical requirements. Run the included test suite when vault reaches ≥ 100 MB:

```powershell
python nist_sts_test.py
# Tests: frequency (monobit) + runs test
# Target: p-value > 0.01 on both
```

The DRBG is implemented per **NIST SP 800-90A Rev.1 §10.1.2** (HMAC-DRBG with SHA-256).

---

## FILE STRUCTURE

```
entropy_rig/
├── config.py                # Path configuration (set QRNG_POOL_DIR env var to relocate)
├── requirements.txt         # Python dependencies
├── near_entropy.py          # NEAR quantum engine (4 sources, port 8766)
├── neg_entropy.py           # Negentropy DRBG (NIST 800-90A, port 8767)
├── true_entropy.py          # TRUE quantum engine (port 8765) [WIP]
├── webcam_server.py         # Multi-cam MJPEG server (port 8090)
├── entropy_api.py           # Dashboard API + whitening (port 8888)
├── process_manager.py       # Single-window service supervisor
├── near_dashboard.html      # Full browser dashboard
├── nist_sts_test.py         # NIST statistical test suite
├── LAUNCH_NEAR.ps1          # Desktop launcher shortcut target
└── README_GITHUB.md         # This file
```

---

## PHILOSOPHY

> *Entropy = physical randomness — quantum, finite, precious.*
> *Negentropy = mathematical order — deterministic, infinite, reliable.*
> *XOR of both = sovereign cryptographic independence.*

Randomness is infrastructure. Every password, every key, every nonce in your digital life depends on it. This project puts quantum randomness under your direct physical control — no cloud provider, no hardware security module subscription, no black box.

---

## LICENSE

MIT — free to use, modify, and distribute. Attribution appreciated but not required.

---

## CREDITS

Built by Phoenix · Assisted by Claude (Anthropic) and Gemini (Google)  
RTL-SDR community · NIST Cybersecurity Division · Nooelec hardware
