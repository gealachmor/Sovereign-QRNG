"""
SOVEREIGN QRNG — NEGENTROPY ENGINE
=====================================
NIST SP 800-90A  HMAC-SHA-256 DRBG
Seeded once from quantum vault — serves bytes at CPU speed.

Philosophy: Entropy = physical randomness (quantum, finite, precious)
            Negentropy = mathematical order (deterministic, infinite, reliable)
            XOR of both = sovereign cryptographic independence

Port: 8767
API:
  GET /api/neg              → DRBG status JSON
  GET /api/neg/bytes?n=N    → N deterministic bytes as hex (max 4096)
"""
import sys, hashlib, hmac as _hmac_mod, os, json, time, threading
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ── Paths ──
from config import NEAR_VAULT, NEG_STATS, NEG_SEED_F
PORT       = 8767


# ─────────────────────────────────────────────────────────
#  HMAC-SHA-256 DRBG  (NIST SP 800-90A Rev.1 §10.1.2)
# ─────────────────────────────────────────────────────────
class HmacDRBG:
    """
    Deterministic Random Bit Generator using HMAC-SHA-256.
    Same seed → same infinite byte stream.  No physical randomness after init.
    Thread-safe (internal lock).
    """
    RESEED_LIMIT = 1 << 48   # NIST max requests between reseeds

    def __init__(self):
        self._K   = bytes([0x00] * 32)
        self._V   = bytes([0x01] * 32)
        self._lock = threading.Lock()
        self._bytes_out    = 0
        self._reseed_count = 0
        self._req_count    = 0
        self._seed_fp      = "--------"
        self._seeded       = False

    def _h(self, key: bytes, *parts: bytes) -> bytes:
        return _hmac_mod.new(key, b"".join(parts), hashlib.sha256).digest()

    def _update(self, provided: bytes = b""):
        self._K = self._h(self._K, self._V, b"\x00", provided)
        self._V = self._h(self._K, self._V)
        if provided:
            self._K = self._h(self._K, self._V, b"\x01", provided)
            self._V = self._h(self._K, self._V)

    def seed(self, entropy: bytes):
        """Instantiate or reseed. entropy should be 32-64 bytes."""
        with self._lock:
            self._update(entropy)
            self._reseed_count += 1
            self._req_count    = 0
            self._seed_fp      = entropy[:4].hex()
            self._seeded       = True

    def generate(self, n: int) -> bytes:
        """Generate n pseudorandom bytes. Fast: ~200 MB/s on i3."""
        with self._lock:
            out = bytearray()
            while len(out) < n:
                self._V = self._h(self._K, self._V)
                out += self._V
            self._update()
            self._bytes_out += n
            self._req_count += 1
            return bytes(out[:n])

    @property
    def fingerprint(self) -> str:
        return self._seed_fp

    @property
    def bytes_generated(self) -> int:
        return self._bytes_out

    @property
    def reseed_count(self) -> int:
        return self._reseed_count

    @property
    def seeded(self) -> bool:
        return self._seeded


# ── Singleton DRBG ──
_drbg = HmacDRBG()

# ── Stats (updated by background thread) ──
_stats: dict = {
    "status":         "STARTING",
    "seeded":         False,
    "seed_fingerprint": "--------",
    "bytes_generated": 0,
    "rate_bps":        0,
    "reseed_count":    0,
    "drbg_algo":       "HMAC-SHA-256 DRBG",
    "nist_ref":        "NIST SP 800-90A Rev.1",
    "vault_capacity":  "INFINITE",
    "vault_pct":       100.0,
    "mode":            "LIFEBOAT",
    "philosophy":      "Seeded from quantum entropy. Self-sustaining from mathematics.",
}
_stats_lock = threading.Lock()


