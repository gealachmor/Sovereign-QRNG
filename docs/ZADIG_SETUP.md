# Zadig — RTL-SDR WinUSB Driver Setup

RTL-SDR dongles ship with no Windows driver, or with the wrong one.
Zadig installs the correct driver in a few clicks.

> **HVCI / Memory Integrity users — read this first:**
> If Windows 11 Memory Integrity (Core Isolation) is active, you **must** use
> **WinUSB** — not libusbK. libusbK uses a kernel-mode driver that fails the
> HVCI code-integrity check on every reboot, silently disabling your dongle.
> WinUSB is signed by Microsoft, passes HVCI, and has identical performance for
> this use case.

---

## Download Zadig

**https://zadig.akeo.ie** — download the latest `.exe`, run as **Administrator**.

No install required — it's a single portable executable.

---

## Setup — Step by Step

### 1. Plug in your RTL-SDR dongle

Plug it in before opening Zadig so it appears in the device list.

### 2. Open Zadig as Administrator

Right-click → **Run as administrator**. UAC prompt will appear — accept it.

### 3. List all devices

Menu bar → **Options** → tick **List All Devices**

This shows every USB device, not just ones needing a driver.

### 4. Select your dongle

In the dropdown, look for one of these names (depends on your dongle's chip):

| What you see | Dongle type |
|---|---|
| `Bulk-In, Interface (Interface 0)` | Most generic RTL2832U dongles |
| `RTL2832U` | Variant name — same thing |
| `RTL2838UHIDIR` | R820T/R828D tuner (e.g. generic SDR sticks) |
| `SMArt XTR v5` | Nooelec SMArt XTR v5 with E4000 tuner |
| `ezcap USB 2.0 DVB-T/DAB/FM dongle` | Original ezcap hardware |

If you see multiple entries with similar names, select **Interface 0** (the bulk
transfer interface — that's the data channel).

### 5. Set driver to WinUSB

In the driver selector box (between the two arrows), set the right-hand side to:

```
WinUSB (v6.1.7600.16385)
```

Do **not** select libusbK, libusb-win32, or any other option.

### 6. Install the driver

Click **Install Driver** (or **Replace Driver** if something else is already installed).

Wait for the green completion bar. This takes 10–30 seconds.

### 7. Repeat for each additional dongle

Unplug the first dongle, plug in the second, and repeat from step 4.

---

## Verify the Driver Installed Correctly

### Device Manager check

1. Open Device Manager (`devmgmt.msc`)
2. Look under **Universal Serial Bus devices** (not "Sound, video and game controllers")
3. Your dongle should appear as e.g. **RTL2838UHIDIR** or **Bulk-In, Interface 0**
4. Right-click → Properties → Driver tab → Driver Provider should say **Microsoft**

If it's under "Imaging Devices" or "DVB-T Receivers", the WinUSB install didn't take — repeat from step 3.

### .inf file check (HVCI proof)

Open an elevated PowerShell and run:

```powershell
Get-ChildItem C:\Windows\INF -Filter "oem*.inf" |
    Select-String -Pattern "WinUSB|libusbK|RTL" |
    Select-Object Filename, Line
```

You should see a line containing `service=WinUSB` (not `service=libusbK`) next to
your dongle's hardware ID. That confirms the HVCI-safe driver is registered.

### Functional test

```powershell
# Replace with your actual rtl_sdr.exe path
$rtl = "$env:QRNG_POOL_DIR\rtlsdr_tools\x64\rtl_sdr.exe"
& $rtl -f 96900000 -s 256000 -g 29 -n 256000 - | Out-Null
Write-Host "Exit: $LASTEXITCODE"   # 0 = dongle opened and captured successfully
```

---

## After a Reboot — Driver Didn't Stick?

On some machines with HVCI active, the WinUSB driver assignment survives reboots fine.
On others (especially if libusbK was previously installed), the OS may fall back.

If your dongle stops working after a reboot:

1. Reopen Zadig as Administrator
2. The dropdown will show the dongle back on its old driver
3. Reinstall WinUSB — takes 30 seconds
4. If this keeps happening, check Device Manager for a conflicting driver package:

```powershell
# Find and remove stale RTL-SDR driver packages
pnputil /enum-drivers | Select-String -Context 0,5 "RTL|rtl|libusbK" | Select-Object -ExpandProperty Line
# Then: pnputil /delete-driver oem<N>.inf /uninstall /force
```

---

## Two Dongles — Different Tuner Chips

SOVEREIGN QRNG uses index `[0]` and `[1]` based on USB enumeration order.
Both must be on WinUSB. Install them one at a time:

| Dongle | Tuner | Zadig device name |
|--------|-------|-------------------|
| Nooelec SMArt XTR v5 | E4000 | `SMArt XTR v5` or `Bulk-In, Interface 0` |
| Generic RTL2838UHIDIR | R828D | `RTL2838UHIDIR` or `Bulk-In, Interface 0` |

After both are installed, run `rtl_test.exe` — it should list both:

```
Found 2 device(s):
  0:  Nooelec, SMArt XTR v5, SN: 00000001
  1:  Realtek, RTL2838UHIDIR, SN: 00000002
```

---

## Quick Reference

```
zadig.akeo.ie → download → Run as Administrator
Options → List All Devices
Select dongle → Set driver to WinUSB → Install Driver
Verify: Device Manager → Universal Serial Bus devices → Driver: Microsoft
Test: rtl_sdr.exe -f 96900000 -s 256000 -g 29 -n 256000 - | Out-Null
```
