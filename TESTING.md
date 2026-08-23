# TESTING — Regatta Finish-Line Timer

**Companion documents:** `regatta-finish-timer-spec.md` (v1.2), `INSTALL.md`,
`system-environment.md`.

Runtime paths below use **`$DATA_ROOT`**, default `~/regatta-data` — the single
source of truth settled in spec §6.7. It is *not* `~/regatta/`, which holds the
source tree, the venv and `reports/`.

This document defines (a) how to prove each stage of the build actually works,
and (b) **the exact files to write when something does not work**. The reporting
protocol in §2 is mandatory: a test that fails and is only mentioned in
conversation is a test that has not been reported.

Tests are numbered `T0`–`T10` and map one-to-one onto the implementation-order
gates in spec §12. Do not begin stage N+1 until stage N's test passes or its
failure is recorded per §2 and explicitly accepted.

---

## 1. Conventions

```
~/regatta/
    reports/
        STATUS.md                     # running ledger, one row per test — always current
        FAIL-T4-20260821T1930.md      # one file per failure, never overwritten
        diag-T4-20260821T1930.tar.gz  # diagnostics bundle referenced by that report
```

- Reports live in the **source** tree (`~/regatta/reports/`), not the data root.
  The data root is disposable; failure history is not.
- Timestamps are `date -u +%Y%m%dT%H%M`.
- Failure files are **append-only**. Fixed a problem? Do not edit the report to
  say "resolved" and move on — update its `Status:` field, add a
  `## Resolution` section at the bottom, and update `STATUS.md`. The record of
  what went wrong is worth more than a tidy directory.

### 1.1 The ledger

Create `reports/STATUS.md` at the start of the build and keep it current. It is
the first thing anyone should read.

```markdown
# Test status

Last updated: 2026-08-21T19:30Z

| Test | Stage (spec §12) | Last run | Result | Report |
|------|------------------|----------|--------|--------|
| T0 | Environment          | 2026-08-21T19:02Z | PASS | — |
| T1 | Transport smoke      | 2026-08-21T19:14Z | PASS | — |
| T2 | MJPEGReader          | 2026-08-21T19:30Z | FAIL | [FAIL-T2-20260821T1930.md](FAIL-T2-20260821T1930.md) |
| T3 | FrameBuffer          | —                 | not run | — |
...

Blockers: T2 (fps sags to 11 after ~4 min).
```

`Result` is one of `PASS`, `FAIL`, `PARTIAL`, `not run`, `blocked`. Never leave
a test at `not run` without a line under Blockers explaining why.

---

## 2. Failure reporting protocol

**When any test below fails, do all three of these before continuing.**

1. Collect diagnostics: `./collect-diagnostics.sh T4` (§7).
2. Write `reports/FAIL-<test>-<timestamp>.md` using the template in §2.1.
3. Update the row and the Blockers line in `reports/STATUS.md`.

Then continue with any *independent* work. A failure in T5 (calibration) does
not block T6 (controller/storage); a failure in T2 (ingest) blocks nearly
everything downstream. Say which case it is in the report's `Blocks:` field.

### 2.1 Report template

Copy this verbatim. Fields marked **required** must be filled; write
`unknown` rather than deleting a field.

````markdown
# FAIL — T<n> <short title>

- **Report id:** FAIL-T<n>-<YYYYMMDDTHHMM>          (required)
- **Date (UTC):** <date -u -Is>                      (required)
- **Test:** T<n>, <name>                             (required)
- **Spec reference:** §<section(s)> the test exists to prove   (required)
- **Severity:** blocker | major | minor              (required)
- **Blocks:** <test ids that cannot now run, or "nothing">     (required)
- **Reproducible:** always | intermittent (<n> of <m> runs) | once
- **Diagnostics:** diag-T<n>-<timestamp>.tar.gz
- **Status:** open | worked-around | resolved

## Expected

<The pass criterion from this document, quoted, with its number.>

## Observed

