"""
SOVEREIGN QRNG — ENTROPY MARKET API
=====================================
Commercial entropy delivery API for AI agent marketplaces.

Port:    8889  (Cloudflare Tunnel → public HTTPS)
Auth:    Authorization: Bearer <api_key>
Admin:   X-Admin-Token: <admin_token>  (set in C:\QRNG_Pool\market_config.json)

ENDPOINTS
─────────
GET /v1/status                          Public — rig health + vault levels
GET /v1/entropy/true?bytes=N&fmt=hex    Authenticated — TRUE entropy (quantum)
GET /v1/entropy/near?bytes=N&fmt=hex    Authenticated — NEAR entropy (RF+cam)
GET /v1/entropy/mixed?bytes=N&fmt=hex   Authenticated — 50/50 blend
GET /v1/usage                           Authenticated — caller's usage stats

POST /admin/keys                        Admin — create API key (JSON body)
DELETE /admin/keys/<key>                Admin — revoke key
GET  /admin/keys                        Admin — list all keys

FORMATS  hex | base64 | bytes (raw binary)
LIMITS   1 byte – 1 048 576 bytes (1 MB) per request
"""

import json, time, os, hashlib, secrets, datetime, threading, base64, struct
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# Ed25519 signing — cryptography package (installed by LAUNCH_NEAR.ps1)
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PrivateFormat, PublicFormat, NoEncryption, load_pem_private_key,
    )
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False
    print("  [WARN] cryptography not installed — Ed25519 signing disabled. Run: pip install cryptography")

# ── PATHS ────────────────────────────────────────────
POOL_DIR       = Path(r"C:\QRNG_Pool")
TRUE_VAULT     = POOL_DIR / "true_vault.bin"
NEAR_VAULT     = POOL_DIR / "near_vault.bin"
TRUE_WR_OFF    = POOL_DIR / "true_offset.txt"      # written by entropy engine
NEAR_WR_OFF    = POOL_DIR / "near_offset.txt"
TRUE_RD_OFF    = POOL_DIR / "true_read_offset.txt" # consumed by market API
NEAR_RD_OFF    = POOL_DIR / "near_read_offset.txt"
TRUE_STATS     = POOL_DIR / "true_stats.json"
NEAR_STATS     = POOL_DIR / "near_stats.json"
KEYS_FILE      = POOL_DIR / "api_keys.json"
CONFIG_FILE    = POOL_DIR / "market_config.json"
USAGE_FILE     = POOL_DIR / "market_usage.json"
SIGNING_KEY_FILE = POOL_DIR / "signing_key.pem"

PORT           = 8889
MAX_BYTES_REQ  = 1 * 1024 * 1024   # 1 MB per request hard cap
MIN_RESERVE    = 10 * 1024 * 1024  # keep 10 MB in vault before serving

# ── CONFIG / ADMIN TOKEN ──────────────────────────────
def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    cfg = {"admin_token": secrets.token_hex(32), "created": str(datetime.datetime.utcnow())}
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    print(f"\n  [!] Admin token generated and saved to {CONFIG_FILE}")
    print(f"      Admin token: {cfg['admin_token']}\n")
    return cfg

CONFIG = load_config()

# ── Ed25519 SIGNING KEY ───────────────────────────────
def _load_or_create_signing_key():
    if not _CRYPTO_OK:
        return None
    if SIGNING_KEY_FILE.exists():
        pem = SIGNING_KEY_FILE.read_bytes()
        return load_pem_private_key(pem, password=None)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    SIGNING_KEY_FILE.write_bytes(pem)
    pub_hex = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    print(f"\n  [Ed25519] New signing keypair generated.")
    print(f"  [Ed25519] Public key : {pub_hex}")
    print(f"  [Ed25519] Saved to   : {SIGNING_KEY_FILE}\n")
    return key

_SIGNING_KEY = _load_or_create_signing_key()

def _pubkey_hex() -> str:
    if not _SIGNING_KEY:
        return ""
    return _SIGNING_KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

def _pubkey_pem() -> str:
    if not _SIGNING_KEY:
        return ""
    return _SIGNING_KEY.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
    ).decode()

def sign_entropy(timestamp: str, n: int, entropy_hex: str) -> str:
    """Return hex Ed25519 signature over '{timestamp}:{n}:{entropy_hex}'."""
    if not _SIGNING_KEY:
        return ""
    msg = f"{timestamp}:{n}:{entropy_hex}".encode()
    return _SIGNING_KEY.sign(msg).hex()

# ── API KEYS ──────────────────────────────────────────
_keys_lock = threading.Lock()

