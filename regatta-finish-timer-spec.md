# Regatta Finish-Line Timing System — Build Specification

**Document version:** 1.2
**Status:** Design complete, not yet implemented
**Audience:** An AI coding assistant or developer implementing this system from scratch.

> **v1.1 changelog.** Revised against the hardware/software audit in
> `system-environment.md` (2026-08-21). The architecture, timing model, transport
> choice and MJPEG decision are **unchanged**. Changes are confined to: the target
> Python version (§2.3, §6), calibration procedure constraints imposed by a single
> 1080p display (§5.5), preview decode strategy for a dual-core CPU (§7.2), the
> dependency and install story (§9.1, now delegated to `INSTALL.md`), race-day
> laptop hygiene (§9.4), and four new failure modes (§11). Two companion
> documents were added: **`INSTALL.md`** (the authoritative environment setup,
> replacing the §9.1 command block) and **`TESTING.md`** (per-stage test
> procedures with quantified pass criteria, plus the mandatory file-based
> failure-reporting protocol referenced by §12.2). A logging requirement was
> added as §6.9 — without it the failure reports have nothing to draw on.

> **v1.2 changelog.** Revised after an independent adversarial review. This
> round found defects in the *original* design, not just environment mismatches,
> and several were silent — they produce a system that passes every test and is
> wrong on the water. The six that would have cost a regatta:
>
> 1. **§6.4 — `set_clock_id()` does not exist.** python-evdev has never
>    implemented it (verified against 1.9.3: the string `clock` appears nowhere
>    in the distribution). The v1.0 "mandatory" call raises `AttributeError`,
>    and the startup check meant to catch clock-domain mixing was the same
>    broken call. Replaced with a raw `EVIOCSCLOCKID` ioctl.
> 2. **§5.4 — the `Δ` formula had the sign of `L` inverted**, selecting a frame
>    ≈2·L (300–600 ms) too early: 3–6× the N2 budget, on every capture.
> 3. **§5.3 — `t0` came through Qt plus a confirmation dialog** while `t_press`
>    came from evdev, destroying the reaction-cancellation argument. Both are
>    now evdev. §5.3.1 adds the start-signal acquisition case the spec never
>    addressed (a radio-relayed start breaks N1 outright).
> 4. **§6.4 — the 150 ms debounce discarded legitimate times**, contradicting
>    §6.5's own "never discard a time" rule in exactly the close-finish case the
>    photos exist for. Now 20 ms, and suspect presses are recorded and flagged.
> 5. **§6.4 — the global evdev trigger defaulted to `KEY_SPACE`**, so typing a
>    bow number fired phantom crossings. Now `KEY_F13` on a dedicated device.
> 6. **§6.5 — `window(before=15, after=15)` could never return 31 frames**,
>    because at press time only `Δ × fps` frames after `target` exist. Window
>    extraction is deferred and expressed in milliseconds.
>
> Also: §5.6's error budget rewritten (it summed two independent error chains
> and counted operator reaction once when it enters twice — the honest N1 figure
> reaches ±113 ms); §6.5 SQLite commit moved off the trigger path; §6.5 adds
> `boot_id` and `resume_race()` so a mid-heat restart is survivable; §6.6 adds
> continuous free-space degradation; §6.7 fixes the schema (`UNIQUE(race_id,
> sequence)`, sequence allocation, `PRAGMA foreign_keys`, one-primary-frame
> constraint); §6.7 settles `<data_root>` as the single source of truth;
> Appendix C replaced with a tested parser (v1.0's EOI slow path silently
> truncated frames to their EXIF thumbnail); §9.3 and §11 add the physical
> race-day failure modes (the USB port cannot power the phone, thermal
> throttling, an unreadable screen in daylight, no paper backup).

> **How to use this document.** This is a complete build specification. Every design
> decision below has a stated reason. Do not substitute an "obvious" alternative
> (e.g. OpenCV `VideoCapture`, RTSP instead of MJPEG, `time.time()` instead of
> `time.monotonic()`) without reading the reason first — several obvious choices
> break the timing accuracy requirement in ways that are invisible until race day.

---

## 1. Purpose

A single-operator system for timing and photographing boats as they cross the
finish line of a regatta.

The operator watches the finish line and presses one key each time a boat
crosses. For each press the system records:

- an **elapsed time** since the race start, and
- a **photograph** of the boat at that moment,

and displays both together in a reviewable list on screen.

The photograph exists so that a race jury can verify the recorded placing and
read the boat's bow number, especially for close finishes.

---

## 2. Requirements

### 2.1 Functional requirements

| ID | Requirement |
|----|-------------|
| F1 | One operator action (single key press) per boat crossing records both a timestamp and an image. |
| F2 | Elapsed time is measured from a race-start event to each crossing event. |
| F3 | Every captured image is visible in the application alongside its associated elapsed time. |
| F4 | The operator can annotate each capture with a bow/lane number after the fact. |
| F5 | The operator can delete or correct an erroneous capture without disturbing the others. |
| F6 | Results (times + image references) are exportable to CSV. |
| F7 | The full race is archived continuously so that a missed or mistimed press can be recovered after the fact. |

### 2.2 Non-functional requirements

| ID | Requirement |
|----|-------------|
| N1 | Total timing error budget: **±100 ms** (stated tolerance). Target design margin: ±50 ms. |
| N2 | The image associated with a recorded time must depict the scene at that recorded time, within the same ±100 ms. |
| N3 | The system must run offline. No internet, no cloud, no LAN infrastructure. |
| N4 | The system must survive a mid-race application crash without losing already-recorded results. |
| N5 | Startup from cold (laptop open, phone in hand) to ready-to-time: under 5 minutes. |

### 2.3 Operating environment

- **Computer:** Lenovo ThinkPad T460s (`borodin`). Intel Core i7-6600U — **two
  physical cores**, 4 threads — 7.5 GiB RAM, 192 GB SSD, Intel HD 520, single
  internal 1920×1080 eDP panel with no external output attached.
- **OS:** Ubuntu development branch, XFCE 4.20, **X11** (`XDG_SESSION_TYPE=x11`),
  kernel 7.0. X11 confirmed, so the §11.3 Wayland caveat does not apply.
- **Python:** system Python is **3.14**, not 3.12. See §6 and `INSTALL.md`.
- **Camera:** iPhone, connected to the laptop by **USB cable**. No Wi-Fi.
- **Subject distance:** approximately 50 m from camera to finish line.
- **Setting:** outdoors, daylight, potentially bright sun or overcast.

Two hardware facts drive design decisions later in this document and should be
kept in mind while reading: there are only **two physical cores** (§7.2), and
there is only **one display** (§5.5). Full audit: `system-environment.md`.

---

## 3. Critical background: why the architecture looks like this

Read this section before implementing anything. It explains constraints that are
not obvious and that a naive implementation will violate.

### 3.1 The USB transport is host→device, TCP-only

An iPhone connected by USB is reached through `usbmuxd` (the USB multiplexing
daemon) and its companion tool `iproxy`. `iproxy` binds a local TCP port on the
laptop and forwards it to a TCP port on the phone.

Two hard consequences:

1. **Connections flow laptop → phone only.** The phone cannot initiate a
   connection to the laptop over this channel. Therefore the iOS app must run a
   **server** (HTTP or RTSP) that the laptop connects to. Apps that *push* a
   stream outward (Larix Broadcaster, Camo, NDI apps, anything RTMP/SRT-based)
   **cannot be used**, because they would need to reach the laptop.
2. **Only TCP traverses the tunnel.** UDP does not. If RTSP is used at all it
   must be forced into TCP-interleaved mode.

There is an alternative transport — iOS USB Personal Hotspot, which creates a
real network interface (`ipheth` kernel driver, phone at `172.20.10.1`). It
supports UDP and bidirectional traffic, but it requires a cellular plan that
permits tethering and is historically unreliable across iOS versions. **Do not
use it as the primary path.** It may be documented as a fallback.

### 3.2 Use MJPEG, not H.264/RTSP

The iOS app of choice exposes both an HTTP/MJPEG stream and an RTSP (H.264)
stream. **Use MJPEG.** Reasons, all of which bear directly on requirement N2:

| Property | MJPEG | H.264 over RTSP |
|---|---|---|
| Frame independence | Every frame is a complete JPEG | Frames depend on preceding keyframes |
| Startup | Instant | Must wait for a keyframe |
| Buffering | None inherent | Jitter buffer, typically ~200 ms extra latency |
| Latency stability | Near-constant | Variable — spikes during motion |
| Decode to save | Not required; write bytes to disk | Required |
| Timestamp precision | Arrival of each JPEG is unambiguous | Obscured by decoder reordering and buffering |

**Variable latency is the enemy.** A constant 200 ms delay can be calibrated
away. A delay that varies by ±150 ms depending on scene motion cannot, and a
finish line is precisely where the scene motion spikes.

### 3.3 Do not use OpenCV `VideoCapture` for ingest

`cv2.VideoCapture(url)` will connect and produce frames, but:

- it maintains an internal buffer of unknown depth, so the frame you receive is
  of unknown age;
- it decodes every frame to a NumPy array whether or not you need it, wasting CPU;
- it provides no reliable wall-clock or monotonic arrival timestamp per frame.

Frame arrival time is the single most important datum in this system. Parse the
MJPEG multipart stream directly (§6.2). OpenCV may still be used for *display*
decoding if convenient, but not for ingest.

### 3.4 Never capture "on trigger" — buffer continuously and select afterwards

The naive design ("operator presses key → grab a frame now") fails N2, because
by the time the press is handled the freshest available frame already depicts
the past by one glass-to-glass latency.

The correct design: frames flow continuously into a ring buffer, each tagged
with its arrival time. The key press records a timestamp. The image is then
*selected* from the ring buffer by timestamp proximity. This decouples the
image pipeline's latency from the timing pipeline's accuracy.

---

## 4. System architecture

