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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import storage as storage_mod
from .calibration import CALIBRATION_FILENAME, Calibration, calibration_path
from .framebuffer import FrameBuffer
from .framestore import FrameStore, nearest
from .mjpeg import Frame
from .storage import Storage


class RaceStateError(Exception):
    """A race cannot start because the controller is not in a startable state
    (already running, or a prior race was never ended)."""


@dataclass(frozen=True)
class Capture:
    """One recorded crossing, delivered to the UI via the ``events`` hook."""
    id: int
    sequence: int
    t_press: float
    elapsed_s: float
    delta_used: float
    image_flag: str | None
    debounce_suspect: bool = False
    t0_reconstructed: bool = False


class CaptureController:
    def __init__(self, config, storage: Storage, framebuffer: FrameBuffer,
                 logger=None, scheduler=None):
        self.config = config
        self.storage = storage
        self.buffer = framebuffer
        self.logger = logger
        # Injection seam: a callable scheduler(delay_s, callback) returning an
        # object with .cancel(). None keeps the default threading.Timer path.
        self._scheduler = scheduler

        self.t0: float | None = None
        self.t0_wall: float | None = None
        self.race_id: int | None = None
        self.race_dir: Path | None = None
        # Gun-indexed frame store for the current race (plan step 5.3). Created
        # in start_race()/resume_race() once race_id, race_dir and t0 are known.
        self.store: FrameStore | None = None
        self.delta = 0.0
        self.running = False
        # N4: True once a resume reconstructed t0 from the wall clock; every
        # capture recorded afterwards is flagged so export/UI can mark it.
        self.t0_reconstructed = False
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

        # ONE UI hook: events(kind, payload). See _emit for the kinds. The UI
        # (or a test) installs a callable that switches on ``kind``.
        self.events: Callable[[str, dict], None] | None = None

        # Deferred selection timing (ms). Margin so after-window frames exist.
        capture = config.section("capture")
        self.window_before_s = float(capture["window_before_ms"]) / 1000.0
        self.window_after_s = float(capture["window_after_ms"]) / 1000.0
        self.window_after_ms = float(capture["window_after_ms"])
        self._margin_s = 0.05

    # --- events -----------------------------------------------------------
    def _emit(self, kind: str, **payload) -> None:
        """Deliver one UI event. Never let a UI failure reach the timing path:
        the writer/timer threads call this, so exceptions are swallowed."""
        if self.events:
            try:
                self.events(kind, payload)
            except Exception:
                pass

    def _warn(self, msg: str) -> None:
        if self.logger:
            self.logger.warning("controller", "warning", message=msg)
        self._emit("warning", message=msg)

    def _emit_capture(self, cap: Capture) -> None:
        self._emit("capture_added", capture=cap)

    @staticmethod
    def _capture_from_row(row) -> Capture:
        """Build the frozen UI dataclass from a storage ``capture`` row."""
        return Capture(row["id"], row["sequence"], row["t_press"], row["elapsed_s"],
                       row["delta_used"], row["image_flag"],
                       bool(row["debounce_suspect"]),
                       bool(row["t0_reconstructed"])
                       if "t0_reconstructed" in row.keys() else False)

    # --- race lifecycle ---------------------------------------------------
    def start_race(self, t_press: float, name: str = "Race",
                   race_no: str | None = None,
                   heat_no: str | None = None) -> int:
        """t_press is an evdev-sourced timestamp (§5.3). Arming happens
        elsewhere; this call is the actual start.

        Returns the new ``race_id`` on success; raises :class:`RaceStateError`
        when a race is already running or the prior race was never ended, and
        re-raises :class:`CalibrationError` when water-mode calibration does not
        match the live stream (§8)."""
        if self.running:
            raise RaceStateError("race already running")
        if self.race_id is not None:
            prior = self.storage.get_race(self.race_id)
            if prior is not None and prior["ended_at"] is None:
                raise RaceStateError(f"end race {self.race_id} first")

        # Radio-relayed starts (§5.3.1): the operator hears the gun D seconds
        # late, so the real t0 is D before the press. Applied to t0 (not to each
        # elapsed) so every time carries the correction; D is 0 unless the race
        # is configured start_mode="radio".
        delay_s = (self.radio_delay_ms / 1000.0
                   if self.start_mode == "radio" else 0.0)
        self.t0 = t_press - delay_s
        self.t0_wall = time.time() - delay_s
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
                raise CalibrationError(f"race NOT started: {exc}") from exc
        self.running = True
        self.t0_reconstructed = False
        self.ended_at_mono = None
        self.ended_capture_count = 0

        race_id = self.storage.create_race(
            name=name, t0_monotonic=self.t0, t0_wall=self.t0_wall,
            start_mode=self.start_mode,
            radio_delay_ms=self.radio_delay_ms if self.start_mode == "radio" else 0.0,
            delta_used=self.delta, viewing_mode=timing_viewing(self.config),
            fps_nominal=self.preview_fps, image_off=self.image_off,
            race_no=race_no, heat_no=heat_no,
            window_before_ms=round(self.window_before_s * 1000),
            window_after_ms=round(self.window_after_s * 1000))
        self.race_id = race_id

        # Race directory is keyed by the unique, never-reused race_id so two
        # races with the same display name (or two started in the same minute)
        # can never collide and overwrite each other's captures.
        self.race_dir = self._race_dir(race_id, name)
        # The frame store writes one file per gun-time under this race's frames/
        # directory (plan step 5.3). Built here, after t0 and race_dir are set.
        self.store = FrameStore(self.storage, race_id, self.race_dir, self.t0)

        self._emit("race_started", race_id=race_id)
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
        self.t0_reconstructed = row["boot_id"] != boot_id
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
        # Recreate the frame store for the resumed race (plan step 5.3): it needs
        # the race's t0 and directory, both now known.
        self.store = FrameStore(self.storage, race_id, self.race_dir, self.t0)

    def end_race(self, t_end: float | None = None) -> int | None:
        """Finish the current race (spec: an explicit End-Race so the operator
        knows when the last end is in and the race is over).

        Stops continuous archiving, persists ``ended_at``/``t_end_monotonic``,
        clears ``running`` (which the UI's grab-sync timer uses to release the
        trigger keyboard), and emits ``race_ended``. Does NOT tear down
        the persistence writer thread — a new race can start next."""
        if not self.running or self.race_id is None:
            self._warn("end ignored: no race running")
            return None
        race_id = self.race_id
        t_end = time.monotonic() if t_end is None else t_end
        self.running = False
        self.ended_at_mono = t_end
        # Commit any press still in the queue before counting, so the race-over
        # summary does not omit the last crossing(s) under disk pressure.
        self._drain_queue()
        rows = self.storage.captures_for_race(race_id)
        self.ended_capture_count = len(rows)

        self.storage.mark_race_ended(race_id, t_end)

        if self.logger:
            self.logger.info("controller", "race_ended",
                             race_id=race_id, ends=self.ended_capture_count,
                             t_end=t_end)
        self._emit("race_ended", race_id=race_id)
        return race_id

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
            "t0": self.t0,
            "store": self.store,
            "image_off": self.image_off,
            "t_press": t_press,
            "elapsed_s": elapsed,
            "target": target,
            "delta_used": self.delta,
            "debounce_suspect": debounce_suspect,
            "t0_reconstructed": self.t0_reconstructed,
        }))
        # UI row appended off-thread once committed.
        return None

    # --- writer thread ----------------------------------------------------
    def _writer_loop(self) -> None:
        while True:
            kind, payload = self._queue.get()
            try:
                if kind == "capture":
                    self._handle_capture(payload)
                elif kind == "stop":
                    break
            except Exception as exc:
                # Never let one bad capture kill the writer: a dead writer means
                # every later crossing is queued but never persisted, silently
                # losing the primary datum. Log and keep serving the queue.
                if self.logger:
                    self.logger.warning("controller", "capture_failed",
                                        reason=str(exc))
                self._emit("warning",
                           message=f"crossing not saved: {exc}")
            finally:
                self._queue.task_done()

    def _drain_queue(self, timeout: float = 2.0) -> None:
        """Wait (bounded) for the writer to commit everything already queued.

        Used by ``end_race`` so its crossing count includes presses still in the
        queue under disk pressure; bounded so the GUI thread can never hang."""
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.002)

    def _handle_capture(self, payload: dict) -> None:
        race_id = payload["race_id"]
        race_dir = payload["race_dir"]
        t0 = payload["t0"]
        store = payload["store"]
        image_off = payload["image_off"]
        t_press = payload["t_press"]
        elapsed = payload["elapsed_s"]
        target = payload["target"]
        sequence = self.storage.next_sequence(race_id)

        # Does the buffer have any frames yet? (§6.5 edge cases)
        span = self.buffer.span()
        if image_off:
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
            debounce_suspect=int(payload["debounce_suspect"]),
            target_ms=round((target - t0) * 1000),
            t0_reconstructed=int(payload["t0_reconstructed"]))

        cap = Capture(capture_id, sequence, t_press, elapsed,
                      payload["delta_used"], image_flag,
                      bool(payload["debounce_suspect"]))
        self._emit_capture(cap)

        # Deferred image selection: schedule after window_after + margin so the
        # after-window frames exist (spec §6.5). The timer removes itself from
        # the set when it fires so the set does not grow for every capture.
        # Skipped entirely in a timing-only race — no images to attach.
        if not image_off:
            # A negative Δ (water mode: Δ = R − L) puts the target later than the
            # press, so the after-window reaches past t_press + window_after and
            # those frames do not exist yet at the base delay. Extend the margin
            # by |Δ| or the post-line evidence is silently truncated (§5.4/§6.5).
            # Cap at the buffer span so a pathological Δ cannot schedule a timer
            # far into the future.
            extra = min(max(0.0, -payload["delta_used"]), self.buffer.seconds)
            delay = self.window_after_s + self._margin_s + extra

            def _fire() -> None:
                with self._timers_lock:
                    self._timers.discard(timer)
                self._select_images(capture_id, sequence, target, race_dir,
                                    store, t0)

            if self._scheduler is not None:
                timer = self._scheduler(delay, _fire)
                with self._timers_lock:
                    self._timers.add(timer)
            else:
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
                       race_dir, store=None, t0=None) -> None:
        """Deferred selection: persist the window frames and pick the primary.

        The window is saved once per race through the gun-indexed frame store
        (plan step 5.3): a frame is named by its ``t_ms`` since the gun and
        shared by every crossing whose window covers it, so no per-crossing
        ``captures/`` copies are written. The primary is the stored frame nearest
        the selection target; ``image_flag`` keeps its existing meaning.

        *store* and *t0* are the press's snapshot, passed by the timer thread so
        a crossing is always attributed to the race that owned it — never to a
        race that started after the press (``record_crossing``'s comment). They
        default to the live values for the in-race ``update_crossing_time`` call.
        """
        store = self.store if store is None else store
        t0 = self.t0 if t0 is None else t0
        if race_dir is None or store is None or t0 is None:
            return
        frames = self.buffer.window(target, self.window_before_s, self.window_after_s)
        rows = store.save(frames)
        target_ms = round((target - t0) * 1000)
        primary = nearest(rows, target_ms)

        # The insertion-time flag reflected the buffer state *before* the deferred
        # selection (latency made target appear newer than the newest frame).
        # Recompute it now from what was actually selected (§6.5): the flag
        # describes the attached image, not the transient state at the press.
        if primary is None:
            flag = "missing"
        else:
            span = self.buffer.span()
            if span is None or target < span[0] or target > span[1]:
                flag = "approximate"
            else:
                flag = None
        self.storage.update_capture(capture_id, image_flag=flag)

        if primary is not None:
            self.storage.set_primary(capture_id, primary["id"])
            # Notify the UI so the last-capture panel / log thumbnails can show
            # the photo now that the deferred selection landed (§3). Emitted from
            # the deferred-timer thread, never the trigger path. ``path`` is
            # already relative to data_root (stored by FrameStore.save).
            self._emit("image_ready", sequence=sequence, path=primary["path"])

    def frames_for_capture(self, capture_id: int) -> list:
        """The frames in a capture's selection window (plan step 5.3).

        Passthrough to Storage for the review screen; the frame rows carry
        ``t_ms``/``path`` and the capture row carries ``target_ms`` and
        ``primary_frame_id``."""
        return self.storage.frames_for_capture(capture_id)

    def set_bow_number(self, capture_id: int, value: str | None) -> None:
        self.storage.update_capture(capture_id, bow_number=value)

    def update_crossing_time(self, capture_id: int, elapsed_s: float) -> bool:
        """Rewrite a crossing's elapsed time and re-derive its frame target.

        ``storage.set_crossing_time`` recomputes the press timestamps; here the
        selection ``target_ms`` is recomputed too (plan step 7.1). If no frame
        now falls in the window the image is flagged ``missing`` and the primary
        cleared, so the review UI can offer "re-pick image". While the race is
        still running and the live buffer holds frames for the new target,
        selection is re-run so the photo follows the edited time."""
        if not self.storage.set_crossing_time(capture_id, elapsed_s):
            return False
        row = self.storage.capture(capture_id)
        if row is None:
            return True
        race_id = row["race_id"]
        race = self.storage.get_race(race_id)
        if race is None or race["t0_monotonic"] is None:
            return True
        target_ms = round(
            (row["t_press"] - row["delta_used"] - race["t0_monotonic"]) * 1000)
        self.storage.update_capture(capture_id, target_ms=target_ms)
        if not self.storage.frames_for_capture(capture_id):
            self.storage.update_capture(
                capture_id, image_flag="missing",
                primary_frame_id=None, primary_image=None)
        target = row["t_press"] - self.delta
        if (self.running and race_id == self.race_id
                and self.buffer.window(target, self.window_before_s,
                                       self.window_after_s)):
            self._select_images(capture_id, row["sequence"], target, self.race_dir)
        return True

    def set_start_time(self, race_id: int, new_t0_wall: float) -> bool:
        """Set the race's wall-clock start time (gun), shifting every crossing's
        wall-clock time by the same delta. Elapsed times are unchanged."""
        return self.storage.set_start_time(race_id, new_t0_wall)

    def set_primary(self, capture_id: int, frame_id: int) -> str | None:
        """Promote *frame_id* to the capture's primary photo (operator review).

        Returns the new primary_image path relative to ``data_root`` (or None).
        Emits ``image_ready`` so list thumbnails refresh.
        """
        self.storage.set_primary(capture_id, frame_id)
        cap = self.storage.capture(capture_id)
        path = cap["primary_image"] if cap else None
        if cap and path:
            self._emit("image_ready", sequence=cap["sequence"], path=path)
        return path

    def remove(self, capture_id: int) -> None:
        """Soft-delete a crossing and announce it (plan step 7.1).

        The row stays on disk with ``deleted=1`` (spec §6.7); the
        ``capture_deleted`` event carries its ``sequence`` so the review UI can
        strike it through rather than drop it."""
        cap = self.storage.capture(capture_id)
        self.storage.update_capture(capture_id, deleted=1)
        if cap is not None:
            self._emit("capture_deleted", sequence=cap["sequence"])

    def restore(self, capture_id: int) -> Capture | None:
        """Undo a soft delete and announce the crossing as (re)added."""
        self.storage.restore_capture(capture_id)
        row = self.storage.capture(capture_id)
        if row is None:
            return None
        cap = self._capture_from_row(row)
        self._emit_capture(cap)
        return cap

    def clone(self, capture_id: int) -> Capture | None:
        """Duplicate a crossing as a new row and announce it (plan step 7.1).

        The clone shares the parent's frames through ``target_ms`` and gets a
        fresh ``MAX(sequence)+1`` (spec §6.7)."""
        new_id = self.storage.clone_capture(capture_id)
        if new_id is None:
            return None
        row = self.storage.capture(new_id)
        if row is None:
            return None
        cap = self._capture_from_row(row)
        self._emit_capture(cap)
        return cap

    def undo_last(self) -> None:
        if self.race_id is None:
            return
        rows = self.storage.captures_for_race(self.race_id, include_deleted=True)
        if not rows:
            return
        cap = rows[-1]
        self.storage.update_capture(cap["id"], deleted=1)
        self._emit("capture_deleted", sequence=cap["sequence"])

    def stop(self) -> None:
        with self._timers_lock:
            for t in list(self._timers):
                t.cancel()
                self._timers.discard(t)
        self._queue.put(("stop", None))
        # Wait (bounded) for queued captures to commit before the process exits.
        self._writer_thread.join(timeout=2.0)


