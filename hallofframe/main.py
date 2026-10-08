"""Application entry point (spec §12).

Wires everything together: transport, MJPEGReader, FrameBuffer, TriggerListener,
CaptureController, storage, logging, and the Qt UI. Under normal
operation the whole app runs under ``systemd-inhibit`` (INSTALL.md §7).
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QObject, Signal

from .config import ConfigError, load_config
from .transport import TransportError


class _TriggerBridge(QObject):
    """Marshal worker-thread triggers/captures onto the Qt main thread.

    The TriggerListener and the controller's persistence writer run on their own
    threads; calling into Qt widgets (or the controller's Qt-facing hooks) from
    there is not thread-safe and Qt drops the calls — and worse, can deadlock the
    GUI (which, with the trigger keyboard grabbed during a race, freezes all
    input). Emitting a signal from a worker thread makes Qt deliver the payload
    as a queued connection to the main thread's event loop (§6.4, §6.5)."""
    crossing = Signal(float, int, bool)  # (t_press, keycode, suspect)
    start = Signal(float)           # (t_press)
    end = Signal(float, int)        # (t_press, keycode)
    capture = Signal(object)        # (Capture)
    image_ready = Signal(int, str)  # (sequence, primary_path_rel)
    capture_deleted = Signal(int)   # (sequence)


class _NullTransport:
    """No-op stand-in for :class:`UsbTransport` when ``[transport] enabled`` is
    false (plan step 7.4). Every lifecycle call is a no-op and the device check
    passes, so ``main()`` and the transport monitor run unchanged while the
    stream comes from somewhere other than the phone (e.g. the fake camera)."""

    max_restarts_idle = 0
    max_restarts_racing = 0

    def check_device(self) -> None:
        return None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def is_alive(self) -> bool:
        return True

    def wait_until_ready(self, timeout: float = 10.0) -> bool:
        return True

    def ensure(self, racing: bool, max_restarts_idle: int = 3,
               max_restarts_racing: int = 0) -> None:
        return None


def build_core(config):
    """Construct transport, reader, buffer, storage and controller.
    Returns a dict of the parts so headless tests can reuse it without Qt."""
    import time
    from .controller import CaptureController
    from .framebuffer import FrameBuffer
    from .mjpeg import MJPEGReader
    from .storage import Storage
    from .transport import UsbTransport

    storage = Storage(config.data_root, event_name=config.event_name)

    transport_cfg = config.section("transport")
    if bool(transport_cfg.get("enabled", True)):
        transport = UsbTransport(
            int(transport_cfg["local_port"]),
            int(transport_cfg["device_port"]),
            udid=transport_cfg["udid"] or None,
            iproxy_path=transport_cfg["iproxy_path"])
        transport.max_restarts_idle = int(transport_cfg["max_restarts_idle"])
        transport.max_restarts_racing = int(transport_cfg["max_restarts_racing"])
    else:
        # The USB tunnel is disabled: no iproxy, no device check. The stream URL
        # may point anywhere (fake camera, network camera). See plan step 7.4.
        transport = _NullTransport()
    stream = config.section("stream")
    buffer = FrameBuffer(seconds=float(stream["buffer_seconds"]),
                         assumed_fps=int(stream["assumed_fps"]))

    controller = CaptureController(config, storage, buffer)

    auth = None
    if stream["username"]:
        auth = (stream["username"], stream["password"])

    def _ingest(frame):
        buffer.append(frame)

    reader = MJPEGReader(stream["url"], _ingest, auth=auth,
                         require_content_length=bool(stream["require_content_length"]))

    return {
        "storage": storage,
        "buffer": buffer,
        "controller": controller,
        "reader": reader,
        "transport": transport,
    }


def _end_handlers(on_end, end_keycodes):
    """Keycode→handler map for the end action. Includes KEY_ESC and ThinkPad's
    non-Fn F12 multimedia codes so Esc or unshifted laptop F12 keys can
    disarm/end while the timing device is grabbed."""
    if on_end is None:
        return {}
    h = {int(c): on_end for c in end_keycodes}
    for c in (1, 364, 156, 171, 148, 464, 225):  # Esc + ThinkPad F12-multimedia
        h.setdefault(c, on_end)
    return h


def build_trigger(config, on_crossing, on_start, on_end=None, logger=None):
    """Construct trigger listener(s) from config; fall back to Qt if unavailable.

    Returns ``(primary, fallback, extras)`` where:
      * ``primary`` is the timing TriggerListener (or None on fallback) reading
        ``device_path`` for start+crossing — the only listener that gets grabbed.
      * ``fallback`` is True when no evdev timing listener could be built.
      * ``extras`` is a list of additional TriggerListeners (never grabbed); one
        is built on ``end_device_path`` to handle ONLY the end keycodes, leaving
        the keyboard ungrabbed so Qt still receives typing (e.g. boat numbers).
    """
    from .trigger import TriggerError, TriggerListener
    trig = config.section("trigger")
    device = trig["device_path"]
    end_device = trig.get("end_device_path") or ""
    debounce_ms = float(config.section("timing")["debounce_ms"])

    handlers = {int(c): on_crossing for c in trig["crossing_keycodes"]}
    # A keycode listed in BOTH crossing and start sets drives the single-key
    # flow (armed first-press = t0, §5.3). Route it to the crossing handler,
    # whose slot dispatches to start_race when armed. setdefault keeps the
    # crossing mapping instead of letting start overwrite it; a key listed only
    # in start_keycodes still maps to on_start as before.
    for c in trig["start_keycodes"]:
        handlers.setdefault(int(c), on_start)

    extras = []
    # If a separate end device is configured, route end keycodes there only, and
    # keep the timing device free of end keys (SayoDevice button = start/cross).
    if end_device:
        if on_end is not None:
            try:
                extras.append(TriggerListener(
                    end_device, _end_handlers(on_end, trig["end_keycodes"]),
                    debounce_ms=debounce_ms, grab=False))
            except TriggerError as exc:
                if logger:
                    logger.warning("trigger", "end_evdev_fallback",
                                   reason=str(exc))
    elif on_end is not None:
        # Single-device mode: the timing device also handles the end keycodes.
        handlers.update(_end_handlers(on_end, trig["end_keycodes"]))

    primary = None
    fallback = False
    if device:
        try:
            # Grab is NOT taken at construction: it is driven entirely by the
            # on_state_changed hook in main() (§9/Opt A). Grabbing here would
            # race the hook's first sync and could leave the keyboard grabbed
            # outside a race (e.g. STREAM_DOWN), where Qt shortcuts like Ctrl+Q
            # are then unreachable and the operator cannot quit.
            primary = TriggerListener(
                device, handlers,
                debounce_ms=debounce_ms, grab=False)
        except TriggerError as exc:
            if logger:
                logger.warning("trigger", "evdev_fallback", reason=str(exc))
            primary = None

    for listener in ([primary] + extras if primary is not None else extras):
        listener.start()
    if primary is None:
        fallback = True
    return primary, fallback, extras


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    config_path = argv[0] if argv else None
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    from PySide6.QtWidgets import QApplication

    from .log import start_logging
    logger = start_logging(config.data_root / "logs", race_id=None,
                           event_name=config.event_name)
    logger.info("app", "start", version=__import__("hallofframe").__version__)

    core = build_core(config)
    app = QApplication(sys.argv)

    from .trigger import TriggerListener
    trig = None

    from .ui.main_window import MainWindow
    win = MainWindow(config, core["controller"], core["buffer"], logger=logger)
    win.showFullScreen()

    # Visual system (§7): one app-wide stylesheet + bundled fonts.
    from .ui.styles import STYLESHEET, load_fonts
    load_fonts()
    app.setStyleSheet(STYLESHEET)

    # Bridge worker-thread events (evdev triggers AND the controller's capture
    # completion) back onto the Qt main thread (§6.4, §6.5). Every Qt-facing
    # callback must reach the GUI thread through these queued signals; calling
    # into widgets directly from a worker thread can deadlock the UI.
    bridge = _TriggerBridge()
    bridge.start.connect(win.on_evdev_start)
    bridge.crossing.connect(win.on_evdev_crossing)
    bridge.end.connect(win.on_evdev_end)
    bridge.capture.connect(win.on_capture)
    bridge.image_ready.connect(win.on_image_ready)
    bridge.capture_deleted.connect(win.on_capture_deleted)
    # The controller emits capture-added / image-ready from its persistence or
    # deferred-timer threads; reroute them through the bridge instead of pointing
    # them straight at Qt widgets. race_ended is emitted from end_race(), which
    # always runs on the GUI thread (button or queued evdev end), so it can call
    # the window directly. warning goes to the toast (same as before).
    def _on_controller_event(kind: str, payload: dict) -> None:
        if kind == "capture_added":
            bridge.capture.emit(payload["capture"])
        elif kind == "capture_deleted":
            bridge.capture_deleted.emit(payload["sequence"])
        elif kind == "image_ready":
            bridge.image_ready.emit(payload["sequence"], payload["path"])
        elif kind == "race_ended":
            win.on_race_ended(payload["race_id"])
        elif kind == "warning":
            win._show_toast(payload["message"])

    core["controller"].events = _on_controller_event

    def _crossing(t_press, code, suspect=False):
        bridge.crossing.emit(t_press, code, suspect)

    def _start(t_press, code, suspect=False):
        bridge.start.emit(t_press)

    def _end(t_press, code, suspect=False):
        bridge.end.emit(t_press, int(code))

    listeners, fallback, extra_listeners = build_trigger(
        config, _crossing, _start, _end, logger)
    if fallback:
        # Qt key-event fallback (§6.4): degraded precision, say so.
        logger.warning("trigger", "qt_fallback")

    # Only the timing (primary) device is grabbed from ARM through RACE_OVER
    # (§9/Opt A). A separate end_device (keyboard) is deliberately left ungrabbed
    # so Qt keeps receiving keys — e.g. future boat-number entry — while the
    # SayoDevice button is grabbed for crossings.
    if listeners is not None and config.section("trigger")["grab_device"]:
        # Option A: the trigger keyboard is grabbed from ARM through RACE_OVER,
        # tied directly to state instead of a 250 ms polling timer (§9). Bow
        # entry moved to REVIEW, so nothing needs the keyboard between arm and
        # end. Recovery on a freeze is via the separate primary keyboard.
        from .ui.state import AppState
        def _sync_grab(state):
            try:
                listeners.set_grab(state in (AppState.ARMED, AppState.RECORDING))
            except Exception:
                pass
        win.state_changed.connect(_sync_grab)
        # The initial READY was applied before the hook was wired, so fire it
        # once to release any grab taken at construction (§9/Opt A).
        _sync_grab(win._last_state)

    # A race left un-ended by a crash/restart (N4, plan step 3.5): offer to
    # resume it or mark it finished. Additive; shown non-modally.
    open_race = core["storage"].open_race()
    if open_race is not None:
        win.offer_resume(open_race["id"])

    # Bring up the USB tunnel before the reader connects (spec §6.1, §9.3).
    import threading

    transport = core["transport"]
    try:
        transport.check_device()
        transport.start()
        transport.wait_until_ready()
    except TransportError as exc:
        logger.warning("transport", "start_failed", reason=str(exc))
        if hasattr(win, "show_error"):
            win.show_error(str(exc))

    def _monitor_transport():
        """Keep iproxy alive. While racing, restarts are unlimited (§6.5);
        idle restarts are capped (§6.1)."""
        racing = False
        while True:
            try:
                racing = core["controller"].running
            except Exception:
                racing = False
            try:
                transport.ensure(
                    racing=racing,
                    max_restarts_idle=transport.max_restarts_idle,
                    max_restarts_racing=transport.max_restarts_racing)
            except TransportError as exc:
                logger.warning("transport", "restart_failed", reason=str(exc))
            if _monitor_stop.wait(timeout=2.0):
                return

    _monitor_stop = threading.Event()
    monitor = threading.Thread(target=_monitor_transport, daemon=True,
                               name="transport-monitor")
    monitor.start()

    core["reader"].start()

    def _cleanup():
        _monitor_stop.set()
        core["controller"].stop()
        for listener in [listeners] + extra_listeners:
            if listener is not None:
                listener.stop()
        core["reader"].stop()
        transport.stop()
        logger.stop()

    app.aboutToQuit.connect(_cleanup)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
