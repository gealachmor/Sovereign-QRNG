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
]

procs: list[subprocess.Popen] = []
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

def wait_port(port: int, label: str, timeout: int = 12) -> bool:
    for _ in range(timeout):
        if port_listening(port):
            cprint("mgr", f"\033[32m[OK]\033[0m {label} :{port}")
            return True
        time.sleep(1)
    cprint("mgr", f"\033[33m[!!]\033[0m {label} :{port} not responding after {timeout}s")
    return False

def launch_service(svc: dict) -> subprocess.Popen | None:
    script = svc["script"]
    if not script.exists():
        cprint("mgr", f"\033[31m[SKIP]\033[0m {svc['label']} — {script.name} not found")
        return None
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
    for p in procs:
        try:
            p.terminate()
            p.wait(timeout=3)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    cprint("mgr", "All services stopped.")

def banner():
    print()
    print("\033[96m  ╔══════════════════════════════════════════════════════════╗\033[0m")
    print("\033[96m  ║   SOVEREIGN QRNG — NEAR QUANTUM  v3                     ║\033[0m")
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
    print("\033[96m  SOVEREIGN QRNG — NEAR QUANTUM ONLINE\033[0m")
    print("\033[96m  ═══════════════════════════════════════════════════════════\033[0m")
    print("\033[96m  Dashboard:   http://127.0.0.1:8888/near\033[0m")
    print("\033[95m  Negentropy:  http://127.0.0.1:8767/api/neg\033[0m")
    from config import NEAR_LOG
    print(f"\033[36m  Log files:   {NEAR_LOG}\033[0m")
    print("\033[90m  Ctrl-C to stop all services\033[0m")
    print()

    if not no_browser:
        time.sleep(1)
        webbrowser.open("http://127.0.0.1:8888/near")

    try:
        # Keep alive — just monitor child processes
        while True:
            time.sleep(5)
            for i, (svc, proc) in enumerate(zip(SERVICES, procs)):
                if proc and proc.poll() is not None:
                    cprint("mgr", f"\033[33m[RESTART]\033[0m {svc['label']} exited (code {proc.returncode}), restarting...")
                    new_proc = launch_service(svc)
                    procs[i] = new_proc
                    if new_proc and svc.get("port"):
                        wait_port(svc["port"], svc["label"])
    except KeyboardInterrupt:
        kill_all()

if __name__ == "__main__":
    main()