<What actually happened, with the measured number. "Slow" is not an
observation; "fps fell from 29.8 to 11.2 after 4m10s" is.>

## Verbatim output

```
<Paste the actual console output / traceback. Do not paraphrase, do not
truncate the middle of a traceback. If it is longer than ~50 lines, put the
whole thing in the diagnostics bundle and paste the last 30 here.>
```

## Reproduction

```bash
<The exact commands, from a fresh shell, that produce this.>
```

## Environment delta

<Anything that differs from `~/regatta/environment-lock.txt` (INSTALL.md §6).
Run: diff <(~/regatta/venv/bin/python -m pip freeze) ~/regatta/environment-lock.txt
If nothing differs, write "none".>

## Hypothesis

<What you think is happening and why. If you have no hypothesis, write
"none — needs investigation". A wrong hypothesis clearly labelled is useful;
a confident wrong hypothesis stated as fact is not.>

## Attempted

<What you already tried, and what each attempt did. Include things that did
not help — that is half the value of the report.>

## Impact on the race requirement

<Which of N1–N5 / F1–F7 is at risk, and whether the system is still usable
for a regatta in a degraded mode. Spec §6.5 is the model here: a missing
photo is recoverable, a missing time is not.>
````

### 2.2 Rules that matter

- **Never report a timing failure without numbers.** Every criterion in §3–§6
  is quantified. Report the measured value against the threshold.
- **Never mark a flaky test PASS.** If it passed 4 times out of 5, that is
  `PARTIAL` with `Reproducible: intermittent (1 of 5 runs)`. On race day the
  fifth run is the one that counts.
- **Distinguish "not implemented" from "broken".** A test that cannot run
  because the module does not exist yet is `not run`, not `FAIL`.
- **If a test cannot be run at all** — no iPhone available, no graphical
  session — record it as `blocked` with the reason. Do not silently skip it.
  **§8's table is the single source of truth** for which tests need what; v1.1
  also carried a prose list here and the two disagreed (it omitted T2 and T7
  from the phone list and T5 from the desktop list).

---

## 3. Bench tests (no iPhone required)

### T0 — Environment

**Proves:** spec §12 step 0. **Procedure:** run the checklist script in
`INSTALL.md` §8.
**Pass:** every line prints `OK` and the script prints `ENVIRONMENT READY`.
**On failure:** the checklist names the failing item; `INSTALL.md` §10 lists the
fix for each. Report only if §10's fix does not resolve it.

### T3 — FrameBuffer

**Proves:** spec §6.3. Pure unit test, no hardware.

```bash
~/regatta/venv/bin/python -m pytest tests/test_framebuffer.py -v
```

Must cover, with synthetic monotonically increasing `t_recv`:

| Case | Expected |
|---|---|
| `nearest()` with target between two frames | the closer one; ties resolve deterministically |
| `nearest()` on an empty buffer | returns `None`, does not raise |
| `nearest()` with target before the oldest frame | returns the oldest |
| `nearest()` with target after the newest frame | returns the newest |
| `window(t, 0.5, 0.5)` mid-buffer | every frame within ±500 ms of `t`, in time order |
| `window()` near the start of the buffer | truncated, not padded, no exception |
| `window()` when the span contains no frames | empty list, no exception |
| `window()` covered span is fps-independent | same time span returned at 30 and 60 fps |
| `span()` on empty | `None` |
| ring eviction | length caps at `int(seconds * fps * 1.5)`; oldest evicted first |
| concurrent append + nearest | 10 000 appends on one thread, continuous `nearest()` on another: no exception, no torn read |

**Pass:** all tests green, including the concurrency case run at least 5 times.

### T6 — CaptureController + storage (headless)

**Proves:** spec §6.5, §6.7, requirement N4. Drive it with a synthetic frame
source — do not require the phone.

Every row of the spec §6.5 edge-case table must be exercised explicitly:

