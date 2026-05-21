"""
SOVEREIGN QRNG â€” DASHBOARD API SERVER
=======================================
Serves the dashboard HTML + aggregates stats from both entropy engines.
Port: 8888

GET /                    â†’ dashboard.html
GET /near                â†’ near_dashboard.html
GET /true                â†’ true_dashboard.html
GET /api/true            â†’ near_entropy stats (port 8765)
GET /api/near            â†’ near_entropy stats (port 8766)
GET /api/both            â†’ combined JSON {true: {...}, near: {...}}
POST /api/contribute     â†’ accept contributed entropy bytes (opt-in pool donation)
GET /api/contribute/stats â†’ contribution stats
"""

import json, os, urllib.request, hashlib, datetime, collections, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DASHBOARD      = Path(__file__).parent / "dashboard.html"
NEAR_DASHBOARD = Path(__file__).parent / "near_dashboard.html"
TRUE_DASHBOARD = Path(__file__).parent / "true_dashboard.html"
from config import TRUE_STATS, NEAR_STATS, CONTRIB_LOG, NEAR_VAULT, NEAR_OFFSET
PORT           = 8888

# Security constants
MAX_POST_BYTES   = 65_536                    # 64 KB hard cap on POST body
NEAR_VAULT_QUOTA = 50  * 1024 * 1024        # 1% of 5 GB vault
TRUE_VAULT_QUOTA = 100 * 1024 * 1024        # 1% of 10 GB vault
RATE_LIMIT_SEC   = 60                        # cooldown per IP

_contrib_count = 0
_contrib_bytes = 0
_rate_limit: dict = collections.defaultdict(float)  # ip -> last_accept_time


def escher_cascade(data: bytes) -> bytes:
    """6-pass whitening: fold, rotate, fall, braid, scatter, SHA-256 seal."""
    buf = bytearray(data)
    n = len(buf)
    if n < 4:
        return hashlib.sha256(bytes(buf)).digest()
    half = n // 2
    for i in range(half):
        buf[i] ^= buf[n - 1 - i]
    rot = int(buf[0]) % n
    buf = bytearray(buf[rot:] + buf[:rot])
    for i in range(1, n):
        buf[i] = (buf[i] + buf[i - 1]) & 0xFF
    evens, odds = buf[0::2], buf[1::2]
    braided = bytearray()
    for e, o in zip(evens, odds):
        braided += bytes([e, o])
    if n % 2:
        braided.append(buf[-1])
    buf = braided
    h = hashlib.sha256(bytes(buf)).digest()
    for i in range(min(32, n)):
        buf[i] ^= h[i]
    return hashlib.sha256(bytes(buf)).digest()

