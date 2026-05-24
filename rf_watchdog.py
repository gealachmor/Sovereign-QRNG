"""
SOVEREIGN QRNG — NETWORK WATCHDOG
===================================
Live threat monitor for shared public WiFi (SJH-Public).
Port: 8768

Monitors:
  - ARP spoofing / gateway MAC changes  (tshark, requires Wireshark+Npcap)
  - New host discovery on the LAN
  - Outbound connection rate spikes      (psutil)
  - Threat level: GREEN / YELLOW / RED

GET /api/watchdog/status   → threat level + summary
GET /api/watchdog/alerts   → last 100 alerts
GET /api/watchdog/arp      → current ARP table
GET /api/watchdog/hosts    → discovered LAN hosts

Gracefully degrades if tshark is not yet installed (monitoring disabled,
API still serves GREEN status so process_manager doesn't crash).
"""
import json, subprocess, threading, time, collections, socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8768

# ── Shared state (guarded by _lock) ──
_lock        = threading.Lock()
_alerts      = collections.deque(maxlen=100)
_arp_table   = {}   # ip -> mac
_hosts       = {}   # ip -> {mac, first_seen, last_seen}
_threat      = "GREEN"
_tshark_ok   = False
_gateway_ip  = None
_gateway_mac = None

# ── Helpers ──

def _ts():
    return time.strftime("%Y-%m-%dT%H:%M:%S")

def _add_alert(level: str, msg: str, data: dict | None = None):
    """Thread-safe alert append.  Caller must NOT hold _lock."""
    global _threat
    entry = {"time": _ts(), "level": level, "msg": msg, "data": data or {}}
    with _lock:
        _alerts.appendleft(entry)
        if level == "RED":
            _threat = "RED"
        elif level == "YELLOW" and _threat == "GREEN":
            _threat = "YELLOW"
    print(f"[WATCHDOG][{level}] {msg}", flush=True)