| Case | Expected | Requirement |
|---|---|---|
| Trigger with no race started | rejected, UI warning, **no DB row** | §6.5 |
| Normal crossing | one `capture` row; `elapsed_s == t_press - t0` to 1 µs; `capture_frame` rows spanning **`window_before_ms` before to `window_after_ms` after** the target, with at least one frame on each side | F1, F2 |
| Window extraction is deferred | the capture row exists immediately; frames are attached ~`window_after_ms` later (spec §6.5) | N2 |
| Soft delete, then a new capture | the new row's `sequence` **does not collide** with the deleted one | F5 |
| Debounced press | row written with `debounce_suspect = 1`, not dropped | §6.4 |
| Buffer empty (stream down) | **row written**, `primary_image IS NULL`, `image_flag='missing'` | §6.5 — the most important row in the table |
| `target` older than buffer span | row written, oldest frame attached, `image_flag='approximate'` | §6.5 |
| `target` newer than newest frame | row written, newest frame attached, `image_flag='approximate'` | §6.5 |
| Soft delete | `deleted=1`; other rows' `sequence` values **unchanged** | F5 |
| Bow number added after the fact | `bow_number` updated, nothing else touched | F4 |

**Crash-recovery sub-test (N4), run it separately and for real:**

```bash
# Start the headless controller, record 5 captures, then:
kill -9 <pid>
# Restart and inspect ($DATA_ROOT defaults to ~/regatta-data):
sqlite3 "$DATA_ROOT/regatta.db" \
  "PRAGMA integrity_check; SELECT count(*), max(sequence) FROM capture WHERE deleted=0;"
```

`sqlite3` is the **CLI**, which Ubuntu's base system does not ship — it is added
to `INSTALL.md` §2 for exactly this test. If you would rather not depend on it,
the venv's stdlib module is always available:

```bash
~/regatta/venv/bin/python -c "
import sqlite3, os
db = sqlite3.connect(os.path.expanduser(os.environ.get('DATA_ROOT','~/regatta-data') + '/regatta.db'))
print(db.execute('PRAGMA integrity_check').fetchone())
print(db.execute('SELECT count(*), max(sequence) FROM capture WHERE deleted=0').fetchone())
"
```

**Pass:** `integrity_check` returns `ok`, and all 5 captures are present with
correct elapsed times. `SIGKILL`, not `SIGTERM` — a clean shutdown proves
nothing about N4.

### T9 — Export

**Proves:** F6, spec §6.8.
**Pass:** CSV has exactly the columns listed in §6.8, in that order;
`elapsed_formatted` renders as `M:SS.mmm` with three decimals; soft-deleted rows
are excluded; the file opens in a spreadsheet with no quoting damage to a
bow number like `07`.

---

## 4. Tests requiring the iPhone

### T1 — Transport smoke

**Proves:** spec §12 step 1. **Procedure:** `INSTALL.md` §5.
**Pass:** `ffplay` shows live video with visually low latency (wave a hand). Also
record the measured throughput from `INSTALL.md` §5 — audit open item 2 — and
write the 1080p30-versus-720p60 decision into the report or into
`environment-lock.txt`. This number is needed by §10.2 and it is easy to forget.

### T2 — MJPEGReader, 10-minute soak

**Proves:** spec §6.2, §12 step 2. Print `seq`, `t_recv`, byte count per frame;
sample RSS every 10 s.

```bash
~/regatta/venv/bin/python -m regatta_timer.tools.ingest_soak --minutes 10 \
  | tee reports/logs/T2-$(date -u +%Y%m%dT%H%M).log
```

**Pass — all four:**

| Criterion | Threshold |
|---|---|
| Mean fps over the run | within ±5% of nominal (≥28.5 for 30 fps) |
| fps drift, first minute vs last minute | < 5% |
| Frame interval outliers (gap > 3× mean) | < 0.1% of frames |
| RSS growth over 10 minutes | < 50 MB, and flat in the last 5 minutes |

A sagging fps here is often the CPU governor, not the code — check
`grep MHz /proc/cpuinfo` during the run and see `INSTALL.md` §7 before writing
the report.