def load_keys():
    if KEYS_FILE.exists():
        try:
            return json.loads(KEYS_FILE.read_text())
        except Exception:
            pass
    return {}

def save_keys(keys):
    KEYS_FILE.write_text(json.dumps(keys, indent=2))

def get_key(token: str):
    with _keys_lock:
        keys = load_keys()
        return keys.get(token)

def create_key(name: str, tier: str = "basic", daily_mb: float = 10.0):
    token = "sk_" + secrets.token_urlsafe(32)
    record = {
        "name":           name,
        "tier":           tier,
        "daily_limit_mb": daily_mb,
        "created":        str(datetime.datetime.utcnow()),
        "active":         True,
        "total_bytes":    0,
    }
    with _keys_lock:
        keys = load_keys()
        keys[token] = record
        save_keys(keys)
    return token, record

def revoke_key(token: str):
    with _keys_lock:
        keys = load_keys()
        if token in keys:
            keys[token]["active"] = False
            save_keys(keys)
            return True
    return False

# ── USAGE METERING ────────────────────────────────────
_usage_lock = threading.Lock()

def load_usage():
    if USAGE_FILE.exists():
        try:
            return json.loads(USAGE_FILE.read_text())
        except Exception:
            pass
    return {}

def record_usage(token: str, n_bytes: int, source: str):
    today = str(datetime.date.today())
    with _usage_lock:
        usage = load_usage()
        if token not in usage:
            usage[token] = {}
        if today not in usage[token]:
            usage[token][today] = {"bytes": 0, "calls": 0, "true": 0, "near": 0}
        usage[token][today]["bytes"] += n_bytes
        usage[token][today]["calls"] += 1
        usage[token][today][source]  += n_bytes
        USAGE_FILE.write_text(json.dumps(usage, indent=2))
        # Update total in key record
        keys = load_keys()
        if token in keys:
            keys[token]["total_bytes"] = keys[token].get("total_bytes", 0) + n_bytes
            save_keys(keys)
        return usage[token][today]

def daily_usage_bytes(token: str) -> int:
    today = str(datetime.date.today())
    with _usage_lock:
        usage = load_usage()
        return usage.get(token, {}).get(today, {}).get("bytes", 0)

# ── VAULT READER ──────────────────────────────────────
_vault_lock = {"true": threading.Lock(), "near": threading.Lock()}

def _read_cursor(path: Path) -> int:
    try:
        return int(path.read_text().strip())
    except Exception:
        return 0

def _write_cursor(vault: str) -> int:
    f = TRUE_WR_OFF if vault == "true" else NEAR_WR_OFF
    return _read_cursor(f)

def _vault_available(vault: str) -> int:
    rd = _read_cursor(TRUE_RD_OFF if vault == "true" else NEAR_RD_OFF)
    wr = _write_cursor(vault)
    avail = wr - rd
    return max(0, avail - MIN_RESERVE)

def serve_entropy(vault: str, n: int) -> bytes | None:
    """
    Read n bytes from vault at the current read cursor.
    Returns bytes or None if insufficient entropy.
    """
    rd_file  = TRUE_RD_OFF  if vault == "true" else NEAR_RD_OFF
    vlt_file = TRUE_VAULT   if vault == "true" else NEAR_VAULT

    with _vault_lock[vault]:
        rd = _read_cursor(rd_file)
        wr = _write_cursor(vault)
        if wr - rd < n + MIN_RESERVE:
            return None
        if not vlt_file.exists():
            return None
        with open(vlt_file, "rb") as f:
            f.seek(rd)
            data = f.read(n)
        if len(data) < n:
            return None
        rd_file.write_text(str(rd + n))
        return data

# ── HELPERS ───────────────────────────────────────────
def read_stats(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}

def format_entropy(data: bytes, fmt: str) -> tuple[bytes, str]:
    if fmt == "base64":
        return base64.b64encode(data), "application/json"
    if fmt == "bytes":
        return data, "application/octet-stream"
    # default: hex
    return data.hex().encode(), "application/json"

