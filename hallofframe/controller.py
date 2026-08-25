"""Capture orchestration (spec §6.5).

Nothing on the trigger path touches disk. ``record_crossing`` computes elapsed
and target, enqueues to a single-writer persistence thread, and emits a Qt
signal for the UI — then returns. The writer thread INSERTs the row, then
*schedules* image selection for ``t_press + window_after_ms + margin`` so the
frames showing the boat actually exist in the buffer by selection time.

Supports ``resume_race()`` for a survivable mid-heat restart (N4).
"""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from . import storage as storage_mod
from .framebuffer import FrameBuffer
from .mjpeg import Frame
from .storage import Storage


class Capture:
    def __init__(self, capture_id: int, sequence: int, t_press: float,
                 elapsed_s: float, delta_used: float, image_flag: str | None,
                 debounce_suspect: bool = False):
        self.id = capture_id
        self.sequence = sequence
        self.t_press = t_press
        self.elapsed_s = elapsed_s
        self.delta_used = delta_used
        self.image_flag = image_flag
        self.debounce_suspect = debounce_suspect


class CaptureController:
    def __init__(self, config, storage: Storage, framebuffer: FrameBuffer,
                 logger=None):
        self.config = config
        self.storage = storage
        self.buffer = framebuffer
        self.logger = logger

        self.t0: float | None = None
        self.t0_wall: float | None = None
        self.race_id: int | None = None
        self.race_dir: Path | None = None
        self.delta = 0.0
        self.running = False
        self.ended_at_mono: float | None = None  # monotonic time of end_race()
        self.ended_capture_count = 0  # non-deleted ends at the moment of ending
        self.preview_fps = float(config.section("stream")["assumed_fps"])

        timing = config.section("timing")
        self.start_mode = timing["start_mode"]
        self.radio_delay_ms = float(timing["radio_delay_ms"])
        self.image_mode = timing["image_mode"]  # "auto" | "off" (timing-only)
        # Per-race flag: True when this race records time only (config "off" or
        # a dead stream at start). Set in start_race(); fixed for the race.
        self.image_off = self.image_mode == "off"

        self._queue: queue.Queue = queue.Queue()
        self._writer_thread = threading.Thread(target=self._writer_loop,
                                               daemon=True, name="persist-writer")
        self._writer_thread.start()

        self._timers: set = set()
        self._timers_lock = threading.Lock()

        # Qt-signal-like hooks for the UI (a real Qt app swaps these).
        self.signal_capture_added = None  # callable(capture)
        self.signal_image_ready = None    # callable(sequence, primary_path)
        self.signal_race_started = None   # callable(race_id)
        self.signal_race_ended = None     # callable(race_id)
        self.signal_warning = None        # callable(str)

        # Deferred selection timing (ms). Margin so after-window frames exist.
        capture = config.section("capture")
        self.window_before_s = float(capture["window_before_ms"]) / 1000.0
        self.window_after_s = float(capture["window_after_ms"]) / 1000.0
        self.window_after_ms = float(capture["window_after_ms"])
        self._margin_s = 0.05

    # --- signals ----------------------------------------------------------
    def _warn(self, msg: str) -> None:
        if self.logger:
            self.logger.warning("controller", "warning", message=msg)
        if self.signal_warning:
            try:
                self.signal_warning(msg)
            except Exception:
                pass

    def _emit_capture(self, cap: Capture) -> None:
        if self.signal_capture_added:
            try:
                self.signal_capture_added(cap)
            except Exception:
                pass

    # --- race lifecycle ---------------------------------------------------
    def start_race(self, t_press: float, name: str = "Race",
                   race_no: str | None = None,
                   heat_no: str | None = None) -> int:
        """t_press is an evdev-sourced timestamp (§5.3). Arming happens
        elsewhere; this call is the actual start."""
        if self.running:
            self._warn("race already running")
            return self.race_id
        if self.race_id is not None and self.storage.get_race(self.race_id):
            self._warn("refusing to start while a prior race is open")

        self.t0 = t_press
        self.t0_wall = time.time()
        # Validate calibration against the live stream BEFORE starting (spec §8):
        # refuse to start with a stale or mismatched Δ. Runs off the timing path's
        # per-crossing work, so a one-time decode here is acceptable.
        #
        # Two cases skip calibration entirely and race timing-only (§6.5):
        #   1. Config image_mode = "off" (the operator never wants video).
        #   2. The stream is DOWN at start (no frames arriving recently).
        #      Calibration requires the live stream to measure latency against,
        #      so it cannot be validated; the race auto-degrades to timing-only
        #      rather than refusing to start. There is no photo to mis-time, so
        #      Δ = 0 is safe.
        # The stream is "down" when no frame has arrived recently (buffer.health),
        # not merely when the ring is empty: if it died seconds ago the ring still
        # holds stale frames, and attaching those to a fresh race would show a
        # pre-arming frame that predates the crossing.
        alive, _fps, _age = self.buffer.health()
        if self.image_mode == "off" or not alive:
            self.image_off = True
            self.delta = 0.0
        else:
            self.image_off = False
            try:
                self.delta = self._compute_delta()
            except CalibrationError as exc:
                self._warn(f"race NOT started: {exc}")
                return self.race_id
        self.running = True
        self.ended_at_mono = None
        self.ended_capture_count = 0

        race_id = self.storage.create_race(
            name=name, t0_monotonic=t_press, t0_wall=self.t0_wall,
            start_mode=self.start_mode,
            radio_delay_ms=self.radio_delay_ms if self.start_mode == "radio" else 0.0,
            delta_used=self.delta, viewing_mode=timing_viewing(self.config),
            fps_nominal=self.preview_fps, image_off=self.image_off,
            race_no=race_no, heat_no=heat_no)
        self.race_id = race_id

        # Race directory is keyed by the unique, never-reused race_id so two
        # races with the same display name (or two started in the same minute)
        # can never collide and overwrite each other's captures.
        self.race_dir = self._race_dir(race_id, name)

        if self.signal_race_started:
            try:
                self.signal_race_started(race_id)
            except Exception:
                pass
        return race_id

    def resume_race(self, race_id: int) -> None:
        row = self.storage.get_race(race_id)
        if row is None:
            raise ValueError(f"no race with id {race_id}")
        boot_id = storage_mod.current_boot_id()
        self.race_id = race_id
        self.delta = row["delta_used"]
        self.image_off = bool(row["image_off"]) if "image_off" in row.keys() \
            else self.image_mode == "off"
        self.start_mode = row["start_mode"]
        self.radio_delay_ms = row["radio_delay_ms"]
        self.running = True
        self.ended_at_mono = None
        self.ended_capture_count = 0
        if row["boot_id"] == boot_id:
            self.t0 = row["t0_monotonic"]
        else:
            # Reconstruct from wall clock; flag everywhere (§6.5).
            self.t0 = time.monotonic() - (time.time() - row["t0_wall"])
            self.storage.mark_race_reconstructed(race_id, self.t0)
            self._warn(f"race {race_id}: t0 reconstructed from wall clock "
                       "(boot_id mismatch); times flagged t0_reconstructed")
        self.race_dir = self._race_dir(race_id, row["name"])

    def end_race(self, t_end: float | None = None) -> int | None:
        """Finish the current race (spec: an explicit End-Race so the operator
        knows when the last end is in and the race is over).

        Stops continuous archiving, persists ``ended_at``/``t_end_monotonic``,
        clears ``running`` (which the UI's grab-sync timer uses to release the
        trigger keyboard), and emits ``signal_race_ended``. Does NOT tear down
        the persistence writer thread — a new race can start next."""
        if not self.running or self.race_id is None:
            self._warn("end ignored: no race running")
            return None
        t_end = time.monotonic() if t_end is None else t_end
        self.running = False
        self.ended_at_mono = t_end
        rows = self.storage.captures_for_race(self.race_id)
        self.ended_capture_count = len(rows)

        self.storage.mark_race_ended(self.race_id, t_end)

        if self.logger:
            self.logger.info("controller", "race_ended",
                             race_id=self.race_id, ends=self.ended_capture_count,
                             t_end=t_end)
        if self.signal_race_ended:
            try:
                self.signal_race_ended(self.race_id)
            except Exception:
                pass
        return self.race_id

    def _race_dir(self, race_id: int, name: str):
        """Unique, deterministic per-race directory: races/<id>_<sanitized name>."""
        clean = "".join(c if (c.isalnum() or c in "._- ") else "_"
                        for c in name).strip()
        clean = clean or "race"
        return self.storage.data_root / "races" / f"{race_id:04d}_{clean}"

    # --- delta ------------------------------------------------------------
    def _compute_delta(self) -> float:
        timing = self.config.section("timing")
        reaction_ms = float(timing["reaction_offset_ms"])
        viewing = timing["viewing_mode"]
        if viewing == "screen":
            # §5.4: screen mode cancels latency entirely; Δ = R. No calibration
            # needed.
            return reaction_ms / 1000.0
        # water mode: Δ = R − L, so the calibrated latency is required and must
        # match the live stream (§8).
        latency_ms = _load_latency(self.config, self.buffer)
        return (reaction_ms - latency_ms) / 1000.0

    # --- crossing ---------------------------------------------------------
    def record_crossing(self, t_press: float, debounce_suspect: bool = False) -> Capture | None:
        if not self.running or self.t0 is None or self.race_id is None:
            self._warn("trigger ignored: no race started")
            return None
        elapsed = t_press - self.t0
        target = t_press - self.delta
        # Fast path: enqueue, return immediately. Nothing on this path hits disk.
        # Snapshot the race identity NOW: the writer thread (and the deferred
        # image selector) may run after this race has ended and the next started,
        # so they must attribute to the race that actually owned the press.
        self._queue.put(("capture", {
            "race_id": self.race_id,
            "race_dir": self.race_dir,
            "t_press": t_press,
            "elapsed_s": elapsed,
            "target": target,
            "delta_used": self.delta,
            "debounce_suspect": debounce_suspect,
        }))
        # UI row appended off-thread once committed.
        return None

    # --- writer thread ----------------------------------------------------
    def _writer_loop(self) -> None:
        while True:
            kind, payload = self._queue.get()
            if kind == "capture":
                self._handle_capture(payload)
            elif kind == "stop":
                break

    def _handle_capture(self, payload: dict) -> None:
        race_id = payload["race_id"]
        race_dir = payload["race_dir"]
        t_press = payload["t_press"]
        elapsed = payload["elapsed_s"]
        target = payload["target"]
        sequence = self.storage.next_sequence(race_id)

        # Does the buffer have any frames yet? (§6.5 edge cases)
        span = self.buffer.span()
        if self.image_off:
            # Timing-only race: never attach an image, regardless of the buffer.
            image_flag = "missing"
        elif span is None:
            # Stream down: record the time anyway, flag the missing photo (§6.5).
            image_flag = "missing"
        elif target < span[0]:
            image_flag = "approximate"
        elif target > span[1]:
            image_flag = "approximate"
        else:
            image_flag = None

        capture_id = self.storage.insert_capture(
            race_id, sequence, t_press, time.time(), elapsed,
            payload["delta_used"], image_flag=image_flag,
            debounce_suspect=int(payload["debounce_suspect"]))

        cap = Capture(capture_id, sequence, t_press, elapsed,
                      payload["delta_used"], image_flag,
                      bool(payload["debounce_suspect"]))
        self._emit_capture(cap)

        # Deferred image selection: schedule after window_after + margin so the
        # after-window frames exist (spec §6.5). The timer removes itself from
        # the set when it fires so the set does not grow for every capture.
        # Skipped entirely in a timing-only race — no images to attach.
        if not self.image_off:
            delay = self.window_after_s + self._margin_s

            def _fire() -> None:
                with self._timers_lock:
                    self._timers.discard(timer)
                self._select_images(capture_id, sequence, target, race_dir)

            timer = threading.Timer(delay, _fire)
            timer.daemon = True
            with self._timers_lock:
                self._timers.add(timer)
            timer.start()

        if self.logger:
            self.logger.info("controller", "capture",
                             sequence=sequence, t_press=t_press,
                             elapsed=elapsed, target=target,
                             image_flag=image_flag,
                             debounce_suspect=int(payload["debounce_suspect"]))

    def _select_images(self, capture_id: int, sequence: int, target: float,
                       race_dir) -> None:
        if race_dir is None:
            return
        frames = self.buffer.window(target, self.window_before_s, self.window_after_s)
        captures_dir = race_dir / "captures"
        captures_dir.mkdir(parents=True, exist_ok=True)

        primary = self.buffer.nearest(target)
        if primary is not None and primary not in frames:
            frames.append(primary)

        chosen_primary: Path | None = None
        chosen_primary_id: int | None = None
        chosen_offset = float("inf")
        for f in sorted(frames, key=lambda f: f.t_recv):
            offset_ms = (f.t_recv - target) * 1000.0
            sign = "-" if offset_ms < 0 else "+"
            fname = f"{sequence:03d}_w{sign}{abs(offset_ms):04.0f}.jpg"
            fpath = captures_dir / fname
            fpath.write_bytes(f.jpeg)
            frame_id = self.storage.insert_frame(
                capture_id, f.t_recv, offset_ms, str(fpath.relative_to(self.storage.data_root)))
            # primary = the frame nearest target (N2), not the earliest window frame
            if abs(offset_ms) < abs(chosen_offset):
                chosen_offset = offset_ms
                chosen_primary = fpath
                chosen_primary_id = frame_id

        # The insertion-time flag reflected the buffer state *before* the deferred
        # selection (latency made target appear newer than the newest frame).
        # Recompute it now from what was actually selected (§6.5): the flag
        # describes the attached image, not the transient state at the press.
        if chosen_primary_id is None:
            flag = "missing"
        else:
            span = self.buffer.span()
            if span is None or target < span[0] or target > span[1]:
                flag = "approximate"
            else:
                flag = None
        self.storage.update_capture(capture_id, image_flag=flag)

        if chosen_primary_id is not None:
            self.storage.set_primary(capture_id, chosen_primary_id)
            # Notify the UI so the last-capture panel / log thumbnails can show
            # the photo now that the deferred selection landed (§3). Emitted from
            # the deferred-timer thread, never the trigger path.
            if self.signal_image_ready:
                try:
                    self.signal_image_ready(sequence, str(chosen_primary.relative_to(
                        self.storage.data_root)))
                except Exception:
                    pass

    def set_bow_number(self, capture_id: int, value: str | None) -> None:
        self.storage.update_capture(capture_id, bow_number=value)

    def set_primary(self, capture_id: int, frame_id: int) -> str | None:
        """Promote *frame_id* to the capture's primary photo (operator review).

        Returns the new primary_image path relative to ``data_root`` (or None).
        Emits ``signal_image_ready`` so list thumbnails refresh.
        """
        self.storage.set_primary(capture_id, frame_id)
        cap = self.storage.capture(capture_id)
        path = cap["primary_image"] if cap else None
        if cap and path and self.signal_image_ready:
            try:
                self.signal_image_ready(cap["sequence"], path)
            except Exception:
                pass
        return path

    def soft_delete(self, capture_id: int) -> None:
        self.storage.update_capture(capture_id, deleted=1)

    def undo_last(self) -> None:
        if self.race_id is None:
            return
        rows = self.storage.captures_for_race(self.race_id, include_deleted=True)
        if not rows:
            return
        self.storage.update_capture(rows[-1]["id"], deleted=1)

    def stop(self) -> None:
        with self._timers_lock:
            for t in list(self._timers):
                t.cancel()
                self._timers.discard(t)
        self._queue.put(("stop", None))