```
┌─────────────────────────────────────────────────────────────┐
│  iPhone                                                     │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ IP Camera Lite (full version)                         │  │
│  │  • rear/telephoto camera, locked AF/AE/AWB            │  │
│  │  • HTTP server → MJPEG stream on device port 8081     │  │
│  └───────────────────────────────────────────────────────┘  │
└───────────────────────────┬─────────────────────────────────┘
                            │ USB cable (Lightning or USB-C)
                            │ usbmuxd pseudo-TCP
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Xubuntu laptop                                             │
│                                                             │
│  iproxy 8081:8081  ──► http://127.0.0.1:8081/...            │
│         │                                                   │
│         ▼                                                   │
│  ┌──────────────────┐      ┌───────────────────────────┐    │
│  │ MJPEGReader      │      │ TriggerListener           │    │
│  │ (background      │      │ (evdev, background        │    │
│  │  thread)         │      │  thread)                  │    │
│  │  • parse         │      │  • raw key events         │    │
│  │    multipart     │      │  • CLOCK_MONOTONIC        │    │
│  │  • stamp         │      │    kernel timestamps      │    │
│  │    t_recv        │      └─────────────┬─────────────┘    │
│  └────────┬─────────┘                    │                  │
│           │                              │ t_press          │
│           ▼                              ▼                  │
│  ┌──────────────────┐      ┌───────────────────────────┐    │
│  │ FrameBuffer      │◄─────│ CaptureController         │    │
│  │ (deque, ~10 s,   │ pick │  • elapsed = t_press - t0 │    │
│  │  timestamped)    │─────►│  • select frames near     │    │
│  └────────┬─────────┘      │    (t_press - Δ)          │    │
│           │                └─────────────┬─────────────┘    │
│           │ tee                          │                  │
│           ▼                              ▼                  │
│  ┌──────────────────┐      ┌───────────────────────────┐    │
│  │ ArchiveWriter    │      │ Storage (SQLite + JPEGs)  │    │
│  │ (all frames to   │      └─────────────┬─────────────┘    │
│  │  disk, async)    │                    │                  │
│  └──────────────────┘                    ▼                  │
│                            ┌───────────────────────────┐    │
│                            │ Qt GUI (PySide6)          │    │
│                            │  • live preview           │    │
│                            │  • capture list           │    │
│                            └───────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. Timing model

This is the heart of the system. Implement it exactly.

### 5.1 Clock discipline

- **All event timing uses `time.monotonic()`** (`CLOCK_MONOTONIC`), which is
  immune to NTP steps, DST changes, and manual clock adjustment.
- **`time.time()` (wall clock) is recorded alongside, for the record only.**
  It must never be used to compute a duration. An NTP correction mid-race would
  silently corrupt every subsequent result.
- One monotonic reference `t0` is captured when the race start is triggered.
  Every crossing's elapsed time is `t_press - t0`.

### 5.2 Definitions

| Symbol | Meaning |
|---|---|
| `T_event` | Real-world instant the boat's bow crosses the line |
| `L` | Glass-to-glass latency: sensor exposure → encode → USB → parse → `t_recv` |
| `t_recv` | Monotonic time at which a frame's final byte arrives at the laptop |
| `t_press` | Monotonic time of the operator's key press (from kernel evdev) |
| `R` | Operator reaction time |
| `Δ` | Calibration constant used to select the image (see §5.4) |
| `t0` | Monotonic time of the race start event |

### 5.3 The recorded time is the key press

`elapsed = t_press - t0`

Rationale: the operator's reaction time `R` is a systematic offset that appears
in both `t0` and `t_press` and therefore largely cancels in the difference. This
is how manual regatta timing has always worked. Do not attempt to "correct" for
reaction time.

**The cancellation only holds if both timestamps are acquired the same way.**
It is the *bias* that cancels, not the *variance* — see §5.6. And it cancels
nothing at all if `t0` comes through a slower path than `t_press`:

- **`t0` must be captured from the same evdev device, at interrupt time, as the
  crossing trigger.** Use a second keycode on the same device. Routing Start
  Race through the Qt event loop — as spec v1.0's §7.4 did — adds tens of
  milliseconds of queue jitter plus, if a confirmation dialog sits in the path,
  an unbounded human delay. That error lands on *every* elapsed time in the
  race as a common bias. It is invisible in the results (all placings stay
  correct) and corrupts every absolute time a jury might compare against
  another timing system or a course record.
- Confirmation happens **before** arming, never between the operator's decision
  and the timestamp. The flow is: operator confirms → the system arms → the
  next trigger press *is* `t0`.

#### 5.3.1 How the start signal reaches the operator

No version of this document previously said, and it determines whether N1 is
achievable at all:

| Case | `R₁` (start reaction) | Consequence |
|---|---|---|
| Operator sees or hears the start directly | 150–300 ms, comparable to `R₂` | §5.3's cancellation applies; N1 achievable |
| Start relayed by radio | 1–3 s, dominated by transmission and the relayer's own reaction | **N1 does not hold.** The error is neither small nor cancelling |
| Start taken from a start-tower clock or an official start time | ~0, but in a different clock domain | Requires converting a wall-clock start into the monotonic domain, with all the hazards of §5.1 |

At a 2000 m course the start is out of sight, so the radio case is the *normal*
one. The application must therefore:

1. Record which case applies as a per-race field (`start_mode`).
2. Expose a configurable, measured `radio_delay_ms` applied to `t0` in the
   radio case, and state in the UI and the CSV export that times carry that
   correction.
3. **State plainly on the results export that N1's ±100 ms does not hold in the
   radio case.** Elapsed times remain correct *relative to each other* — which
   is what decides placings — but their absolute accuracy is bounded by the
   relay, not by this system.

### 5.4 The image is selected by proximity to `t_press - Δ`

> **Corrected in v1.2.** Spec v1.0 and v1.1 gave `Δ = latency_median +
> reaction_offset`. That has the sign of `L` inverted, and with the default
> `reaction_offset = 0` it selects a frame **2·L too early** — 300–600 ms at a
> realistic `L`, three to six times the N2 budget. The derivation below is the
> corrected one. Do not restore the old formula.

Selection works in the `t_recv` domain, so `target` must be the *arrival* time
of the frame we want, not the instant it depicts. For a frame depicting
`T_event`, that arrival time is `t_recv ≈ T_event + L`. Therefore:

```
target  = T_event + L
chosen_frame = argmin over frames of |frame.t_recv - target|
```

Now substitute how `t_press` relates to `T_event`, which differs by viewing mode:

**Watching the water directly** — `t_press ≈ T_event + R`, so `T_event = t_press - R`:

```
target = t_press - R + L        ⟹   Δ = R - L
```

**Watching the screen** — `t_press ≈ T_event + L + R`, so `T_event = t_press - L - R`:

```
target = t_press - R            ⟹   Δ = R
```

In screen-watching mode `L` cancels entirely: the operator is already reacting to
a delayed image, so the latency is baked into the press. This is why the two
modes cannot share one formula, and why viewing mode is an explicit config field
(`[timing] viewing_mode = "water" | "screen"`) rather than an informal note.

Consequences worth internalising:

- **`Δ` is expected to be small, and may legitimately be negative.** In
  water-watching mode `Δ = R - L` with `R` ≈ 150–300 ms and `L` ≈ 150–300 ms, so
  `Δ ≈ 0` is a perfectly ordinary result. The v1.0 rule "refuse to start a race
  with `delta_ms == 0`" was therefore wrong twice over — it rejects a valid
  calibration and it accepts a stale one. Replaced in §11: refuse to start
  unless `calibration.json` exists **and** its recorded capture format and mean
  frame size match the live stream.
- `Δ` must be re-measured whenever resolution, frame rate, lens, **scene
  content** (§5.5 constraint 4) or viewing mode changes.

### 5.5 Calibration procedure (must be implemented as a feature)

1. The application displays a full-screen counter showing `time.monotonic()`
   in milliseconds, in a large, high-contrast, sans-serif font. Update it every
   display frame.
2. The operator points the iPhone at the laptop screen so the counter fills the
   frame and is legible. **This is done at close range (~0.5 m), not from the
   tripod position** — a millisecond counter on a 1080p panel is unreadable at
   50 m. See the constraints note below, which is mandatory reading.
3. The application captures 20 frames from the buffer, recording each frame's
   `t_recv` and saving the JPEG.
4. For each frame, the value of the counter visible *in the image* is `T_shown`.
   Then `L = t_recv - T_shown`.
5. The operator reads each of the 20 values from the images and enters them
   (or, better, the application OCRs them — see §12 optional).
6. The application computes the **median** of the 20 `L` values (median, not
   mean — a single dropped frame produces an outlier that would skew a mean)
   and writes it to `calibration.json` as the latency component, together with
   the resolution, fps, lens and **mean frame size in bytes** in force at the
   time.
7. The reaction component is a separate configurable offset, defaulting to 0.
   The operator may tune it after a practice run by checking whether the saved
   image shows the boat before or after the line.
8. The application also records the **interquartile range** of the 20 `L`
   values. The median tells you the offset to correct; the IQR tells you whether
   the offset is meaningful at all. A stream with ±150 ms of per-frame jitter
   yields a beautifully stable median and an unusable `Δ`.

```
viewing_mode = "water"   →   Δ = reaction_offset - latency_median
viewing_mode = "screen"  →   Δ = reaction_offset
```

See §5.4 for the derivation. Note the **minus** sign in the water case and the
absence of `latency_median` in the screen case.

#### Calibration constraints imposed by this hardware

These four constraints are not optional refinements; ignoring any of the first,
second or fourth silently invalidates the measurement.

1. **Calibrate with the exact lens, resolution and frame rate the race will
   use.** Only the focus distance may differ. `L` is encode-plus-transport
   latency and is essentially independent of subject distance, but it is *not*
   independent of capture format — and §9.2 step 4 instructs the operator to
   switch to the telephoto lens, which on an iPhone may offer different formats
   than the wide lens. Calibrating on the wide lens at 1080p30 and then racing
   on the telephoto at a different format measures `L` for a configuration that
   will not exist during the race. The calibration dialog must display the
   stream's current resolution and measured fps, and the value must be recorded
   in `calibration.json` alongside `L` so a mismatch is detectable later.

2. **The calibration flow must be sequential, not side-by-side.** `eDP-1` is
   the only active output (DP-1, DP-2, HDMI-1, HDMI-2 are all disconnected), so
   the full-screen counter occupies the entire screen and the operator cannot
   see the counter and a value-entry form at the same time. Implement it as:
   full-screen counter → capture 20 frames → leave full-screen → enter the 20
   values one frame at a time. Do not design a two-pane dialog.

3. **There is a hard precision floor of roughly ±10–20 ms**, from the 60 Hz
   panel quantising the filmed counter to ~16.7 ms, plus the iPhone's rolling
   shutter. This is already absorbed by the "calibration residual" line in
   §5.6, but note that §12 step 5's gate ("`L` stable to within ±20 ms") sits
   directly on this floor. A ±20 ms spread across repeated runs is the expected
   result on this hardware, not a defect to be chased.

   Note also that the floor is not symmetric. The compositor and panel add a
   **one-sided positive bias**: the counter's pixels reach the glass one to two
   vsyncs after `time.monotonic()` was sampled, plus panel response, so the
   measured `L` is systematically *too large* by roughly 20–45 ms. Disable
   xfwm4 compositing during calibration to remove one vsync of it, and carry
   the remainder as a known bias in §5.6's N2 table rather than pretending it
   is noise.

4. **The calibration scene must have similar JPEG entropy to the race scene.**
   This is the constraint most likely to be skipped and it is worth as much as
   the other three combined. `L` is dominated by encode time and transfer time,
   and *both scale with compressed frame size*:

   | Scene | Typical 1080p JPEG | Transfer at 5 MB/s |
   |---|---|---|
   | White numerals on black, filmed at 0.5 m | 20–40 KB | ~6 ms |
   | Sunlit water, moving shell, trees, spectators | 150–250 KB (§6.6's own budget) | ~44 ms |

   Calibrating against a black screen therefore *underestimates* `L` by roughly
   40–80 ms on a bandwidth-limited usbmuxd tunnel — a systematic error that no
   test in `TESTING.md` can see, because T5 and T10 both film the same
   low-entropy screen. Render the counter over a high-entropy background (a
   noise field or a photograph) sized so the JPEG lands in the 150–250 KB band,
   record `mean_frame_bytes` in `calibration.json`, and warn at race start if
   the live stream's mean frame size differs from the calibrated one by more
   than 30%.

### 5.6 Error budget

> **Rewritten in v1.2.** The v1.0/v1.1 table summed a single RSS over terms
> belonging to *two independent error chains*, and counted operator reaction
> once when it enters twice. Its headline "≈ ±50–90 ms, within budget" was the
> sentence justifying the whole design's adequacy, and it was too optimistic.

N1 (the recorded *time* is right) and N2 (the *image* depicts that time) are
separate chains with almost no terms in common. `Δ` and frame quantisation never
touch `elapsed`; reaction variance never touches image selection. They must be
budgeted separately.

#### N1 — accuracy of the recorded elapsed time

| Source | Magnitude | Kind |
|---|---|---|
| evdev timestamp jitter | < 1 ms | random |
| `t0` acquisition asymmetry | 0 if `t0` is evdev-sourced per §5.3; tens of ms via Qt; **seconds** via radio relay (§5.3.1) | systematic |
| Operator reaction differential `√2·σ_R` | ±42–113 ms | random |
| **RSS (direct-start, evdev `t0`)** | **±42–113 ms** | |

The reaction term enters **twice** — once in `t0`, once in `t_press` — so the
variances add and the term is `√2·σ_R`, not `σ_R`. With a steady operator
(`σ_R` ≈ 30 ms) N1 is comfortably met at ±42 ms. With a tired or distracted one
(`σ_R` ≈ 80 ms) the figure is **±113 ms, outside the ±100 ms budget**. This is
an honest statement of the design's limit, not a defect to engineer away: it is
the irreducible cost of a human trigger, and it is why §5.6's old conclusion
("don't optimise elsewhere") still holds — but as a statement about priorities,
not about having margin in hand.

Mitigation is procedural, not technical: a rested operator, a rehearsed start,
and the frame window (§7.3) so a jury can correct a visibly bad press after the
fact.

#### N2 — the image depicts the recorded time

| Source | Magnitude | Kind |
|---|---|---|
| Frame quantisation | ±17 ms at 30 fps, ±8 ms at 60 fps | random |
| Per-frame latency jitter (usbmuxd burst delivery) | ±20–60 ms, measured as the IQR in §5.5 step 8 | random |
| Calibration residual (`Δ`) | ±20–40 ms | random |
| Display-chain bias during calibration | **+20–45 ms** | systematic |
| Rolling-shutter row position (counter fills frame; finish line does not) | **0 to +1 sensor readout** | systematic |
| Scene-entropy mismatch (§5.5 constraint 4) | **+40–80 ms if uncalibrated, ~0 if calibrated** | systematic |
| **RSS of random terms** | **≈ ±33–74 ms** | |
| **Sum of systematic terms, uncorrected** | **+60–125 ms** | |

Systematic terms **add linearly** and do not cancel. The conclusion is the one
that matters for build planning: **N2 is met only if the systematic terms are
calibrated out.** Do that and the budget is ±33–74 ms with margin. Skip §5.5
constraint 4 and disable nothing in the compositor, and the images are
consistently ~100 ms stale — every one of them, in the same direction, in a way
that looks like a plausible photo until a jury measures it.

---

## 6. Component specifications

Target language: **Python 3.14** — the system Python on the target machine.
(Spec v1.0 said 3.12; the audit found 3.14.6.) Nothing in this design depends on
a 3.12-only feature, and the two 3.14 behaviour changes that could matter —
`forkserver` as the default multiprocessing start method, and the optional
free-threaded build — are irrelevant here: this application uses `threading`
only, and must be run on the ordinary GIL build. Package layout:

```
regatta_timer/
    __init__.py
    main.py              # entry point, wires everything together
    config.py            # config load/save, dataclass
    transport.py         # iproxy lifecycle management
    mjpeg.py             # MJPEGReader
    framebuffer.py       # FrameBuffer
    trigger.py           # TriggerListener (evdev)
    controller.py        # CaptureController
    storage.py           # SQLite + filesystem
    archive.py           # ArchiveWriter
    calibration.py       # latency calibration routine
    log.py               # structured logging setup (§6.9)
    ui/
        __init__.py
        main_window.py
        preview_widget.py
        capture_list.py
        calibration_dialog.py
    export.py            # CSV export
