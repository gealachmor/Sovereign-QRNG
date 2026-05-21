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
import cv2, threading, time, socket, json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8090
FPS  = 15

# ── Camera discovery — DSHOW only, single pass ───────────────────────────────
def find_usb_cameras(max_check=10):
    """
    Scan for cameras using DirectShow only (avoids MSMF locking on Windows).
    Returns list of (index, backend, is_builtin, native_w, native_h).
    Built-in HP TrueVision = index 0, 640x480. USB cams = higher index or larger res.
    """
    found = []
    for i in range(max_check):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            continue
        ret, frame = cap.read()
        if ret and frame is not None:
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            is_builtin = (i == 0 and w <= 640 and h <= 480)
            found.append((i, cv2.CAP_DSHOW, is_builtin, w, h))
            print(f"[SCAN] index {i} DSHOW: {w}x{h} {'[BUILT-IN]' if is_builtin else '[USB]'}")
        cap.release()
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
            while self.running:
                ret, bgr = cap.read()
                if not ret:
                    break
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
            info = [{"index": b.index, "label": b.label, "is_builtin": b.is_builtin,
                     "stream": f"/stream/{i}.mjpeg", "snapshot": f"/snapshot/{i}.jpg",
                     "width": b.native_w, "height": b.native_h}
                    for i, b in enumerate(buffers)]
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

    labels = "ABCDEFGH"
    for i, (idx, backend, is_builtin, nw, nh) in enumerate(indices):
        buf = CameraBuffer(idx, labels[i], backend, is_builtin=is_builtin,
                           native_w=nw, native_h=nh)
        buf.start()
        buffers.append(buf)
        bname = 'DSHOW' if backend == cv2.CAP_DSHOW else 'MSMF'
        tag   = '[BUILT-IN]' if is_builtin else '[USB]'
        print(f"  CAM-{labels[i]} -> index {idx} [{bname}] {nw}x{nh} {tag}")

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
