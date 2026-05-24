"""
SOVEREIGN QRNG — LASER PHOTON SHOT NOISE ENTROPY ENGINE
=========================================================
Source:   USB HD webcam + laser
Method:   Quantum photon shot noise via rapid frame differencing on CMOS sensor
Pipeline: Webcam frame -> laser ROI detection -> frame diff -> LSB extract
          -> Von Neumann debias -> SHA-256 conditioning -> entropy pool

Dual-camera support: pulls from CAM-A and CAM-B if both are online.
"""

import hashlib, time, os, datetime, logging, urllib.request, io, json, sys
from pathlib import Path
from collections import deque
import numpy as np

# =============================================================================
# CONFIGURATION  (updated for PHOENIX / HP user)
# =============================================================================

CAM_A_URL     = os.getenv("WEBCAM_URL",   "http://127.0.0.1:8090/snapshot.jpg")
CAM_B_URL     = os.getenv("WEBCAM_B_URL", "http://127.0.0.1:8090/snapshot_b.jpg")
_qpool        = Path(os.getenv("QRNG_POOL_DIR", r"D:\STORAGE\QRNG_Pool"))
POOL_DIR      = Path(os.getenv("ENTROPY_POOL_DIR", str(_qpool / "pool")))
VAULT_FILE    = Path(os.getenv("VAULT_FILE",       str(_qpool / "entropy_vault.bin")))
VAULT_SIZE    = 10 * 1024 * 1024 * 1024
LOG_FILE      = _qpool / "laser_entropy.log"
STATS_FILE    = _qpool / "laser_entropy_stats.json"
OFFSET_FILE   = _qpool / "vault_offset.txt"

CAPTURE_HZ    = 15
LSB_DEPTH     = 4
CHUNK_BYTES   = 256
MAX_POOL_MB   = 500
NOISE_FLOOR   = 0.1
LASER_MIN_VAL = 30

POOL_DIR.mkdir(parents=True, exist_ok=True)

# =============================================================================
# LOGGING
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("laser_entropy")

# =============================================================================
# FRAME CAPTURE
# =============================================================================
def fetch_frame(url: str):
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            data = r.read()
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("L")
        return np.array(img, dtype=np.int16)
    except Exception:
        return None

# =============================================================================
# LASER ROI DETECTION
# =============================================================================
def detect_laser_roi(frame, percentile=99.0):
    threshold = np.percentile(frame, percentile)
    mask = frame >= threshold
    if mask.sum() < 10:
        mask = frame >= np.percentile(frame, 95.0)
    return mask, float(threshold)

def get_roi_stats(frame, mask):
    roi = frame[mask]
    if len(roi) == 0:
        return 0, 0, 0
    y, x = np.where(mask)
    return int(x.mean()), int(y.mean()), int(max(x.max()-x.min(), y.max()-y.min()) / 2) + 1

# =============================================================================
# ENTROPY EXTRACTION
# =============================================================================
def von_neumann_debias(bits):
    bits = bits[:len(bits) - len(bits) % 2]
    pairs = bits.reshape(-1, 2)
    diff  = pairs[:, 0] != pairs[:, 1]
    return pairs[diff, 0]

