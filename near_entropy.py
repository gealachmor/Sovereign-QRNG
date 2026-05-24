"""
SOVEREIGN QRNG — NEAR ENTROPY ENGINE v3
========================================
Sources:
  1. Built-in HP TrueVision webcam  — CMOS photon/thermal shot noise, RGB LSB + frame diff
  2. RTL-SDR dongle(s) x2           — ADC thermal noise floor, IQ LSB extraction
  3. CPU timing jitter              — perf_counter_ns() jitter + BCryptGenRandom (CNG)
  4. Microphone / audio ADC         — thermal noise LSBs via sounddevice or pyaudio

All sources feed a shared entropy_queue → Escher-6 whitening → NEAR vault.

Vault:    C:\\QRNG_Pool\\near_vault.bin   (5 GB)
Pool:     C:\\QRNG_Pool\\near_pool\\
Stats:    C:\\QRNG_Pool\\near_stats.json
API:      http://127.0.0.1:8766/
"""

import ctypes, hashlib, time, os, datetime, logging, urllib.request, io, json, sys, threading, socket, struct, subprocess, select
from pathlib import Path
from collections import deque
import numpy as np
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
CAM_URL       = os.getenv("NEAR_CAM_URL", "http://127.0.0.1:8090/snapshot_builtin.jpg")
from config import QRNG_POOL_DIR, RTL_SDR_EXE, RTL_TCP_EXE
NEAR_DIR      = QRNG_POOL_DIR
POOL_DIR      = NEAR_DIR / "near_pool"
VAULT_FILE    = NEAR_DIR / "near_vault.bin"
STATS_FILE    = NEAR_DIR / "near_stats.json"
LOG_FILE      = NEAR_DIR / "near_entropy.log"
OFFSET_FILE   = NEAR_DIR / "near_offset.txt"

VAULT_SIZE    = 5 * 1024 * 1024 * 1024
CAPTURE_HZ    = 15
PIXEL_SAMPLE  = 2000
CHUNK_BYTES   = 256
MAX_POOL_MB   = 300
NOISE_FLOOR   = 0.02

# RTL-SDR config — rtl_sdr.exe stdout pipe
RTL_FREQ        = 100.0e6    # stats display only; actual freqs are per-dongle below
RTL_SAMPLE_RATE = 256e3   # 256 KSPS — pipe fills at 512 KB/s; 128ms headroom vs 5ms GIL switch
RTL_GAIN        = 29.7       # ~30 dB — E4000 snaps to 29.7 dB step; puts FM band thermal+ambient noise at ~30-50% ADC range (std 30-60); 40 dB was amplifying ADC quantization noise floor only (std ~9)
RTL_RETRY_SEC   = 20         # longer retry gap — prevents rapid hammering that corrupts USB state
RTL_MAX_DEVICES = 2          # Both dongles on WinUSB/HVCI-safe; [0]=SMArt XTR v5 E4000, [1]=R828D
# Per-dongle: E4000 (SN:00000001) confirmed on 100 MHz. 300 MHz caused "Failed to set center freq" on this unit.
RTL_TCP_FREQS   = [96.9e6, 500.0e6]     # [0]=96.9MHz inter-station FM gap (dense ambient noise floor, avoids DC LO artifact); [1]=500MHz R828D (slot reserved)
RTL_TCP_PORTS   = [1234,     1235]       # one rtl_tcp instance per dongle
# RTL_SDR_EXE and RTL_TCP_EXE imported from config.py (set QRNG_POOL_DIR env var to relocate)
RTL_TCP_BLOCK   = 16384  # 16 KB chunks — reader drains buffer faster than rtl_tcp fills it

# CPU jitter config
CPU_JITTER_BITS_PER_ITER = 512   # bits collected per tight-loop burst
CPU_JITTER_SLEEP         = 0.02  # seconds between bursts (throttle)

# Audio config
AUDIO_SAMPLE_RATE   = 44100
AUDIO_BLOCK_FRAMES  = 4096
AUDIO_RETRY_SEC     = 3

NEAR_DIR.mkdir(exist_ok=True)
POOL_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [NEAR] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("near_entropy")

# ─────────────────────────────────────────────
# WINDOWS BCryptGenRandom (CNG)
# ─────────────────────────────────────────────
try:
    _bcrypt = ctypes.WinDLL("Bcrypt.dll")
    _BCRYPT_USE_SYSTEM_PREFERRED_RNG = 0x00000002
    def bcrypt_gen_random(n: int) -> bytes:
        buf = (ctypes.c_ubyte * n)()
        _bcrypt.BCryptGenRandom(None, buf, n, _BCRYPT_USE_SYSTEM_PREFERRED_RNG)
        return bytes(buf)
    log.info("BCryptGenRandom (Windows CNG) loaded")
