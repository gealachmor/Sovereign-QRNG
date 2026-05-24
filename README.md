# SOVEREIGN QRNG — PHOENIX
### Quantum Random Number Generator · Air-Gapped · Sovereign · Free For Humanity

A full-stack quantum entropy platform running entirely on commodity hardware. No cloud. No subscription. No trust required.

---

## WHAT IT DOES

Harvests true randomness from physical quantum phenomena — photon shot noise (webcam), RF atmospheric noise (RTL-SDR dongles), CPU timing jitter, and microphone ADC — mixes them through the **Escher-6 whitening cascade**, stores the result in a local binary vault, and serves it through a browser dashboard with a built-in password vault, NIST-validated DRBG, and cryptographic tools.

| Pool | Sources | Rate | Vault |
|------|---------|------|-------|
| NEAR QUANTUM | Built-in webcam + RTL-SDR(s) + CPU jitter + Audio ADC | ~24 Kbps | 5 GB |
| TRUE QUANTUM | USB webcam + laser shot noise (photon counting) | ~8 Kbps | 10 GB |
| NEGENTROPY | HMAC-SHA-256 DRBG (NIST SP 800-90A) seeded from quantum vault | CPU-speed | Virtual |

Everything runs locally. No telemetry. No internet required after setup.

---

## THE STORY

Built on a 4 GB RAM HP laptop (Intel i3-1005G1, Windows 11 Home) in active collaboration with **Claude (Anthropic)** as co-author, debugging partner, and hardware troubleshooter.

Every RTL-SDR driver fight, every USB power management rabbit hole, every canvas animation — all of it on that machine with Claude running in an Administrator PowerShell terminal beside it.

**The fastest way to get everything working is to use Claude Code (or another agentic AI) running in an admin terminal.** Hardware setup — driver conflicts, USB power state machines, RTL-SDR tuner quirks — involves chains of interdependent steps that vary by machine. An AI that can read your device state, run commands, inspect registry entries, and adapt live is categorically better than any static guide.

> *An AI-powered troubleshooting wizard is also built directly into the SOVEREIGN QRNG dashboard — so future users get guided hardware setup without needing a separate terminal.*

---

## HARDWARE REQUIREMENTS

**Minimum (NEAR QUANTUM only):**
- Any laptop or desktop with a built-in webcam
- Windows 10/11 (Python 3.10+)
- ~6 GB free disk space

**Recommended:**
- 1–2× RTL-SDR dongle (Nooelec SMArt or equivalent) for RF entropy
- USB webcam (second camera for TRUE QUANTUM laser channel)
- 650 nm laser pointer (~$5) aimed at the USB webcam lens

**Tested configuration:**
- HP laptop · i3-1005G1 · 8 GB RAM · Windows 11 Home · HVCI active
- Nooelec SMArt XTR v5 (E4000 tuner) — RTL-SDR dongle [0]
- Generic RTL2838UHIDIR (R828D tuner) — RTL-SDR dongle [1]
- Built-in HP TrueVision HD webcam (CAM-A, NEAR QUANTUM)
- USB 2.0 webcam + 650 nm laser (CAM-B, TRUE QUANTUM)

---

## QUICK START

```powershell
# 1. Install dependencies
pip install -r requirements.txt

# 2. (Optional) Set vault location — defaults to D:\STORAGE\QRNG_Pool
$env:QRNG_POOL_DIR = "D:\STORAGE\QRNG_Pool"   # PowerShell
# set QRNG_POOL_DIR=D:\STORAGE\QRNG_Pool       # CMD

# 3. Launch all services (single window)
python process_manager.py

# 4. Dashboard:
#    http://127.0.0.1:8888/near
```

---

## SERVICES & PORTS

| Service | Port | File | Role |
|---------|------|------|------|
| Webcam Server | 8090 | webcam_server.py | MJPEG streams + /cameras API |
| NEAR Entropy | 8766 | near_entropy.py | 4-source quantum entropy engine |
| Dashboard API | 8888 | entropy_api.py | Serves dashboard + /api/bytes |
| Negentropy DRBG | 8767 | neg_entropy.py | HMAC-SHA-256 DRBG |
| TRUE Entropy | 8765 | true_entropy.py | Laser photon shot noise engine |

---

## RTL-SDR SETUP (CRITICAL — READ THIS FIRST)

### Step 1 — Driver (Zadig)

> **If running Windows 11 with Memory Integrity (HVCI) enabled, you MUST use WinUSB — not libusbK.** libusbK is not HVCI-compatible and will fail to load after a reboot with Memory Integrity active. WinUSB is Microsoft-signed and passes HVCI with no issues.

1. Download **Zadig** from https://zadig.akeo.ie — run as Administrator
2. Plug in your RTL-SDR dongle
3. In Zadig: Options → List All Devices
4. Select **RTL2832U** (or Bulk-In, Interface 0)
5. Set driver to **WinUSB**
6. Click **Install Driver** → wait for completion
7. Repeat for each additional dongle

### Step 2 — Power Management (prevents 10-second dropout)

The RTL-SDR will disconnect every ~10 seconds unless you disable selective suspend:

```powershell
# Run as Administrator

# High Performance power plan
powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c

# System-wide USB selective suspend
reg add "HKLM\SYSTEM\CurrentControlSet\Services\USB" /v "DisableSelectiveSuspend" /t REG_DWORD /d 1 /f
```

Also go to: Device Manager → Universal Serial Bus controllers → each USB Root Hub → Properties → Power Management → uncheck "Allow the computer to turn off this device to save power"

Reboot after applying.

### Step 3 — Verify

```powershell
# Replace with your actual rtl_sdr.exe path
rtl_sdr.exe -f 96900000 -s 256000 -g 29 -n 256000 - | Out-Null
# Should run silently for 1+ seconds without "Failed to open" errors
```

---

## 🤖 AGENTIC AI SETUP (RECOMMENDED)

**The fastest way to get everything working is Claude Code (or a similar agentic AI) in an admin PowerShell terminal.**

```
1. Open PowerShell as Administrator
2. Run: claude
3. Say: "Help me set up SOVEREIGN QRNG. I have [your hardware]."
```

The agent can read your device state, fix driver conflicts, apply registry settings, and debug failures in real time — far faster than any static guide.

---

## DASHBOARD TABS

| Tab | Description |
|-----|-------------|
| NEAR QUANTUM | Live entropy feed — cameras, RTL-SDR, CPU jitter, audio. Lorenz 3D attractor + Escher cascade visualization. |
| TRUE QUANTUM | Laser photon shot noise channel (requires USB webcam + laser pointer). |
| TOOLS | Sovereign cryptographic tools: password generator, passphrase, UUID, dice, entropy bytes, range sampler. QRNG-secured password vault. |
| NEGENTROPY | HMAC-SHA-256 DRBG — always full, always available. Recamán sequence visualization. |
| INTEL | Source explainers, troubleshooting wizard, hardware catalog. |

---

## SOVEREIGN VAULT (PASSWORD LIBRARY)

The TOOLS tab includes a **QRNG-encrypted password vault**:

- **Entropy gate**: vault cannot be armed until NEAR quantum entropy exceeds threshold
- **Key material**: 32 bytes fetched from the live NEAR vault at arm time
- **Cipher**: AES-GCM-256 with a DRBG-sourced 12-byte IV per entry
- **Key scope**: session-scoped — key lives in browser memory only, never written to disk
- **Storage**: encrypted ciphertext in `localStorage`

> **Long-term storage note:** The vault is session-scoped by design — when you close the browser tab the key is gone. For permanent password storage pair this with **KeePass** (free, open-source): generate your master password with SOVEREIGN QRNG, store the rest in KeePass.

---

## SECURITY MODEL

```
Physical entropy (camera / RTL-SDR / CPU jitter / mic)
         ↓
   Escher-6 Cascade  (fold → rotate → fall → braid → scatter → SHA-256 seal)
         ↓
     near_vault.bin  (5 GB ring buffer, fsync-safe, atomic offset)
         ↓  seeds once per GB
   HMAC-SHA-256 DRBG  (NIST SP 800-90A Rev.1 §10.1.2)
         ↓
   AES-GCM-256 vault key  (session memory only)
         ↓
   Encrypted password entries  (localStorage)
```

All entropy stays on your machine. Zero network calls outside `127.0.0.1`.

---

## NIST VALIDATION

The NEAR entropy engine targets NIST SP 800-90B statistical requirements. Run the included test suite when the vault reaches ≥ 100 MB:

```powershell
python nist_sts_test.py
# 6 tests: frequency, runs, block_frequency, autocorrelation (lag 1/2/8), longest_run
# Target: p-value > 0.01 on all tests
```

The DRBG is implemented per **NIST SP 800-90A Rev.1 §10.1.2** (HMAC-DRBG with SHA-256).

---

## FILE STRUCTURE

```
entropy_rig/
├── config.py                # Path configuration (QRNG_POOL_DIR env var)
├── requirements.txt         # Python dependencies
├── near_entropy.py          # NEAR quantum engine — 4 sources, port 8766
├── neg_entropy.py           # Negentropy DRBG — NIST 800-90A, port 8767
├── true_entropy.py          # TRUE quantum engine — laser photon noise, port 8765
├── webcam_server.py         # Multi-cam MJPEG server, port 8090
├── entropy_api.py           # Dashboard API + whitening, port 8888
├── process_manager.py       # Single-window service supervisor
├── near_dashboard.html      # Full browser dashboard (5 tabs)
├── nist_sts_test.py         # NIST statistical test suite (6 tests)
└── LAUNCH_NEAR.ps1          # Desktop launcher
```

---

## PHILOSOPHY

> *Entropy = physical randomness — quantum, finite, precious.*
> *Negentropy = mathematical order — deterministic, infinite, reliable.*
> *XOR of both = sovereign cryptographic independence.*

Randomness is infrastructure. Every password, every key, every nonce in your digital life depends on it. This project puts quantum randomness under your direct physical control — no cloud provider, no hardware security module, no black box.

---

## LICENSE

MIT — free to use, modify, and distribute. Attribution appreciated but not required.

---

## CREDITS

Built by Phoenix · Co-authored with Claude (Anthropic) and Gemini (Google)
RTL-SDR community · NIST Cybersecurity Division · Nooelec hardware
