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
    crossing = Signal(float, int)   # (t_press, keycode)
    start = Signal(float)           # (t_press)
    end = Signal(float)             # (t_press)
    capture = Signal(object)        # (Capture)


def build_core(config):
    """Construct transport, reader, buffer, storage and controller.
    Returns a dict of the parts so headless tests can reuse it without Qt."""
    import time
    from .controller import CaptureController
    from .framebuffer import FrameBuffer
    from .mjpeg import MJPEGReader
    from .storage import Storage
    from .transport import UsbTransport

    storage = Storage(config.data_root)

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
    try:
        listener = TriggerListener(
            device, handlers,
            debounce_ms=float(config.section("timing")["debounce_ms"]),
            grab=bool(trig["grab_device"]))
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

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .log import start_logging
    logger = start_logging(config.data_root / "logs", race_id=None)
    logger.info("app", "start", version=__import__("regatta_timer").__version__)

    core = build_core(config)
    app = QApplication(sys.argv)

    from .trigger import TriggerListener
    trig = None

    from .ui.main_window import MainWindow
    win = MainWindow(config, core["controller"], core["buffer"])
    win.showFullScreen()

    # Bridge worker-thread events (evdev triggers AND the controller's capture
    # completion) back onto the Qt main thread (§6.4, §6.5). Every Qt-facing
    # callback must reach the GUI thread through these queued signals; calling
    # into widgets directly from a worker thread can deadlock the UI.
    bridge = _TriggerBridge()
    bridge.start.connect(win.on_evdev_start)
    bridge.crossing.connect(win.on_evdev_crossing)
    bridge.end.connect(win.on_evdev_end)
    bridge.capture.connect(win.on_capture)
    # The controller emits capture-added from its persistence writer thread; reroute
    # it through the bridge instead of letting MainWindow._connect_controller point
    # it straight at the Qt widgets. signal_race_ended is emitted from end_race(),
    # which always runs on the GUI thread (button or queued evdev end), so it can
    # call the window directly.
    core["controller"].signal_capture_added = lambda cap: bridge.capture.emit(cap)
    core["controller"].signal_race_ended = win.on_race_ended

    def _crossing(t_press, code, suspect=False):
        bridge.crossing.emit(t_press, code)

    def _start(t_press, code, suspect=False):
        bridge.start.emit(t_press)

    def _end(t_press, code, suspect=False):
        bridge.end.emit(t_press)

    listener, fallback = build_trigger(config, _crossing, _start, _end, logger)
    if fallback:
        # Qt key-event fallback (§6.4): degraded precision, say so.
        logger.warning("trigger", "qt_fallback")

    if listener is not None and config.section("trigger")["grab_device"]:
        # Grab the trigger device only while a race is active (§6.4), so bow
        # numbers can still be typed between races.
        def _sync_grab():
            try:
                listener.set_grab(core["controller"].running)
            except Exception:
                pass
        grab_timer = QTimer()
        grab_timer.timeout.connect(_sync_grab)
        grab_timer.start(250)

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
