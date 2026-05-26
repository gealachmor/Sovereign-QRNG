# Wireshark + Npcap — Network Watchdog Setup

`rf_watchdog.py` (port 8768) monitors your LAN for ARP spoofing, gateway MAC
changes, new hosts, and outbound connection spikes. It uses `tshark` — the
command-line engine bundled with Wireshark — and the Npcap packet capture library.

**This is optional.** If tshark is not installed, `rf_watchdog.py` starts anyway
and serves `{"threat":"GREEN"}` with monitoring disabled. The rest of the entropy
rig is unaffected.

---

## What Gets Installed

| Component | Role |
|-----------|------|
| **Npcap** | Windows packet capture driver (Wireshark's replacement for WinPcap) |
| **Wireshark** | Installs `tshark.exe` alongside the GUI — that's what rf_watchdog uses |

---

## Install Order — Npcap First

### Step 1 — Npcap

**https://npcap.com** — download the latest installer (currently 1.88).

Install options to set:
- ✅ **Install Npcap in WinPcap API-compatible Mode** — leave unchecked (not needed)
- ✅ **Restrict Npcap driver's access to Administrators only** — **check this**
- ✅ **Support raw 802.11 traffic** — check if you want WiFi-layer monitoring

> **HVCI note:** Npcap 1.88+ is HVCI-compatible. Older versions (pre-1.0) use an
> unsigned driver that fails Memory Integrity. If you have an old Npcap installed,
> uninstall it and reboot before installing 1.88.

Run the installer as Administrator. Reboot when prompted.

### Step 2 — Wireshark (for tshark)

**https://www.wireshark.org/download.html** — download the Windows 64-bit installer.

During install:
- ✅ Keep **tshark** ticked in the component list (it's on by default)
- The installer will detect Npcap already installed and skip it

After install, verify tshark is on your PATH:

```powershell
tshark --version
# Should print: TShark (Wireshark) 4.x.x
```

If it's not on PATH, add Wireshark's install dir:

```powershell
# Default install location
$env:PATH += ";C:\Program Files\Wireshark"

# Permanent (machine-wide, as Administrator)
[System.Environment]::SetEnvironmentVariable(
    "PATH",
    [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";C:\Program Files\Wireshark",
    "Machine"
)
```

---

## Verify rf_watchdog Can Use It

Start `rf_watchdog.py` (or let `process_manager.py` start it) and check the status:

```powershell
Invoke-RestMethod http://127.0.0.1:8768/api/watchdog/status
```

With tshark installed:
```json
{"threat": "GREEN", "tshark_active": true, "alerts": 0, "hosts_seen": 4}
```

Without tshark (graceful degrade):
```json
{"threat": "GREEN", "tshark_active": false, "note": "tshark not found — ARP monitoring disabled"}
```

---

## What rf_watchdog Monitors

| Check | How | Alert level |
|-------|-----|-------------|
| ARP spoofing | tshark ARP capture, MAC change on gateway IP | RED |
| Gateway MAC change | Tracks known-good gateway MAC at startup | RED |
| New LAN host | Any new IP/MAC pair appears on subnet | YELLOW |
| Outbound connection spike | psutil connection count delta > threshold | YELLOW |

Alerts are available at `http://127.0.0.1:8768/api/watchdog/alerts` (last 100).

---

## HVCI Compatibility Notes

- **Npcap 1.88** — HVCI-safe. Uses KMDF-based driver signed by Microsoft.
- **WinPcap** — **do not use** — unsigned legacy driver, blocked by HVCI.
- **Npcap < 1.0** — **do not use** — old driver signing scheme, HVCI conflict.

If Npcap installation fails with a driver signing error, ensure Secure Boot and
HVCI are active and retry with 1.88+. Older installs that survive a reboot with
Memory Integrity active should be treated as suspicious — reinstall cleanly.
