# Test status

Last updated: 2026-08-22T14:40Z

| Test | Stage (spec §12) | Last run | Result | Report |
|------|------------------|----------|--------|--------|
| T0  | Environment          | 2026-08-22T14:38Z | **PASS** | — |
| T1  | Transport smoke      | 2026-08-22T14:33Z | **PASS** | see §1 below |
| T2  | MJPEGReader          | 2026-08-22T14:50Z | **PASS** | see §2 below |
| T3  | FrameBuffer          | 2026-08-22T14:40Z | PASS | — |
| T4  | Trigger / clock      | 2026-08-22T21:20Z | **PASS** | see §4 |
| T5  | Calibration          | 2026-08-22T22:41Z | **PASS** | see §5 |
| T6  | Controller + storage | 2026-08-22T14:40Z | PASS | — |
| T8  | Archive soak         | 2026-08-22T21:30Z | **PASS** (e2e) | see §6 |
| T7  | UI                   | 2026-08-22T15:00Z | **PASS** (smoke) | see §3 |
| T8  | Archive soak         | — | not run | — |
| T9  | Export               | 2026-08-22T14:40Z | PASS | — |
| T10 | Acceptance (N2)      | — | not run | — |
| T11 | Offline (N3)         | — | not run | — |
| T12 | Cold start (N5)      | — | not run | — |
| DR  | Dress rehearsal      | — | not run | — |

## Environment (T0) — resolved 2026-08-22

`verify-env.sh` (INSTALL.md §8) prints **ENVIRONMENT READY**, exit 0 — all 16
lines OK. `environment-lock.txt` written. Hold applied
(`python3`, `python3-minimal`, `libpython3-stdlib`); unattended-upgrades and
apt-daily timers disabled.

The remaining items the checklist cannot cover (phone-dependent) are carried
forward to §12 step 1: `idevice_id -l` returns a UDID, and sustained usbmuxd
throughput.

## §1 Transport smoke (T1) — resolved 2026-08-22

USB-only (no Wi-Fi) verified live against the real iPhone over `iproxy`:

- `idevice_id -l` → `00008150-000569601E8A401C` (iPhone, iOS 26.6)
- `iproxy 8081 8081` tunnel up; stream URL `http://127.0.0.1:8081/video`,
  auth `admin`/`admin`
- `Content-Type: multipart/x-mixed-replace; boundary=--BoundaryString`
- Measured over 8 s: **~29.5 fps, ~60 Mbps (7.5 MB/s), ~256 KB/frame**

**Conclusion:** 1080p30 over USB is feasible (60 Mbps well within the usbmuxd
tunnel). This resolves open verification item #2 in `system-environment.md`
§7 and locks in the 1080p30 decision (spec §10.2). The app's declared path is
`/video`, not the `/live` default — note this for `config.toml` `[stream].url`.

## §5 Calibration (T5) — implementation + math verified 2026-08-22

- `compute_latency` validated against synthetic frames: 30 fps, true latency
  200 ms → **median 200.3 ms, IQR 0.3 ms**. `write_calibration` produces a
  valid `calibration.json` with format fields.
- Live capture in the real event loop: **20 distinct frames** collected while the
  full-screen counter rendered (non-blocking), `_measure_format` reads real
  **1440×1080**, mean 176652 bytes.
- **Camera format discovered: the phone streams 1440×1080, not 1920×1080.** The
  calibration records the real format so the §8 race-start mismatch check stays
  consistent.

Fixes made:
- Counter showed `monotonic() - t0` (ms since counter open) — wrong domain for
  `L = t_recv - T_shown`. Now shows `int(time.monotonic() * 1000)` (spec §5.5).
- `_capture` blocked the GUI thread with `time.sleep`, so the full-screen counter
  never painted. Rewritten to QTimer-driven sampling (spec §5.5 constraint 2).