```

### 6.1 `transport.py` — iproxy lifecycle

```python
class UsbTransport:
    def __init__(self, local_port: int, device_port: int, udid: str | None = None): ...
    def start(self) -> None:
        """Launch `iproxy <local>:<device>` as a subprocess. Raise TransportError
        if the binary is missing or no device is paired."""
    def stop(self) -> None: ...
    def is_alive(self) -> bool: ...
    def wait_until_ready(self, timeout: float = 10.0) -> None:
        """Poll a TCP connect to 127.0.0.1:<local_port> until it succeeds."""
```

Implementation notes:
- Shell out to `iproxy` (from `libimobiledevice-utils`). Do not reimplement usbmuxd.
- **Require libimobiledevice ≥ 1.4.0 and usbmuxd ≥ 1.1.1.** Pairing with current
  iOS releases does not work on libimobiledevice 1.3.0, which is what a stable
  Ubuntu LTS still ships. The target machine has 1.4.0 / 1.1.1 — this is one
  concrete advantage of its development-branch release, and it is a reason
  *not* to "stabilise" the machine by moving it to an LTS. Check the version at
  startup and warn if it is older.
- Before starting, verify a device is present and trusted by running
  `idevice_id -l` and checking for non-empty output. If empty, surface the
  message: *"No iPhone detected. Plug in the cable, unlock the phone, and tap
  Trust."*
- Monitor the subprocess. If it dies mid-race, attempt automatic restart up to
  3 times, and raise a prominent but **non-modal** UI banner. Never show a modal
  dialog during a race — it would block the trigger.

### 6.2 `mjpeg.py` — MJPEG stream reader

The stream is an HTTP response with
`Content-Type: multipart/x-mixed-replace; boundary=<token>`. Each part has its
own headers (typically `Content-Type: image/jpeg` and `Content-Length`) followed
by raw JPEG bytes.

```python
@dataclass(frozen=True, slots=True)
class Frame:
    t_recv: float        # time.monotonic() at completion of this frame
    t_wall: float        # time.time() at the same instant, for the record
    seq: int             # monotonically increasing frame counter
    jpeg: bytes          # complete JPEG, SOI..EOI

class MJPEGReader(threading.Thread):
    def __init__(self, url: str, on_frame: Callable[[Frame], None],
                 auth: tuple[str, str] | None = None): ...
    def run(self) -> None: ...
    def stop(self) -> None: ...
    @property
    def fps(self) -> float:
        """Rolling average over the last 30 frames, for the status bar."""
```

Parsing algorithm:

1. Open the HTTP connection with **streaming enabled** and **no buffering**
   (`requests.get(url, stream=True)` then iterate `raw.read(N)`, or use
   `http.client` directly).
   *Do not bother setting `TCP_NODELAY`.* v1.0 called for it; it disables Nagle
   for data the socket **sends**, and this socket sends one request header and
   then nothing. All the latency-sensitive traffic is inbound, the peer's socket
   is unreachable, and usbmuxd terminates the TCP connection locally anyway.
   urllib3 already sets it by default. Per-frame arrival jitter on this path is
   a property of the tunnel's burst delivery — measure it (`TESTING.md` T2),
   do not try to tune it with socket options.
2. Read into a `bytearray` accumulator in chunks of 8192 bytes. **Cap the
   accumulator at 4 MB**; on overflow, log and force a reconnect. Without this
   cap, any condition that prevents the boundary from ever matching (see below)
   grows the buffer at the stream bitrate and exhausts 7.5 GiB of RAM in about
   20 minutes, taking the application down mid-regatta.
3. Derive the boundary token from the `Content-Type` header, then **strip any
   leading `--` before prepending your own**. Several MJPEG servers declare
   `boundary=--token`; blindly prepending yields `----token`, which never
   matches and triggers the unbounded-growth case above.
4. Locate the boundary marker; parse the part headers, accepting **both
   `\r\n\r\n` and `\n\n`** as the header terminator (embedded servers emit
   bare-LF headers, and a `\r\n\r\n`-only search is another never-matches
   case). If `Content-Length` is present, read exactly that many bytes (fast
   path). If absent, fall back to the marker walk in step 5 — **not** a raw
   scan for `\xFF\xD9`.
5. **The EOI slow path must walk JPEG marker segments, not `find(b"\xff\xd9")`.**
   Entropy-coded scan data is byte-stuffed so a literal `FF D9` cannot occur
   there — but APP segments are not stuffed, and an EXIF thumbnail is a
   complete nested JPEG carrying *its own* EOI. A raw `find` truncates every
   such frame to the thumbnail, and the truncated result still begins `FFD8`
   and ends `FFD9`, so it looks like a valid JPEG to any cheap check. Skip
   `FFE0`–`FFEF`, `FFDB`, `FFC4` and `FFC0`–`FFCF` by their length fields, and
   only scan for EOI once inside the `FFDA` scan segment.
   Better still: **treat a missing `Content-Length` as a configuration error at
   connect time** and refuse to proceed. IP Camera Lite does send it; a server
   that does not is one you should discover during setup, not during a heat.
4. **Take `time.monotonic()` immediately after the final byte of the JPEG has
   been read into the accumulator**, before any copying, validation, or
   callback dispatch. This is `t_recv`. Precision here directly sets N2.
5. Emit a `Frame` via `on_frame`. The callback must be non-blocking; it appends
   to the ring buffer and hands off to the archive queue.
6. **Take `time.monotonic()` immediately after the final byte** (see step 7's
   placement note in Appendix C).
7. Emit a `Frame` via `on_frame`. The callback must be non-blocking.
8. On connection loss: retry with exponential backoff (0.5 s, 1 s, 2 s, capped
   at 2 s), indefinitely, logging each attempt. **Never exit the thread on
   error** — and that means the *entire* body must be wrapped, not just the
   read loop. v1.0's Appendix C could raise out of the thread in three places
   before reaching any retry logic: `r.headers["Content-Type"]` (KeyError),
   `ctype.split("boundary=")[1]` (IndexError), and
   `int(line.split(":", 1)[1])` on a header like `Content-Length: 4096 bytes`
   (ValueError). Any of those kills ingest permanently, because the reconnect
   logic lives inside the function that just died.

`seq` is **instance state on `MJPEGReader`, not a local variable**, and is never
reset across reconnects. v1.0's Appendix C initialised it inside the parse
function, so every reconnect restarted numbering at 1. Since §6.6 writes `seq`
into `index.jsonl` — the index a recovery tool uses to find a frame by
timestamp — a mid-race reconnect produced a duplicated, non-monotonic index.
The dress rehearsal in `TESTING.md` §6 *mandates* a mid-run cable pull, so this
fires on purpose. On reconnect, log a `stream_resync` event carrying the seq at
the gap, so a recovery tool can tell "frames lost in the tunnel" from "frames
never sent".

Do not validate JPEG integrity on the hot path. Corrupt frames are rare and
harmless (a broken thumbnail), and validation costs latency. Note that this is
an argument against *checking* frames, not against *parsing them correctly* —
step 5's marker walk is parsing, and it is required.

### 6.3 `framebuffer.py` — timestamped ring buffer

```python
class FrameBuffer:
    def __init__(self, seconds: float = 10.0, assumed_fps: int = 30): ...
    def append(self, frame: Frame) -> None:
        """Thread-safe. O(1)."""
    def nearest(self, target_t: float) -> Frame | None:
        """Frame whose t_recv is closest to target_t."""
    def window(self, target_t: float,
               before_s: float, after_s: float) -> list[Frame]:
        """All frames whose t_recv lies in
        [target_t - before_s, target_t + after_s], in time order.
        Bounded by a time span, not a frame count, so the covered interval does
        not change when fps does (§6.5). May return fewer frames than the span
        implies, or none."""
    def span(self) -> tuple[float, float] | None:
        """(oldest t_recv, newest t_recv), or None if empty."""
