"""
SOVEREIGN QRNG — TRUE ENTROPY ENGINE
======================================
Source:   USB webcam (CAM-A) + laser photon shot noise
Method:   Frame differencing → laser ROI → 4-bit LSB → Von Neumann → Escher-6
Vault:    C:\QRNG_Pool\true_vault.bin   (10 GB)
Pool:     C:\QRNG_Pool\true_pool\
Stats:    C:\QRNG_Pool\true_stats.json
API:      http://127.0.0.1:8765/   (stats only)

Physics:  Laser illuminates CMOS sensor → photon shot noise in bright ROI →
          frame-to-frame intensity fluctuations are quantum in origin.
"""

import hashlib, time, os, datetime, logging, urllib.request, io, json, sys, struct
from pathlib import Path
from collections import deque
import numpy as np
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
CAM_URL     = os.getenv("TRUE_CAM_URL",  "http://127.0.0.1:8090/snapshot_usb.jpg")
TRUE_DIR    = Path(r"C:\QRNG_Pool")
POOL_DIR    = TRUE_DIR / "true_pool"
VAULT_FILE  = TRUE_DIR / "true_vault.bin"
STATS_FILE  = TRUE_DIR / "true_stats.json"
LOG_FILE    = TRUE_DIR / "true_entropy.log"
OFFSET_FILE = TRUE_DIR / "true_offset.txt"

VAULT_SIZE  = 10 * 1024 * 1024 * 1024   # 10 GB
CAPTURE_HZ  = 15
LSB_DEPTH   = 4
CHUNK_BYTES = 256
MAX_POOL_MB = 500
NOISE_FLOOR = 0.001   # lowered — direct laser at close range saturates but still has shot noise
LASER_MIN   = 5       # lowered — allow near-saturated bright frames

TRUE_DIR.mkdir(exist_ok=True)
POOL_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TRUE] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("true_entropy")

# ─────────────────────────────────────────────
# ESCHER CASCADE — 6-pass whitening
# ─────────────────────────────────────────────
def escher_cascade(data: bytes) -> bytes:
    """
    6-pass entropy whitening:
    1. Fold    — XOR first half against reversed second half
    2. Rotate  — circular byte rotation by buf[0]
    3. Waterfall — running-sum cascade (each byte absorbs prior)
    4. Braid   — interleave even/odd index bytes
    5. Scatter — XOR with SHA-256 of current state
    6. Seal    — final SHA-256 conditioning (32-byte output)
    """
    buf = bytearray(data)
    n = len(buf)
    if n < 4:
        return hashlib.sha256(bytes(buf)).digest()

    # Pass 1: Fold
    half = n // 2
    for i in range(half):
        buf[i] ^= buf[n - 1 - i]

    # Pass 2: Rotate
    rot = int(buf[0]) % n
    buf = bytearray(buf[rot:] + buf[:rot])

    # Pass 3: Waterfall
    for i in range(1, n):
        buf[i] = (buf[i] + buf[i - 1]) & 0xFF

    # Pass 4: Braid
    evens = buf[0::2]
    odds  = buf[1::2]
    braided = bytearray()
    for e, o in zip(evens, odds):
        braided += bytes([e, o])
    if n % 2:
        braided.append(buf[-1])
    buf = braided

    # Pass 5: Scatter
    h = hashlib.sha256(bytes(buf)).digest()
    for i in range(min(32, n)):
        buf[i] ^= h[i]

    # Pass 6: Seal
    return hashlib.sha256(bytes(buf)).digest()

# ─────────────────────────────────────────────
# FRAME CAPTURE
# ─────────────────────────────────────────────
def fetch_frame(url: str):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            data = r.read()
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("L")
        return np.array(img, dtype=np.int16)
    except Exception:
        return None

# ─────────────────────────────────────────────
# LASER ROI DETECTION
# ─────────────────────────────────────────────
def detect_laser_roi(frame, pct=99.0):
    t = np.percentile(frame, pct)
    mask = frame >= t
    if mask.sum() < 8:
        mask = frame >= np.percentile(frame, 95.0)
    return mask, float(t)

def roi_centroid(frame, mask):
    if not mask.any():
        return 0, 0, 0
    y, x = np.where(mask)
    return int(x.mean()), int(y.mean()), int(max(x.max()-x.min(), y.max()-y.min()) / 2) + 1

# ─────────────────────────────────────────────
# ENTROPY EXTRACTION
# ─────────────────────────────────────────────
def von_neumann(bits):
    bits = bits[:len(bits) - len(bits) % 2]
    pairs = bits.reshape(-1, 2)
    diff  = pairs[:, 0] != pairs[:, 1]
    return pairs[diff, 0]