def timing_viewing(config) -> str:
    return config.section("timing")["viewing_mode"]


class CalibrationError(Exception):
    """Calibration missing, unreadable, or inconsistent with the live stream."""


def _load_latency(config, buffer) -> float | None:
    """Latency median (ms) from calibration.json, validated against the live
    stream (spec §8): refuse to start unless the file exists AND its resolution
    and fps match the live stream.

    Latency is a property of the PIPELINE (device → tunnel → decode → buffer),
    driven by resolution and fps — not by the scene. mean_frame_bytes is
    deliberately NOT gated: JPEG size varies with scene complexity, so a scene
    change would otherwise demand a needless re-calibration even though the
    pipeline latency is unchanged."""
    import json as _json
    root = config.data_root
    cal = root / "calibration.json"
    if not cal.exists():
        raise CalibrationError(
            "calibration.json missing — run Calibrate first (§8, §5.5)")
    try:
        data = _json.loads(cal.read_text())
    except Exception as exc:
        raise CalibrationError(f"calibration.json unreadable: {exc}")

    median = float(data.get("latency_median_ms", 0.0))
    cal_res = str(data.get("resolution", "") or "")
    cal_fps = float(data.get("fps", 0) or 0)

    live_res, _live_mean, live_fps = _measure_live(buffer)

    if cal_res and live_res and cal_res != live_res:
        raise CalibrationError(
            f"calibrated resolution {cal_res} != live {live_res} — re-calibrate")
    if cal_fps and live_fps and abs(live_fps - cal_fps) > max(1, cal_fps * 0.05):
        raise CalibrationError(
            f"calibrated fps {cal_fps:.0f} != live {live_fps:.1f} — re-calibrate")
    return median


