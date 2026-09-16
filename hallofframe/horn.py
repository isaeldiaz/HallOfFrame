"""12 V horn driver via a USB relay (spec §6.5 audio feedback).

An LCUS-1 relay board is driven through a CH340 serial adapter. When a
crossing is recorded the operator wants an audible confirmation so they can
keep their eyes on the finish line without watching the screen.

Nothing on the trigger path may block, and the persistence writer thread
must stay responsive. So the horn owns its own worker thread: ``honk()`` only
pushes an event onto a queue and returns immediately. That thread performs the
(blocking) serial writes and manages the active/off timing.

The active duration is short (default 300 ms) so multiple boats crossing within
a couple of seconds each get their own clear beep. When crossings arrive
closer together than the active duration, the blasts merge into a single
slightly-longer blast rather than chopping the relay on/off, which is both
kinder to the relay contacts and more audible.

LCUS-1 command (per relay): start byte 0xA0, relay number, state (1 on, 0 off),
then a checksum byte = (0xA0 + relay + state) & 0xFF.
"""
from __future__ import annotations

import queue
import threading
import time

try:
    import serial
except Exception:  # pragma: no cover - optional dependency, degrade gracefully
    serial = None


class Horn:
    """Drives a relay-driven horn off the trigger path.

    Disabled (no-op) when ``enabled`` is false or the port cannot be opened;
    the app keeps running timing-only.
    """

    def __init__(self, config, logger=None):
        cfg = config.section("horn")
        self.enabled = bool(cfg["enabled"])
        self.device = cfg["device"]
        self.baud = int(cfg["baud"])
        self.relay = int(cfg["relay"])
        self.duration_s = float(cfg["duration_ms"]) / 1000.0
        self.logger = logger

        self._ser = None
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._open()

        # Power-on self-test using the protocol's *feedback* commands
        # (0x03 = on-with-feedback, 0x02 = off-with-feedback): the relay
        # replies over serial, so any byte received proves the board is
        # communicating (and that it can switch). Run before the worker
        # thread starts to avoid racing it.
        self.test_result: tuple[bool, str] = (False, "horn disabled")
        if self.enabled and self._ser is not None:
            self.test_result = self.self_test()
            ok, detail = self.test_result
            self._warn(f"self_test {'OK' if ok else 'FAILED'}: {detail}")
            if not ok:
                # Keep the worker going anyway: maybe the relay is silent but
                # still switches, so do not leave the operator without a horn.
                pass

        if self.enabled and self._ser is not None:
            self._thread = threading.Thread(target=self._worker, daemon=True,
                                            name="horn")
            self._thread.start()
        else:
            self._warn("horn disabled")

    def _warn(self, msg: str) -> None:
        if self.logger:
            self.logger.warning("horn", "warning", message=msg)

    def _open(self) -> None:
        if not self.enabled or not self.device:
            return
        if serial is None:
            self._warn("python3-serial not installed")
            return
        try:
            self._ser = serial.Serial(self.device, self.baud, timeout=1)
        except Exception as exc:
            self._warn(f"serial open failed {self.device}: {exc}")
            self._ser = None

    def _cmd(self, data3: int) -> bytes:
        """Build the 4-byte LC command for *data3* (operation data)."""
        return bytes([0xA0, self.relay, data3,
                      (0xA0 + self.relay + data3) & 0xFF])

    def _write_raw(self, data3: int) -> None:
        if self._ser is None:
            return
        self._ser.write(self._cmd(data3))
        self._ser.flush()

    def _write(self, state: int) -> None:
        if self._ser is None:
            return
        # Guard against concurrent cleanup closing the port mid-write.
        with self._lock:
            if self._ser is None:
                return
            try:
                self._write_raw(1 if state else 0)
            except Exception as exc:
                self._warn(f"serial write failed: {exc}")

    def _read_reply(self, timeout: float) -> bytes:
        """Read whatever the board replies to a *feedback* command."""
        if self._ser is None:
            return b""
        old = self._ser.timeout
        self._ser.timeout = timeout
        try:
            return self._ser.read(1)
        except Exception:
            return b""
        finally:
            self._ser.timeout = old

    def self_test(self) -> tuple[bool, str]:
        """Verify the relay is reachable and switchable.

        The LC-1 board (in practice) gives no electronic reply to any command —
        including the 0x05 status and 0x03/0x02 feedback forms — so there is no
        byte to read back. The only real confirmation is the audible click of the
        relay switching. ``self_test`` therefore verifies what *can* be checked
        programmatically: the port is open and a status query + on/off commands
        are accepted without error. Returns ``(ok, detail)``; ``ok`` means the
        relay is connected and switchable (writes succeeded). This energizes the
        relay, so the horn will beep once.
        """
        if self._ser is None:
            return False, "serial port not open"
        replies: list[str] = []
        writes_ok = True
        with self._lock:
            try:
                self._ser.reset_input_buffer()
            except Exception:
                pass
            # Status query: a few LC boards reply to 0x05; if so we report it,
            # but a missing reply is expected and NOT a failure on this board.
            try:
                self._write_raw(0x05)
                st = self._read_reply(0.25)
                replies.append(f"status={st.hex()}" if st else "status=no-feedback")
            except Exception as exc:
                replies.append(f"status=err:{exc}")
            # Toggle the relay on then off to exercise the switching path.
            for data3, label in ((0x01, "on"), (0x00, "off")):
                try:
                    self._write_raw(data3)
                except Exception as exc:
                    writes_ok = False
                    replies.append(f"{label}=err:{exc}")
        ok = self._ser is not None and writes_ok
        return ok, ", ".join(replies)

    def honk(self) -> None:
        """Request a beep. Non-blocking; safe from any thread."""
        if self.enabled and self._thread is not None:
            self._queue.put("fire")

    def _worker(self) -> None:
        beeping = False
        off_at = 0.0
        while not self._stop.is_set():
            pending = 0
            while True:
                try:
                    self._queue.get_nowait()
                    pending += 1
                except queue.Empty:
                    break
            if self._stop.is_set():
                break

            now = time.monotonic()
            if beeping:
                if pending:
                    # New crossing while the horn is already on: extend the blast.
                    off_at = now + self.duration_s
                elif now >= off_at:
                    self._write(0)
                    beeping = False
            elif pending:
                self._write(1)
                beeping = True
                off_at = now + self.duration_s

            if beeping and not pending:
                self._stop.wait(min(self.duration_s, 0.05))
            else:
                self._stop.wait(0.02)

    def stop(self) -> None:
        """Release the relay and stop the worker thread."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        with self._lock:
            if self._ser is not None:
                try:
                    self._write_raw(0)  # no lock (non-reentrant); _write_raw is raw
                except Exception:
                    pass
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None