```

Implementation:
- Back with `collections.deque(maxlen=int(seconds * assumed_fps * 1.5))`.
  Note the `int()` — `maxlen` rejects a float, and the v1.0 text omitted it.
- Guard with a `threading.Lock`. Contention is negligible (one writer, one
  reader, sub-microsecond critical sections).
- `nearest` may binary-search since `t_recv` is monotonically increasing;
  convert the deque to a list under the lock, or maintain a parallel list of
  timestamps. Simplicity is acceptable here — a linear scan of 450 entries is
  microseconds.

Memory: the deque holds `maxlen` frames, not `seconds * fps` frames — the
`×1.5` applies to both. At the defaults that is `int(10.0 * 30 * 1.5)` = **450
frames = 15 s**, and at 200 KB/frame **≈ 88 MB** (66 MB at 150 KB, 110 MB at
250 KB). v1.0 quoted 60 MB by computing from 300 frames while allocating 450.
Compute the warning threshold from `maxlen * frame_size` too, and warn above
500 MB.

**`assumed_fps` must track the stream, not just the config file.** `maxlen`
derives from `assumed_fps`, so switching the phone to 720p60 per §10.2 without
editing the config silently halves the buffer's time span to 7.5 s — which makes
§6.5's "target older than buffer span" edge case start firing during a heat.
`MJPEGReader` publishes measured fps; `FrameBuffer` must resize (or at minimum
warn loudly) when the measurement diverges from `assumed_fps` by more than 20%.

### 6.4 `trigger.py` — key press capture via evdev

**Do not use Qt key events for the trigger.** GUI event queues add tens of
milliseconds of jitter under load, and the load spikes exactly when frames are
arriving fastest.

```python
class TriggerListener(threading.Thread):
    def __init__(self, device_path: str,
                 on_trigger: Callable[[float, int], None],
                 keycodes: set[int]): ...
```

Implementation:
- Use the `evdev` package. The `evdev` distribution is **sdist-only**, so
  `pip install evdev` compiles a C extension and generates its `ecodes` module
  by parsing `/usr/include/linux/input.h` and `input-event-codes.h`. That
  requires `python3-dev` and `build-essential`. See `INSTALL.md`. Do not rely on
  the apt package `python3-evdev`: it is invisible from a venv created without
  `--system-site-packages`. A prebuilt fallback exists — the upstream project's
  own `evdev-binary` distribution ships `cp314` wheels — but it is compiled
  against fixed kernel headers and may not expose all codes on kernel 7.0, so
  prefer building from source and keep it as a documented fallback only.

- **Set the clock domain with a raw ioctl. `set_clock_id()` does not exist.**

  ```python
  import fcntl, struct, time
  EVIOCSCLOCKID = 0x400445A0          # _IOW('E', 0xA0, int)
  fcntl.ioctl(dev.fd, EVIOCSCLOCKID, struct.pack('i', time.CLOCK_MONOTONIC))
  ```

  Spec v1.0 and v1.1 instructed `device.set_clock_id(time.CLOCK_MONOTONIC)` and
  called it mandatory. **python-evdev has never implemented that method.** In
  1.9.3 the string `clock` does not appear anywhere in the distribution, and
  `EVIOCSCLOCKID` is absent from `input.c`'s ioctl table — the call raises
  `AttributeError`.

  This mattered more than an ordinary typo, because it is the exact silent
  failure §11 warns about and the *detector* was broken too: without the ioctl
  the kernel stamps events from `CLOCK_REALTIME` (≈1.8×10⁹) while frames carry
  `time.monotonic()` (seconds since boot). `elapsed = t_press - t0` survives
  (both ends come from evdev), but `target = t_press - Δ` is compared against
  `Frame.t_recv` in the other domain, so `nearest()` returns the newest frame
  every time and **every capture is silently flagged `approximate` with a photo
  ~56 years off**.

  Verify the request number against `linux/input.h` on the target kernel — the
  `_IOW` layout is stable, but check rather than trust. Then keep the startup
  assertion (`|event.timestamp() - time.monotonic()| < 1 s`) as a **hard
  failure that refuses to start a race**, not a warning.
- Filter to `EV_KEY` events with `value == 1` (key down; ignore auto-repeat
  `value == 2` and release `value == 0`).
- Pass the event's own timestamp to `on_trigger`, **not** a fresh
  `time.monotonic()` call. The kernel timestamp is taken at interrupt time.
- **Debounce: default 20 ms, and never discard the time.** v1.0 specified
  150 ms and *dropped* the second press, keeping only a log line. That
  contradicts §6.5's central rule — "the time is the primary datum; never
  discard a time" — and it does so in the highest-value case in the sport: at
  5 m/s, two boats 100 ms apart are 0.5 m apart, exactly the close finish the
  photo evidence exists to adjudicate. v1.0's own text conceded the case was
  realistic and then shipped a default that loses it.

  150 ms is also 15× longer than any real bounce. Contact bounce is sub-10 ms,
  the keyboard controller and the kernel input layer already suppress it, and
  auto-repeat is separately filtered by `value == 2`.

  The correct semantics: a trigger inside the debounce window is **written to
  the database as a normal capture row carrying a `debounce_suspect` flag**, and
  the UI marks it for review. A row the operator can glance at and delete is
  recoverable. A log line, during a heat, is not.

Device selection:
- Enumerate `/dev/input/event*` and list devices with `EV_KEY` capability in a
  settings dialog, so the operator can pick the footswitch or keypad.
- Reading `/dev/input/event*` requires membership in the `input` group. The
  setup script must add the user and instruct them to log out and back in.
  If permission is denied, fall back to Qt key events with a clear UI warning
  that timing precision is degraded.

#### The trigger is global — this collides with bow-number entry

evdev reads below the display server and therefore **has no concept of keyboard
focus**. Combined with v1.0's default `keycodes = [57]` (`KEY_SPACE`), that
creates a race-losing interaction with §7.3's inline bow-number field and
§7.4's `Tab` binding: bow numbers are typed *during* the heat, and every space
typed into that field — or into any other window, or the desktop — fires a
phantom crossing with a real timestamp, inserted into the middle of the
sequence. The operator then has to find and soft-delete it under time pressure
while boats are still finishing.

Required mitigations, in order of preference:

1. **A dedicated trigger device.** A USB footswitch or numeric keypad on its own
   event node. Both enumerate as ordinary keyboards. A footswitch frees the
   operator's hands and eyes and is the recommended hardware. For race use this
   is a **requirement, not a recommendation** — v1.0's phrasing was too soft.
2. **Refuse a trigger device that is also the active keyboard.** If the operator
   selects the internal keyboard's event node, warn prominently at startup and
   refuse to start a race without an explicit override.
3. **Default the keycode to something un-typeable.** `KEY_F13`–`KEY_F24` (or the
   footswitch's own code), never `KEY_SPACE`. §8's default is changed
   accordingly.
4. **If the internal keyboard must be used**, `grab()` the device for the
   duration of the race (`evdev.InputDevice.grab()` exists and does exactly
   this) so keystrokes cannot reach Qt at all — and then bow-number entry must
   move behind an explicit mode switch that ungrabs.

### 6.5 `controller.py` — capture orchestration

```python
class CaptureController:
    def start_race(self, t_press: float) -> None:
        """Set t0 from an evdev-sourced timestamp (§5.3). Reset the capture
        list. Begin archiving."""
    def resume_race(self, race_id: int) -> None:
        """Reattach to an existing race after an application restart. See
        'Resuming after a restart' below."""
    def record_crossing(self, t_press: float) -> Capture:
        """Compute elapsed, persist the time, schedule image selection."""
    def undo_last(self) -> None: ...
```

`record_crossing` sequence — note that **nothing on this path touches the
disk**:

1. `elapsed = t_press - self.t0`
2. `target = t_press - config.delta`
3. Hand `(t_press, elapsed, target)` to the persistence queue. Return.
4. Emit a Qt signal so the UI appends a row immediately.

Everything else happens off the trigger thread:

5. The single-writer persistence thread INSERTs the capture row.
6. **Image selection is deferred**, scheduled for
   `t_press + window_after_ms + margin`. See below.
7. The writer thread queues the selected JPEGs to the archive writer.

**Why the SQLite write moved off the trigger path (v1.2).** v1.0 said "Persist
synchronously to SQLite (a single INSERT — sub-millisecond)" on one line and
"Do not block the trigger path on disk I/O" on the next. Those contradict:
§6.7 sets `PRAGMA synchronous=FULL`, which fsyncs the WAL on **every** commit,
and the target's Samsung MZNLF192 is a low-end TLC SATA SSD with no
power-loss-protected cache, sharing the filesystem with 4.5–7.5 MB/s of
continuous archive writes. Under that load fsync latency reaches tens to low
hundreds of milliseconds during garbage collection. The *recorded time* is
safe either way — it comes from the kernel event timestamp — but a stalled
commit means the evdev reader thread is not reading, so a second boat's press
sits in the kernel buffer and a long enough stall risks `SYN_DROPPED`. The
archive load is the specific mechanism by which disk pressure can perturb the
timing path, and it must not be on that path. N4 requires that a *committed*
result survives a crash, not that it commits instantly.

**Why image selection is deferred (v1.2).** v1.0 selected
`window(target, before=15, after=15)` synchronously at the moment of the press.
That can never return 31 frames: at selection time the newest frame in the
buffer is `t_recv ≈ t_press`, so the number of frames *after* `target` that
exist yet is only `Δ × fps` — about 4 at Δ=150 ms and 30 fps, and essentially
**zero** once §5.4's corrected `Δ ≈ 0` is used. The operator would get 15 frames
before the moment and almost none after, so the frames actually showing the boat
crossing — the ones a jury needs — are the missing ones.

Express the window in **milliseconds, not frames** (`window_before_ms`,
`window_after_ms`), so it is fps-independent; v1.0's frame counts silently
halved the covered time span when switching to 720p60 per §10.2.

Edge cases that must be handled explicitly:

| Case | Behaviour |
|---|---|
| No race started yet | Reject the trigger, flash a UI warning. Do not record. |
| Buffer empty (stream down) | Record the time anyway with `image_id = NULL`. **The time is the primary datum; never discard a time because the camera failed.** Mark the row visually as "no image". |
| `target` older than buffer span | Record the time; attach the oldest available frame; flag the row as "image approximate". |
| `target` newer than newest frame | Record the time; attach the newest frame; flag as "image approximate". |

That second row is the most important behaviour in this table. A regatta result
with a missing photo is recoverable. A missing time is not. §6.4's debounce
handling follows the same rule: a suspected double-press is recorded and
flagged, never dropped.

#### Resuming after a restart

Requirement N4 says the system must survive a mid-race crash "without losing
already-recorded results". v1.0 satisfied that only in the narrow sense that the
rows persist — **the race itself could not be continued**, because `start_race`
was the sole entry point and it resets `t0` and the capture list. The realistic
race-day sequence is unforgiving: the cable is nudged at minute 4, `iproxy`
exhausts its restarts, the operator restarts the application to recover the
stream, and the heat's start reference is gone while crews are still on the
water.

Three changes are required:

1. **Record `boot_id`** (from `/proc/sys/kernel/random/boot_id`) on the `race`
   row. `CLOCK_MONOTONIC` is seconds since boot: stored monotonic values are
   meaningless across a reboot and *collide* between races on different days.
   This also silently corrupts F7's recovery story, since archive filenames
   embed `t_recv_ms` — a post-hoc tool matching frames to captures from a prior
   boot would confidently match the wrong frames.
2. **`resume_race(race_id)`.** If `boot_id` matches, reload `t0_monotonic` and
   continue timing exactly as before. If it does not, reconstruct an approximate
   `t0` from `t0_wall`, mark the race and every subsequent capture with a
   `t0_reconstructed` flag, and say so in the UI and the export — those times
   are no longer defensible to ±100 ms, and the jury must know.
3. **Never require an application restart to recover the transport.** Add an
   explicit "Reconnect camera" action in the UI, and make `iproxy` restart
   attempts **unlimited with backoff while a race is active** rather than
   v1.0's three. Three attempts and then permanent failure is how a recoverable
   cable jostle becomes a lost heat.

### 6.6 `archive.py` — continuous recording

Requirement F7. Every frame received during a race is written to disk with its
timestamp, so a fumbled or missed press can be recovered afterwards.

```python
class ArchiveWriter(threading.Thread):
    def __init__(self, directory: Path, queue_maxsize: int = 300): ...
    def submit(self, frame: Frame) -> None:
        """Non-blocking. If the queue is full, drop the frame and increment
        a dropped counter surfaced in the UI. Never block the reader thread."""
