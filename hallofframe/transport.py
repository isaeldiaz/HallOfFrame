"""iproxy lifecycle management (spec §6.1).

Shell out to ``iproxy`` (libimobiledevice-utils); do not reimplement usbmuxd.
Checks a device is present/trusted first, monitors the subprocess, and supports
automatic restart with backoff. During a race restarts are unlimited (§6.5);
idle restarts are capped.
"""
from __future__ import annotations

import shlex
import socket
import subprocess
import time


class TransportError(Exception):
    pass


class UsbTransport:
    def __init__(self, local_port: int, device_port: int, udid: str | None = None,
                 iproxy_path: str = "iproxy"):
        self.local_port = local_port
        self.device_port = device_port
        self.udid = udid
        self.iproxy_path = iproxy_path
        self.proc: subprocess.Popen | None = None
        self.restart_count = 0

    @staticmethod
    def _device_udid() -> str | None:
        import shutil
        if not shutil.which("idevice_id"):
            return None
        try:
            out = subprocess.run(["idevice_id", "-l"], capture_output=True,
                                 text=True, timeout=10).stdout.strip()
        except Exception:
            return None
        return out.splitlines()[0] if out else None

    def check_device(self) -> None:
        if self.udid:
            return
        if not self._device_udid():
            raise TransportError(
                "No iPhone detected. Plug in the cable, unlock the phone, "
                "and tap Trust.")

    def start(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        args = [self.iproxy_path, f"{self.local_port}:{self.device_port}"]
        if self.udid:
            args = [self.iproxy_path, "-u", self.udid,
                    f"{self.local_port}:{self.device_port}"]
        try:
            self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
        except FileNotFoundError as exc:
            raise TransportError(f"iproxy not found: {self.iproxy_path}") from exc

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def wait_until_ready(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._tcp_ready():
                return True
            time.sleep(0.1)
        return False

    def _tcp_ready(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.local_port), timeout=0.5):
                return True
        except OSError:
            return False

    def ensure(self, racing: bool, max_restarts_idle: int = 3,
               max_restarts_racing: int = 0) -> None:
        """Restart the tunnel as needed. Returns after the tunnel is up, or
        raises if idle restarts are exhausted."""
        cap = max_restarts_racing if racing else max_restarts_idle
        while True:
            if self.is_alive() and self._tcp_ready():
                return
            self.stop()
            self.start()
            self.restart_count += 1
            if self.wait_until_ready(2.0):
                return
            if cap and self.restart_count >= cap:
                raise TransportError(
                    f"iproxy failed after {self.restart_count} restarts")
            time.sleep(0.5 * min(2.0, self.restart_count))