- Counter was **white text on a light window** → invisible numbers on screen and
  in captured frames. Now solid black bg, 180 pt white digits, red
  "RECORDING — POINT THE PHONE AT THIS COUNTER" banner.
- **Modal `CalibrationDialog.exec()` blocked the full-screen counter from being
  shown** (Qt modality). Changed to non-modal `show()` + `WA_DeleteOnClose`.
- Dialog now hides during capture and re-shows after (spec §5.5 constraint 2 is
  sequential). Counter re-raised every 100 ms so nothing sits on top.
- Fixed invalid `QTimer.singleShot(int, callable, int)` that crashed `_capture`
  mid-flow and left the counter stuck (had to ctrl+C). Capture errors now close
  the counter and restore the dialog.
- `_capture` now SAVES the 20 JPEGs and shows each image beside its input box so
  the operator can read the counter value from the image (spec §5.5 step 3+5).
- **Root cause of "no timer on screen": the immediate sampling tick.** Starting
  the sampling QTimer with interval 0 ran `_sample` (which touches the shared
  FrameBuffer) at the same instant the full-screen counter window was being
  mapped, and while the reader thread is live this prevented the counter from
  ever appearing. Sampling is now started only after the 1.5 s aiming delay.
  Verified in the full deployment (reader + transport monitor + trigger grab +
  grab-sync + MainWindow): counter visible + fullscreen.
- **Captured frames didn't show the counter.** `_sample` walked the ring buffer
  from the OLDEST, so it collected 20 stale frames from before the counter
  appeared. Now it records the capture-start instant and walks NEWEST-first,
  stopping as soon as frames fall before it — so the 20 images show the counter.
  Verified with a synthetic buffer spanning the capture start (all sampled
  t_recv >= capture start, newest-first).

## §5b Calibration run on device — 2026-08-22T22:41Z

`calibration.json` written from the physical session:

- **median L = 94.3 ms, IQR = 7.0 ms** (stable)
- 1440×1080, 30 fps, mean 135082 bytes; `viewing_mode: water`
- With default `reaction_offset_ms = 0`, `Δ = 0 − 94.3 = −94.3 ms` at race start.

Blur/overexposure of the fast (low) digits was reduced by: updating the counter at
the panel rate (~16 ms) instead of 5 ms, spacing the digits, and using off-white
instead of pure white. (The camera shutter speed remains the spec §10.1 item.)

**§8 race-start validation — implemented 2026-08-22.** `_load_latency` now
refuses to start in water mode unless `calibration.json` exists and its
resolution, fps and mean frame size (within >30%) match the live stream; screen
mode never needs calibration (§5.4). `lens` is skipped while unset. Added 5
regression tests (now 42 total). Verified live-format cases (no-cal refuses,
matching starts, mismatched res refuses, mean-bytes >30% refuses).

## §6 End-to-end capture + archive (T6/T8) — resolved 2026-08-22

Live against the real USB tunnel: start race → two crossings → verified.

- 2 captures, `image_flag=None` (well-centred primaries at +11/+12 ms), 30
  window frames each, all valid JPEGs; DB `PRAGMA integrity_check` OK.
- Continuous archive wrote **136 frames + 136 index.jsonl lines** to
  `races/<race>/archive/` (F7). This was broken before the fix below.

Fixes made:
- **Archive wiring bug (F7).** `build_core` created its own `ArchiveWriter`
  (never `.start()`ed) that the reader fed, while `start_race` created a separate
  one nothing fed → nothing ever written. Fixed by routing `_ingest` to
  `controller.archive_writer` (per-race, `None` between races).
- **`image_flag` computed too early.** It was set at insertion time, before the
  deferred selection, so latency made every target appear newer than the newest
  frame → every capture flagged `approximate` even though selection later found a
  well-centred primary. `_select_images` now recomputes the flag from what was
  actually selected (§6.5). Also removed the dead `prior_flag` reference that
  caused a `NameError` in the thread.

