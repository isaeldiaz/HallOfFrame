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
    transport = UsbTransport(
        int(transport_cfg["local_port"]),
        int(transport_cfg["device_port"]),
        udid=transport_cfg["udid"] or None,
        iproxy_path=transport_cfg["iproxy_path"])
    transport.max_restarts_idle = int(transport_cfg["max_restarts_idle"])
    transport.max_restarts_racing = int(transport_cfg["max_restarts_racing"])
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


def build_trigger(config, on_crossing, on_start, on_end=None, logger=None):
    """Construct a TriggerListener from config; fall back to Qt if unavailable.
    Returns (listener_or_None, used_fallback: bool)."""
    from .trigger import TriggerError, TriggerListener
    trig = config.section("trigger")
    device = trig["device_path"]
    if not device:
        return None, True
    handlers = {int(c): on_crossing for c in trig["crossing_keycodes"]}
    handlers.update({int(c): on_start for c in trig["start_keycodes"]})
    if on_end is not None:
        handlers.update({int(c): on_end for c in trig["end_keycodes"]})
        # Map KEY_ESC (1) and common laptop F12 non-Fn multimedia codes
        # (ThinkPad KEY_FAVORITES, BOOKMARKS, CONFIG, PROG1, STAR, BRIGHTNESSUP)
        # so Esc or unshifted laptop F12 keys can disarm/end while grabbed.
        handlers.setdefault(1, on_end)      # KEY_ESC
        handlers.setdefault(364, on_end)    # KEY_FAVORITES (ThinkPad F12 default)
        handlers.setdefault(156, on_end)    # KEY_BOOKMARKS
        handlers.setdefault(171, on_end)    # KEY_CONFIG
        handlers.setdefault(148, on_end)    # KEY_PROG1
        handlers.setdefault(464, on_end)    # KEY_STAR
        handlers.setdefault(225, on_end)    # KEY_BRIGHTNESSUP
    try:
        # Grab is NOT taken at construction: it is driven entirely by the
        # on_state_changed hook in main() (§9/Opt A). Grabbing here would race
        # the hook's first sync and could leave the keyboard grabbed outside a
        # race (e.g. STREAM_DOWN), where Qt shortcuts like Ctrl+Q are then
        # unreachable and the operator cannot quit.
        listener = TriggerListener(
            device, handlers,
            debounce_ms=float(config.section("timing")["debounce_ms"]),
            grab=False)
        listener.start()
        return listener, False
    except TriggerError as exc:
        if logger:
            logger.warning("trigger", "evdev_fallback", reason=str(exc))
        return None, True


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
    # them straight at Qt widgets. signal_race_ended is emitted from end_race(),
    # which always runs on the GUI thread (button or queued evdev end), so it can
    # call the window directly.
    core["controller"].signal_capture_added = lambda cap: bridge.capture.emit(cap)
    core["controller"].signal_capture_deleted = lambda seq: bridge.capture_deleted.emit(seq)
    core["controller"].signal_image_ready = lambda seq, p: bridge.image_ready.emit(seq, p)
    core["controller"].signal_race_ended = win.on_race_ended

    def _crossing(t_press, code, suspect=False):
        bridge.crossing.emit(t_press, code, suspect)

    def _start(t_press, code, suspect=False):
        bridge.start.emit(t_press)

    def _end(t_press, code, suspect=False):
        bridge.end.emit(t_press, int(code))

    listener, fallback = build_trigger(config, _crossing, _start, _end, logger)
    if fallback:
        # Qt key-event fallback (§6.4): degraded precision, say so.
        logger.warning("trigger", "qt_fallback")

    if listener is not None and config.section("trigger")["grab_device"]:
        # Option A: the trigger keyboard is grabbed from ARM through RACE_OVER,
        # tied directly to state instead of a 250 ms polling timer (§9). Bow
        # entry moved to REVIEW, so nothing needs the keyboard between arm and
        # end. Recovery on a freeze is via the separate primary keyboard.
        from .ui.state import AppState
        def _sync_grab(state):
            try:
                listener.set_grab(state in (AppState.ARMED, AppState.RECORDING))
            except Exception:
                pass
        win.on_state_changed = _sync_grab
        # The initial READY was applied before the hook was wired, so fire it
        # once to release any grab taken at construction (§9/Opt A).
        _sync_grab(win._last_state)

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
        if listener:
            listener.stop()
        core["reader"].stop()
        transport.stop()
        logger.stop()

    app.aboutToQuit.connect(_cleanup)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