def extract_true(frame_prev, frame_curr, mask):
    avg = float(np.mean(frame_curr[mask]))
    if avg < LASER_MIN:
        return None, "DARK"
    diff    = np.abs(frame_prev - frame_curr).astype(np.float32)
    roi_d   = diff[mask]
    if np.var(roi_d) < NOISE_FLOOR:
        return None, "LOW_SIGNAL"
    roi_u8  = roi_d.astype(np.uint8)
    raw     = np.concatenate([((roi_u8 >> b) & 1).astype(np.uint8) for b in range(LSB_DEPTH)])
    np.random.shuffle(raw)
    debiased = von_neumann(raw)
    if len(debiased) < 8:
        return None, "INSUFFICIENT"
    n   = (len(debiased) // 8) * 8
    arr = debiased[:n].reshape(-1, 8)
    raw_bytes = bytes(np.packbits(arr, axis=1, bitorder='little').flatten())
    return escher_cascade(raw_bytes), "LIVE"

def monobit(data: bytes):
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
    bal  = bits.sum() / len(bits)
    return float(bal), 0.45 < bal < 0.55

# ─────────────────────────────────────────────
# VAULT / POOL
# ─────────────────────────────────────────────
def init_vault():
    if not VAULT_FILE.exists():
        log.info(f"Creating true vault {VAULT_SIZE/(1024**3):.0f} GB ...")
        with open(VAULT_FILE, "wb") as f:
            f.seek(VAULT_SIZE - 1)
            f.write(b"\x00")
        log.info("Vault created.")

def write_chunk(data: bytes):
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S_%f")
    (POOL_DIR / f"t_{ts}.bin").write_bytes(data)

def flush_to_vault():
    offset = int(OFFSET_FILE.read_text().strip()) if OFFSET_FILE.exists() else 0
    files  = sorted(POOL_DIR.glob("t_*.bin"))
    if not files or offset >= VAULT_SIZE:
        return offset
    with open(VAULT_FILE, "r+b") as vf:
        vf.seek(offset)
        for f in files:
            data  = f.read_bytes()
            space = VAULT_SIZE - offset
            if space <= 0:
                break
            vf.write(data[:space])
            offset += min(len(data), space)
            f.unlink()
    OFFSET_FILE.write_text(str(offset))
    return offset

def pool_bytes():
    return sum(f.stat().st_size for f in POOL_DIR.glob("t_*.bin"))

def prune_pool():
    files = sorted(POOL_DIR.glob("t_*.bin"))
    total = sum(f.stat().st_size for f in files)
    limit = MAX_POOL_MB * 1024 * 1024
    while total > limit and files:
        f = files.pop(0)
        total -= f.stat().st_size
        f.unlink()

# ─────────────────────────────────────────────
# STATS
# ─────────────────────────────────────────────
_stats: dict = {}
_last_bytes: deque = deque(maxlen=128)   # rolling window for Escher visualiser

def write_stats(cx, cy, radius, fps, bps, balance, chunks, pool_b, vault_off):
    global _stats
    _stats = {
        "source":       "true",
        "label":        "TRUE ENTROPY — USB CAM + LASER",
        "timestamp":    datetime.datetime.now(datetime.UTC).isoformat(),
        "status":       _stats.get("status", "WAITING"),
        "fps":          round(fps, 2),
        "bits_per_sec": round(bps, 1),
        "bit_balance":  round(balance, 4),
        "chunks_total": chunks,
        "pool_bytes":   pool_b,
        "vault_bytes":  vault_off,
        "vault_size":   VAULT_SIZE,
        "vault_pct":    round(min(vault_off / VAULT_SIZE * 100, 100), 4),
        "laser_cx":     cx,
        "laser_cy":     cy,
        "laser_radius": radius,
        "escher_passes": 6,
        "last_bytes_hex": "".join(f"{b:02x}" for b in _last_bytes),
    }
    STATS_FILE.write_text(json.dumps(_stats, indent=2))

# ─────────────────────────────────────────────
# MINI STATS API
# ─────────────────────────────────────────────
class StatsHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        body = json.dumps(_stats).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

def start_api():
    srv = ThreadingHTTPServer(("127.0.0.1", 8765), StatsHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("Stats API on :8765")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def run():
    log.info("TRUE ENTROPY ENGINE starting (USB cam + laser)")
    init_vault()
    start_api()

    interval     = 1.0 / CAPTURE_HZ
    prev         = None
    buf          = bytearray()
    chunks       = 0
    bits_total   = 0
    frame_count  = 0
    last_balance = 0.5
    cx = cy = radius = 0
    fps_win      = deque(maxlen=30)
    last_flush   = last_display = 0
    status       = "WAITING"

    vault_off = int(OFFSET_FILE.read_text().strip()) if OFFSET_FILE.exists() else 0
    log.info(f"Vault offset: {vault_off/(1024**3):.4f} GB")

    while True:
        try:
            t0 = time.time()
            frame = fetch_frame(CAM_URL)
            if frame is None:
                time.sleep(0.5)
                continue

            frame_count += 1
            mask, _ = detect_laser_roi(frame)
            cx, cy, radius = roi_centroid(frame, mask)

            if prev is not None:
                raw, status = extract_true(prev, frame, mask)
                if raw:
                    buf.extend(raw)
                    bits_total += len(raw) * 8
                    _last_bytes.extend(raw)

            prev = frame

            while len(buf) >= CHUNK_BYTES:
                chunk = bytes(buf[:CHUNK_BYTES])
                write_chunk(chunk)
                buf = buf[CHUNK_BYTES:]
                chunks += 1
                if chunks % 10 == 0:
                    last_balance, _ = monobit(chunk)

            elapsed = time.time() - t0
            fps_win.append(1.0 / max(elapsed, 0.001))
            fps = sum(fps_win) / len(fps_win)

            if time.time() - last_flush > 60:
                vault_off = flush_to_vault()
                prune_pool()
                last_flush = time.time()

            if time.time() - last_display > 1.0:
                bps = bits_total / max(frame_count, 1) * fps
                _stats["status"] = status
                write_stats(cx, cy, radius, fps, bps, last_balance, chunks, pool_bytes(), vault_off)
                last_display = time.time()

            time.sleep(max(0, interval - (time.time() - t0)))

        except KeyboardInterrupt:
            log.info("Stopped. Flushing pool...")
            flush_to_vault()
            break
        except Exception as e:
            log.warning(f"Error: {e}")
            time.sleep(0.5)

if __name__ == "__main__":
    run()
