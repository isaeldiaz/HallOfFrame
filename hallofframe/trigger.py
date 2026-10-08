"""Key-press trigger via evdev (spec §6.4).

Timing precision is why we read /dev/input/event* below the display server:
Qt key events add tens of ms of queue jitter under load. We take the kernel
timestamp (CLOCK_MONOTONIC via the EVIOCSCLOCKID ioctl) as ``t_press`` and pass
it through verbatim — never a fresh time.monotonic().

IMPORTANT: ``InputDevice.set_clock_id()`` DOES NOT EXIST in python-evdev. The
ioctl is issued with fcntl against a raw EVIOCSCLOCKID request (spec §6.4,
INSTALL.md §4).
"""
from __future__ import annotations

import struct
import threading
from typing import Callable

EVIOCSCLOCKID = 0x400445A0  # _IOW('E', 0xA0, int)


class TriggerError(Exception):
    pass


class TriggerListener(threading.Thread):
    def __init__(self, device_path: str,
                 handlers: dict[int, Callable[[float, int, bool], None]],
                 debounce_ms: float = 20.0,
                 clock_monotonic: bool = True,
                 grab: bool = False):
        super().__init__(daemon=True, name="trigger-listener")
        self.device_path = device_path
        self.handlers = dict(handlers)
        self.keycodes = set(self.handlers)
        self.debounce_s = debounce_ms / 1000.0
        self.clock_monotonic = clock_monotonic
        self.grab_requested = grab
        self._is_grabbed = False
        self._stop = threading.Event()
        self._device = None
        self._last_trigger_mono: float = 0.0
        self.debounce_suspect_count = 0
        self.permission_error = False

        try:
            import evdev
        except ImportError as exc:
            # No evdev (e.g. CI, or a laptop without it): build_trigger catches
            # TriggerError and falls back to Qt key events (§6.4).
            raise TriggerError(
                "python-evdev is not installed; falling back to Qt key events"
            ) from exc
        try:
            self._device = evdev.InputDevice(self.device_path)
        except PermissionError:
            self.permission_error = True
            raise TriggerError(
                f"permission denied reading {self.device_path}; "
                "join the 'input' group and log out/in (§6.4)")
        except OSError as exc:
            raise TriggerError(f"cannot open {self.device_path}: {exc}")

        self._apply_clock_domain()
        try:
            self._verify_clock_domain()
        except TriggerError:
            try:
                self._device.close()
            except Exception:
                pass
            raise
        if self.grab_requested:
            try:
                self._device.grab()
                self._is_grabbed = True
            except Exception:
                self._device = None
                raise TriggerError(
                    f"could not grab {self.device_path}; a desktop app (or the "
                    "window manager) already holds it exclusive")

    def stop(self) -> None:
        self._stop.set()
        if self._is_grabbed and self._device is not None:
            try:
                self._device.ungrab()
                self._is_grabbed = False
            except Exception:
                pass

    def set_grab(self, grab: bool) -> None:
        """Grab/ungrab the device (spec §6.4). Only meaningful when the trigger
        device is the active keyboard: while grabbed, keystrokes cannot reach Qt,
        so typing in the bow field is unavailable and trigger keys cannot double
        through the GUI. Called around race start/stop."""
        self.grab_requested = grab
        dev = self._device
        if dev is None:
            return
        try:
            if grab and not self._is_grabbed:
                dev.grab()
                self._is_grabbed = True
            elif not grab and self._is_grabbed:
                dev.ungrab()
                self._is_grabbed = False
        except Exception:
            pass

    def _apply_clock_domain(self) -> None:
        if not self.clock_monotonic or self._device is None:
            return
        import fcntl
        import time
        fcntl.ioctl(self._device.fd, EVIOCSCLOCKID,
                    struct.pack("i", time.CLOCK_MONOTONIC))

    def _verify_clock_domain(self, timeout: float = 0.3) -> None:
        """Confirm the device's timestamps are CLOCK_MONOTONIC (spec §6.4).

        The EVIOCSCLOCKID ioctl above is silent when it does not take effect;
        without this check a CLOCK_REALTIME device leaves ``t_press`` ~56 years
        from ``Frame.t_recv``, so ``nearest()`` returns the newest frame and every
        photo is wrong while ``elapsed`` still looks fine. If an event is already
        pending, compare its kernel timestamp with ``time.monotonic()`` and raise
        a hard :class:`TriggerError` on a mismatch (which refuses the race and
        falls back to Qt). No event pending means nothing to compare; the device
        stays usable."""
        import select
        import time
        if self._device is None:
            return
        try:
            ready, _, _ = select.select([self._device.fd], [], [], timeout)
        except (OSError, ValueError):
            return
        if not ready:
            return
        try:
            event = self._device.read_one()
        except OSError:
            return
        if event is None:
            return
        if abs(event.timestamp() - time.monotonic()) >= 1.0:
            raise TriggerError(
                "evdev clock domain is not CLOCK_MONOTONIC "
                f"(event {event.timestamp():.1f} vs monotonic "
                f"{time.monotonic():.1f}); refusing to start")

    def _dispatch(self, code: int, value: int, timestamp: float) -> None:
        if value != 1:
            return  # ignore release (0) and auto-repeat (2)
        handler = self.handlers.get(code)
        if handler is None:
            return
        now = timestamp  # kernel timestamp, interrupt time
        # A press inside the debounce window is a suspect double.
        # RECORD and flag it — never drop (§6.4).
        suspect = now - self._last_trigger_mono < self.debounce_s
        if suspect:
            self.debounce_suspect_count += 1
        self._last_trigger_mono = now
        handler(now, code, suspect)

    def run(self) -> None:
        import evdev
        if self._device is None:
            return

        if not self._stop.is_set():
            try:
                for event in self._device.read_loop():
                    if self._stop.is_set():
                        break
                    if event.type != evdev.ecodes.EV_KEY:
                        continue
                    self._dispatch(event.code, event.value, event.timestamp())
            except (evdev.UInputError, OSError):
                pass
            finally:
                if self._is_grabbed and self._device is not None:
                    try:
                        self._device.ungrab()
                        self._is_grabbed = False
                    except Exception:
                        pass