# ── REQUEST HANDLER ───────────────────────────────────
class MarketHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    # ── response helpers ────────────────────────────────
    def _json(self, code, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _raw(self, code, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, msg):
        self._json(code, {"error": msg, "code": code})

    # ── authentication ──────────────────────────────────
    def _auth(self):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None, None
        token = auth[7:].strip()
        rec   = get_key(token)
        if not rec or not rec.get("active"):
            return None, None
        return token, rec

    def _admin(self):
        return self.headers.get("X-Admin-Token", "") == CONFIG["admin_token"]

    # ── parse request ───────────────────────────────────
    def _parse(self):
        parsed = urlparse(self.path)
        qs     = parse_qs(parsed.query)
        return parsed.path.rstrip("/"), qs

    # ── OPTIONS (CORS preflight) ────────────────────────
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers",
                         "Authorization, X-Admin-Token, Content-Type")
        self.send_header("Access-Control-Allow-Methods",
                         "GET, POST, DELETE, OPTIONS")
        self.end_headers()

    # ── GET ─────────────────────────────────────────────
    def do_GET(self):
        path, qs = self._parse()

        # ── Public: pubkey ───────────────────────────
        if path == "/v1/pubkey":
            self._json(200, {
                "algorithm":      "Ed25519",
                "public_key_hex": _pubkey_hex(),
                "public_key_pem": _pubkey_pem(),
                "signing_enabled": _CRYPTO_OK and _SIGNING_KEY is not None,
                "verify_message":  "{timestamp}:{bytes}:{entropy_hex}",
                "note": "Verify with: Ed25519.verify(pubkey_hex, msg.encode(), sig_hex)",
            })
            return

        # ── Public: status ────────────────────────────
        if path == "/v1/status":
            ts  = read_stats(TRUE_STATS)
            ns  = read_stats(NEAR_STATS)
            self._json(200, {
                "service":        "Sovereign QRNG Entropy Market API",
                "version":        "1.0",
                "timestamp":      datetime.datetime.utcnow().isoformat() + "Z",
                "true_entropy": {
                    "status":       ts.get("status", "UNKNOWN"),
                    "bits_per_sec": ts.get("bits_per_sec", 0),
                    "sources":      ["USB webcam", "laser photon shot noise"],
                    "vault_pct":    ts.get("vault_pct", 0),
                    "available_mb": round(_vault_available("true") / (1024**2), 2),
                },
                "near_entropy": {
                    "status":       ns.get("status", "UNKNOWN"),
                    "bits_per_sec": ns.get("bits_per_sec", 0),
                    "sources":      ns.get("active_sources", ["webcam"]),
                    "vault_pct":    ns.get("vault_pct", 0),
                    "available_mb": round(_vault_available("near") / (1024**2), 2),
                },
                "pricing": {
                    "true_entropy":  "$0.05 / MB",
                    "near_entropy":  "$0.02 / MB",
                    "mixed_entropy": "$0.03 / MB",
                },
                "docs": "https://github.com/sovereign-qrng",
                "signing": {
                    "algorithm":      "Ed25519",
                    "enabled":        _CRYPTO_OK and _SIGNING_KEY is not None,
                    "pubkey_endpoint": f"http://127.0.0.1:{PORT}/v1/pubkey",
                },
            })
            return

        # ── Authenticated routes ──────────────────────
        token, rec = self._auth()
        if path.startswith("/v1/entropy") or path == "/v1/usage":
            if not token:
                self._err(401, "Missing or invalid API key. "
                               "Include: Authorization: Bearer <key>")
                return

        # ── Usage ─────────────────────────────────────
        if path == "/v1/usage":
            today  = str(datetime.date.today())
            usage  = load_usage().get(token, {})
            today_u = usage.get(today, {"bytes": 0, "calls": 0})
            limit  = rec.get("daily_limit_mb", 10) * 1024 * 1024
            self._json(200, {
                "key_name":        rec.get("name"),
                "tier":            rec.get("tier"),
                "today":           today_u,
                "daily_limit_mb":  rec.get("daily_limit_mb"),
                "daily_used_mb":   round(today_u.get("bytes", 0) / (1024**2), 4),
                "daily_remain_mb": round(max(0, limit - today_u.get("bytes", 0)) / (1024**2), 4),
                "total_bytes":     rec.get("total_bytes", 0),
            })
            return

        # ── Entropy endpoints ──────────────────────────
        if path in ("/v1/entropy/true", "/v1/entropy/near", "/v1/entropy/mixed"):
            source = path.split("/")[-1]   # true | near | mixed

            # Parse params
            try:
                n = int(qs.get("bytes", ["256"])[0])
            except ValueError:
                self._err(400, "bytes must be an integer"); return
            fmt = qs.get("fmt", ["hex"])[0].lower()
            if fmt not in ("hex", "base64", "bytes"):
                self._err(400, "fmt must be hex | base64 | bytes"); return
            if not (1 <= n <= MAX_BYTES_REQ):
                self._err(400, f"bytes must be 1–{MAX_BYTES_REQ}"); return

            # Daily rate limit check
            limit_bytes = rec.get("daily_limit_mb", 10) * 1024 * 1024
            if daily_usage_bytes(token) + n > limit_bytes:
                self._err(429, f"Daily limit reached ({rec.get('daily_limit_mb')} MB). "
                               "Upgrade your tier or wait until midnight UTC.")
                return

            # Fetch entropy from vault(s)
            if source == "mixed":
                half   = n // 2
                rem    = n - half
                t_data = serve_entropy("true", half)
                n_data = serve_entropy("near", rem)
                if t_data is None or n_data is None:
                    self._err(503, "Insufficient entropy in pool. Pool is rebuilding — retry in 60s.")
                    return
                raw = t_data + n_data
            else:
                raw = serve_entropy(source, n)
                if raw is None:
                    self._err(503, "Insufficient entropy in pool. Pool is rebuilding — retry in 60s.")
                    return

            # Record usage
            usage_today = record_usage(token, n, "true" if source == "true" else "near")

            # Format and respond
            body, ctype = format_entropy(raw, fmt)

            if fmt == "bytes":
                self._raw(200, body, ctype)
            else:
                ts          = datetime.datetime.utcnow().isoformat() + "Z"
                entropy_val = body.decode()
                # Sign over canonical message: timestamp:bytes:entropy_hex
                entropy_hex_for_sig = raw.hex() if fmt == "base64" else entropy_val
                sig = sign_entropy(ts, n, entropy_hex_for_sig)
                self._json(200, {
                    "bytes":          n,
                    "format":         fmt,
                    "source":         source,
                    "entropy":        entropy_val,
                    "timestamp":      ts,
                    "escher_passes":  6,
                    "usage_today_mb": round(usage_today["bytes"] / (1024**2), 4),
                    "signature":      sig,
                    "pubkey":         _pubkey_hex(),
                    "signed_message": f"{ts}:{n}:{entropy_hex_for_sig}",
                })
            return

        # ── Admin: list keys ──────────────────────────
        if path == "/admin/keys":
            if not self._admin():
                self._err(403, "Invalid admin token"); return
            keys = load_keys()
            self._json(200, {
                k: {**v, "key": k[:8] + "..." if len(k) > 8 else k}
                for k, v in keys.items()
            })
            return

        self._err(404, f"Unknown endpoint: {path}")

    # ── POST ────────────────────────────────────────────
    def do_POST(self):
        path, _ = self._parse()

        if path == "/admin/keys":
            if not self._admin():
                self._err(403, "Invalid admin token"); return
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length))
            except Exception:
                self._err(400, "Invalid JSON body"); return
            name      = body.get("name", "unnamed")
            tier      = body.get("tier", "basic")
            daily_mb  = float(body.get("daily_limit_mb", 10))
            token, rec = create_key(name, tier, daily_mb)
            self._json(201, {"key": token, **rec,
                             "note": "Store this key — it will not be shown again."})
            return

        self._err(404, f"Unknown endpoint: {path}")

    # ── DELETE ───────────────────────────────────────────
    def do_DELETE(self):
        path, _ = self._parse()

        if path.startswith("/admin/keys/"):
            if not self._admin():
                self._err(403, "Invalid admin token"); return
            token = path.split("/admin/keys/")[-1]
            ok = revoke_key(token)
            if ok:
                self._json(200, {"revoked": True, "key": token[:12] + "..."})
            else:
                self._err(404, "Key not found")
            return

        self._err(404, f"Unknown endpoint: {path}")


