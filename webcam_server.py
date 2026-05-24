#!/usr/bin/env python3
"""
SOVEREIGN WEBCAM SERVER — OpenCV edition (no FFmpeg required)
Streams all USB cameras as MJPEG + serves /snapshot.jpg per camera.

Endpoints:
  /stream.mjpeg          — CAM-A MJPEG stream
  /stream_b.mjpeg        — CAM-B MJPEG stream
  /snapshot.jpg          — CAM-A latest frame
  /snapshot_b.jpg        — CAM-B latest frame
  /cameras               — JSON list of active cameras
"""
import cv2, threading, time, socket, json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8090
FPS  = 15

# ── Simulation mode — set via --simulate N flag ───────────────────────────────
SIMULATE_CAMS = 0
if "--simulate" in sys.argv:
    try:
        SIMULATE_CAMS = int(sys.argv[sys.argv.index("--simulate") + 1])
        print(f"[SIM] Simulation mode: {SIMULATE_CAMS} virtual cameras from device 0")
    except (IndexError, ValueError):
        pass

# ── Max resolution probe — try common resolutions, pick highest that works ────
_PROBE_RES = [
    (3840, 2160), (2560, 1440), (1920, 1080),
    (1280, 720),  (1024, 768),  (800, 600),  (640, 480),
]

def probe_max_resolution(index):
    """Try resolutions highest-first; return (w, h) of the best the device accepts."""
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    best_w, best_h = 640, 480
    for target_w, target_h in _PROBE_RES:
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            cap.release()
            continue
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, target_w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, target_h)
        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        ret, frame = cap.read()
        cap.release()
        if ret and frame is not None and actual_w >= 32 and actual_h >= 32:
            if actual_w >= target_w * 0.8 and actual_h >= target_h * 0.8:
                best_w, best_h = actual_w, actual_h
                break   # highest working resolution found
            elif actual_w > best_w:
                best_w, best_h = actual_w, actual_h
        time.sleep(0.1)
    return best_w, best_h

# ── Camera validation — verify index is a real video device ──────────────────
def is_valid_camera(index):
    """
    Check that an OpenCV index exposes a real video stream.
    Rejects audio/HID devices (headsets, AirPods, USB dongles) that don't
    produce frames. Forces DirectShow on Windows for explicit video enumeration.
    Returns (True, width, height) on success, (False, 0, 0) on failure.
    """
    try:
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            cap.release()
            return False, 0, 0
        ret, frame = cap.read()
        if not ret or frame is None:
            cap.release()
            return False, 0, 0
        if frame.ndim != 3:
            cap.release()
            return False, 0, 0
        h, w = frame.shape[:2]
        if w < 32 or h < 32:
            cap.release()
            return False, 0, 0
        cap.release()
        return True, w, h
    except Exception:
        try: cap.release()
        except Exception: pass
        return False, 0, 0

# ── Camera discovery — DSHOW only, single pass ───────────────────────────────
def find_usb_cameras(max_check=10):
    """
    Scan for cameras using DirectShow only (avoids MSMF locking on Windows).
    Returns list of (index, backend, is_builtin, native_w, native_h).
    Built-in camera = index 0, 640x480. USB cams = higher index or larger res.
    """
    found = []
    for i in range(max_check):
        valid, w, h = is_valid_camera(i)
        if not valid:
            print(f"[SKIP] index {i}: not a valid video device")
            continue
        # Probe for the highest resolution this device actually supports
        best_w, best_h = probe_max_resolution(i)
        is_builtin = (i == 0 and best_w <= 640 and best_h <= 480)
        found.append((i, cv2.CAP_DSHOW, is_builtin, best_w, best_h))
        ratio = f"{best_w/best_h:.2f}" if best_h else "?"
        print(f"[SCAN] index {i} DSHOW: {best_w}x{best_h} (ratio {ratio}) {'[BUILT-IN]' if is_builtin else '[USB]'}")
        time.sleep(0.2)   # let Windows release the handle before next index

    # Put USB cams first, built-in last
    found.sort(key=lambda x: (x[2], x[0]))
    return found

# ── Frame buffers ─────────────────────────────────────────────────────────────
class CameraBuffer:
    def __init__(self, index, label, backend=cv2.CAP_DSHOW, is_builtin=False,
                 native_w=640, native_h=480):
        self.index      = index
        self.label      = label
        self.backend    = backend
        self.is_builtin = is_builtin
        self.native_w   = native_w
        self.native_h   = native_h
        self.lock    = threading.Lock()
        self.frame   = b""
        self.event   = threading.Event()
        self.running = False

    def start(self):
        self.running = True
        t = threading.Thread(target=self._capture, daemon=True)
        t.start()

    def _capture(self):
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, 82]
        time.sleep(0.5)   # brief pause so discovery fully releases the handle
        while self.running:
            cap = cv2.VideoCapture(self.index, self.backend)
            # Use native resolution — don't force 1280x720 on cameras that can't do it
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self.native_w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.native_h)
            cap.set(cv2.CAP_PROP_FPS,          FPS)
            if not cap.isOpened():
                time.sleep(2)
                continue
            print(f"[CAM-{self.label}] Opened index {self.index} ({self.native_w}x{self.native_h})")
            consecutive_fails = 0
            while self.running:
                ret, bgr = cap.read()
                if not ret or bgr is None:
                    consecutive_fails += 1
                    if consecutive_fails >= 3:
                        print(f"[CAM-{self.label}] index {self.index} offline — 3 consecutive bad reads")
                        break
                    continue
                consecutive_fails = 0
                ok, buf = cv2.imencode('.jpg', bgr, encode_params)
                if ok:
                    with self.lock:
                        self.frame = buf.tobytes()
                    self.event.set()
                    self.event.clear()
            cap.release()
            time.sleep(1)

    def get_frame(self):
        with self.lock:
            return self.frame

