"""
SOVEREIGN QRNG — PROCESS MANAGER
==================================
Runs all NEAR QUANTUM services in ONE terminal window.
Color-coded output per service. Ctrl-C kills all cleanly.

Usage: python process_manager.py
       python process_manager.py --no-browser
       python process_manager.py --core-only
"""
import subprocess, sys, threading, os, time, webbrowser
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
from pathlib import Path

RIG  = Path(__file__).parent
PY   = sys.executable

# ── Window size: set console to 110×35 on startup ──
try:
    import ctypes
    STD_OUTPUT_HANDLE = -11
    hOut = ctypes.windll.kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
    # COORD: 110 cols, 35 rows visible
    ctypes.windll.kernel32.SetConsoleWindowInfo  # just check it exists
    # Use mode con to resize:
    os.system("mode con cols=110 lines=35")
    ctypes.windll.kernel32.SetConsoleTitleW("SOVEREIGN QRNG — NEAR QUANTUM")
except Exception:
    pass

# ── ANSI colour codes ──
RESET  = "\033[0m"
BOLD   = "\033[1m"
COLORS = {
    "webcam":  "\033[36m",   # cyan
    "near":    "\033[94m",   # bright blue
    "api":     "\033[35m",   # magenta
    "rtl":     "\033[96m",   # bright cyan
    "neg":     "\033[95m",   # bright magenta
    "true":    "\033[33m",   # amber   (TRUE quantum)
    "watch":   "\033[33m",   # yellow  (network watchdog)
    "health":  "\033[32m",   # green   (glances sidecar)
}

def cprint(tag: str, line: str):
    col = COLORS.get(tag, "\033[37m")
    prefix = f"{col}[{tag.upper():>7}]{RESET} "
    print(prefix + line.rstrip(), flush=True)

# ── Service definitions ──
SERVICES = [
    {
        "tag":  "webcam",
        "script": RIG / "webcam_server.py",
        "port": 8090,
        "label": "Webcam Server",
    },
    {
        "tag":  "near",
        "script": RIG / "near_entropy.py",
        "port": 8766,
        "label": "NEAR Entropy Engine v3",
    },
    {
        "tag":  "api",
        "script": RIG / "entropy_api.py",
        "port": 8888,
        "label": "Dashboard API",
    },
    {
        "tag":  "neg",
        "script": RIG / "neg_entropy.py",
        "port": 8767,
        "label": "Negentropy DRBG Engine",
    },
    {
        "tag":  "true",
        "script": RIG / "true_entropy.py",
        "port": 8765,
        "label": "TRUE Entropy Engine (laser)",
    },
    {
        "tag":    "watch",
        "script": RIG / "rf_watchdog.py",
        "port":   8768,
        "label":  "Network Watchdog",
    },
    {
        "tag":    "health",
        "script": None,   # glances is launched as a subprocess directly
        "port":   8099,
        "label":  "Glances Health Sidecar",
        "cmd":    ["glances", "-w", "--port", "8099", "--disable-plugin", "now", "-q"],
    },
]

procs: list[subprocess.Popen | None] = []
_stop = threading.Event()

def stream_output(proc: subprocess.Popen, tag: str):
    """Read stdout+stderr from subprocess, print with tag."""
    try:
        for line in proc.stdout:
            if _stop.is_set():
                break
            cprint(tag, line)
    except Exception:
        pass

def port_listening(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0

def kill_proc(proc: subprocess.Popen, label: str = ""):
    """Terminate a single process and wait for it to exit."""
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=4)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass
    if label:
        cprint("mgr", f"Stopped {label}")

def free_port(port: int, timeout: int = 6) -> bool:
    """Kill any process already listening on port, wait until clear."""
    if not port_listening(port):
        return True
    cprint("mgr", f"\033[33m[CLEAN]\033[0m Port {port} occupied — evicting old process...")
    if sys.platform == "win32":
        # Find and kill PID owning the port via netstat
        try:
            out = subprocess.check_output(
                ["netstat", "-ano"], text=True, stderr=subprocess.DEVNULL
            )
            for line in out.splitlines():
                if f":{port} " in line and "LISTENING" in line:
                    parts = line.split()
                    pid = int(parts[-1])
                    if pid > 4:  # never kill System/IDLE
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                       capture_output=True)
                        cprint("mgr", f"\033[33m[CLEAN]\033[0m Killed PID {pid} on :{port}")
        except Exception:
            pass
    for _ in range(timeout):
        if not port_listening(port):
            return True
        time.sleep(1)
    cprint("mgr", f"\033[31m[WARN]\033[0m :{port} still in use after {timeout}s — service may fail")
    return False

