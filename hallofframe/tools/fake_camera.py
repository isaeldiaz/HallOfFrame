"""Fake MJPEG camera (plan step 7.4).

Serves a folder of JPEGs as an ``multipart/x-mixed-replace`` MJPEG stream that
the app's :class:`~hallofframe.mjpeg.MJPEGReader` consumes unchanged. This lets
the whole pipeline (ingest -> ring buffer -> deferred frame selection ->
review/web) run on a laptop with no phone attached; point ``[stream] url`` at it
and set ``[transport] enabled = false`` so no USB tunnel is attempted.

    python -m hallofframe.tools.fake_camera --folder DIR --fps 30 --port 8081 [--loop]

Each part carries a ``Content-Length`` (the reader requires one by default) and
frames are paced with ``time.monotonic()``. With ``--counter`` the current
``int(time.monotonic() * 1000)`` is drawn onto every frame with PIL, so the
latency-calibration flow can be exercised too.
"""
from __future__ import annotations

import argparse
import io
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BOUNDARY = b"frame"
_JPEG_EXTS = {".jpg", ".jpeg"}


def _draw_counter(jpeg: bytes, text: int) -> bytes:
    """Return *jpeg* with *text* drawn in the top-left corner (PIL)."""
    from PIL import Image, ImageDraw

    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    ImageDraw.Draw(im).text((6, 6), str(text), fill=(255, 255, 255))
    out = io.BytesIO()
    im.save(out, "JPEG")
    return out.getvalue()


class FakeCamera:
    """A folder of JPEGs played back at *fps* over one multipart response."""

    def __init__(self, folder, fps: float = 30.0, loop: bool = False,
                 counter: bool = False):
        self.folder = Path(folder)
        self.fps = float(fps)
        self.loop = bool(loop)
        self.counter = bool(counter)
        self._stop = threading.Event()
        self._frames: list[bytes] = []

    def load(self) -> int:
        """Read the JPEGs in filename order; returns the frame count."""
        files = sorted(
            (p for p in self.folder.iterdir()
             if p.is_file() and p.suffix.lower() in _JPEG_EXTS),
            key=lambda p: p.name)
        if not files:
            raise FileNotFoundError(f"no JPEGs in {self.folder}")
        self._frames = [p.read_bytes() for p in files]
        return len(self._frames)

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    def stop(self) -> None:
        self._stop.set()

    def stream(self, wfile) -> None:
        """Write the multipart parts to *wfile* until stopped or exhausted."""
        frames = self._frames
        n = len(frames)
        if n == 0:
            return
        period = 1.0 / self.fps if self.fps > 0 else 0.0
        i = 0
        next_t = time.monotonic()
        while not self._stop.is_set():
            jpeg = frames[i % n]
            if self.counter:
                jpeg = _draw_counter(jpeg, int(time.monotonic() * 1000))
            header = (b"--" + BOUNDARY + b"\r\n"
                      b"Content-Type: image/jpeg\r\n"
                      b"Content-Length: " + str(len(jpeg)).encode("ascii")
                      + b"\r\n\r\n")
            try:
                wfile.write(header)
                wfile.write(jpeg)
                wfile.write(b"\r\n")
                wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                break
            i += 1
            if not self.loop and i >= n:
                break
            next_t += period
            delay = next_t - time.monotonic()
            if delay > 0:
                self._stop.wait(delay)
            else:
                next_t = time.monotonic()


class FakeCameraHandler(BaseHTTPRequestHandler):
    server: "FakeCameraServer"

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        camera = self.server.camera
        self.send_response(200)
        self.send_header(
            "Content-Type",
            f"multipart/x-mixed-replace; boundary={BOUNDARY.decode('ascii')}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            camera.stream(self.wfile)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def log_message(self, fmt, *args):  # keep the console quiet
        return


class FakeCameraServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, camera: FakeCamera):
        super().__init__(address, FakeCameraHandler)
        self.camera = camera


def start_server(folder, *, host: str = "127.0.0.1", port: int = 8081,
                 fps: float = 30.0, loop: bool = False,
                 counter: bool = False):
    """Bind and serve in a daemon thread. Returns ``(server, thread, camera)``.

    Pass ``port=0`` for an ephemeral port; read it back from
    ``server.server_address[1]``."""
    camera = FakeCamera(folder, fps=fps, loop=loop, counter=counter)
    camera.load()
    server = FakeCameraServer((host, port), camera)
    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name="fake-camera")
    thread.start()
    return server, thread, camera


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="hallofframe.tools.fake_camera",
        description="Serve a folder of JPEGs as a fake MJPEG camera.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--folder", required=True,
                        help="directory of .jpg/.jpeg frames, in filename order")
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--loop", action="store_true",
                        help="repeat the folder forever (default: play once)")
    parser.add_argument("--counter", action="store_true",
                        help="draw int(time.monotonic()*1000) on every frame")
    args = parser.parse_args(argv)

    server, thread, camera = start_server(
        args.folder, host=args.host, port=args.port, fps=args.fps,
        loop=args.loop, counter=args.counter)
    url = f"http://{args.host}:{server.server_address[1]}/video"
    print(f"fake camera: {camera.frame_count} frames at {args.fps:g} fps on {url}")
    print("point [stream] url at that address and set [transport] enabled = false")
    try:
        while thread.is_alive():
            thread.join(timeout=1.0)
    except KeyboardInterrupt:
        pass
    finally:
        camera.stop()
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