### T4 — TriggerListener clock domain and trigger discipline

**Proves:** spec §6.4, §12 step 4, and the "clock domain mixed up" row in §11.
**Procedure:** the `EVIOCSCLOCKID` ioctl snippet in `INSTALL.md` §4, or the real
module. Do **not** use `set_clock_id()` — it does not exist in python-evdev and
raises `AttributeError`; that was the defect that made v1.1's version of this
test unable to run at all.

**Pass — all five:**

| Criterion | Threshold |
|---|---|
| `\|time.monotonic() - event.timestamp()\|` over 20 presses | **< 5 ms** |
| Auto-repeat (`value == 2`) and release (`value == 0`) | ignored |
| Two presses 100 ms apart | **two rows in the database**, the second carrying `debounce_suspect = 1` |
| A space typed into a bow-number field | **no capture is created** (spec §6.4: the trigger keycode must not be `KEY_SPACE`) |
| Trigger device is also the active keyboard | startup warns and refuses to start a race without an override |

A skew of ~1.8×10¹² ms means the ioctl did not take and you are comparing
`CLOCK_REALTIME` against `CLOCK_MONOTONIC`. That is a **blocker**: elapsed times
stay correct while every selected photo is ~56 years stale, so nothing else in
the suite will catch it.

The third row is the one v1.1 got wrong. It asserted the debounce rejection was
"surfaced"; spec v1.2 §6.4 requires the suspect press to be **recorded as a
capture row**, because two boats 100 ms apart is an ordinary close finish and a
log line is not a recoverable result.

### T5 — Calibration stability

**Proves:** spec §5.5, §12 step 5.
**Procedure:** run the calibration routine three times, at close range, **with
the exact lens, resolution and frame rate the race will use** (spec §5.5
constraint 1 — calibrating on a different capture format invalidates the result).
**Pass — both:**

| Criterion | Threshold |
|---|---|
| The three median `L` values agree with each other | within **±20 ms** |
| The interquartile range of the 20 raw `L` values *within* a single run | **< 30 ms** |

The second criterion is not optional padding. A median is stable across runs
even when per-frame latency swings by ±150 ms, so v1.1's median-only criterion
was satisfiable by a stream with catastrophic jitter — and per-frame jitter is
precisely the quantity N2 depends on. Spec §5.5 step 8 requires the IQR be
recorded in `calibration.json` for this reason.

Run the calibration against a **high-entropy background**, per spec §5.5
constraint 4. A black screen with white numerals produces a 20–40 KB JPEG where
the race produces 150–250 KB, and on a bandwidth-limited tunnel that
underestimates `L` by 40–80 ms — a systematic error no test in this document can
otherwise see, because T5 and T10 both film the same screen.

Read spec §5.5 constraint 3 before reporting a failure here: ±20 ms is the
hardware floor imposed by the 60 Hz panel and the phone's rolling shutter. A
±20 ms spread is the expected result, not a defect. Report only a spread
materially worse than that — and include all three medians and all 60 raw
values in the bundle.

### T8 — ArchiveWriter, 10-minute soak

**Proves:** F7, spec §6.6, §12 step 8. Run with the full pipeline live.
**Pass — all three:**

| Criterion | Threshold |
|---|---|
| Application dropped-frame counter (queue full) | **0** over 10 minutes |
| Trigger-path latency (evdev timestamp → UI row emitted), p99 | < 20 ms |
| `index.jsonl` `seq` values | strictly increasing, **no gaps**; every referenced file exists |

The second criterion is the point of the test. Spec §6.5 forbids blocking the
trigger path on disk I/O; this is where that claim gets measured. Note that
v1.2 moved the SQLite commit *off* the trigger path precisely because
`synchronous=FULL` fsyncs on every commit while the archive is writing
4.5–7.5 MB/s to the same disk — so measure to the point where the trigger
thread is free, not to the commit. Report p50 and p99, not just pass/fail.