def wait_port(port: int, label: str, timeout: int = 12) -> bool:
    for _ in range(timeout):
        if port_listening(port):
            cprint("mgr", f"\033[32m[OK]\033[0m {label} :{port}")
            return True
        time.sleep(1)
    cprint("mgr", f"\033[33m[!!]\033[0m {label} :{port} not responding after {timeout}s")
    return False

def launch_service(svc: dict) -> subprocess.Popen | None:
    # Custom command (e.g. glances) takes priority over script path
    if svc.get("cmd"):
        if svc.get("port"):
            free_port(svc["port"])
        cprint("mgr", f"Starting {svc['label']}...")
        try:
            proc = subprocess.Popen(
                svc["cmd"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            t = threading.Thread(target=stream_output, args=(proc, svc["tag"]), daemon=True)
            t.start()
            return proc
        except FileNotFoundError:
            cprint("mgr", f"\033[33m[SKIP]\033[0m {svc['label']} — command not found (run: pip install glances)")
            return None
    script = svc["script"]
    if not script.exists():
        cprint("mgr", f"\033[31m[SKIP]\033[0m {svc['label']} — {script.name} not found")
        return None
    if svc.get("port"):
        free_port(svc["port"])   # evict any orphan before launching
    cprint("mgr", f"Starting {svc['label']}...")
    proc = subprocess.Popen(
        [PY, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    t = threading.Thread(target=stream_output, args=(proc, svc["tag"]), daemon=True)
    t.start()
    return proc

def kill_all():
    _stop.set()
    cprint("mgr", "Stopping all services...")
    for svc, p in zip(SERVICES, procs):
        kill_proc(p, svc["label"])
    cprint("mgr", "All services stopped.")

def banner():
    print()
    print("\033[96m  ╔══════════════════════════════════════════════════════════╗\033[0m")
    print("\033[96m  ║   SOVEREIGN QRNG — NEAR + TRUE QUANTUM  v3               ║\033[0m")
    print("\033[96m  ║   Process Manager — All services in one window           ║\033[0m")
    print("\033[96m  ║   Ctrl-C to stop all                                     ║\033[0m")
    print("\033[96m  ╚══════════════════════════════════════════════════════════╝\033[0m")
    print()

def main():
    no_browser = "--no-browser" in sys.argv

    banner()

    for svc in SERVICES:
        proc = launch_service(svc)
        procs.append(proc)

        if proc and svc.get("port"):
            wait_port(svc["port"], svc["label"])

        # Small gap between launches to avoid hammering resources
        time.sleep(1)

    print()
    print("\033[96m  ═══════════════════════════════════════════════════════════\033[0m")
    print("\033[96m  SOVEREIGN QRNG — NEAR + TRUE QUANTUM ONLINE\033[0m")
    print("\033[96m  ═══════════════════════════════════════════════════════════\033[0m")
    print("\033[96m  Dashboard:   http://127.0.0.1:8888/near\033[0m")
    print("\033[95m  Negentropy:  http://127.0.0.1:8767/api/neg\033[0m")
    print("\033[33m  TRUE:        http://127.0.0.1:8765/\033[0m")
    print("\033[33m  Watchdog:    http://127.0.0.1:8768/api/watchdog/status\033[0m")
    print("\033[32m  Health:      http://127.0.0.1:8888/api/health\033[0m")
    print("\033[32m  Glances:     http://127.0.0.1:8099\033[0m")
    from config import NEAR_LOG
    print(f"\033[36m  Log files:   {NEAR_LOG}\033[0m")
    print("\033[90m  Ctrl-C to stop all services\033[0m")
    print()

    if not no_browser:
        time.sleep(1)
        webbrowser.open("http://127.0.0.1:8888/near")

    try:
        # Keep alive — monitor children, restart if crashed
        while True:
            time.sleep(5)
            for i, (svc, proc) in enumerate(zip(SERVICES, procs)):
                if proc and proc.poll() is not None:
                    code = proc.returncode
                    cprint("mgr", f"\033[33m[RESTART]\033[0m {svc['label']} exited (code {code}), cleaning up...")
                    kill_proc(proc, svc["label"])     # ensure fully dead
                    procs[i] = None
                    if svc.get("port"):
                        free_port(svc["port"])        # wait for port to clear
                    cprint("mgr", f"\033[33m[RESTART]\033[0m Relaunching {svc['label']}...")
                    new_proc = launch_service(svc)
                    procs[i] = new_proc
                    if new_proc and svc.get("port"):
                        wait_port(svc["port"], svc["label"])
    except KeyboardInterrupt:
        kill_all()

if __name__ == "__main__":
    main()