def _measure_live(buffer):
    """Return (resolution_str, mean_frame_bytes, measured_fps) from the buffer."""
    try:
        with buffer._lock:
            snap = list(buffer._buf)
    except AttributeError:
        snap = []
    frames = snap[-30:] if len(snap) > 30 else snap
    if not frames:
        raise CalibrationError("no frames in buffer — is the stream up?")
    mean = int(sum(len(f.jpeg) for f in frames) / len(frames))
    # resolution from the newest frame
    w = h = 0
    try:
        from PIL import Image
        import io as _io
        im = Image.open(_io.BytesIO(frames[-1].jpeg)); im.load()
        w, h = im.size
    except Exception:
        pass
    res = f"{w}x{h}" if w and h else ""
    # fps from frame timestamps. Only report when the sampled window spans a
    # meaningful duration; at startup frames arrive in a burst so a short window
    # reads an inflated instantaneous rate and would spuriously flag the
    # calibration as stale. fps=0 defers the check to the stream health instead.
    if len(frames) >= 2:
        span = frames[-1].t_recv - frames[0].t_recv
        fps = (len(frames) - 1) / span if span >= 0.5 else 0.0
    else:
        fps = 0.0
    return res, mean, fps


def live_lens_name(resolution: str) -> str:
    """No lens info is available from the stream; the check is skipped unless the
    calibration recorded a lens. Kept as a hook for future lens telemetry."""
    return ""