The third criterion changed because v1.1's version was **self-referential**:
`index.jsonl` is written one line per frame written, so "line count equals
frames written" is true by construction. Checking `seq` for gaps instead tests
something real — frames lost in the usbmuxd tunnel, which the application's own
dropped-frame counter cannot observe. It also catches the v1.0 bug where `seq`
reset to 1 on every reconnect (spec §6.2).

### T7 — UI

**Proves:** F3, F4, F5, spec §7. Needs a desktop session.
**Pass:** preview holds ~10 fps with ingest at 30 fps; every capture appears in
the list with its thumbnail and time; the review panel shows all 31 window
frames labelled with their millisecond offsets; a window frame can be promoted
to primary; inline bow-number editing survives an auto-scroll (spec §7.3 —
auto-scroll must not steal focus); no modal dialog can appear while a race is
active (spec §7.5).

Also check CPU: with the preview running, the application should not saturate
both cores. If it does, confirm `QImageReader.setScaledSize()` is actually being
used (spec §7.2) — that is the single fix for this on an i7-6600U.

---

## 5. T10 — Acceptance test

**This test proves N2. It does not prove N1** — see the note after the pass
criterion. Spec §12.1. Nothing else substitutes for it; a system that passes
T0–T9 and fails T10 does not work.

1. Point the camera at the laptop screen showing the millisecond counter.
2. Start a race.
3. Press the trigger 10 times at irregular intervals.
4. For each capture, read the counter value visible in the saved primary image
   and compare it to `t0 + elapsed`.

**Pass:** all 10 discrepancies within **±100 ms**, median within **±50 ms**.

Record all 10 discrepancies in the report — pass or fail. On a pass they are the
baseline that any later regression is measured against, so put the table in
`reports/STATUS.md` too:

```
| # | recorded elapsed | counter in image | discrepancy |
|---|------------------|------------------|-------------|
| 1 | 0:12.483         | ...              | +23 ms      |
```

**What this test does and does not prove.** `elapsed` is *defined* as
`t_press - t0`, so `t0 + elapsed ≡ t_press` identically and step 4 reduces to
comparing `T_shown` against `t_press`. That is a statement about frame
*selection* — N2. Any error in the recorded time cancels from both sides.
v1.1 claimed this test proved N1 as well; it cannot, because the system has no
ground-truth external event to compare against. Spec §12.1 states what is being
assumed instead. Measuring N1 properly needs an external reference: an LED
flash visible to the camera and wired to a second input channel.

**Diagnosing a failure:**

| Symptom | Cause | Action |
|---|---|---|
| Systematic offset ≈ −2·`L`, all ten the same sign | The `Δ` **formula** is wrong — v1.0's `Δ = latency + reaction` instead of spec v1.2 §5.4's `Δ = reaction − latency` | Fix the formula. Re-running T5 will **not** help: it re-measures `L` perfectly and changes nothing |
| Systematic offset, smaller, one sign | `Δ` measured against the wrong capture format or a low-entropy scene | Re-run T5 per spec §5.5 constraints 1 and 4 |
| Large scatter, no consistent sign | Variable transport latency | Confirm MJPEG and not the RTSP fallback (spec §3.2); check T5's IQR criterion |

v1.1's guidance here said a systematic offset means "re-run T5 rather than
editing anything in the timing path". That was actively misleading: the most
likely cause of a systematic offset was a sign error in the formula itself, and
re-running T5 would have confirmed a perfectly good `L` while the images stayed
300–600 ms stale.

---

### T11 — Offline operation (N3)

**Proves:** N3, which v1.1 never tested.
**Procedure:** disconnect Ethernet, `nmcli radio wifi off`, `timedatectl set-ntp
false`, then run T10 end to end.
**Pass:** T10 passes with no network, and no component logs a failed outbound
connection. Check the §6.9 log for DNS or HTTP attempts to anything other than
`127.0.0.1`.

### T12 — Cold start to ready (N5)

**Proves:** N5 — laptop open, phone in hand, to ready-to-time in **under 5
minutes**. Also never tested in v1.1.
**Procedure:** stopwatch the whole of spec §9.3 from a cold laptop.
**Pass:** under 5 minutes to the first valid trigger.