def read_stats(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"status": "OFFLINE", "error": f"{path.name} not found"}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _client_ip(self) -> str:
        return self.client_address[0]

    def _rate_ok(self) -> bool:
        ip = self._client_ip()
        now = time.monotonic()
        if now - _rate_limit[ip] < RATE_LIMIT_SEC:
            return False
        _rate_limit[ip] = now
        return True

    def _err(self, code: int, msg: str):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": False, "error": msg}).encode())

    def do_POST(self):
        global _contrib_count, _contrib_bytes
        p = self.path.split("?")[0]
        if p == "/api/contribute":
            # Rate limit
            if not self._rate_ok():
                self._err(429, f"rate limited — wait {RATE_LIMIT_SEC}s between contributions")
                return
            # 64 KB body cap
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_POST_BYTES:
                self._err(413, f"body too large — max {MAX_POST_BYTES} bytes")
                return
            try:
                body = self.rfile.read(length)
                data = json.loads(body)
                hex_str = data.get("entropy_hex", "")
                if not hex_str or len(hex_str) % 2 != 0:
                    raise ValueError("invalid entropy_hex")
                raw = bytes.fromhex(hex_str)
                # Whiten through Escher cascade before vaulting
                whitened = escher_cascade(raw)
                # Vault quota: 1% cap
                offset = int(NEAR_OFFSET.read_text().strip()) if NEAR_OFFSET.exists() else 0
                if offset >= NEAR_VAULT_QUOTA:
                    self._err(507, "contribution quota reached — sovereign sources have priority")
                    return
                if NEAR_VAULT.exists():
                    with open(NEAR_VAULT, "r+b") as vf:
                        vf.seek(offset)
                        space = NEAR_VAULT_QUOTA - offset
                        vf.write(whitened[:space])
                    NEAR_OFFSET.write_text(str(offset + len(whitened[:space])))
                # Log
                _contrib_count += 1
                _contrib_bytes += len(raw)
                record = {
                    "ts": datetime.datetime.now(datetime.UTC).isoformat(),
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest()[:16],
                }
                with open(CONTRIB_LOG, "a") as f:
                    f.write(json.dumps(record) + "\n")
                self._json({"ok": True, "bytes_accepted": len(raw)})
            except Exception as e:
                self._err(400, str(e))
        elif p == "/api/purge_near":
            # Triple-lock purge — wipe near vault and reset offset
            try:
                purged = 0
                if NEAR_VAULT.exists():
                    purged = NEAR_VAULT.stat().st_size
                    with open(NEAR_VAULT, "wb") as vf:
                        chunk = b'\x00' * 65536
                        written = 0
                        while written < purged:
                            n = min(65536, purged - written)
                            vf.write(chunk[:n])
                            written += n
                NEAR_OFFSET.write_text("0")
                self._json({"ok": True, "purged_bytes": purged})
            except Exception as e:
                self._err(500, str(e))
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/dashboard", "/index.html"):
            if DASHBOARD.exists():
                self._html(DASHBOARD.read_bytes())
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"dashboard.html not found")
        elif p in ("/near", "/near/"):
            if NEAR_DASHBOARD.exists():
                self._html(NEAR_DASHBOARD.read_bytes())
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"near_dashboard.html not found")
        elif p in ("/true", "/true/"):
            if TRUE_DASHBOARD.exists():
                self._html(TRUE_DASHBOARD.read_bytes())
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"true_dashboard.html not found")
        elif p == "/api/true":
            self._json(read_stats(TRUE_STATS))
        elif p == "/api/near":
            self._json(read_stats(NEAR_STATS))
        elif p == "/api/both":
            self._json({"true": read_stats(TRUE_STATS), "near": read_stats(NEAR_STATS)})
        elif p == "/api/contribute/stats":
            self._json({
                "contributions": _contrib_count,
                "bytes_total": _contrib_bytes,
                "kb_total": round(_contrib_bytes / 1024, 2),
            })
        elif p.startswith("/api/bytes"):
            qs = self.path.split("?", 1)
            n_str = "32"
            if len(qs) > 1:
                for part in qs[1].split("&"):
                    if part.startswith("n="):
                        n_str = part[2:]
            try:
                n = min(4096, max(1, int(n_str)))
            except Exception:
                n = 32
            offset = int(NEAR_OFFSET.read_text().strip()) if NEAR_OFFSET.exists() else 0
            if offset >= n and NEAR_VAULT.exists():
                with open(NEAR_VAULT, "rb") as vf:
                    vf.seek(max(0, offset - n))
                    raw = vf.read(n)
            else:
                raw = os.urandom(n)
            self._json({"n": n, "hex": raw.hex()})
        else:
            self.send_response(404)
            self.end_headers()

if __name__ == "__main__":
    print(f"=== SOVEREIGN QRNG DASHBOARD API ===")
    print(f"Dashboard: http://127.0.0.1:{PORT}/")
    print(f"True API:  http://127.0.0.1:{PORT}/api/true")
    print(f"Near API:  http://127.0.0.1:{PORT}/api/near")
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.serve_forever()