def calibration_status(config, buffer):
    """Proactive calibration health for the idle UI.

    Returns ``(ok: bool, detail: str)`` comparing the calibrated resolution/fps
    against the live stream — the two pipeline properties that determine latency.
    ``ok`` is False when calibration is missing, unreadable, or the live
    resolution/fps no longer match. Does NOT raise (UI-safe); on measurement
    failure it reports ok=True with an empty detail so the status bar can keep
    showing stream health without spurious alarms."""
    import json as _json
    root = config.data_root
    cal = root / "calibration.json"
    if not cal.exists():
        return False, "no calibration.json — run Calibrate"
    try:
        data = _json.loads(cal.read_text())
    except Exception:
        return False, "calibration.json unreadable"
    cal_res = str(data.get("resolution", "") or "")
    cal_fps = float(data.get("fps", 0) or 0)

    try:
        live_res, _mean, live_fps = _measure_live(buffer)
    except Exception:
        return True, ""  # stream measurement unavailable; defer to stream health
    if not live_res and live_fps <= 0:
        return True, ""

    if cal_res and live_res and cal_res != live_res:
        return False, f"RESOLUTION CHANGED: cal {cal_res} vs live {live_res} — re-calibrate"
    if cal_fps and live_fps and abs(live_fps - cal_fps) > max(1, cal_fps * 0.05):
        return False, (f"FPS CHANGED: cal {cal_fps:.0f} vs live {live_fps:.1f} "
                       "— re-calibrate")
    return True, ""