except Exception:
    def bcrypt_gen_random(n: int) -> bytes:
        return os.urandom(n)
    log.warning("BCryptGenRandom unavailable — falling back to os.urandom for CNG seed")

# ─────────────────────────────────────────────
# ESCHER CASCADE — 6-pass whitening
# ─────────────────────────────────────────────
def escher_cascade(data: bytes) -> bytes:
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

def von_neumann(bits):
    bits = bits[:len(bits) - len(bits) % 2]
    pairs = bits.reshape(-1, 2)
    diff = pairs[:, 0] != pairs[:, 1]
    return pairs[diff, 0]

def monobit(data: bytes):
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
    bal = bits.sum() / len(bits)
    return float(bal), 0.45 < bal < 0.55

# ─────────────────────────────────────────────
# SHARED STATE
# ─────────────────────────────────────────────
entropy_queue: deque = deque(maxlen=8192)
_stats: dict = {}
_last_bytes: deque = deque(maxlen=128)

# Per-source status
_rtl  = [{"status": "NOT_CONNECTED", "bps": 0.0} for _ in range(RTL_MAX_DEVICES)]
_cpu  = {"status": "STARTING",  "bps": 0.0}
_audio = {"status": "STARTING", "bps": 0.0}
_rtl_lock = threading.Lock()

# ─────────────────────────────────────────────
# SOURCE 1 — WEBCAM (built-in cam, RGB LSB)
# ─────────────────────────────────────────────
def fetch_frame_rgb(url: str):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            data = r.read()
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGB")
        return np.array(img, dtype=np.uint8)
    except Exception:
        return None

def extract_cam(prev, curr):
    if prev is None:
        return None, "WARMING"
    h, w, _ = curr.shape
    total = h * w
    idx = np.random.choice(total, min(PIXEL_SAMPLE, total), replace=False)
    diff = np.abs(curr.astype(np.int16) - prev.astype(np.int16)).astype(np.uint8)
    flat = diff.reshape(-1, 3)
    samp = flat[idx % len(flat)]
    if np.var(samp.astype(np.float32)) < NOISE_FLOOR:
        return None, "LOW_SIGNAL"
    bits = np.concatenate([
        (samp[:, 0] & 1).astype(np.uint8),
        (samp[:, 1] & 1).astype(np.uint8),
        (samp[:, 2] & 1).astype(np.uint8),
        ((samp[:, 0] >> 1) & 1).astype(np.uint8),
        ((samp[:, 1] >> 1) & 1).astype(np.uint8),
        ((samp[:, 2] >> 1) & 1).astype(np.uint8),
    ])
    np.random.shuffle(bits)
    debiased = von_neumann(bits)
    if len(debiased) < 8:
        return None, "INSUFFICIENT"
    n = (len(debiased) // 8) * 8
    raw = bytes(np.packbits(debiased[:n].reshape(-1, 8), axis=1, bitorder='little').flatten())
    return escher_cascade(raw), "LIVE"

# ─────────────────────────────────────────────
# SOURCE 2 — RTL-SDR DUAL DONGLE via rtl_tcp.exe
#
# Root cause of previous blocking: libusb uses Windows IOCP (I/O Completion Ports)
# which are process-local and non-inheritable. Even multiprocessing.Process children
# hit a zero-wait IOCP path (libusb issue #103/#1043) causing read_bytes() to hang.
#
# Fix: rtl_tcp.exe owns all USB I/O in its own process with a stable IOCP event loop.
# Python connects via plain TCP socket — fully thread-safe, no IOCP involvement.
#
# Pipeline per 64 KB block:
#   1. DC offset removal  — block-subtract mean (removes LO leakage)
#   2. IQ imbalance corr  — Gram-Schmidt orthogonalization
#   3. Phase entropy      — angle(I+jQ) ~ Uniform[-π,π], quantized to 8 bits
#   4. IQ LSB bits        — 4 bits/sample pair (2 LSBs each channel)
#   5. Peres unbiasing    — iterative BFS, extracts 50-90% vs 22-25% VN
#   6. Escher-6 cascade   — SHA-256 sealed whitening
# ─────────────────────────────────────────────

def _launch_rtl_tcp(device_index: int, port: int, freq_hz: float) -> subprocess.Popen:
    """
    Launch rtl_tcp for one dongle.
    Prefers the official C binary (avoids Python ctypes MemoryError on Windows).
    Falls back to embedded Python server if binary is absent.
    """
    log_path = NEAR_DIR / f"rtl_tcp_srv_{device_index}.log"
    log_fh   = open(log_path, "a")

    if RTL_TCP_EXE.exists():
        # Official binary: clean C-level USB I/O, no Python callback issues
        cmd = [
            str(RTL_TCP_EXE),
            "-a", "127.0.0.1",
            "-p", str(port),
            "-d", str(device_index),
            "-f", str(int(freq_hz)),
            "-s", str(int(RTL_SAMPLE_RATE)),
            "-g", str(int(RTL_GAIN * 10)),   # tenths of dB
        ]
    else:
        # Fallback: embedded Python server (may hit ctypes MemoryError on Windows)
        cmd = [
            sys.executable, __file__, "--rtl-tcp-srv",
            str(device_index), str(port), str(int(freq_hz)),
        ]

    # DETACHED_PROCESS breaks the console inheritance chain — rtl_tcp.exe will no
    # longer receive CTRL_C events from the PowerShell parent, which was causing
    # the 5-second "Signal caught, exiting!" disconnect.
    DETACHED = 0x00000008
    return subprocess.Popen(
        cmd,
        stdout=log_fh,
        stderr=log_fh,
        creationflags=subprocess.CREATE_NO_WINDOW | DETACHED,
        close_fds=True,
    )

def _launch_rtl_sdr(device_index: int, freq_hz: float) -> subprocess.Popen:
    """Launch rtl_sdr.exe piping raw uint8 IQ samples to stdout.
    No TCP layer — eliminates the signal-propagation dropout seen with rtl_tcp.exe.
    Gain flag is dB directly (not tenths like the TCP protocol 0x04 command)."""
    log_path = NEAR_DIR / f"rtl_sdr_{device_index}.log"
    cmd = [
        str(RTL_SDR_EXE),
        "-f", str(int(freq_hz)),
        "-s", str(int(RTL_SAMPLE_RATE)),
        "-g", str(int(RTL_GAIN)),   # dB directly — not tenths
        "-d", str(device_index),
        "-n", "0",                  # unlimited samples
        "-",                        # write to stdout
    ]
    # CREATE_NO_WINDOW only — DETACHED_PROCESS breaks stdout pipe inheritance on Windows
    # bufsize=65536: Python-side buffer so read(n) blocks for exactly n bytes (bufsize=0 returns
    # whatever is immediately available, producing odd-length partial blocks that fail I/Q split)
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=open(log_path, "a"),
        creationflags=subprocess.CREATE_NO_WINDOW,
        close_fds=True,
        bufsize=65536,
    )