```

- Filename: `{seq:08d}_{t_recv_ms}.jpg` in `archive/{race_id}/`.
- Write an `index.jsonl` with one line per frame:
  `{"seq":…, "t_recv":…, "t_wall":…, "file":…}`. This lets a recovery tool find
  a frame by timestamp without reading every filename.
- Disk budget: 1080p JPEG ≈ 150–250 KB. At 30 fps that is ~4.5–7.5 MB/s, or
  **16–27 GB/hour**.

**A start-time check alone is not enough.** 20 GB free buys only **44–75
minutes** of archiving, so a heat can pass the pre-flight check and then run the
disk dry mid-race. (`system-environment.md` read the same numbers as "6–8
hours"; that figure is the headroom from 156 GB, not from the 20 GB threshold.)
A full regatta day of continuous archiving consumes 100–160 GB — essentially the
whole disk — and no retention policy existed in any document.

Required behaviour:

| Free space | Action |
|---|---|
| Below `min_free_gb` at race start | Refuse to start, with an explicit override |
| Below 10 GB during a race | Automatically switch to `every_nth_frame = 5`, banner in the UI |
| Below 3 GB during a race | Stop archiving entirely; **captures and times continue** |

- Set `min_free_gb` to cover a realistic session (60 GB), or compute it as
  `expected_hours × measured_rate`.
- Add an inter-heat retention policy: prompt to purge or offload the previous
  heat's archive once its captures are exported.
- **Reserve headroom the OS cannot lose.** The data root shares the single ext4
  root filesystem with the OS (audit: `/dev/sda2` is the only filesystem), so
  filling it does not merely stop the archive — SQLite commits fail, the JSONL
  log fails, and the desktop session degrades. §11's "archive degrades
  gracefully, captures continue" is not achievable on a filesystem that just
  filled. Either put the data root on its own filesystem, or `fallocate` a 3 GB
  ballast file at startup and delete it when space is exhausted.

### 6.7 `storage.py` — persistence

SQLite, with `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=FULL` so that a
crash cannot lose a committed result (requirement N4). Commits happen on a
single writer thread, never on the trigger path — see §6.5.

`PRAGMA foreign_keys = ON` **on every connection**. SQLite disables foreign-key
enforcement by default, so without it the `REFERENCES` clauses below are purely
decorative.

Consider `synchronous=NORMAL` instead: in WAL mode it is already crash-safe
against application death, which is the case N4 actually names, and only weakens
against sudden power loss. If you take that trade, state it here rather than
leaving it implicit.

```sql
CREATE TABLE race (
    id                INTEGER PRIMARY KEY,
    name              TEXT NOT NULL,
    boot_id           TEXT NOT NULL,   -- /proc/sys/kernel/random/boot_id (§6.5)
    t0_monotonic      REAL NOT NULL,
    t0_wall           REAL NOT NULL,   -- UTC epoch seconds, for the record only
    t0_reconstructed  INTEGER NOT NULL DEFAULT 0,  -- t0 recovered from wall clock
    start_mode        TEXT NOT NULL,   -- 'direct' | 'radio' | 'external' (§5.3.1)
    radio_delay_ms    REAL NOT NULL DEFAULT 0,
    delta_used        REAL NOT NULL,   -- Δ in effect at race start
    viewing_mode      TEXT NOT NULL,   -- 'water' | 'screen' (§5.4)
    fps_nominal       REAL,
    notes             TEXT,
    created_at        TEXT NOT NULL,
    CHECK (start_mode   IN ('direct','radio','external')),
    CHECK (viewing_mode IN ('water','screen'))
);

CREATE TABLE capture (
    id              INTEGER PRIMARY KEY,
    race_id         INTEGER NOT NULL REFERENCES race(id),
    sequence        INTEGER NOT NULL,   -- 1, 2, 3 … order of crossing
    t_press         REAL NOT NULL,      -- monotonic
    t_press_wall    REAL NOT NULL,      -- wall clock, record only
    elapsed_s       REAL NOT NULL,      -- t_press - race.t0_monotonic
    delta_used      REAL NOT NULL,      -- Δ at THIS capture, not just at t0
    bow_number      TEXT,               -- entered by operator, nullable
    primary_image   TEXT,               -- relative path, nullable
    image_flag      TEXT,               -- NULL | 'approximate' | 'missing'
    debounce_suspect INTEGER NOT NULL DEFAULT 0,  -- §6.4; recorded, never dropped
    deleted         INTEGER NOT NULL DEFAULT 0,
    notes           TEXT,
    UNIQUE (race_id, sequence),
    CHECK (image_flag IS NULL OR image_flag IN ('approximate','missing'))
);