def timing_viewing(config) -> str:
    return config.section("timing")["viewing_mode"]


class CalibrationError(Exception):
    """Calibration missing, unreadable, or inconsistent with the live stream."""


def _load_latency(config, buffer) -> float | None:
    """Latency median (ms) from the calibration file, validated against the live
    stream (spec §8): refuse to start unless the file exists AND its resolution
    and fps match the live stream.

    Thin wrapper over :class:`calibration.Calibration`: it keeps the historical
    name/return type and raises :class:`CalibrationError` on a missing,
    unreadable, or mismatched calibration."""
    cal = Calibration.load(config.data_root)
    if cal is None:
        if calibration_path(config.data_root).exists():
            raise CalibrationError(f"{CALIBRATION_FILENAME} unreadable")
        raise CalibrationError(
            f"{CALIBRATION_FILENAME} missing — run Calibrate first (§8, §5.5)")
    try:
        live_res, _live_mean, live_fps = buffer.live_format()
    except ValueError as exc:
        raise CalibrationError(str(exc))
    reason = cal.mismatch(live_res, live_fps)
    if reason:
        raise CalibrationError(reason)
    return cal.latency_median_ms


def calibration_status(config, buffer):
    """Proactive calibration health for the idle UI.

    Returns ``(ok: bool, detail: str)`` comparing the calibrated resolution/fps
    against the live stream — the two pipeline properties that determine latency.
    ``ok`` is False when calibration is missing, unreadable, or the live
    resolution/fps no longer match. Does NOT raise (UI-safe); on measurement
    failure it reports ok=True with an empty detail so the status bar can keep
    showing stream health without spurious alarms.

    Thin wrapper over :class:`calibration.Calibration` (spec §8)."""
    cal = Calibration.load(config.data_root)
    if cal is None:
        if calibration_path(config.data_root).exists():
            return False, f"{CALIBRATION_FILENAME} unreadable"
        return False, f"no {CALIBRATION_FILENAME} — run Calibrate"
    try:
        live_res, _mean, live_fps = buffer.live_format()
    except Exception:
        return True, ""  # stream measurement unavailable; defer to stream health
    if not live_res and live_fps <= 0:
        return True, ""
    reason = cal.mismatch(live_res, live_fps)
    if reason:
        return False, reason
    return True, ""