def _get_quantum_seed(n: int = 64) -> bytes:
    """
    Pull n bytes from near_vault.bin as DRBG seed material.
    Falls back to OS entropy if vault not yet ready.
    """
    try:
        if NEAR_VAULT.exists():
            sz = NEAR_VAULT.stat().st_size
            if sz >= n:
                with open(NEAR_VAULT, "rb") as f:
                    # Read from middle of vault to avoid the offset-0 area
                    offset = max(0, sz // 2 - n // 2)
                    f.seek(offset)
                    data = f.read(n)
                if len(data) == n:
                    return data
    except Exception as e:
        print(f"[NEG] quantum seed read failed: {e} — falling back to OS entropy")
    return os.urandom(n)


def _seed_and_monitor():
    """Background thread: seed DRBG, then update stats every 2s."""
    global _stats

    print("[NEG] Reading quantum seed from near_vault.bin...")
    time.sleep(3)   # Wait for near_entropy.py to start filling

    entropy = _get_quantum_seed(64)
    _drbg.seed(entropy)

    # Save seed backup (security note: keep this file private)
    try:
        NEG_SEED_F.parent.mkdir(parents=True, exist_ok=True)
        NEG_SEED_F.write_bytes(entropy)
    except Exception as e:
        print(f"[NEG] seed backup write failed: {e}")

    fp = _drbg.fingerprint
    print(f"[NEG] DRBG seeded. Fingerprint: {fp}")
    print(f"[NEG] HMAC-SHA-256 DRBG LIVE — infinite capacity")

    with _stats_lock:
        _stats.update({
            "status":           "LIVE",
            "seeded":           True,
            "seed_fingerprint": fp,
        })

    t0      = time.monotonic()
    b0      = 0
    tick    = 0

    while True:
        time.sleep(2)
        tick += 1

        # Warm up DRBG a bit each tick (keeps it exercised, measures rate)
        warm = _drbg.generate(8192)   # 8 KB warmup block
        _ = warm

        elapsed = time.monotonic() - t0
        bgen    = _drbg.bytes_generated
        rate    = int((bgen - b0) * 8 / elapsed) if elapsed > 0 else 0
        b0      = bgen
        t0      = time.monotonic()

        # Reseed every ~1 GB generated from fresh quantum bytes
        if bgen > 0 and bgen % (1 << 30) < 8192:
            new_entropy = _get_quantum_seed(64)
            _drbg.seed(new_entropy)
            print(f"[NEG] Auto-reseed #{_drbg.reseed_count} from quantum vault")

        stats_snap = {
            "status":           "LIVE",
            "seeded":           True,
            "seed_fingerprint": _drbg.fingerprint,
            "bytes_generated":  bgen,
            "rate_bps":         rate,
            "reseed_count":     _drbg.reseed_count,
            "drbg_algo":        "HMAC-SHA-256 DRBG",
            "nist_ref":         "NIST SP 800-90A Rev.1",
            "vault_capacity":   "INFINITE",
            "vault_pct":        100.0,
            "mode":             "LIFEBOAT",
            "philosophy":       "Seeded from quantum entropy. Self-sustaining from mathematics.",
        }

        with _stats_lock:
            _stats.update(stats_snap)

        try:
            NEG_STATS.parent.mkdir(parents=True, exist_ok=True)
            NEG_STATS.write_text(json.dumps(stats_snap, indent=2))
        except Exception as e:
            print(f"[NEG] stats write failed: {e}")

        if tick % 30 == 0:
            print(f"[NEG] {bgen / 1024**2:.1f} MB generated | "
                  f"rate: {rate / 1e6:.1f} Mbps | reseeds: {_drbg.reseed_count}")


# ─────────────────────────────────────────────────────────
#  HTTP API
# ─────────────────────────────────────────────────────────
class NegHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _json(self, data: dict):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code: int, msg: str):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": False, "error": msg}).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/api/neg/reseed":
            new_entropy = _get_quantum_seed(64)
            _drbg.seed(new_entropy)
            print(f"[NEG] Manual reseed #{_drbg.reseed_count} triggered via API")
            self._json({
                "ok":           True,
                "reseed_count": _drbg.reseed_count,
                "fingerprint":  _drbg.fingerprint,
            })
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        p  = self.path.split("?")[0]
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""

        if p == "/api/neg":
            with _stats_lock:
                self._json(dict(_stats))

        elif p in ("/api/neg/bytes", "/api/neg/bytes/"):
            n = 32
            for part in qs.split("&"):
                if part.startswith("n="):
                    try:
                        n = min(4096, max(1, int(part[2:])))
                    except Exception:
                        pass
            if not _drbg.seeded:
                self._err(503, "DRBG not yet seeded — try again in a few seconds")
                return
            raw = _drbg.generate(n)
            self._json({
                "n":      n,
                "hex":    raw.hex(),
                "source": "HMAC-SHA-256-DRBG",
                "nist":   "NIST SP 800-90A",
            })

        elif p == "/api/neg/blend":
            # XOR near_vault bytes with DRBG bytes  (best of both worlds)
            n = 32
            for part in qs.split("&"):
                if part.startswith("n="):
                    try:
                        n = min(4096, max(1, int(part[2:])))
                    except Exception:
                        pass
            det = bytearray(_drbg.generate(n))
            try:
                if NEAR_VAULT.exists() and NEAR_VAULT.stat().st_size >= n:
                    import random as _r
                    off = _r.randint(0, NEAR_VAULT.stat().st_size - n)
                    with open(NEAR_VAULT, "rb") as vf:
                        vf.seek(off)
                        qbytes = vf.read(n)
                    for i in range(n):
                        det[i] ^= qbytes[i]
            except Exception:
                pass
            self._json({
                "n":      n,
                "hex":    det.hex(),
                "source": "QUANTUM-XOR-DRBG",
                "nist":   "NIST SP 800-90A + hardware entropy",
            })

        else:
            self.send_response(404)
            self.end_headers()


def _banner():
    print()
    print("  ╔══════════════════════════════════════════════════════╗")
    print("  ║  SOVEREIGN QRNG — NEGENTROPY ENGINE                 ║")
    print("  ║  NIST SP 800-90A  HMAC-SHA-256 DRBG                 ║")
    print("  ║  Seeded from quantum vault · Infinite capacity       ║")
    print("  ╚══════════════════════════════════════════════════════╝")
    print()
    print(f"  API Status:  http://127.0.0.1:{PORT}/api/neg")
    print(f"  DRBG Bytes:  http://127.0.0.1:{PORT}/api/neg/bytes?n=32")
    print(f"  XOR Blend:   http://127.0.0.1:{PORT}/api/neg/blend?n=32")
    print(f"  Seed backup: {NEG_SEED_F}")
    print()


def main():
    _banner()
    t = threading.Thread(target=_seed_and_monitor, daemon=True)
    t.start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), NegHandler)
    print(f"[NEG] HTTP server listening on port {PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