**Expect this to fail as spec'd, and read the failure carefully.** §9.3 is
thirteen steps including mounting a tripod, aligning the overlay and — in v1.0 —
running the calibration routine, which per spec §5.5 constraint 2 is
*sequential* on a single display: full-screen counter → capture 20 frames →
leave full screen → the operator reads and types 20 values one frame at a time.
That step alone exceeds five minutes.

Spec v1.2 §9.3 step 8 resolves this by making calibration a per-**session**
step rather than a per-race one — nothing about `Δ` changes between heats unless
the capture format does. If T12 still fails after that, the remaining options
are to add OCR of the calibration counter (spec §13 lists it as future work) or
to renegotiate N5. Heats run to a schedule; a 20-minute setup between events is
a race lost, so this is a real requirement, not a nicety.

## 6. Pre-regatta dress rehearsal

Run once, on the day before, in full field configuration — tripod, 50 m,
daylight, the real trigger device.

1. `INSTALL.md` §7 race-day prep.
2. T0, T1, T5, T10 in that order.
3. A 20-minute continuous run with archiving on, someone walking through frame
   at intervals, and at least 15 triggers.
4. Mid-run, **unplug the USB cable for 5 seconds and plug it back in.** Expected
   per spec §6.1 and §11: `iproxy` auto-restarts, the indicator goes red within
   2 s and recovers, no modal dialog appears, and any triggers pressed during
   the outage are recorded as times with `image_flag='missing'`. Losing images
   is acceptable; losing a time is not.
5. Export the CSV and check free space against the archive burn rate.

Any failure at step 4 is a blocker for race day regardless of how T0–T10 went.

---

## 7. Diagnostics bundle

Save as `~/regatta/collect-diagnostics.sh`, `chmod +x`.

```bash
#!/usr/bin/env bash
# collect-diagnostics.sh <test-id>   e.g. ./collect-diagnostics.sh T4
#
# Deliberately NOT `set -u` / `set -e`. This script's job is to gather whatever
# it can from a broken machine; aborting on a missing variable is the opposite
# of that. v1.1 used `set -u` and referenced $XDG_SESSION_TYPE unguarded, so
# over SSH it died mid-collection, wrote no tarball, and left the operator with
# nothing — while the failure message went into the redirected file rather than
# the terminal. T1, T2 and T8 are all marked "needs desktop: no", so collecting
# after an SSH soak is the expected workflow, not an edge case.
T="${1:-unknown}"
TS=$(date -u +%Y%m%dT%H%M)
D=$(mktemp -d)
R=~/regatta/reports
DATA_ROOT="${DATA_ROOT:-$HOME/regatta-data}"
mkdir -p "$R"
trap 'rm -rf "$D"' EXIT

{
  echo "=== when ==="; date -u -Is; uptime
  echo "=== machine ==="; hostnamectl; uname -a
  echo "=== python ==="; python3 -VV; ~/regatta/venv/bin/python -VV 2>/dev/null
  echo "=== venv packages ==="; ~/regatta/venv/bin/python -m pip freeze 2>/dev/null
  echo "=== drift from lock ==="
  diff <(~/regatta/venv/bin/python -m pip freeze 2>/dev/null) \
       ~/regatta/environment-lock.txt 2>/dev/null || echo "(lock file missing or differs, above)"
  echo "=== apt (relevant) ==="; dpkg -l | grep -E 'usbmuxd|imobiledevice|xcb-cursor|python3-(dev|venv)'
  echo "=== holds ==="; apt-mark showhold
  echo "=== session ==="
  echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-unset} DISPLAY=${DISPLAY:-unset}"
  echo "=== cpu ==="; grep -E 'model name|MHz' /proc/cpuinfo
  cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null
  echo "=== memory ==="; free -m
  echo "=== disk ==="; df -h /home
  echo "=== usb ==="; lsusb 2>/dev/null
  udid=$(idevice_id -l 2>&1); echo "idevice_id -l -> '${udid:-<empty: no device>}'"
  echo "=== input ==="; id -nG; ls -l /dev/input/event* | head -20
  ~/regatta/venv/bin/python -c \
    "import evdev;[print(d.path,d.name) for d in map(evdev.InputDevice,evdev.list_devices())]" 2>&1
  echo "=== processes ==="; ps -eo pid,pcpu,pmem,rss,etime,comm --sort=-pcpu | head -15
  echo "=== usbmuxd unit ==="; systemctl status usbmuxd --no-pager 2>&1 | head -20
} > "$D/environment.txt" 2>&1

echo "data_root=$DATA_ROOT" > "$D/paths.txt"
cp -r "$DATA_ROOT/logs"             "$D/logs"     2>/dev/null
cp    "$DATA_ROOT/config.toml"      "$D/"         2>/dev/null
cp    "$DATA_ROOT/calibration.json" "$D/"         2>/dev/null
cp -r ~/regatta/reports/logs        "$D/testlogs" 2>/dev/null
journalctl -b --since "30 min ago" > "$D/journal.txt" 2>/dev/null

# Prefer the venv's stdlib sqlite3 over the CLI: it is always present.
~/regatta/venv/bin/python - "$DATA_ROOT/regatta.db" > "$D/db-tail.txt" 2>&1 <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
print(db.execute("PRAGMA integrity_check").fetchone())
for row in db.execute("SELECT id,sequence,elapsed_s,image_flag,debounce_suspect,deleted "
                      "FROM capture ORDER BY id DESC LIMIT 30"):
    print(row)
PY

tar czf "$R/diag-$T-$TS.tar.gz" -C "$D" .
echo "wrote $R/diag-$T-$TS.tar.gz"
echo "now write $R/FAIL-$T-$TS.md using the template in TESTING.md §2.1"
```