# ── MAIN ──────────────────────────────────────────────
if __name__ == "__main__":
    # Ensure read cursor files exist
    for f in (TRUE_RD_OFF, NEAR_RD_OFF):
        if not f.exists():
            f.write_text("0")

    print(f"\n{'='*58}")
    print(f"  SOVEREIGN QRNG — ENTROPY MARKET API")
    print(f"{'='*58}")
    print(f"  Port:        {PORT}")
    print(f"  Status:      http://127.0.0.1:{PORT}/v1/status")
    print(f"  Admin token: {CONFIG['admin_token'][:12]}...  (full: {CONFIG_FILE})")
    print(f"  Keys file:   {KEYS_FILE}")
    print(f"  Usage log:   {USAGE_FILE}")
    print(f"{'='*58}")
    print(f"\n  Expose publicly:")
    print(f"    cloudflared tunnel --url http://127.0.0.1:{PORT}")
    print(f"  Signing:     Ed25519 {'ENABLED — pubkey: ' + _pubkey_hex()[:24] + '...' if _SIGNING_KEY else 'DISABLED (pip install cryptography)'}")
    print(f"  Pubkey URL:  http://127.0.0.1:{PORT}/v1/pubkey")
    print(f"\n  Create first API key:")
    print(f"    python key_manager.py create --name \"agent_001\" --tier pro --mb 100")
    print(f"{'='*58}\n")

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), MarketHandler)
    srv.serve_forever()