## §4 Trigger / clock domain (T4) — resolved 2026-08-22

Trigger device configured as the internal keyboard `/dev/input/event3`
(`[trigger] device_path`). Live verification on the physical device:

- Device opens via `input` group membership.
- `EVIOCSCLOCKID` ioctl sets CLOCK_MONOTONIC (spec §6.4; `set_clock_id()`
  does not exist in python-evdev).
- Space press (code 57) timestamped `82906.266292` vs `time.monotonic()`
  `82906.266396` → **diff 0.0001 s**, well inside the §6.4 hard 1 s assertion.

Code changes:
- `trigger.py`: `on_trigger(t_press, code, debounce_suspect)` now propagates the
  suspect flag (§6.4 — record and flag, never drop); added `set_grab()`/grab on
  startup for the active-keyboard case.
- `main.py`: 3-arg callbacks; a QTimer syncs grab/ungrab with `controller.running`
  (keyboard grabbed only during a race so bow numbers stay typable between races).
- `config.toml` + defaults: `crossing=KEY_SPACE(57)`, `start=KEY_ENTER(28)`,
  `grab_device=true`.

## §3 GUI smoke test (T7) — resolved 2026-08-22

`python -m regatta_timer` on the live X session, phone streaming over USB:

- App launches its own `iproxy` (spec §6.1 wiring added to `main.py`) and the
  window appears (1280x720, "Regatta Finish-Line Timer").
- Live preview renders real frames (verified via Qt introspection: `_pm` set,
  buffer span ~1 s of frames).
- Structured JSONL logging confirmed in `~/regatta-data/logs/regatta-app.jsonl`.
- Trigger falls back to Qt (`qt_fallback`) — expected, since no trigger device is
  configured yet (`[trigger] device_path = ""`).

Fixes made during this test:
- `regatta_timer/__main__.py` added (INSTALL §7 invokes `python -m regatta_timer`).
- `ui/preview_widget.py`: `QImageReader` needs a `QBuffer`, not `io.BytesIO`; and
  `drawImage` (not `drawPixmap`) for a `QImage`.
- `log.py`: `QueueListener` was given a `QueueHandler` instead of the underlying
  `queue.Queue` (spec §6.9) — now emits correct JSONL.
- `main.py`: `UsbTransport` wired in; app checks device, starts the tunnel, and a
  monitor thread restarts `iproxy` (unlimited while racing per §6.5).
- Config default + `config.toml` stream URL corrected to verified `/video`.

## §2 MJPEGReader live ingest (T2) — resolved 2026-08-22

`MJPEGReader` against the real USB tunnel (8 s run):

- 228 frames received; **measured 29.95 fps** (reader.fps = 29.81)
- All frames valid JPEG (start `FFD8`, end `FFD9`); `seq` monotonic
- mean frame bytes 112 KB; no drift, no accumulator growth
- Backoff/reconnect path unchanged from the Appendix C reference

Note: mean frame size depends on scene entropy (§5.5 constraint 4) — 112 KB here
was a live scene; calibration must record its own `mean_frame_bytes`.

## Bench tests (T3, T6, T9, MJPEG parser) — venv, 2026-08-22T14:40Z

Re-run inside the venv with `~/regatta/venv/bin/python -m pytest tests/`:
**37 passed in 3.59s.** (Previously run on system python 3.14.)

| Suite | Result |
|-------|--------|
| T3  FrameBuffer (11 cases incl. concurrency ×5) | PASS |
| T6  Controller + storage (10 cases incl. N4 edge table) | PASS |
| T9  Export | PASS |
| T?  MJPEG parser (Appendix C F8 cases) | PASS |

## T10 baseline (proves N2; see TESTING.md §5 on why it cannot prove N1)

Filled in on the first passing acceptance run; later runs are compared against it.

| # | recorded elapsed | counter in image | discrepancy |
|---|------------------|------------------|-------------|