**Do not put race photographs in the bundle** unless the failure is about image
content — they are large and they are the one thing a jury may need intact.
Reference the paths instead.

---

## 8. Quick reference

This table is the **single source of truth** for what each test needs.

| Test | Needs phone | Needs desktop | Proves | Blocks next stage | Blocks race day |
|------|-------------|---------------|--------|-------------------|-----------------|
| T0 Environment      | no  | no  | §12 step 0   | yes | yes |
| T1 Transport smoke  | yes | no  | §12.1        | yes | yes |
| T2 MJPEGReader      | yes | no  | §6.2         | yes | yes |
| T3 FrameBuffer      | no  | no  | §6.3         | yes | yes |
| T4 Trigger/clock    | no  | no  | §6.4         | yes | yes |
| T5 Calibration      | yes | yes | §5.5, N2     | yes | yes |
| T6 Controller/DB    | no  | no  | §6.5, N4     | yes | yes |
| T7 UI               | yes | yes | **F3–F5**    | no  | **yes** |
| T8 Archive soak     | yes | no  | F7           | no  | **yes** |
| T9 Export           | no  | no  | F6           | no  | yes |
| T10 Acceptance      | yes | yes | **N2**       | yes | yes |
| T11 Offline         | yes | yes | N3           | no  | yes |
| T12 Cold start      | yes | yes | N5           | no  | yes |
| DR Dress rehearsal  | yes | yes | end-to-end   | n/a | **yes** |

v1.1 had a single "Blocker if failed" column and marked T7 "no (degraded)" —
but T7 proves F3, which spec §7.3 calls *"the primary deliverable of the whole
system"*, plus F4 and F5. Splitting the column keeps the build-order meaning
("you can keep coding") without pre-authorising a regatta with no working
capture list. Note T4 no longer claims to prove N1: see T10.

**Requirement coverage.** F1→T6, F2→T6, F3→T7, F4→T6/T7, F5→T6/T7, F6→T9,
F7→T8, N1→*not directly testable, see T10*, N2→T5/T10, N3→T11, N4→T6, N5→T12.