def _detect_gateway() -> str | None:
    try:
        out = subprocess.check_output("ipconfig", text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if "Default Gateway" in line and ":" in line:
                val = line.split(":", 1)[-1].strip()
                if val and val not in ("0.0.0.0", ""):
                    return val
    except Exception:
        pass
    return None

def _detect_wifi_iface() -> str:
    """Return the tshark interface index or name for the Wi-Fi adapter."""
    try:
        out = subprocess.check_output(
            ["tshark", "-D"], text=True, stderr=subprocess.DEVNULL, timeout=5
        )
        for line in out.splitlines():
            low = line.lower()
            if "wi-fi" in low or "wireless" in low or "wifi" in low or "wlan" in low:
                idx = line.split(".", 1)[0].strip()
                return idx
    except Exception:
        pass
    return "Wi-Fi"

def _check_tshark() -> bool:
    try:
        subprocess.run(
            ["tshark", "--version"], capture_output=True, timeout=5
        )
        return True
    except Exception:
        return False

# ── Monitor threads ──

def _arp_monitor():
    """Capture ARP replies; alert on gateway MAC change or new hosts."""
    global _tshark_ok, _gateway_mac
    if not _check_tshark():
        print("[WATCHDOG] tshark not found — install Wireshark+Npcap to enable ARP monitoring.", flush=True)
        return

    iface = _detect_wifi_iface()
    cmd   = [
        "tshark", "-i", iface,
        "-Y", "arp.opcode==2",
        "-T", "fields",
        "-e", "arp.src.proto_ipv4",
        "-e", "arp.src.hw_mac",
        "-l", "--no-promiscuous-mode",
    ]
    print(f"[WATCHDOG] ARP monitor started on interface {iface}", flush=True)
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        _tshark_ok = True
        for line in proc.stdout:
            parts = line.strip().split("\t")
            if len(parts) != 2:
                continue
            ip, mac = parts[0].strip(), parts[1].strip().lower()
            if not ip or not mac or len(mac) < 11:
                continue
            with _lock:
                prev_mac = _arp_table.get(ip)
                _arp_table[ip] = mac
                now = time.time()
                if ip not in _hosts:
                    _hosts[ip] = {"mac": mac, "first_seen": now, "last_seen": now}
                    is_new = True
                else:
                    _hosts[ip]["last_seen"] = now
                    is_new = False
            if is_new:
                _add_alert("INFO", f"New host: {ip}  ({mac})", {"ip": ip, "mac": mac})
            if prev_mac and prev_mac != mac:
                is_gw = (ip == _gateway_ip)
                severity = "RED" if is_gw else "YELLOW"
                _add_alert(severity,
                    f"ARP MAC CHANGE{'  *** GATEWAY ***' if is_gw else ''}: "
                    f"{ip}  {prev_mac} → {mac}",
                    {"ip": ip, "old_mac": prev_mac, "new_mac": mac, "gateway": is_gw})
                if is_gw:
                    with _lock:
                        _gateway_mac = mac
    except FileNotFoundError:
        _tshark_ok = False
        print("[WATCHDOG] tshark binary not found — ARP monitoring disabled.", flush=True)
    except Exception as e:
        print(f"[WATCHDOG] ARP monitor error: {e}", flush=True)

def _connection_monitor():
    """Rate-monitor outbound connections using psutil."""
    try:
        import psutil
    except ImportError:
        print("[WATCHDOG] psutil not installed — connection monitoring disabled.", flush=True)
        return

    prev = set()
    print("[WATCHDOG] Connection monitor started.", flush=True)
    while True:
        try:
            curr = {
                (c.raddr.ip, c.raddr.port)
                for c in psutil.net_connections(kind="tcp")
                if c.raddr and c.status == "ESTABLISHED"
                and c.raddr.ip not in ("127.0.0.1", "::1", "")
            }
            new = curr - prev
            if len(new) > 15:
                _add_alert("YELLOW",
                    f"Connection spike: {len(new)} new external connections in 5 s",
                    {"new_count": len(new), "sample": list(new)[:5]})
            prev = curr
        except Exception:
            pass
        time.sleep(5)

def _threat_decay():
    """Auto-decay YELLOW → GREEN after 5 min of quiet."""
    global _threat
    while True:
        time.sleep(60)
        with _lock:
            if _threat != "YELLOW":
                continue
            cutoff = time.time() - 300
            recent_warn = any(
                time.mktime(time.strptime(a["time"], "%Y-%m-%dT%H:%M:%S")) > cutoff
                and a["level"] in ("YELLOW", "RED")
                for a in _alerts
            )
            if not recent_warn:
                _threat = "GREEN"

# ── HTTP API ──

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _json(self, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        with _lock:
            if p == "/api/watchdog/status":
                now = time.time()
                cutoff = now - 300
                recent = sum(
                    1 for a in _alerts
                    if time.mktime(time.strptime(a["time"], "%Y-%m-%dT%H:%M:%S")) > cutoff
                )
                self._json({
                    "threat_level":  _threat,
                    "tshark_active": _tshark_ok,
                    "known_hosts":   len(_hosts),
                    "arp_entries":   len(_arp_table),
                    "alerts_5min":   recent,
                    "gateway_ip":    _gateway_ip,
                    "gateway_mac":   _gateway_mac or _arp_table.get(_gateway_ip),
                    "ts":            _ts(),
                })
            elif p == "/api/watchdog/alerts":
                self._json({"count": len(_alerts), "alerts": list(_alerts)})
            elif p == "/api/watchdog/arp":
                self._json({"arp_table": _arp_table})
            elif p == "/api/watchdog/hosts":
                self._json({
                    "count": len(_hosts),
                    "hosts": {
                        ip: {
                            "mac":        h["mac"],
                            "first_seen": time.strftime("%Y-%m-%dT%H:%M:%S",
                                          time.localtime(h["first_seen"])),
                            "last_seen":  time.strftime("%Y-%m-%dT%H:%M:%S",
                                          time.localtime(h["last_seen"])),
                        }
                        for ip, h in _hosts.items()
                    },
                })
            else:
                self.send_response(404)
                self.end_headers()

if __name__ == "__main__":
    _gateway_ip = _detect_gateway()
    print("=== SOVEREIGN QRNG — NETWORK WATCHDOG ===")
    print(f"Port:    {PORT}")
    print(f"Gateway: {_gateway_ip or 'auto-detecting...'}")
    print(f"Status:  http://127.0.0.1:{PORT}/api/watchdog/status")
    print(f"Hosts:   http://127.0.0.1:{PORT}/api/watchdog/hosts")

    threading.Thread(target=_arp_monitor,       daemon=True).start()
    threading.Thread(target=_connection_monitor, daemon=True).start()
    threading.Thread(target=_threat_decay,       daemon=True).start()

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.serve_forever()