def _tcp_recv_exact(sock: socket.socket, n: int) -> bytes:
    """Blocking receive of exactly n bytes from a TCP socket."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("rtl_tcp disconnected")
        buf.extend(chunk)
    return bytes(buf)

def _rtl_tcp_cmd(sock: socket.socket, cmd: int, param: int):
    """Send 5-byte rtl_tcp command: 1B cmd + 4B big-endian param."""
    sock.sendall(struct.pack(">BI", cmd, param))

def _peres_unbias(bits: np.ndarray) -> np.ndarray:
    """Peres (1992) iterative unbiasing — extracts 50-90% of bits vs 22-25% VN.
    Uses collections.deque for O(1) popleft — list.pop(0) is O(n) and causes
    quadratic blowup on 98K-bit inputs (takes minutes instead of milliseconds)."""
    from collections import deque
    result_parts = []
    queue = deque([bits])
    while queue:
        b = queue.popleft()
        if len(b) < 32:          # stop at 32 bits — prevents 2^17 tiny-array explosion
            continue
        n = (len(b) // 2) * 2
        pairs = b[:n].reshape(-1, 2)
        same = pairs[pairs[:, 0] == pairs[:, 1]]   # 00 or 11 — biased, recurse
        diff = pairs[pairs[:, 0] != pairs[:, 1]]   # 01 or 10 — uniform output
        if len(diff) == 0:
            continue                                # all pairs same-valued: same.flatten()==b, infinite-loops
        result_parts.append(diff[:, 0])
        if len(same) >= 32:
            queue.append(same.flatten())            # recurse on same-pair bits
        if len(diff) >= 32:
            queue.append(diff[:, 1])               # recurse on second bits of diff
    return np.concatenate(result_parts) if result_parts else np.array([], dtype=np.uint8)

def rtl_worker(device_index: int):
    """RTL-SDR entropy via rtl_sdr.exe stdout pipe — producer/consumer pattern.

    rtl_sdr.exe writes raw uint8 IQ samples directly to stdout.
    No TCP layer means no signal-propagation gap and no 5-second
    'Signal caught, exiting!' dropout that plagued rtl_tcp.exe.

    Pipeline per block:
      1. DC offset removal
      2. IQ imbalance correction (Gram-Schmidt)
      3. Phase entropy quantized to 8 bits
      4. IQ LSB bits (2 LSBs/channel)
      5. Peres unbiasing
      6. Escher-6 cascade
    """
    if not RTL_SDR_EXE.exists():
        with _rtl_lock:
            _rtl[device_index]["status"] = "NO_RTL_SDR_EXE"
        log.error(f"RTL-SDR[{device_index}]: rtl_sdr.exe not found at {RTL_SDR_EXE}")
        return

    freq = RTL_TCP_FREQS[device_index % len(RTL_TCP_FREQS)]

    _iq_queue: deque = deque(maxlen=40)
    _proc_active = threading.Event()
    _proc_active.set()

    def _processor():
        bits_this_sec = 0
        last_rate_t   = time.time()
        with _rtl_lock:
            _rtl[device_index]["dbg_blocks"] = 0
        while _proc_active.is_set():
            if not _iq_queue:
                time.sleep(0.005)
            else:
                raw = _iq_queue.popleft()
                with _rtl_lock:
                    _rtl[device_index]["dbg_blocks"] += 1
                try:
                    raw8 = np.frombuffer(raw, dtype=np.uint8)
                    # Skip malformed blocks — odd-length or too small (happen on crash flush)
                    if len(raw8) >= 4 and len(raw8) % 2 == 0:
                        i_u8 = raw8[0::2]; q_u8 = raw8[1::2]

                        i_f = i_u8.astype(np.float32) - np.mean(i_u8)
                        q_f = q_u8.astype(np.float32) - np.mean(q_u8)

                        i_pwr = float(np.mean(i_f ** 2)) + 1e-12
                        cross = float(np.mean(i_f * q_f))
                        q_f   = q_f - (cross / i_pwr) * i_f
                        q_pwr = float(np.mean(q_f ** 2)) + 1e-12
                        q_f   = q_f * np.sqrt(i_pwr / q_pwr)

                        phases = np.angle(i_f + 1j * q_f)
                        # Adaptive quantization: stretch observed phase range to full 0-255.
                        # Fixed 0-2π mapping wastes 5-7 MSBs when signal is weak (std≈9, unique≈100).
                        p_min = float(phases.min()); p_max = float(phases.max())
                        p_range = p_max - p_min
                        if p_range > 1e-6:
                            phase_u8 = ((phases - p_min) / p_range * 255).clip(0, 255).astype(np.uint8)
                        else:
                            phase_u8 = np.zeros(len(phases), dtype=np.uint8)
                        phase_bits = np.unpackbits(phase_u8)

                        lsb_bits = np.concatenate([
                            (i_u8 & 1).astype(np.uint8), (q_u8 & 1).astype(np.uint8),
                            ((i_u8 >> 1) & 1).astype(np.uint8), ((q_u8 >> 1) & 1).astype(np.uint8),
                        ])

                        # IQ delta bits: consecutive sample differences carry thermal noise
                        # floor variation independent of the signal mean.
                        i_d = np.diff(i_u8.astype(np.int16))
                        q_d = np.diff(q_u8.astype(np.int16))
                        delta_bits = np.concatenate([
                            (i_d & 1).astype(np.uint8), (q_d & 1).astype(np.uint8),
                            ((i_d >> 1) & 1).astype(np.uint8), ((q_d >> 1) & 1).astype(np.uint8),
                        ])

                        combined = np.concatenate([phase_bits, lsb_bits, delta_bits])
                        np.random.shuffle(combined)
                        debiased = _peres_unbias(combined)

                        if len(debiased) >= 8:
                            n8 = (len(debiased) // 8) * 8
                            rb = bytes(np.packbits(
                                debiased[:n8].reshape(-1, 8), axis=1, bitorder='little'
                            ).flatten())
                        else:
                            # Peres returned < 8 bits — XOR IQ window with BCryptGenRandom
                            # so fallback output is not a deterministic hash of constant data.
                            seed = bcrypt_gen_random(64)
                            rb = bytes(a ^ b for a, b in zip(raw8[:64].tobytes(), seed))
                        cond = escher_cascade(rb)
                        entropy_queue.append(cond)
                        bits_this_sec += len(cond) * 8
                except Exception as exc:
                    log.warning(f"RTL-SDR[{device_index}] processor skipping bad block ({len(raw)}B): {exc}")

            now = time.time()
            if now - last_rate_t >= 1.0:
                with _rtl_lock:
                    _rtl[device_index]["bps"] = bits_this_sec / (now - last_rate_t)
                bits_this_sec = 0
                last_rate_t   = now

    proc_thread = threading.Thread(target=_processor, daemon=True, name=f"rtl-proc-{device_index}")
    proc_thread.start()

    while True:
        sdr_proc = None
        _iq_queue.clear()
        # Restart processor thread if it died from an unhandled exception
        if not proc_thread.is_alive():
            log.warning(f"RTL-SDR[{device_index}]: processor thread died, restarting")
            proc_thread = threading.Thread(target=_processor, daemon=True, name=f"rtl-proc-{device_index}")
            proc_thread.start()
        try:
            log.info(f"RTL-SDR[{device_index}]: launching rtl_sdr.exe, {freq/1e6:.2f} MHz, {RTL_GAIN} dB...")
            sdr_proc = _launch_rtl_sdr(device_index, freq)
            # NO sleep here — rtl_sdr.exe streams ~4MB/s and fills the 64KB pipe in ~15ms.
            # Any sleep before first read causes "Short write, samples lost, exiting!" and exit.
            # Read immediately; EOF on first block means device busy, handled below.

            first_block = True
            while True:
                raw = sdr_proc.stdout.read(RTL_TCP_BLOCK)
                if not raw:
                    if first_block:
                        log.warning(f"RTL-SDR[{device_index}]: rtl_sdr.exe exited immediately (device busy?)")
                        with _rtl_lock:
                            _rtl[device_index]["status"] = "NOT_CONNECTED"
                    raise EOFError("rtl_sdr.exe stdout closed")
                if first_block:
                    with _rtl_lock:
                        _rtl[device_index]["status"] = "LIVE"
                    log.info(f"RTL-SDR[{device_index}]: LIVE via stdout pipe, {freq/1e6:.2f} MHz, {RTL_GAIN} dB")
                    first_block = False
                _iq_queue.append(raw)

        except Exception as e:
            log.warning(f"RTL-SDR[{device_index}] error: {e}. Retry in {RTL_RETRY_SEC}s")
        finally:
            with _rtl_lock:
                _rtl[device_index]["status"] = "DISCONNECTED"
                _rtl[device_index]["bps"]    = 0.0
            if sdr_proc and sdr_proc.poll() is None:
                sdr_proc.terminate()
                try: sdr_proc.wait(timeout=3)
                except: sdr_proc.kill()

        time.sleep(RTL_RETRY_SEC)

# ─────────────────────────────────────────────
# SOURCE 3 — CPU TIMING JITTER + BCryptGenRandom
# ─────────────────────────────────────────────
def cpu_jitter_worker():
    _cpu["status"] = "LIVE"
    bits_this_sec = 0
    last_rate_t = time.time()

    log.info("CPU jitter source: started (perf_counter_ns + BCryptGenRandom)")

    while True:
        try:
            # --- Source A: SHA-256 workload timing jitter (full deltas, not LSBs) ---
            sha_deltas = []
            prev = time.perf_counter_ns()
            for _ in range(CPU_JITTER_BITS_PER_ITER):
                hashlib.sha256(str(time.perf_counter_ns()).encode()).digest()
                now = time.perf_counter_ns()
                sha_deltas.append(now - prev)
                prev = now

            # --- Source B: OS scheduler sleep overshoot (independent of L1 cache) ---
            sched_deltas = []
            for _ in range(4):
                t0 = time.perf_counter_ns()
                time.sleep(0.001)
                sched_deltas.append(abs((time.perf_counter_ns() - t0) - 1_000_000))

            # XOR-condition with BCryptGenRandom for defense-in-depth
            bcrypt_seed = bcrypt_gen_random(32)

            # Pack raw deltas from both sources; Escher acts as hash extractor (no VN needed)
            sha_raw   = struct.pack(f">{len(sha_deltas)}Q", *sha_deltas)
            sched_raw = struct.pack(f">{len(sched_deltas)}Q", *sched_deltas)
            combined  = bytearray(sha_raw + sched_raw)
            for i in range(min(32, len(combined))):
                combined[i] ^= bcrypt_seed[i]

            conditioned = escher_cascade(bytes(combined))
            entropy_queue.append(conditioned)
            bits_this_sec += len(conditioned) * 8

            now = time.time()
            if now - last_rate_t >= 1.0:
                _cpu["bps"] = bits_this_sec / (now - last_rate_t)
                bits_this_sec = 0
                last_rate_t = now

            time.sleep(CPU_JITTER_SLEEP)

        except Exception as e:
            log.warning(f"CPU jitter error: {e}")
            _cpu["status"] = "ERROR"
            time.sleep(0.5)
            _cpu["status"] = "LIVE"

# ─────────────────────────────────────────────
# SOURCE 4 — MICROPHONE / AUDIO ADC
# ─────────────────────────────────────────────
def _try_sounddevice(samples_out: list) -> bool:
    try:
        import sounddevice as sd
        data = sd.rec(AUDIO_BLOCK_FRAMES, samplerate=AUDIO_SAMPLE_RATE,
                      channels=1, dtype='int16', blocking=True)
        samples_out.extend(data.flatten().tolist())
        return True
    except Exception:
        return False

def _try_pyaudio(samples_out: list) -> bool:
    try:
        import pyaudio
        p = pyaudio.PyAudio()
        stream = p.open(format=pyaudio.paInt16, channels=1, rate=AUDIO_SAMPLE_RATE,
                        input=True, frames_per_buffer=AUDIO_BLOCK_FRAMES)
        raw = stream.read(AUDIO_BLOCK_FRAMES, exception_on_overflow=False)
        stream.stop_stream(); stream.close(); p.terminate()
        samples_out.extend(np.frombuffer(raw, dtype=np.int16).tolist())
        return True
    except Exception:
        return False

def audio_worker():
    bits_this_sec = 0
    last_rate_t = time.time()
    warned = False

    while True:
        try:
            samples = []
            ok = _try_sounddevice(samples) or _try_pyaudio(samples)

            if not ok:
                if not warned:
                    _audio["status"] = "NOT_INSTALLED"
                    log.warning("Audio source: no sounddevice or pyaudio found. "
                                "Run: pip install sounddevice")
                    warned = True
                time.sleep(AUDIO_RETRY_SEC)
                continue

            warned = False
            arr = np.array(samples, dtype=np.int16)
            bits = np.concatenate([
                (arr & 1).astype(np.uint8),
                ((arr >> 1) & 1).astype(np.uint8),
                ((arr >> 2) & 1).astype(np.uint8),
            ])
            np.random.shuffle(bits)
            debiased = von_neumann(bits)
            if len(debiased) >= 8:
                n = (len(debiased) // 8) * 8
                raw_bytes = bytes(np.packbits(
                    debiased[:n].reshape(-1, 8), axis=1, bitorder='little'
                ).flatten())
                conditioned = escher_cascade(raw_bytes)
                entropy_queue.append(conditioned)
                bits_this_sec += len(conditioned) * 8

            _audio["status"] = "LIVE"

            now = time.time()
            if now - last_rate_t >= 1.0:
                _audio["bps"] = bits_this_sec / (now - last_rate_t)
                bits_this_sec = 0
                last_rate_t = now

        except Exception as e:
            _audio["status"] = "ERROR"
            _audio["bps"] = 0.0
            log.warning(f"Audio source error: {e}")
            time.sleep(AUDIO_RETRY_SEC)

# ─────────────────────────────────────────────
# VAULT / POOL
# ─────────────────────────────────────────────
def init_vault():
    if not VAULT_FILE.exists():
        log.info(f"Creating near vault ({VAULT_SIZE/(1024**3):.0f} GB)...")
        with open(VAULT_FILE, "wb") as f:
            f.seek(VAULT_SIZE - 1)
            f.write(b"\x00")
        log.info("Near vault ready.")

def write_chunk(data: bytes):
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S_%f")
    (POOL_DIR / f"n_{ts}.bin").write_bytes(data)

def flush_to_vault():
    offset = int(OFFSET_FILE.read_text().strip()) if OFFSET_FILE.exists() else 0
    files = sorted(POOL_DIR.glob("n_*.bin"))
    if not files or offset >= VAULT_SIZE:
        return offset
    flushed = []
    with open(VAULT_FILE, "r+b") as vf:
        vf.seek(offset)
        for f in files:
            data = f.read_bytes()
            space = VAULT_SIZE - offset
            if space <= 0: break
            vf.write(data[:space])
            offset += min(len(data), space)
            flushed.append(f)
        vf.flush()
        os.fsync(vf.fileno())
    for f in flushed:
        f.unlink()
    tmp = OFFSET_FILE.with_suffix(".tmp")
    tmp.write_text(str(offset))
    tmp.replace(OFFSET_FILE)
    return offset

def pool_bytes():
    return sum(f.stat().st_size for f in POOL_DIR.glob("n_*.bin"))

def prune_pool():
    files = sorted(POOL_DIR.glob("n_*.bin"))
    total = sum(f.stat().st_size for f in files)
    limit = MAX_POOL_MB * 1024 * 1024
    while total > limit and files:
        f = files.pop(0); total -= f.stat().st_size; f.unlink()

# ─────────────────────────────────────────────
# STATS API — port 8766
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
    srv = ThreadingHTTPServer(("127.0.0.1", 8766), StatsHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("Near stats API on :8766")

def write_stats(cam_status, fps, cam_bps, balance, chunks, pool_b, vault_off):
    with _rtl_lock:
        rtl_snap = [d.copy() for d in _rtl]
    rtl_total_bps = sum(d["bps"] for d in rtl_snap if d["status"] == "LIVE")
    total_bps = cam_bps + rtl_total_bps + _cpu["bps"] + _audio["bps"]

    active = []
    if cam_status == "LIVE":   active.append("webcam")
    for i, d in enumerate(rtl_snap):
        if d["status"] == "LIVE": active.append(f"RTL-SDR[{i}]")
    if _cpu["status"] == "LIVE":   active.append("cpu_jitter")
    if _audio["status"] == "LIVE": active.append("audio_adc")

    _stats.update({
        "source":        "near",
        "label":         "NEAR QUANTUM — CAM + RTL-SDR + CPU JITTER + AUDIO",
        "timestamp":     datetime.datetime.now(datetime.UTC).isoformat(),
        "status":        cam_status,
        "fps":           round(fps, 2),
        # combined
        "bits_per_sec":  round(total_bps, 1),
        # per-source
        "cam_bps":       round(cam_bps, 1),
        "cam_status":    cam_status,
        # RTL-SDR per-dongle
        "rtl0_status":   rtl_snap[0]["status"] if len(rtl_snap) > 0 else "DISABLED",
        "rtl0_bps":      round(rtl_snap[0]["bps"], 1) if len(rtl_snap) > 0 else 0,
        "rtl0_dbg_blocks": rtl_snap[0].get("dbg_blocks", -1),
        "rtl1_status":   rtl_snap[1]["status"] if len(rtl_snap) > 1 else "DISABLED",
        "rtl1_bps":      round(rtl_snap[1]["bps"], 1) if len(rtl_snap) > 1 else 0,
        "rtl_freq_mhz":  RTL_FREQ / 1e6,
        # legacy compat
        "rtl_status":    rtl_snap[0]["status"],
        "rtl_bps":       round(rtl_total_bps, 1),
        # CPU jitter
        "cpu_status":    _cpu["status"],
        "cpu_bps":       round(_cpu["bps"], 1),
        # Audio
        "audio_status":  _audio["status"],
        "audio_bps":     round(_audio["bps"], 1),
        # quality / vault
        "bit_balance":   round(balance, 4),
        "chunks_total":  chunks,
        "pool_bytes":    pool_b,
        "vault_bytes":   vault_off,
        "vault_size":    VAULT_SIZE,
        "vault_pct":     round(min(vault_off / VAULT_SIZE * 100, 100), 4),
        "pixel_samples": PIXEL_SAMPLE,
        "active_sources": active,
        "source_count":  len(active),
        "escher_passes": 6,
        "last_bytes_hex": "".join(f"{b:02x}" for b in _last_bytes),
    })
    STATS_FILE.write_text(json.dumps(_stats, indent=2))

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def run():
    log.info("NEAR ENTROPY ENGINE v3 starting — 4 sources: webcam + RTL-SDR(x2) + CPU jitter + audio")
    init_vault()
    start_api()

    # Source 2 — RTL-SDR threads (one per dongle slot)
    for idx in range(RTL_MAX_DEVICES):
        threading.Thread(target=rtl_worker, args=(idx,), daemon=True,
                         name=f"rtl-sdr-{idx}").start()
    log.info(f"RTL-SDR threads started (up to {RTL_MAX_DEVICES} dongles, auto-detect)")

    # Source 3 — CPU jitter thread
    threading.Thread(target=cpu_jitter_worker, daemon=True, name="cpu-jitter").start()
    log.info("CPU jitter thread started")

    # Source 4 — Audio thread
    threading.Thread(target=audio_worker, daemon=True, name="audio-adc").start()
    log.info("Audio ADC thread started")

    interval     = 1.0 / CAPTURE_HZ
    prev         = None
    buf          = bytearray()
    chunks       = 0
    bits_total   = 0
    cam_bits     = 0
    frame_count  = 0
    last_balance = 0.5
    fps_win      = deque(maxlen=30)
    last_flush   = last_display = 0
    cam_status   = "WAITING"

    vault_off = int(OFFSET_FILE.read_text().strip()) if OFFSET_FILE.exists() else 0
    log.info(f"Near vault offset: {vault_off/(1024**3):.4f} GB")

    while True:
        try:
            t0 = time.time()

            # Source 1 — Webcam
            frame = fetch_frame_rgb(CAM_URL)
            if frame is not None:
                frame_count += 1
                raw, cam_status = extract_cam(prev, frame)
                if raw:
                    buf.extend(raw)
                    cam_bits   += len(raw) * 8
                    bits_total += len(raw) * 8
                    _last_bytes.extend(raw)
                prev = frame

            # Sources 2/3/4 — drain shared queue (RTL-SDR, CPU jitter, audio all push here)
            while entropy_queue:
                chunk = entropy_queue.popleft()
                buf.extend(chunk)
                bits_total += len(chunk) * 8
                _last_bytes.extend(chunk)

            # Write full chunks to pool
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
                cam_bps = cam_bits / max(frame_count, 1) * fps
                write_stats(cam_status, fps, cam_bps, last_balance, chunks, pool_bytes(), vault_off)
                last_display = time.time()

            time.sleep(max(0, interval - (time.time() - t0)))

        except KeyboardInterrupt:
            log.info("Stopped. Flushing pool to vault...")
            flush_to_vault()
            break
        except Exception as e:
            log.warning(f"Main loop error: {e}")
            time.sleep(0.5)

def _run_rtl_tcp_srv(device_index: int, port: int, freq_hz: float):
    """
    Embedded rtl_tcp-compatible TCP server using pyrtlsdr async streaming.
    Runs from a clean subprocess.Popen (no libusb IOCP deadlock).
    Uses read_bytes_async (raw uint8, no float64 alloc in ctypes callback) + select-based cmd loop
    (avoids conn.settimeout poisoning the shared socket send path).
    Launch via: python near_entropy.py --rtl-tcp-srv <idx> <port> <freq_hz>
    """
    try:
        from rtlsdr import RtlSdr
    except ImportError:
        print("pyrtlsdr not installed", flush=True); sys.exit(1)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(1)
    print(f"rtl_tcp_srv[{device_index}] listening port={port} freq={freq_hz/1e6:.2f}MHz", flush=True)

    while True:
        sdr = None
        conn = None
        stop_evt = threading.Event()
        data_buf = []
        data_lock = threading.Lock()

        try:
            conn, _ = srv.accept()
            print(f"rtl_tcp_srv[{device_index}] client connected", flush=True)

            try:
                sdr = RtlSdr(device_index)
                sdr.sample_rate = RTL_SAMPLE_RATE
                sdr.center_freq = freq_hz
                sdr.gain        = RTL_GAIN
                print(f"rtl_tcp_srv[{device_index}] SDR opened: {RTL_SAMPLE_RATE/1e6:.3f}M {freq_hz/1e6:.2f}MHz {RTL_GAIN}dB", flush=True)
            except Exception as e:
                print(f"rtl_tcp_srv[{device_index}] SDR open failed: {e}", flush=True)
                conn.close()
                time.sleep(2)
                continue

            conn.sendall(b"RTL0" + struct.pack(">II", 5, 29))

            def _async_callback(raw_bytes, context):
                """Raw uint8 IQ bytes — no float64 alloc in ctypes callback context."""
                if stop_evt.is_set():
                    sdr.cancel_read_async()
                    return
                with data_lock:
                    data_buf.append(bytes(raw_bytes))

            def _async_reader():
                try:
                    sdr.read_bytes_async(_async_callback, num_bytes=RTL_TCP_BLOCK)
                except Exception as e:
                    print(f"rtl_tcp_srv[{device_index}] async: {e}", flush=True)
                finally:
                    stop_evt.set()

            def _cmd_loop():
                # Use select so we never set a timeout on the shared conn socket
                buf = bytearray()
                while not stop_evt.is_set():
                    try:
                        ready = select.select([conn], [], [], 1.0)[0]
                        if not ready:
                            continue
                        chunk = conn.recv(5 - len(buf))
                        if not chunk:
                            stop_evt.set()
                            try: sdr.cancel_read_async()
                            except: pass
                            return
                        buf.extend(chunk)
                        if len(buf) < 5:
                            continue
                        cmd, param = struct.unpack(">BI", bytes(buf[:5]))
                        buf = buf[5:]
                        if   cmd == 0x01: sdr.center_freq = param
                        elif cmd == 0x02: sdr.sample_rate = param
                        elif cmd == 0x04: sdr.gain        = param / 10.0
                    except Exception as ex:
                        print(f"rtl_tcp_srv[{device_index}] cmd: {ex}", flush=True)
                        stop_evt.set()
                        try: sdr.cancel_read_async()
                        except: pass
                        return

            threading.Thread(target=_async_reader, daemon=True).start()
            threading.Thread(target=_cmd_loop, daemon=True).start()

            bytes_sent = 0
            first_diag = False
            while not stop_evt.is_set():
                with data_lock:
                    chunks = data_buf[:]
                    data_buf.clear()
                if chunks:
                    for chunk in chunks:
                        conn.sendall(chunk)
                        bytes_sent += len(chunk)
                    if not first_diag and bytes_sent >= RTL_TCP_BLOCK:
                        arr = np.frombuffer(chunks[0], dtype=np.uint8)
                        print(f"rtl_tcp_srv[{device_index}] DIAG min={arr.min()} max={arr.max()} mean={arr.mean():.1f} std={arr.std():.3f} uniq={len(np.unique(arr))}", flush=True)
                        first_diag = True
                    if bytes_sent % (RTL_TCP_BLOCK * 1000) == 0 and bytes_sent > 0:
                        print(f"rtl_tcp_srv[{device_index}] sent {bytes_sent//1024}KB", flush=True)
                else:
                    time.sleep(0.001)

        except Exception as e:
            print(f"rtl_tcp_srv[{device_index}] error: {e}", flush=True)
        finally:
            stop_evt.set()
            try: sdr.cancel_read_async()
            except: pass
            try: conn.close()
            except: pass
            try: sdr.close()
            except: pass
            print(f"rtl_tcp_srv[{device_index}] session ended", flush=True)
            time.sleep(0.5)


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "--rtl-tcp-srv":
        _run_rtl_tcp_srv(int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4]))
    else:
        run()