buffers: list[CameraBuffer] = []

# ── HTTP handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silent

    def _mjpeg(self, buf: CameraBuffer):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=--frame")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            while True:
                buf.event.wait(timeout=2)
                frame = buf.get_frame()
                if frame:
                    hdr = (f"--frame\r\nContent-Type: image/jpeg\r\n"
                           f"Content-Length: {len(frame)}\r\n\r\n").encode()
                    self.wfile.write(hdr + frame + b"\r\n")
                    self.wfile.flush()
        except Exception:
            pass

    def _snapshot(self, buf: CameraBuffer):
        frame = buf.get_frame()
        if frame:
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(frame)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(frame)
        else:
            self.send_response(503)
            self.end_headers()

    def do_GET(self):
        p = self.path.split("?")[0]
        usb_buf     = next((b for b in buffers if not b.is_builtin), buffers[0] if buffers else None)
        builtin_buf = next((b for b in buffers if b.is_builtin), None)

        if p in ("/stream.mjpeg", "/") and buffers:
            self._mjpeg(buffers[0])
        elif p == "/stream_b.mjpeg" and len(buffers) > 1:
            self._mjpeg(buffers[1])
        elif p == "/stream_usb.mjpeg" and usb_buf:
            self._mjpeg(usb_buf)
        elif p == "/stream_builtin.mjpeg" and builtin_buf:
            self._mjpeg(builtin_buf)
        elif p == "/snapshot.jpg" and buffers:
            self._snapshot(buffers[0])
        elif p == "/snapshot_b.jpg" and len(buffers) > 1:
            self._snapshot(buffers[1])
        elif p == "/snapshot_usb.jpg" and usb_buf:
            self._snapshot(usb_buf)
        elif p == "/snapshot_builtin.jpg" and builtin_buf:
            self._snapshot(builtin_buf)
        elif p == "/snapshot_builtin.jpg":
            # fallback: if no dedicated built-in found, use last buffer
            if buffers:
                self._snapshot(buffers[-1])
            else:
                self.send_response(503); self.end_headers()
        elif p.startswith("/stream/") and p.endswith(".mjpeg"):
            try:
                n = int(p[8:-6])
                if 0 <= n < len(buffers):
                    self._mjpeg(buffers[n])
                else:
                    self.send_response(404); self.end_headers()
            except (ValueError, IndexError):
                self.send_response(400); self.end_headers()
        elif p.startswith("/snapshot/") and p.endswith(".jpg"):
            try:
                n = int(p[10:-4])
                if 0 <= n < len(buffers):
                    self._snapshot(buffers[n])
                else:
                    self.send_response(404); self.end_headers()
            except (ValueError, IndexError):
                self.send_response(400); self.end_headers()
        elif p == "/cameras":
            real = [{"index": b.index, "label": b.label, "is_builtin": b.is_builtin,
                     "stream": f"/stream/{i}.mjpeg", "snapshot": f"/snapshot/{i}.jpg",
                     "width": b.native_w, "height": b.native_h}
                    for i, b in enumerate(buffers)]
            if SIMULATE_CAMS > len(real):
                cam_labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                src = real[0] if real else {"stream":"/stream/0.mjpeg","snapshot":"/snapshot/0.jpg","width":640,"height":480,"is_builtin":True}
                for j in range(len(real), SIMULATE_CAMS):
                    real.append({
                        "index": 0,
                        "label": cam_labels[j] if j < 26 else f"SIM{j}",
                        "is_builtin": False,
                        "stream": src["stream"],   # all point to device 0 stream
                        "snapshot": src["snapshot"],
                        "width": src["width"],
                        "height": src["height"],
                        "simulated": True,
                    })
            info = real
            data = json.dumps(info).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== SOVEREIGN WEBCAM SERVER (OpenCV) ===")
    print("Discovering cameras...")
    indices = find_usb_cameras()
    if not indices:
        print("ERROR: No cameras found."); exit(1)

    cam_labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for i, (idx, backend, is_builtin, nw, nh) in enumerate(indices):
        lbl = cam_labels[i] if i < 26 else str(i)
        buf = CameraBuffer(idx, lbl, backend, is_builtin=is_builtin,
                           native_w=nw, native_h=nh)
        buf.start()
        buffers.append(buf)
        bname = 'DSHOW' if backend == cv2.CAP_DSHOW else 'MSMF'
        tag   = '[BUILT-IN]' if is_builtin else '[USB]'
        ratio = f"{nw/nh:.2f}" if nh else "?"
        print(f"  CAM-{lbl} -> index {idx} [{bname}] {nw}x{nh} (ratio {ratio}) {tag}")
    if SIMULATE_CAMS:
        print(f"  [SIM] Virtual cameras: {SIMULATE_CAMS} total ({SIMULATE_CAMS - len(buffers)} simulated from CAM-A)")

    time.sleep(1.5)  # let cameras warm up

    ip = socket.gethostbyname(socket.gethostname())
    print(f"\nStream A:    http://localhost:{PORT}/stream.mjpeg")
    if len(buffers) > 1:
        print(f"Stream B:    http://localhost:{PORT}/stream_b.mjpeg")
    print(f"Snapshot A:  http://localhost:{PORT}/snapshot.jpg")
    print(f"Cameras:     http://localhost:{PORT}/cameras")
    print(f"Network:     http://{ip}:{PORT}/stream.mjpeg\n")

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.serve_forever()