CREATE TABLE capture_frame (
    id              INTEGER PRIMARY KEY,
    capture_id      INTEGER NOT NULL REFERENCES capture(id),
    t_recv          REAL NOT NULL,
    offset_ms       REAL NOT NULL,      -- t_recv - target; negative = earlier
    path            TEXT NOT NULL,
    is_primary      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_capture_race ON capture(race_id, sequence);
CREATE INDEX idx_frame_capture ON capture_frame(capture_id, t_recv);

-- Exactly one primary frame per capture (§7.3 lets the operator promote one).
CREATE UNIQUE INDEX idx_one_primary ON capture_frame(capture_id)
    WHERE is_primary = 1;
```

Deletion is **soft** (`deleted = 1`). Requirement F5 says corrections must not
disturb other records; renumbering or hard-deleting rows risks exactly that.
Display order uses `sequence`; the UI shows deleted rows struck through with an
undo affordance.

**Sequence allocation, which v1.0 left undefined.** The next sequence is

```sql
SELECT COALESCE(MAX(sequence), 0) + 1 FROM capture WHERE race_id = ?;
```

**including soft-deleted rows**, so numbers are never reused. Computing it from
`COUNT(*) WHERE deleted = 0` — the obvious alternative — produces a *duplicate*
sequence after any deletion, and since §6.8's CSV export is ordered by
`sequence`, two crews would share a placing. That is F5 violated in the
deliverable itself. `UNIQUE (race_id, sequence)` above makes the mistake fail
loudly instead of silently.

`delta_used` is per-capture, not just per-race, because §5.5 step 7 explicitly
invites the operator to tune the reaction offset after a practice run. If `Δ`
changes between heats — or mid-heat — the record must say which value produced
which image.

Filesystem layout, rooted at **`<data_root>`**, which defaults to
`~/regatta-data/` and is configurable. It is **not** `~/regatta/`: that is the
source tree, and v1.0's layout collided the two. `<data_root>` is the single
source of truth for every runtime path in this document, in `INSTALL.md` and in
`TESTING.md`.

```
<data_root>/                          # default ~/regatta-data/
    regatta.db
    config.toml
    calibration.json
    logs/
        regatta-<race_id>.jsonl
    races/
        2026-08-21_heat3/
            captures/
                001_primary.jpg
                001_w-0167.jpg        # window frames, offset in MILLISECONDS
                001_w+0100.jpg
                002_primary.jpg
            archive/
                index.jsonl
                00000001_1234567.jpg
                ...
            export.csv
```

Two corrections to v1.0's tree: the archive lives under
`races/<race>/archive/`, not the bare `archive/{race_id}/` that §6.6's filename
rule implied; and window-frame filenames carry a **millisecond** offset, since
§6.5 now specifies the window in milliseconds rather than frames.

**`config.toml` is hand-edited; `calibration.json` is machine-written.** v1.0
listed both without saying which owns `Δ`, and §5.5 wrote the calibration result
"to config". Resolved: the application never writes `config.toml`. Everything
produced by the calibration routine — `latency_median`, its IQR, `viewing_mode`,
resolution, fps, lens, `mean_frame_bytes` — lives in `calibration.json`, and the
application computes `Δ` from that plus `reaction_offset_ms` out of
`config.toml`. This also removes the `tomli-w` dependency.

### 6.8 `export.py`

CSV columns:
`sequence, bow_number, elapsed_seconds, elapsed_formatted, wall_clock_utc, image_file, image_flag, notes`

`elapsed_formatted` as `M:SS.mmm` (e.g. `6:12.483`). Three decimal places
throughout — the tolerance is 100 ms, but truncating to 0.1 s would make ties
appear where none exist.

### 6.9 `log.py` — structured logging

Absent from v1.0. It is required, because §7.5 forbids interrupting the operator
with dialogs during a race: every anomaly that is *not* shown to the operator
must still be recoverable afterwards, and the failure reports defined in
`TESTING.md` §2 have nothing to cite without it.

- Write JSON Lines to `<data_root>/logs/regatta-<race_id>.jsonl`, one object per
  event, each carrying `t_mono`, `t_wall`, `level`, `component`, `event` and
  event-specific fields.
- Log at minimum: application start with the full environment fingerprint
  (Python version, package versions, `Δ` in effect, stream URL, resolution,
  nominal fps); every stream connect, disconnect and reconnect attempt; every
  `iproxy` restart; every debounce rejection (§6.4); every dropped archive frame
  (§6.6); every capture with its `t_press`, `elapsed`, selected frame offset and
  `image_flag`; every free-space warning.
- Roll fps and dropped-frame counters into a heartbeat line once per 10 s. This
  is what makes a post-hoc "when did the fps start sagging?" question answerable,
  and it is the primary input to test T2 and T8.
- **Logging must not touch the trigger path.** Use `QueueHandler` with a
  background `QueueListener`, so a stalled disk cannot delay a capture.
- Never log at a level that writes per-frame lines during a race. At 30 fps that
  is 108 000 lines an hour and it competes with the archive for the same disk.

---

## 7. User interface specification

Framework: **PySide6** (Qt 6), installed **from pip**, not from apt. PySide6
6.11.2 ships as a `cp310-abi3` stable-ABI wheel declaring
`requires_python = ">=3.10,<3.15"`, so a single wheel covers Python 3.10 through
3.14 and the target's Python 3.14 is fully supported. Do not depend on a
`python3-pyside6.*` apt package — it is not present under that name on every
Ubuntu release. Two consequences are recorded as failure modes in §11: the
`<3.15` ceiling, and the `libxcb-cursor0` runtime dependency of Qt's `xcb`
platform plugin.

### 7.1 Layout

```
┌────────────────────────────────────────────────────────────────┐
│ [Race: Heat 3 ▾]   ● REC 00:04:17.2     iPhone ✓ 29.8 fps      │  status bar
├──────────────────────────────────────┬─────────────────────────┤
│                                      │  CAPTURES               │
│                                      │  ┌───────────────────┐  │
│                                      │  │ ▣  1   0:00.000   │  │
│         LIVE PREVIEW                 │  │    START          │  │
│         (letterboxed)                │  ├───────────────────┤  │
│                                      │  │ ▣  2   6:12.483   │  │
│         ┊ finish line overlay ┊      │  │    bow: [ 14 ]    │  │
│                                      │  ├───────────────────┤  │
│                                      │  │ ▣  3   6:14.902   │  │
│                                      │  │    bow: [    ]    │  │
│                                      │  └───────────────────┘  │
│                                      │                         │
├──────────────────────────────────────┴─────────────────────────┤
│ [Start Race]  [Calibrate]  [Export CSV]      Trigger: SPACE    │
└────────────────────────────────────────────────────────────────┘
```

### 7.2 Live preview

- Decode and display at **10 fps maximum**, independent of the ingest rate.
  Decoding 30 fps of 1080p JPEG for a preview wastes CPU that the ingest and
  archive threads need. Use a `QTimer` at 100 ms that pulls the newest frame
  from the buffer.
- **Decode the preview at reduced size, using `QImageReader.setScaledSize()`
  before `read()`** — do not decode full 1080p and then downscale. libjpeg
  performs 1/2, 1/4 and 1/8 scaling in the DCT domain during decode, which is
  roughly an order of magnitude cheaper than a full decode. On this machine's
  dual-core i7-6600U the preview is the only meaningful CPU pressure point in
  the whole application, and this one call removes most of it. Everything else
  on the hot path is byte copying.
- Scale with `Qt.FastTransformation` for any residual resize. Quality does not
  matter here; the saved JPEGs are untouched originals.
- Draw a **vertical finish-line overlay** — a draggable, persisted vertical
  line the operator aligns with the real finish line during setup. This is a
  small feature with outsized practical value: it gives the operator a
  consistent visual reference and gives the jury a reference in the saved
  images (the line position is stored in config and can be drawn on exported
  images).
- Show the current fps and a connection indicator. If no frame has arrived in
  2 s, turn the indicator red. Do not open a dialog.

### 7.3 Capture list

Requirement F3 — this is the primary deliverable of the whole system.

- One row per capture: thumbnail (120 px wide), sequence number, elapsed time in
  `M:SS.mmm`, and an inline editable bow-number field.
- Newest at top, or newest at bottom with auto-scroll — pick one and make it
  configurable. Auto-scroll must not steal focus from the bow-number field the
  operator is typing in.
- Clicking a row opens a **frame review panel**: the full-size primary image
  plus a slider across the ±15 saved window frames, each labelled with its
  offset in milliseconds from the recorded time. The operator can promote any
  window frame to primary. This is how a jury resolves a close finish.
- Right-click → delete (soft), add note.

### 7.4 Keyboard and trigger bindings

| Action | Default binding | Path |
|---|---|---|
| Record crossing | `KEY_F13` (or the footswitch's own code) | evdev (precise) |
| Arm race start | `Ctrl+S`, then confirm | Qt — confirmation only, **not** the timestamp |
| Start race (`t0`) | `KEY_F14` on the same evdev device | **evdev — precision identical to the crossing trigger** |
| Undo last capture | `Ctrl+Z` | Qt |
| Focus bow field of last capture | `Tab` | Qt |
| Reconnect camera | `Ctrl+R` | Qt |

**Both timestamps come from evdev.** v1.0 routed Start Race through Qt and
called its precision "irrelevant"; §5.3 explains why that is wrong — the
reaction-time cancellation requires the two measurements to be symmetric, and
a Qt path plus a confirmation dialog puts tens to hundreds of milliseconds of
one-sided error onto every elapsed time in the race. Confirmation happens
*before* arming; the next press on the start keycode is `t0`.

The crossing keycode defaults to `KEY_F13`, **not** `KEY_SPACE`. See §6.4: the
evdev trigger is global and has no concept of focus, so a space typed into a bow
number field would fire a phantom crossing.

Everything except the two timestamps may use Qt.

### 7.5 Behaviour under stress

The application must never block the trigger path. Specifically:

- No modal dialogs while a race is active.
- Errors appear as a dismissible banner at the top of the window.
- Disk writes, thumbnail generation, and SQLite `VACUUM` all happen off the
  trigger thread.

---

## 8. Configuration

`<data_root>/config.toml` (default `~/regatta-data/config.toml`):

```toml
[paths]
data_root = "~/regatta-data"   # everything in §6.7 is relative to this

[transport]
local_port = 8081
device_port = 8081
udid = ""                    # empty = first device found
iproxy_path = "iproxy"
max_restarts_idle = 3        # outside a race
max_restarts_racing = 0      # 0 = unlimited with backoff (§6.5)

[stream]
url = "http://127.0.0.1:8081/live"
username = "admin"
password = "admin"
buffer_seconds = 10.0
assumed_fps = 30             # MUST match the phone's actual fps (§6.3)
require_content_length = true

[timing]
viewing_mode = "water"       # "water" | "screen" — selects the Δ formula (§5.4)
reaction_offset_ms = 0.0     # operator-tuned component
debounce_ms = 20             # suspect presses are RECORDED and flagged (§6.4)
start_mode = "direct"        # "direct" | "radio" | "external" (§5.3.1)
radio_delay_ms = 0.0         # measured; only meaningful when start_mode="radio"
# Δ itself is NOT stored here. It is derived at race start from
# calibration.json + reaction_offset_ms. See §5.4 and §6.7.

[capture]
window_before_ms = 500       # milliseconds, not frames — fps-independent (§6.5)
window_after_ms = 500

[trigger]
device_path = ""             # e.g. /dev/input/event5; empty = Qt fallback
crossing_keycodes = [183]    # KEY_F13 — never KEY_SPACE (§6.4)
start_keycodes = [184]       # KEY_F14
grab_device = false          # true = exclusive grab; required if using the
                             # internal keyboard as the trigger (§6.4)

[archive]
enabled = true
every_nth_frame = 1
min_free_gb = 60             # 20 GB buys only 44-75 minutes (§6.6)
degrade_at_gb = 10           # switch to every_nth_frame = 5
stop_at_gb = 3               # stop archiving; captures continue
ballast_gb = 3               # fallocate reserve so the OS cannot be starved

[ui]
finish_line_x = 0.5          # normalised 0..1 position of the overlay
preview_fps = 10
```

The stream URL path (`/live`) and ports are **app- and version-dependent**.
The setup runbook (§9) must have the operator verify them in the iOS app's own
UI rather than assuming.

**This file is never written by the application.** v1.0 had §5.5 write the
calibration result back into `[timing]`, which would have required a TOML writer
(stdlib `tomllib` is read-only). Resolved in §6.7: `config.toml` is
hand-edited; everything machine-produced goes to `calibration.json`, and `Δ` is
derived at race start. `tomllib` alone suffices and the `tomli-w` dependency is
dropped.

`calibration.json`, written by §5.5:

```json
{
  "measured_at": "2026-08-21T09:14:02Z",
  "latency_median_ms": 214.0,
  "latency_iqr_ms": 18.5,
  "samples_ms": [212.0, 218.0, "…20 values…"],
  "viewing_mode": "water",
  "resolution": "1920x1080",
  "fps": 30,
  "lens": "telephoto",
  "mean_frame_bytes": 187000
}
```

At race start the application computes `Δ` per §5.4 and refuses to start if
`calibration.json` is missing, or if its `resolution`, `fps`, `lens` or
`mean_frame_bytes` disagree with the live stream (the last by more than 30%).
That check replaces v1.0's `delta_ms == 0` rule, which was wrong in both
directions — a correctly calibrated water-watching `Δ` is legitimately near
zero, while a stale non-zero `Δ` sailed through.

---

## 9. Setup runbook

### 9.1 One-time laptop setup

> **The authoritative, step-by-step install procedure — with the reason for every
> package, verification commands after each stage, offline pre-staging, and
> troubleshooting — is in `INSTALL.md`. Follow that document, not this summary.**

The v1.0 command block in this section was incorrect for the target machine in
four ways and would have failed partway through: it omitted the C toolchain that
`evdev` needs in order to build, omitted Qt 6's `libxcb-cursor0` runtime
dependency, omitted a TOML writer, and named a `libimobiledevice6` package that
does not exist on this release. Corrected summary:

```bash
sudo apt update
sudo apt install -y \
    usbmuxd libimobiledevice-utils \
    python3-pip python3-venv python3-dev build-essential \
    libxcb-cursor0 usbutils ffmpeg

sudo usermod -aG input "$USER"     # required for evdev trigger; log out/in after

python3 -m venv ~/regatta/venv
source ~/regatta/venv/bin/activate
pip install PySide6-Essentials requests evdev pillow
```

Note `libimobiledevice-utils` rather than an explicitly named runtime library:
apt resolves the correct `libimobiledevice-1.0-N` dependency itself, and naming
it by hand is how the v1.0 block broke. Note also `PySide6-Essentials` rather
than `PySide6`: this application uses only QtCore, QtGui and QtWidgets, and the
full `PySide6` metapackage additionally pulls PySide6-Addons — several hundred
megabytes of WebEngine, 3D, Charts and Multimedia that will never be imported.

**Data directory.** §6.7 puts runtime data in `~/regatta/`, which is also where
the source tree and this specification live. Separate them before scaffolding:
put code in `~/regatta/` and data under a configurable root defaulting to
`~/regatta-data/`. The audit confirms 156 GB free on that filesystem, so they
may share a disk — they must not share a directory.

### 9.2 One-time iPhone setup

1. Install **IP Camera Lite** from the App Store. **Purchase the full version.**
   The free edition stamps a watermark on every frame and snapshot, which
   compromises a photo used to settle a protest.
2. Open the app, enable the IP Camera Server, and note the HTTP port and the
   MJPEG URL path it displays.
3. Set username and password (defaults are `admin`/`admin`); record them in
   `config.toml`.
4. Select the rear camera. **If the phone has a telephoto lens, select it** —
   at 50 m the standard wide lens renders an eight-oar shell very small in frame.
5. Set resolution and frame rate (see §10.2 for the trade-off).
6. **Verify what manual exposure control the app actually exposes, and write it
   down.** §10.1 prescribes a shutter of 1/500 s or faster, but explicit
   shutter selection requires `setExposureModeCustom(duration:iso:)` and
   comparatively few streaming apps surface it — AE/AF/AWB *lock* is common,
   *shutter selection* is not. This has never been verified against IP Camera
   Lite. If the app offers lock only, the shutter is whatever the locked
   exposure chose, which in overcast light may be 1/60 s — a shell at 5 m/s
   smears 8 cm at that speed, and a smeared bow ball is an unusable protest
   photo. If manual shutter is unavailable, either accept the smear in writing
   or switch apps (Appendix A).

### 9.3 Per-race setup

1. Connect the iPhone by USB. Unlock it. Tap **Trust** if prompted.
2. Verify: `idevice_id -l` prints a UDID.
3. Start the application. It launches `iproxy` and connects.
4. Smoke test before trusting the app — this validates the whole transport
   independently. **Include the credentials**: §9.2 step 3 sets a username and
   password, so an unauthenticated request gets HTTP 401 and a blank window,
   which reads exactly like a broken transport.
   ```bash
   iproxy 8081:8081 &
   ffplay -fflags nobuffer -flags low_delay \
          http://admin:admin@127.0.0.1:8081/live
   ```
   They must match `[stream]` in `config.toml`.
5. Mount the phone on a tripod, positioned **on the extended finish line**,
   perpendicular to the course, so that "bow crosses the line" is directly
   judgeable in the image rather than inferred from an oblique angle.
6. Align the finish-line overlay in the preview with the real line.
7. **Lock focus, exposure, and white balance** in the iOS app. Autofocus
   hunting at the instant of a finish is the classic failure mode of this
   kind of system.
8. **Run the calibration routine** (§5.5) — but see §5.5 constraint 4 and N5
   below. Calibration is a per-*session* step, not a per-race one: it takes far
   longer than N5's five-minute budget and nothing about it changes between
   heats unless the capture format does.
9. Disable iPhone auto-lock. Keep the app in the foreground — **iOS suspends
   backgrounded apps and the stream will die.** Consider Guided Access to
   prevent accidental app switching.
10. **Check the phone's battery trend over the first 10 minutes — do not
    assume it is charging.** v1.0 told the operator to "confirm the phone is
    charging"; on this laptop it will not be. A ThinkPad T460s USB 3.0
    downstream port supplies 900 mA at 5 V = **4.5 W**, and an iPhone capturing
    1080p30, JPEG-encoding every frame and running an HTTP server draws well
    above that. It will net-discharge, and over a six-hour regatta that ends the
    session. Put a **powered USB hub** between the laptop and the phone —
    usbmuxd is unaffected by the hub — or run the phone from its own battery
    pack.
11. **Shade the phone.** A phone on a tripod in direct sun, encoding 1080p30 for
    hours, reaches its thermal limit; iOS responds by throttling the camera
    pipeline (fps drops, which changes the frame-quantisation term and
    invalidates `Δ`) and eventually by disabling the camera outright. A white
    cover or an umbrella is sufficient.
12. **Shade the laptop screen and enable the audible stream alarm.** A 2016
    ThinkPad panel is ~250 nits and is unreadable in daylight, so §7.2's entire
    failure-detection strategy — a red indicator and an fps readout — is
    invisible outdoors, as is the finish-line overlay in step 6.
13. **Set up the paper backup.** A second person with a stopwatch and a printed
    form. Everything above rides on one ten-year-old laptop with aged batteries;
    a kernel panic, a tripped power strip or a spilled drink ends the regatta
    otherwise, and §13 puts a redundant second laptop out of scope.

### 9.4 Race-day laptop hygiene

Absent from v1.0, and each of these has killed a race on laptop-based timing
systems. Run before the first heat; see `INSTALL.md` §7 for a script that does
all of it.

1. **Mains power, always.** The audit found aged dual batteries (one reporting
   23.54 Wh design capacity). Continuous disk writes plus USB power delivery to
   the phone will outrun them.
2. **Disable blanking, DPMS and suspend-on-lid-close.** A session suspend takes
   `usbmuxd` down with it and ends the race. `xset s off -dpms`, plus the XFCE
   power-manager settings for lid action and display sleep.
3. **Set the CPU governor to `performance`.** The audit found the machine
   scaling at ~67% under an ondemand-class governor. Sustained ingest, archive
   and preview on a 15 W U-series part will otherwise ride the governor's ramp
   at exactly the wrong moments.
4. **Wi-Fi off and NTP off** — `nmcli radio wifi off`, `timedatectl set-ntp
   false`. Requirement N3. Timing is monotonic-based so an NTP step is harmless
   to results, but it would corrupt the `t_wall` audit record.
5. **Automatic updates off.** This is a development-branch release; an
   unattended upgrade mid-regatta could replace Python, Qt or usbmuxd underneath
   a running application. See §11 and `INSTALL.md` §6.

---

## 10. Camera and optical guidance

### 10.1 Field settings

| Setting | Recommendation | Reason |
|---|---|---|
| Lens | Telephoto if available | 50 m subject distance |
| Focus | Manual, locked on the finish line | No hunting at the critical instant |
| Exposure | Manual, locked | Prevents brightness swings as boats enter frame |
| White balance | Locked | Consistent images across a session |
| Shutter | 1/500 s or faster in daylight | A rowing shell at ~5 m/s smears otherwise; a crisp bow ball is what the jury reads |

### 10.2 Resolution and frame rate trade-off

Higher frame rate halves the frame-quantisation error (±17 ms at 30 fps versus
±8 ms at 60 fps). Higher resolution makes bow numbers legible at 50 m.

But `usbmuxd` throughput is well below USB 2.0's theoretical ceiling in
practice, and MJPEG has **no inter-frame compression** — every frame carries its
own full JPEG (v1.0 said "uncompressed between frames", which reads as though
the frames themselves were uncompressed).

**Do not treat 720p60 as the low-bandwidth option.** It is not:

| Configuration | Typical frame | Sustained rate |
|---|---|---|
| 1080p30 | 150–250 KB | 4.5–7.5 MB/s |
| 720p60 | 60–100 KB | **3.6–6.0 MB/s** |
| 720p30 | 60–100 KB | 1.8–3.0 MB/s |
| 1080p30, higher JPEG compression | 80–120 KB | 2.4–3.6 MB/s |

720p60 overlaps 1080p30 and sits above its floor, so a tunnel that cannot
sustain 1080p30 will not sustain 720p60 either — and choosing it in response to
a bandwidth shortfall costs bow-number legibility at 50 m, which is the reason
§9.2 step 4 mandates the telephoto lens in the first place. The genuine
low-bandwidth options are the bottom two rows.

**Decide by measured delivered frame rate, not by bytes.** Run each candidate
for 60 s and pick the one with the highest sustained delivered fps at acceptable
bow-number legibility. 1080p30 is the recommended starting point.

The application must display measured fps prominently so a degraded link is
visible immediately rather than discovered during a heat.

---

## 11. Known failure modes

| Failure | Symptom | Mitigation |
|---|---|---|
| iOS suspends the app | Stream stops, fps → 0 | Keep app foregrounded; Guided Access; UI indicator turns red within 2 s |
| Cable jostled | `iproxy` dies | Auto-restart up to 3×; times still recorded without images |
| `Δ` stale, or calibrated against the wrong capture format / scene | Images systematically show the boat before or after the line | Refuse to start unless `calibration.json` exists **and** its resolution, fps, lens and mean frame size match the live stream (§8). v1.0's `delta_ms == 0` rule is removed: a correct water-watching `Δ` is legitimately near zero (§5.4) |
| **`Δ` computed with the v1.0 formula** | Every image ~2·L stale — 300–600 ms — while every test still passes | §5.4. `Δ = reaction_offset − latency_median` for water-watching, `Δ = reaction_offset` for screen-watching. Never `latency_median + reaction_offset` |
| evdev permission denied | Trigger silently degraded | Detect at startup, warn in the UI, fall back to Qt |
| Clock domain mixed up | Image selection always returns the newest frame; every capture flagged `approximate` | The `EVIOCSCLOCKID` ioctl in §6.4 is mandatory. **`set_clock_id()` does not exist in python-evdev** — v1.0 and v1.1 specified a method that raises `AttributeError`. Assert at startup that an evdev timestamp is within 1 s of `time.monotonic()`, and treat failure as a hard refusal to start |
| **Phantom crossings from the global trigger** | Extra captures appear while the operator types bow numbers | §6.4: dedicated trigger device, `KEY_F13` not `KEY_SPACE`, refuse a device that is also the active keyboard, `grab()` if it must be |
| **Two boats inside the debounce window** | The second crew's time is lost | §6.4: debounce defaults to 20 ms, and a suspect press is **recorded and flagged**, never dropped |
| **Mid-race restart** | `t0` lost; heat cannot be continued | §6.5: `boot_id` on the race row, `resume_race()`, a UI reconnect action, and unlimited `iproxy` retries while racing |
| **Disk fills mid-heat** | SQLite commits fail; the whole session degrades | §6.6: continuous monitor, graceful degradation at 10 GB and 3 GB, `fallocate` ballast |
| **Phone net-discharges on USB** | Stream dies after a few hours | §9.3 step 10: a T460s port supplies 4.5 W, less than the phone draws. Powered hub or battery pack |
| **Phone thermal throttling in sun** | fps sags, then the camera stops | §9.3 step 11: shade the phone. Detectable as an fps decline in the §6.9 heartbeat |
| **Laptop screen unreadable in daylight** | Operator cannot see the red indicator or fps | §9.3 step 12: sunshade plus an **audible** alarm, not only a visual one |
| **Total laptop failure** | Regatta lost | §9.3 step 13: mandatory paper backup with a stopwatch |
| **Tripod vibration at 50 m on telephoto** | Soft images despite a fast shutter | §10.1: weighted tripod, wind shielding |
| Disk full mid-race | Archive writes fail | Pre-flight free-space check; archive degrades gracefully, captures continue |
| Wayland session | evdev may behave differently; screen capture for calibration differs | Prefer an X11 session; detect `XDG_SESSION_TYPE` and warn. *The target machine is confirmed X11, so this should not fire.* |
| **Distro moves to Python 3.15** | Venv breaks; `pip install PySide6` fails outright | PySide6 6.11.2 declares `requires_python <3.15`. On a development-branch release this is the single largest schedule risk in the project. `apt-mark hold python3 python3-minimal libpython3-stdlib` and disable unattended upgrades — see `INSTALL.md` §6 |
| **`libxcb-cursor0` missing** | *"Could not load the Qt platform plugin xcb"* — the GUI never appears | Qt ≥ 6.5 requires it at runtime and the pip wheel does not bundle it. Installed in §9.1; verified by the Qt platform check in `INSTALL.md` §4 |
| **Laptop suspends or blanks mid-race** | Stream dies, `usbmuxd` drops, race lost | §9.4 step 2. Verify before the first heat, not after |
| **CPU governor throttling** | fps sags under sustained load; dropped frames | §9.4 step 3 |

### 11.3 Session type

Xubuntu defaults to X11, which is what this design assumes. If
`XDG_SESSION_TYPE == "wayland"`, warn at startup: evdev still works (it is
below the display server), but test the calibration display path.

---

## 12. Implementation order

Build and verify in this sequence. Each step is independently testable.

**The gates below are stated in one line each; the executable version of each —
procedure, quantified pass criteria, and what to record — is `TESTING.md`,
tests T0–T10, which map one-to-one onto these steps. Do not treat a gate as
passed on the basis of the one-line summary here.** When a gate fails, follow
the reporting protocol in §12.2 before moving on.

0. **Environment.** Work through `INSTALL.md` end to end. *Gate: its §8
   verification checklist passes — every line green — before any code is
   written.* Several of its steps require an interactive `sudo` password and a
   logout/login for `input` group membership, so this cannot be scripted
   unattended.
1. **Transport + smoke test.** `iproxy` starts, `ffplay` shows video. No Python
   yet. *Gate: video visible.*
2. **`MJPEGReader` → console.** Print `seq`, `t_recv`, and byte count per frame.
   *Gate: steady fps, no drift, no memory growth over 10 minutes.*
3. **`FrameBuffer`.** Add ring buffer; verify `nearest()` and `window()` against
   synthetic timestamps. *Gate: unit tests pass.*
4. **`TriggerListener`.** Print `t_press` on each key press. *Gate: assert
   evdev timestamps are in the same clock domain as `time.monotonic()`.*
5. **Calibration.** Full-screen millisecond counter; capture 20 frames; compute
   median `L`. *Gate: `L` is stable to within ±20 ms across repeated runs.*
6. **`CaptureController` + `storage.py`.** Headless: press key, row appears in
   SQLite, JPEGs on disk. *Gate: all §6.5 edge cases exercised.*
7. **Qt UI.** Preview, capture list, review panel. *Gate: requirement F3 met.*
8. **`ArchiveWriter`.** *Gate: 10-minute run with no dropped frames and no
   trigger-path latency increase.*
9. **Export, race management, polish.**

### 12.1 Acceptance test

The end-to-end test that validates the whole system:

1. Point the camera at the laptop screen showing the millisecond counter.
2. Start a race.
3. Press the trigger 10 times at irregular intervals.
4. For each capture, read the counter value visible in the saved primary image
   and compare it to the recorded elapsed time plus `t0`.
5. **Pass criterion: all 10 discrepancies within ±100 ms; median within ±50 ms.**

This test exercises transport, ingest, timestamping, calibration, selection and
storage simultaneously. It is test **T10** in `TESTING.md`. Record all ten
discrepancies whether it passes or fails: on a pass they are the baseline that
later regressions are measured against.

**It proves N2, not N1.** v1.0 claimed both. But `elapsed` is *defined* as
`t_press - t0`, so `t0 + elapsed ≡ t_press` identically, and step 4 reduces to
comparing `T_shown` against `t_press` — a statement about frame *selection*.
Any error in the recorded time cancels from both sides of the comparison.

N1 cannot be proven by this system against itself, because the system has no
ground-truth external event. What is being assumed instead, and should be stated
plainly rather than tested away: evdev kernel timestamps are accurate to <1 ms,
`t0` and `t_press` traverse identical paths (§5.3), and the residual is operator
reaction variance (§5.6). If N1 must actually be *measured*, it needs an
external reference — an LED flash visible to the camera and wired to a second
input channel, so the same physical instant produces both a frame and an
independent event.

`TESTING.md` §6 additionally defines a **pre-regatta dress rehearsal** — full
field configuration, 20-minute run, and a deliberate mid-run cable pull to prove
the §6.1 auto-restart and the §6.5 "record the time even with no image"
behaviour. Passing T0–T10 on the bench is not the same as being ready for a
regatta.

### 12.2 Test reporting protocol

Failures must be reported **as files**, not as remarks. When any gate above
fails:

1. Run `./collect-diagnostics.sh T<n>` (`TESTING.md` §7), which captures the
   environment fingerprint, the drift from `environment-lock.txt`, the recent
   journal, the log tail and the database tail into
   `reports/diag-T<n>-<timestamp>.tar.gz`.
2. Write `reports/FAIL-T<n>-<timestamp>.md` using the template in `TESTING.md`
   §2.1. Required fields include severity, what the failure **blocks**, the
   quoted pass criterion, the measured value, verbatim output, a reproduction
   command, and which of F1–F7 / N1–N5 is at risk.
3. Update the ledger `reports/STATUS.md`.

Three rules carry most of the value, and all three are violated by default:

- **Numbers, not adjectives.** Every criterion in `TESTING.md` is quantified;
  report the measured value against the threshold. "fps was low" is not a
  report; "fps fell from 29.8 to 11.2 after 4m10s" is.
- **A flaky test is not a pass.** Four passes out of five is `PARTIAL`, recorded
  as intermittent. On race day the fifth run is the one that counts.
- **Reports are append-only.** When something is fixed, update the `Status:`
  field and add a `## Resolution` section — do not delete the report. The record
  of what went wrong outlives the bug.

Failures are expected during a build; unrecorded failures are what make the
system unreliable on the water.

---

## 13. Out of scope (possible future work)

- Automatic bow-number recognition (OCR on the saved frames).
- Automatic crossing detection by motion analysis, removing the operator.
- Multiple simultaneous cameras (multi-lane finishes).
- Integration with regatta management software result formats.
- OCR of the calibration counter, to automate §5.5 steps 4–5.
- Network sync to a second laptop for redundancy.
- Photo-finish style line-scan imaging (a fundamentally different, more accurate
  technique — a single-pixel column sampled at high rate — worth considering if
  the tolerance ever tightens below ~50 ms).

---

## Appendix A: Alternative iOS apps

If IP Camera Lite proves unsuitable, these are the viable alternatives. All must
be **servers**, per §3.1.

| App | Protocols | Manual shutter? | Notes |
|---|---|---|---|
| IP Camera Lite | HTTP/MJPEG, RTSP, ONVIF | **Unverified — check per §9.2 step 6** | **Recommended.** Proven with `iproxy`. Free version watermarks. |
| SimpleIPCamera | HTTP/MJPEG | Unverified | Free, minimal, no support. Good fallback. |
| OctoStream RTSP Server | RTSP | Unverified | Free. RTSP only — accept the H.264 latency penalty. |
| RTSP Stream (Krusade) | RTSP | Unverified | Free. RTSP only. |

The "manual shutter" column is unfilled on purpose: none of these was checked
against §10.1's 1/500 s requirement, and AE *lock* is not the same capability as
shutter *selection*. Fill it in during §9.2 and treat a "no" as disqualifying
for protest photography.

Apps that **cannot** be used because they push rather than serve: Larix
Broadcaster, Camo, NDI HX Camera, EpocCam, iVCam, and any RTMP/SRT streamer.

## Appendix B: RTSP fallback

If MJPEG is unavailable, RTSP can be made to work but **must** be forced to TCP:

```
ffmpeg -rtsp_transport tcp -fflags nobuffer -flags low_delay \
       -i rtsp://admin:admin@127.0.0.1:8554/live ...
```

Or with GStreamer:

```
rtspsrc location=rtsp://... protocols=tcp latency=0 drop-on-latency=true
  ! rtph264depay ! h264parse ! avdec_h264
  ! appsink sync=false max-buffers=1 drop=true
```

Expect ~200 ms additional latency and, more importantly, latency that *varies*
with scene motion. Re-run calibration and widen the saved frame window if this
path is used.

## Appendix C: Reference implementation of the MJPEG parse loop

> **Replaced in v1.2.** The v1.0 loop was correct on the happy path and on
> several adversarial cases (boundaries spanning chunk edges, boundary bytes
> inside the JPEG payload, whitespace in `Content-Length`), but it had four
> input-dependent failure modes. The code below fixes all four and has been
> executed against each case; see the table after it.

```python
import re, time

MAX_BUF = 4 * 1024 * 1024
_LEN_RE = re.compile(rb"content-length:\s*(\d+)", re.I)

class StreamError(Exception): pass

def parse_boundary(ctype: str) -> bytes:
    if "boundary=" not in ctype:
        raise StreamError(f"no boundary in Content-Type: {ctype!r}")
    tok = ctype.split("boundary=", 1)[1].strip().strip('"')
    return b"--" + tok.lstrip("-").encode("latin-1")   # <-- F8 case 2

def find_headers_end(buf, start):
    """Return (end_of_headers_index, body_start). Accepts CRLFCRLF and LFLF."""
    a = buf.find(b"\r\n\r\n", start)
    b = buf.find(b"\n\n", start)
    if a < 0 and b < 0: return (-1, -1)
    if a < 0 or (0 <= b < a): return (b, b + 2)        # <-- F8 case 3
    return (a, a + 4)

def jpeg_end(buf, start):
    """Walk JPEG marker segments; return index just past EOI, or -1.
    Never mistakes an EXIF thumbnail's EOI for the frame's."""
    i = start
    n = len(buf)
    if n - i < 2 or buf[i] != 0xFF or buf[i+1] != 0xD8:
        return -1                                       # not SOI
    i += 2
    while i + 1 < n:
        if buf[i] != 0xFF:
            i += 1; continue
        m = buf[i+1]
        if m == 0xFF:
            i += 1; continue
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2; continue
        if m == 0xD9:
            return i + 2
        if i + 3 >= n: return -1
        seglen = (buf[i+2] << 8) | buf[i+3]
        if seglen < 2: return -1
        if m == 0xDA:                                   # start of scan
            j = i + 2 + seglen
            while j + 1 < n:                            # entropy data is stuffed:
                if buf[j] == 0xFF and buf[j+1] == 0xD9: # a real FFD9 ends the frame
                    return j + 2
                if buf[j] == 0xFF and buf[j+1] != 0x00 and not (0xD0 <= buf[j+1] <= 0xD7):
                    i = j; break
                j += 1
            else:
                return -1
            continue
        i += 2 + seglen                                 # <-- F8 case 1: skips APP1/EXIF
    return -1

def feed(buf, boundary, on_frame, seq, require_len=True):
    """Consume complete parts from `buf` in place. Returns updated seq."""
    while True:
        start = buf.find(boundary)
        if start < 0:
            if len(buf) > MAX_BUF: raise StreamError("accumulator overflow")
            return seq
        hdr_end, body = find_headers_end(buf, start)
        if hdr_end < 0:
            if len(buf) > MAX_BUF: raise StreamError("accumulator overflow")
            return seq
        m = _LEN_RE.search(bytes(buf[start:hdr_end]))
        if m:
            end = body + int(m.group(1))
            if len(buf) < end: return seq
        else:
            if require_len: raise StreamError("part has no Content-Length")
            end = jpeg_end(buf, body)
            if end < 0:
                if len(buf) > MAX_BUF: raise StreamError("accumulator overflow")
                return seq
        t_recv = time.monotonic()          # <-- the critical line, unchanged
        t_wall = time.time()
        jpeg = bytes(buf[body:end])
        del buf[:end]
        seq += 1
        on_frame(t_recv, t_wall, seq, jpeg)
```

Driving it, with the reconnect discipline §6.2 step 8 requires — note that
**every** exception reaches the backoff, including those raised while reading
the response headers:

```python
def read_mjpeg_forever(url, auth, on_frame, stop):
    backoff, seq = 0.5, 0
    while not stop.is_set():
        try:
            with requests.get(url, auth=auth, stream=True, timeout=(5, 10)) as r:
                r.raise_for_status()
                boundary = parse_boundary(r.headers.get("Content-Type", ""))
                buf, raw = bytearray(), r.raw
                backoff = 0.5
                while not stop.is_set():
                    chunk = raw.read(8192)
                    if not chunk:
                        break
                    buf += chunk
                    seq = feed(buf, boundary, on_frame, seq)   # seq persists
        except Exception as exc:
            log.warning("stream_reconnect", exc_info=exc, extra={"seq": seq})
        time.sleep(backoff)
        backoff = min(backoff * 2, 2.0)
```

`seq` lives outside the retry loop (§6.2). The `time.monotonic()` call inside
`feed` keeps v1.0's placement exactly: taken the instant a complete JPEG is
known to be present, before slicing, copying or dispatching. Moving it later
introduces variable error.

### C.1 Cases exercised

| Input | v1.0 behaviour | Corrected behaviour |
|---|---|---|
| Normal stream with `Content-Length`, 7-byte chunks | correct | correct |
| **No `Content-Length`, JPEG carries an EXIF thumbnail** | **truncates a 274-byte frame to the 61-byte thumbnail — and the result still begins `FFD8` and ends `FFD9`, so it looks valid** | full frame recovered |
| `boundary=--token` (dashes in the declaration) | never matches; buffer grows forever | correct |
| Bare-LF part headers | never matches; buffer grows forever | correct |
| `Content-Length: 274 bytes` | `ValueError`, thread dies | correct |
| `Content-Type` with no `boundary=` | `IndexError`, thread dies at connect | clean `StreamError` → reconnect |
| 5 MB of garbage, no boundary ever | unbounded growth → OOM | `StreamError` at 4 MB → reconnect |
| Boundary bytes inside the JPEG payload | correct | correct |

The EXIF-thumbnail row is the dangerous one, because it is **silent**: fps is
normal, memory is flat, and `TESTING.md` T2 and T8 both pass. It surfaces only
when a jury opens the photo of a protested finish and finds a 160×120
thumbnail. This is also why §6.2's "do not validate JPEG integrity on the hot
path" must not be read as licence to parse loosely — a cheap SOI/EOI check
would have passed this frame too.