def extract_entropy(frame_a, frame_b, mask):
    avg = np.mean(frame_b[mask])
    if avg < LASER_MIN_VAL:
        return None, "OFFLINE_DARK"
    diff     = np.abs(frame_a - frame_b).astype(np.float32)
    roi_diff = diff[mask]
    if np.var(roi_diff) < NOISE_FLOOR:
        return None, "OFFLINE_LOW_ENERGY"
    roi_u8   = roi_diff.astype(np.uint8)
    raw_bits = np.concatenate([((roi_u8 >> b) & 1).astype(np.uint8) for b in range(LSB_DEPTH)])
    np.random.shuffle(raw_bits)
    debiased = von_neumann_debias(raw_bits)
    if len(debiased) < 8:
        return None, "INSUFFICIENT"
    n    = (len(debiased) // 8) * 8
    arr  = debiased[:n].reshape(-1, 8)
    return bytes(np.packbits(arr, axis=1, bitorder='little').flatten()), "LIVE"

def sha256_condition(raw: bytes) -> bytes:
    return hashlib.sha256(raw).digest()

def monobit_test(data: bytes):
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
    bal  = bits.sum() / len(bits)
    return float(bal), 0.45 < bal < 0.55

# =============================================================================
# POOL / VAULT WRITING
# =============================================================================
def write_pool_chunk(data: bytes):
    ts    = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S_%f")
    (POOL_DIR / f"entropy_{ts}.bin").write_bytes(data)

def flush_pool_to_vault():
    """Consolidate pool chunks into the main vault binary."""
    offset = int(OFFSET_FILE.read_text().strip()) if OFFSET_FILE.exists() else 0
    files  = sorted(POOL_DIR.glob("entropy_*.bin"))
    if not files or offset >= VAULT_SIZE:
        return offset
    with open(VAULT_FILE, "r+b") as vf:
        vf.seek(offset)
        for f in files:
            data       = f.read_bytes()
            space_left = VAULT_SIZE - offset
            if space_left <= 0:
                break
            chunk = data[:space_left]
            vf.write(chunk)
            offset += len(chunk)
            f.unlink()
    OFFSET_FILE.write_text(str(offset))
    return offset

def pool_size_bytes():
    return sum(f.stat().st_size for f in POOL_DIR.glob("entropy_*.bin"))

def prune_pool():
    files = sorted(POOL_DIR.glob("entropy_*.bin"))
    total = sum(f.stat().st_size for f in files)
    limit = MAX_POOL_MB * 1024 * 1024
    while total > limit and files:
        oldest = files.pop(0)
        total -= oldest.stat().st_size
        oldest.unlink()

# =============================================================================
# DISPLAY
# =============================================================================
def display_stats(frame_count, chunks, bits_total, balance, cx, cy, radius,
                  fps, status, vault_offset, dual_cam):
    os.system("cls")
    pool_mb  = pool_size_bytes() / (1024*1024)
    bps      = bits_total / max(frame_count, 1) * fps
    vault_gb = vault_offset / (1024**3)
    vault_pct= min(vault_offset / VAULT_SIZE * 100, 100)

    wave_chars = ["_",".","-","~","=","+","*","#","@"]
    wave = "".join(wave_chars[min(int(abs(balance-0.5)*30 + np.random.randint(0,2)),8)] for _ in range(30))

    color = "\033[92m" if status == "LIVE" else "\033[91m"
    dual  = "\033[93m[DUAL-CAM]\033[0m" if dual_cam else "\033[90m[SINGLE-CAM]\033[0m"

    print("\033[96m")
    print("  ╔══════════════════════════════════════════════════════════╗")
    print("  ║      SOVEREIGN QRNG — LASER PHOTON SHOT NOISE           ║")
    print("  ╚══════════════════════════════════════════════════════════╝")
    print(f"\033[0m  {dual}  {color}STATUS: {status}\033[0m")
    print(f"\n  [ LASER ] >>>>>> || >>>>>> [ SENSOR ]")
    print(f"  FLOW: {wave}")
    print(f"\n  {'─'*58}")
    print(f"  Laser Lock:    ({cx}, {cy})  radius {radius}px")
    print(f"  Frame Rate:    {fps:.1f} fps")
    print(f"  Entropy Rate:  {bps:.0f} bits/sec  ({bps/8:.0f} bytes/sec)")
    print(f"  Bit Balance:   {balance*100:.2f}%  {'[OK]' if 0.45<balance<0.55 else '[BIASED]'}")
    print(f"  {'─'*58}")
    print(f"  Pool Buffer:   {pool_mb:.2f} MB  ({chunks} chunks written)")
    print(f"  Vault Fill:    {vault_gb:.4f} GB / 10.00 GB  ({vault_pct:.3f}%)")
    print(f"  {'─'*58}")
    print(f"  Pool:   C:\\QRNG_Pool\\pool\\")
    print(f"  Vault:  C:\\QRNG_Pool\\entropy_vault.bin")
    print(f"  API:    http://127.0.0.1:8765/entropy")
    print(f"  UI:     http://127.0.0.1:8888")

def save_stats(cx, cy, radius, fps, bps, balance, chunks, pool_bytes, vault_offset):
    STATS_FILE.write_text(json.dumps({
        "timestamp":    datetime.datetime.now(datetime.UTC).isoformat(),
        "laser_spot":   {"x": cx, "y": cy, "radius": radius},
        "fps":          round(fps, 2),
        "bits_per_sec": round(bps, 1),
        "bit_balance":  round(balance, 4),
        "chunks":       chunks,
        "pool_bytes":   pool_bytes,
        "vault_offset": vault_offset,
        "rig_id":       "SOVEREIGN-QRNG-PHOENIX"
    }, indent=2))

# =============================================================================
# MAIN LOOP
# =============================================================================
def run():
    log.info("SOVEREIGN QRNG Engine starting on PHOENIX...")
    interval     = 1.0 / CAPTURE_HZ
    prev_a       = prev_b = None
    buffer       = bytearray()
    chunks       = 0
    bits_total   = 0
    frame_count  = 0
    last_balance = 0.5
    cx = cy = radius = 0
    fps_win      = deque(maxlen=30)
    last_display = last_vault_flush = 0
    status       = "WAITING"

    vault_offset = int(OFFSET_FILE.read_text().strip()) if OFFSET_FILE.exists() else 0
    log.info(f"Vault offset: {vault_offset / (1024**3):.4f} GB")

    while True:
        try:
            t0 = time.time()

            frame_a = fetch_frame(CAM_A_URL)
            frame_b = fetch_frame(CAM_B_URL)
            dual    = frame_a is not None and frame_b is not None
            frame   = frame_a if frame_a is not None else frame_b
            if frame is None:
                time.sleep(0.5)
                continue

            frame_count += 1
            mask, _ = detect_laser_roi(frame)
            cx, cy, radius = get_roi_stats(frame, mask)

            # Extract entropy from CAM-A vs its previous frame
            if prev_a is not None and frame_a is not None:
                raw, status = extract_entropy(prev_a, frame_a, mask)
                if raw:
                    buffer.extend(sha256_condition(raw))
                    bits_total += len(raw) * 8

            # Also mix in CAM-B vs its previous frame
            if prev_b is not None and frame_b is not None:
                mask_b, _ = detect_laser_roi(frame_b)
                raw_b, _ = extract_entropy(prev_b, frame_b, mask_b)
                if raw_b:
                    buffer.extend(sha256_condition(raw_b))
                    bits_total += len(raw_b) * 8

            if frame_a is not None: prev_a = frame_a
            if frame_b is not None: prev_b = frame_b

            while len(buffer) >= CHUNK_BYTES:
                chunk = bytes(buffer[:CHUNK_BYTES])
                write_pool_chunk(chunk)
                buffer = buffer[CHUNK_BYTES:]
                chunks += 1
                if chunks % 10 == 0:
                    last_balance, _ = monobit_test(chunk)

            elapsed = time.time() - t0
            fps_win.append(1.0 / max(elapsed, 0.001))
            fps = sum(fps_win) / len(fps_win)

            # Flush pool → vault every 60s
            if time.time() - last_vault_flush > 60:
                vault_offset = flush_pool_to_vault()
                prune_pool()
                last_vault_flush = time.time()

            if time.time() - last_display > 1.0:
                bps = bits_total / max(frame_count, 1) * fps
                display_stats(frame_count, chunks, bits_total, last_balance,
                              cx, cy, radius, fps, status, vault_offset, dual)
                save_stats(cx, cy, radius, fps, bps, last_balance, chunks,
                           pool_size_bytes(), vault_offset)
                last_display = time.time()

            time.sleep(max(0, interval - (time.time() - t0)))

        except KeyboardInterrupt:
            log.info("Stopped by user. Flushing pool...")
            flush_pool_to_vault()
            break
        except Exception as e:
            log.warning(f"Frame error: {e}")
            time.sleep(0.5)

if __name__ == "__main__":
    run()
