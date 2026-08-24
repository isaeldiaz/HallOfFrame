# INSTALL — Regatta Finish-Line Timer

**Target machine:** `borodin` — Lenovo ThinkPad T460s, Ubuntu development branch
(XFCE, X11), Python 3.14.6, kernel 7.0.
**Companion documents:** `regatta-finish-timer-spec.md` (v1.1) and
`system-environment.md` (audit, 2026-08-21).
**Authority:** this file supersedes the summary command block in spec §9.1.

Work through §1–§6 once. Re-run §7 before every regatta. §8 is the gate that
spec §12 step 0 refers to — do not start writing code until it passes clean.

Every `sudo` command needs an interactive password (the audit confirms there is
no passwordless sudo), and §3 requires a logout/login. This procedure therefore
cannot be run unattended.

---

## 0. Why this document exists

The v1.0 spec's install block was written before anyone looked at the machine.
On this hardware it fails in four places:

| v1.0 said | Reality |
|---|---|
| `pip install evdev` | evdev ships **sdist only** — no wheels for any Python version. It compiles a C extension, so it needs `python3-dev` and `build-essential`, neither of which was listed. |
| `pip install PySide6` | Works on Python 3.14 (good news, see §4), but Qt ≥ 6.5's `xcb` platform plugin needs **`libxcb-cursor0`** at runtime and the wheel does not bundle it. Without it the GUI never opens. |
| `libimobiledevice6` | Not a package on this release. The runtime library is `libimobiledevice-1.0-N`; naming it by hand aborts the whole `apt install`. |
| (nothing) | `sqlite3` — the CLI, not the stdlib module — is required by `TESTING.md` T6 (the only test of N4) and by `collect-diagnostics.sh`. Ubuntu ships `libsqlite3-0` in the base system but not the CLI. |

---

## 1. Preconditions

```bash
# Confirm you are on the right machine and the audit still holds.
hostnamectl | sed -n '1p;/Operating System/p;/Kernel/p'
python3 -VV                      # expect 3.14.x
echo "$XDG_SESSION_TYPE"         # expect: x11
df -h /home                      # expect >100 GB free
```

You need working internet for §2 and §4. Everything after that is offline-capable
— see §9 if the machine will be rebuilt without a network.

---

## 2. System packages

```bash
sudo apt update
sudo apt install -y \
    usbmuxd \
    libimobiledevice-utils \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    libxcb-cursor0 \
    usbutils \
    sqlite3 \
    ffmpeg
```

What each one is for, so nothing here is cargo cult:

| Package | Needed by | Why |
|---|---|---|
| `usbmuxd` | spec §3.1 | The USB multiplexing daemon; the entire transport. Already installed (1.1.1-7). Its systemd unit shows **inactive until a device is attached** — that is normal, not a fault. |
| `libimobiledevice-utils` | §6.1, §9.3 | Provides `iproxy` and `idevice_id`. Apt resolves the correct `libimobiledevice-1.0-N` runtime dependency itself — **do not name that library manually**. |
| `python3-pip` | §9.1 | The audit found no `pip`, no `pip3`, and no `ensurepip`. |
| `python3-venv` | §9.1 | Provides `ensurepip`, without which `python3 -m venv` fails. Pulls `python3.14-venv`. |
| `python3-dev` | §6.4 | Python 3.14 C headers (`Python.h`), to compile `evdev`. Pulls `python3.14-dev`. |
| `build-essential` | §6.4 | `gcc` and `make`, plus `libc6-dev` → `linux-libc-dev`, which provides `/usr/include/linux/input.h` and `input-event-codes.h`. evdev's build **parses those headers** to generate its `ecodes` module; without them the build fails even with a compiler present. |
| `libxcb-cursor0` | §7 | Runtime dependency of Qt 6.5+'s `xcb` platform plugin. Not bundled in the PySide6 wheel. |
| `usbutils` | audit item 3 | Provides `lsusb`. This is why the audit's `lsusb` printed nothing — the tool was simply absent. The runbook relies on `idevice_id`, so this is diagnostic convenience only. |
| `sqlite3` | `TESTING.md` T6, §7 | The command-line shell. `TESTING.md` T6's crash-recovery check (`PRAGMA integrity_check`) is the **only** test of requirement N4, and `collect-diagnostics.sh` uses it for `db-tail.txt`. Ubuntu's base system has the *library* but not the CLI, so both would fail with `command not found` — and in the diagnostics script that message lands silently inside a redirect. |
| `ffmpeg` | §12 step 1, App. B | `ffplay` for the transport smoke test — the gate that proves the USB path before any Python exists. |

Optional, for the CPU governor in §7:

```bash
sudo apt install -y linux-tools-common "linux-tools-$(uname -r)"
```

If that exact kernel's tools package is unavailable on the development branch —
likely — skip it. §7 has a sysfs fallback that needs no extra packages.

### Verify

```bash
command -v iproxy idevice_id ffplay lsusb gcc sqlite3
dpkg -s libimobiledevice-utils | grep -E '^(Package|Version)'
ls /usr/include/linux/input-event-codes.h
python3 -c "import ensurepip; print('ensurepip ok')"
```

The `libimobiledevice-utils` version **must be ≥ 1.4.0**. Current iOS releases do
not pair with 1.3.0, which is what a stable Ubuntu LTS still ships. The audit
found 1.4.0 on this machine — that is a genuine advantage of its
development-branch release, and a reason not to "stabilise" it onto an LTS.

---

## 3. Input group (evdev trigger)

Spec §6.4 requires reading `/dev/input/event*`, which are `root:input`, mode
`crw-rw----`. The audit confirms the operator user is **not** in `input`.

```bash
sudo usermod -aG input "$USER"
```

**Now log out and log back in.** Group membership is established at session
start; `newgrp input` affects only the one shell you run it in and will mislead
you into thinking it worked. A reboot is equally fine.

### Verify (after logging back in)

```bash
id -nG | tr ' ' '\n' | grep -qx input && echo "input group: OK" || echo "input group: MISSING"
```

If this stays MISSING, the application still runs — spec §6.4 falls back to Qt
key events — but timing precision is degraded and the UI must say so. Do not
accept the fallback for a real regatta.

---

## 4. Python environment

```bash
python3 -m venv ~/regatta/venv
source ~/regatta/venv/bin/activate

python -m pip install --upgrade pip setuptools wheel
python -m pip install PySide6-Essentials requests evdev pillow
```

Notes on each choice:

- **`PySide6-Essentials`, not `PySide6`.** The application imports only QtCore,
  QtGui and QtWidgets. The plain `PySide6` metapackage additionally pulls
  `PySide6-Addons` — WebEngine, 3D, Charts, Multimedia — several hundred MB that
  will never be imported. On a 192 GB SSD that is affordable but pointless.
- **Python 3.14 is fine.** PySide6 6.11.2 publishes
  `pyside6-6.11.2-cp310-abi3-manylinux_2_34_x86_64.whl`: a **stable-ABI** wheel
  with `requires_python = ">=3.10,<3.15"` and an explicit 3.14 classifier. One
  wheel covers 3.10 through 3.14. This closes open verification item 1 in the
  audit — no alternate interpreter, no `--system-site-packages` trick, no
  building Qt.
- **`evdev` compiles here.** This is the step that needs §2's `python3-dev` and
  `build-essential`. It takes a few seconds; if it fails, see §10.
  A prebuilt fallback exists if the build will not work on kernel 7.0 headers:
  the upstream project's own **`evdev-binary`** distribution ships `cp314`
  wheels (`evdev_binary-1.9.3-cp314-cp314-manylinux_2_28_x86_64.whl`), and
  evdev's own `setup.py` points at it in its build-failure message. It is
  compiled against fixed kernel headers, so it may not expose every code on a
  7.0 kernel — prefer the source build and treat this as a documented escape
  hatch, not the default.
- **`requests` and `pillow` are reinstalled inside the venv** even though the
  system has them (2.32.5 / 12.1.1). That is deliberate: the venv is created
  without `--system-site-packages` so it cannot see them. Pillow publishes
  `cp314` wheels on PyPI (`pillow-12.3.0-cp314-cp314-manylinux_2_27_x86_64.whl`),
  so this is a download, not a build. (The system having Pillow 12.1.1 on
  Python 3.14 is *not* evidence for that — Ubuntu's is a distro-built `.deb`
  compiled against the system interpreter, which says nothing about PyPI.)
- **No TOML writer is needed.** Spec v1.2 §8 settles that the application never
  writes `config.toml`; everything machine-produced goes to `calibration.json`.
  Stdlib `tomllib` suffices, and the `tomli-w` dependency listed in v1.1 is
  dropped.

### Verify

```bash
source ~/regatta/venv/bin/activate

python -c "import PySide6; print('PySide6', PySide6.__version__)"
python -c "from importlib.metadata import version; print('evdev', version('evdev'))"
python -c "import evdev.ecodes as e; print('KEY_F13 =', e.KEY_F13, ' KEY_F14 =', e.KEY_F14)"
python -c "import requests, PIL; print('requests/pillow ok')"
```

Note `importlib.metadata.version('evdev')` — **the module has no
`__version__` attribute**, so v1.1's `evdev.__version__` raised `AttributeError`
right next to the (also broken) clock snippet below, giving two consecutive
failures in the same verification block.

Expect `KEY_F13 = 183` and `KEY_F14 = 184`, matching the `crossing_keycodes` and
`start_keycodes` defaults in spec §8. (Spec v1.2 moved the trigger off
`KEY_SPACE = 57`; see §6.4 — the evdev trigger is global and a space typed into
a bow-number field would otherwise fire a phantom crossing.)

Qt actually opening a window — this needs a graphical session, so run it from a
desktop terminal, not over SSH:

```bash
QT_QPA_PLATFORM=xcb python -c "
from PySide6.QtWidgets import QApplication
import sys
app = QApplication(sys.argv)
s = app.primaryScreen()
print('Qt platform:', app.platformName())
print('screen:', s.size().width(), 'x', s.size().height(), '@', round(s.refreshRate()), 'Hz')
"
```

Expect `xcb`, `1920 x 1080`, and ~60 Hz. That 60 Hz is the calibration precision
floor discussed in spec §5.5 — worth seeing it printed once so the number is
not a surprise later.

Enumerating input devices (this is the real §3 test — `list_devices()` only
returns nodes it can actually open):

```bash
python -c "
import evdev
ds = [evdev.InputDevice(p) for p in evdev.list_devices()]
print(len(ds), 'readable input devices')
for d in ds: print(' ', d.path, '-', d.name)
"
```

The audit counted 15 `/dev/input/event*` nodes. If this prints `0`, §3 did not
take effect — log out and back in.

Finally, the clock-domain assertion that spec §11 makes mandatory. Substitute a
real keyboard path from the listing above, run it, and press a key:

```bash
python -c "
import evdev, time, sys, fcntl, struct
EVIOCSCLOCKID = 0x400445A0                     # _IOW('E', 0xA0, int)
d = evdev.InputDevice(sys.argv[1])
fcntl.ioctl(d.fd, EVIOCSCLOCKID, struct.pack('i', time.CLOCK_MONOTONIC))
print('press any key on', d.name)
for ev in d.read_loop():
    if ev.type == evdev.ecodes.EV_KEY and ev.value == 1:
        skew = time.monotonic() - ev.timestamp()
        print(f'skew vs time.monotonic(): {skew*1000:+.2f} ms')
        break
" /dev/input/eventN
```

**The ioctl is not a stylistic preference — `InputDevice.set_clock_id()` does
not exist.** Spec v1.0/v1.1 and v1.1 of this document both called it mandatory;
python-evdev has never implemented it. Verified against the 1.9.3 sdist: the
string `clock` appears nowhere in the distribution, and `EVIOCSCLOCKID` is
absent from `input.c`'s ioctl table. The old snippet raised `AttributeError`
before it could print a skew — so the check designed to catch a clock-domain
mix-up could not itself run.

A skew of a few milliseconds means the domains match. A skew of ~1.8×10¹²
milliseconds means the ioctl did not take and you are comparing
`CLOCK_REALTIME` against `CLOCK_MONOTONIC` — the silent error spec §11 warns
about, which leaves elapsed times correct while making every selected photo
about 56 years stale.

---

## 5. Transport smoke test

Spec §12 step 1. No Python involved; this validates the USB path on its own.

```bash
# iPhone connected by USB, unlocked, "Trust" tapped.
idevice_id -l                    # expect a 40-char UDID (or 24-char with a dash)
                                 # NOTE: exits 0 even with no device — test the
                                 # OUTPUT, never the exit status (see §7)
iproxy 8081:8081 &
ffplay -fflags nobuffer -flags low_delay http://admin:admin@127.0.0.1:8081/live
```

**Include the credentials.** Spec §9.2 step 3 has the operator set a username
and password on the iOS app, so an unauthenticated request returns HTTP 401 and
`ffplay` shows nothing — which looks exactly like a broken transport. T1 is a
blocker gate whose whole pass criterion is "video appears", and the natural
response to a blank window is to spend an hour on usbmuxd or to disable auth on
the phone (which then breaks the application's authenticated request instead, at
a later stage, with a different symptom). The credentials must match `[stream]`
in `config.toml`.

Substitute the real path and port that the IP Camera Lite app displays — spec §8
warns that `/live` and `8081` are app- and version-dependent.

While you are here, capture what the server actually sends. `TESTING.md` T1 asks
for this, and it decides whether the Appendix C parser's strict mode is usable:

```bash
curl -s -u admin:admin http://127.0.0.1:8081/live | head -c 2000 | xxd | head -30
```

Confirm the `Content-Type` boundary format (does it already contain leading
dashes?) and that each part carries a `Content-Length`. Spec §8's
`require_content_length = true` depends on the answer.

While this is running, measure the throughput that audit open item 2 asks about,
because it decides 1080p30 versus 720p60 (spec §10.2):

```bash
curl -s --output /dev/null --write-out '%{speed_download} B/s\n' \
     --max-time 30 -u admin:admin http://127.0.0.1:8081/live
```

Roughly 4.5–7.5 MB/s sustained means 1080p30 is viable.

**If it measures materially less, 720p60 is not the answer.** A 720p JPEG runs
60–100 KB, so 60 fps needs 3.6–6.0 MB/s — overlapping 1080p30's range and above
its floor. A tunnel that cannot carry 1080p30 will not carry 720p60 either, and
choosing it costs bow-number legibility at 50 m. The genuine low-bandwidth
options are **720p30** (1.8–3.0 MB/s) or **1080p30 at higher JPEG compression**
(2.4–3.6 MB/s). See spec §10.2 for the full table.

Decide on **delivered frame rate**, not bytes: run each candidate for 60 s and
take the one with the highest sustained delivered fps at acceptable legibility.
Whichever you choose, set `[stream] assumed_fps` to match — spec §6.3 warns that
a stale `assumed_fps` silently shrinks the ring buffer's time span.

---

## 6. Pin the environment

This machine runs a **development-branch** release. Package versions move under
you, and one specific move breaks the project outright: PySide6 6.11.2 declares
`requires_python = ">=3.10,<3.15"`, so the day the distro's default Python
becomes 3.15, PySide6 becomes uninstallable and the venv is dead. Spec §11 lists
this as the largest schedule risk in the project.

Once §4 verifies clean:

```bash
sudo apt-mark hold python3 python3-minimal libpython3-stdlib
sudo apt-mark showhold

sudo systemctl disable --now unattended-upgrades.service
sudo systemctl disable --now apt-daily.timer apt-daily-upgrade.timer
```

Record exactly what you validated against, so a future failure is diagnosable:

```bash
{ date -Is; python3 -VV; dpkg -l | grep -E 'usbmuxd|libimobiledevice'; \
  ~/regatta/venv/bin/python -m pip freeze; } > ~/regatta/environment-lock.txt
```

The trade-off is real: holding `python3` on a development branch will eventually
block unrelated upgrades. Accept that until after the regatta season. If you must
release the hold, re-run §4's verification and §8's checklist before trusting the
system again.

---

## 7. Race-day preparation

Run this before the first heat, every time. Spec §9.4.

```bash
#!/usr/bin/env bash
# race-day-prep.sh — run from a desktop terminal, not over SSH.
set -u
PREP_FAILED=0

echo "== power =="
on_ac=$(cat /sys/class/power_supply/A{C,DP}*/online 2>/dev/null | head -1)
[ "${on_ac:-0}" = "1" ] && echo "  mains: OK" || echo "  mains: *** PLUG IN THE LAPTOP ***"

echo "== display sleep =="
xset s off
xset -dpms
xset s noblank
echo "  blanking and DPMS disabled"

echo "== lid and screensaver (XFCE) =="
# --create/--type are REQUIRED: xfconf-query --set fails on a property that does
# not exist yet, and XFCE only materialises these once touched in the GUI.
# Read every value back — a silent no-op here is worse than no mitigation,
# because it stops the operator from checking.
xq() {  # xq <channel> <property> <type> <value>
  xfconf-query -c "$1" -p "$2" --create --type "$3" -s "$4" 2>/dev/null
  got=$(xfconf-query -c "$1" -p "$2" 2>/dev/null)
  if [ "$got" = "$4" ]; then printf '  %-46s = %s\n' "$2" "$got"
  else printf '  %-46s FAILED (got %s, wanted %s)\n' "$2" "${got:-<unset>}" "$4"; PREP_FAILED=1; fi
}
xq xfce4-power-manager /xfce4-power-manager/lid-action-on-ac int  0
xq xfce4-power-manager /xfce4-power-manager/dpms-enabled     bool false
xq xfce4-power-manager /xfce4-power-manager/blank-on-ac      int  0
xq xfce4-screensaver   /saver/enabled                        bool false

echo "== cpu governor =="
avail=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors 2>/dev/null)
echo "  available: ${avail:-unknown}"
if [[ "$avail" == *performance* ]]; then
  for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    echo performance | sudo tee "$g" >/dev/null
  done
  echo "  set to: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)"
fi

echo "== offline (spec N3) =="
nmcli radio wifi off        && echo "  wifi off"
sudo timedatectl set-ntp false && echo "  ntp off"

echo "== disk =="
df -h --output=avail /home | tail -1 | xargs echo "  free on /home:"

echo "== phone =="
# idevice_id -l exits 0 with an empty list, so test the OUTPUT, not the status.
udid=$(idevice_id -l 2>/dev/null)
if [ -n "$udid" ]; then echo "  phone: $udid"
else echo "  *** NO DEVICE: unlock the phone and tap Trust ***"; PREP_FAILED=1; fi

echo
if [ "${PREP_FAILED:-0}" = 0 ]; then echo "PREP OK"; else echo "PREP INCOMPLETE — see FAILED lines above"; fi
exit "${PREP_FAILED:-0}"
```

The script must be able to fail: v1.1's version had no exit status and printed
"requested" whether or not anything happened.

Property names under `xfce4-power-manager` vary between XFCE versions;
`xfconf-query -c xfce4-power-manager -lv` lists what actually exists on 4.20.4.
Reconcile any FAILED line against that listing before race day — and note that
"the name is wrong" and "the setting did not apply" look identical from here,
which is exactly why the read-back exists.

**The inhibitor lock below is the primary mechanism, not a supplement.** The
`xset` calls are real but xfce4-power-manager routinely overrides them minutes
later; the xfconf block depends on property names that vary. Only
`systemd-inhibit` is version-independent and cannot be undone by the desktop's
own power daemon. Run the application under it:

```bash
systemd-inhibit \
  --what=handle-lid-switch:sleep:idle \
  --who="regatta-timer" --why="finish-line timing in progress" \
  ~/regatta/venv/bin/python -m hallofframe
```

Do both. A session suspend mid-heat takes `usbmuxd` down with it and the race is
gone.

---

## 8. Verification checklist

This is the gate for spec §12 step 0. Every line must print OK.

```bash
#!/usr/bin/env bash
# verify-env.sh
ok(){ printf '  %-42s %s\n' "$1" "${2:-OK}"; }
no(){ printf '  %-42s FAIL — %s\n' "$1" "$2"; FAILED=1; }
FAILED=0
V=~/regatta/venv/bin/python

[ "$(python3 -c 'import sys;print(sys.version_info[:2]>=(3,10) and sys.version_info[:2]<(3,15))')" = True ] \
  && ok "python in PySide6-supported range" || no "python range" "PySide6 needs >=3.10,<3.15"
[ "$XDG_SESSION_TYPE" = x11 ] && ok "session type x11" || no "session type" "expected x11, got $XDG_SESSION_TYPE"
for b in iproxy idevice_id ffplay gcc lsusb sqlite3; do
  command -v $b >/dev/null && ok "binary: $b" || no "binary: $b" "apt install (see §2)"
done
ls /usr/include/linux/input-event-codes.h >/dev/null 2>&1 \
  && ok "kernel input headers" || no "kernel input headers" "build-essential"
ldconfig -p | grep -q libxcb-cursor  && ok "libxcb-cursor0" || no "libxcb-cursor0" "Qt xcb plugin will not load"
id -nG | tr ' ' '\n' | grep -qx input && ok "input group" || no "input group" "usermod -aG input; log out/in"
[ -x "$V" ] && ok "venv present" || no "venv present" "see §4"
$V -c "import PySide6,evdev,requests,PIL" 2>/dev/null \
  && ok "python imports" || no "python imports" "pip install (see §4)"
[ "$($V -c 'import evdev;print(len(evdev.list_devices()))' 2>/dev/null)" -gt 0 ] 2>/dev/null \
  && ok "readable input devices" || no "readable input devices" "input group not active in this session"
apt-mark showhold | grep -qx python3 && ok "python3 held" || no "python3 held" "see §6"
avail=$(df --output=avail -BG /home | tail -1 | tr -dc 0-9)
[ "${avail:-0}" -ge 60 ] 2>/dev/null \
  && ok "free space ${avail}G (>=60G)" || no "free space" "${avail:-0}G, spec §6.6 wants 60G"

echo
if [ "$FAILED" = 0 ]; then echo "ENVIRONMENT READY"; else echo "ENVIRONMENT NOT READY"; fi
exit "$FAILED"
```

Two things v1.1 got wrong here, both worth understanding rather than just
patching:

- **The free-space check was the right-hand side of a pipeline**, so its
  `FAILED=1` ran in a subshell and was discarded. The script printed the
  per-line `FAIL` *and* then `ENVIRONMENT READY`. `TESTING.md` T0's pass
  criterion is "every line prints OK **and** the script prints ENVIRONMENT
  READY" — the two halves disagreed, and the summary line, the one a rushed
  operator actually reads, was the one that lied. Free space is also the check
  most likely to fail in practice.
- **The script had no exit status**, so it could not gate anything
  automatically. It does now.

The threshold moved from 20 GB to 60 GB: spec v1.2 §6.6 shows 20 GB buys only
44–75 minutes of archiving, not the 6–8 hours `system-environment.md` claimed.

Two items this checklist deliberately cannot cover, because they need the phone
physically attached — carry them forward to spec §12 step 1:

- `idevice_id -l` returns a UDID.
- Sustained usbmuxd throughput, which decides 1080p30 versus 720p60 (§5, §10.2).

---

## 9. Offline / rebuild pre-staging

Spec N3 requires the system to work with no network. Installation does not have
to happen offline, but a rebuild the night before a regatta might. Stage
everything while online:

```bash
# Python side — note `pip wheel`, not `pip download`: it *builds* evdev into a
# real wheel, so the restore needs no compiler.
source ~/regatta/venv/bin/activate
pip wheel -w ~/regatta/wheelhouse PySide6-Essentials requests evdev pillow

# Debian side. --reinstall is REQUIRED: §2 already installed these, and a plain
# --download-only resolves to "0 newly installed" and downloads nothing.
# Download into our own directory rather than scavenging /var/cache/apt.
mkdir -p ~/regatta/debs/partial
sudo apt-get install --reinstall --download-only -y \
    -o Dir::Cache::archives="$HOME/regatta/debs" \
    usbmuxd libimobiledevice-utils python3-pip python3-venv python3-dev \
    build-essential libxcb-cursor0 usbutils sqlite3 ffmpeg
sudo chown -R "$USER:$USER" ~/regatta/debs
ls ~/regatta/debs/*.deb | wc -l      # assert this is plausible, not 0
```

v1.1's version silently staged nothing. `apt install --download-only` on
already-installed packages is a no-op, and §9 runs *after* §2 — so the
subsequent `cp /var/cache/apt/archives/*.deb` copied whatever happened to still
be in the cache, which §6 had just stopped `apt-daily` from cleaning on any
schedule. The failure would have surfaced the night before a regatta, offline.
Verify the restore in a scratch directory or a container while you still have a
network; do not trust an untested staging directory.

Restore, with no network:

```bash
sudo dpkg -i ~/regatta/debs/*.deb; sudo apt-get -f install
python3 -m venv ~/regatta/venv && source ~/regatta/venv/bin/activate
pip install --no-index --find-links ~/regatta/wheelhouse \
    PySide6-Essentials requests evdev pillow
```

The wheelhouse is valid only for the same Python minor version and architecture
it was built on — one more reason for the `apt-mark hold` in §6.

---

## 10. Troubleshooting

**`qt.qpa.plugin: Could not load the Qt platform plugin "xcb"`**
Almost always `libxcb-cursor0` (§2). Diagnose with `QT_DEBUG_PLUGINS=1 python
your_script.py 2>&1 | grep -i cannot`, which names the exact missing `.so`. If a
different library is named, the rest of Qt's X11 set is:
`libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0
libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libgl1`. Under a full XFCE
desktop these are normally already present; `libxcb-cursor0` is the one that
reliably is not.

**`evdev` build fails: `fatal error: Python.h: No such file or directory`**
`python3-dev` missing, or it installed headers for a different Python than the
venv's. Check `python -c "import sysconfig;print(sysconfig.get_paths()['include'])"`
and confirm that directory exists.

**`evdev` build fails: `linux/input.h: No such file`**
`build-essential` missing (it pulls `libc6-dev` → `linux-libc-dev`).

**`pip install PySide6-Essentials`: "No matching distribution found"**
Either the interpreter is outside `>=3.10,<3.15` — check `python -VV`, and see
§6, this is the failure mode the hold exists to prevent — or pip is too old to
parse the `manylinux_2_34` / `abi3` tags. `python -m pip install --upgrade pip`.

**`python3 -m venv` fails with "ensurepip is not available"**
`python3-venv` missing (§2). The audit confirmed no `ensurepip` on this machine.

**`evdev.list_devices()` returns `[]`**
Not in the `input` group *in this session*. `id -nG` may already list it while
the running session predates the change — log out and back in (§3).

**`idevice_id -l` prints nothing**
Unlock the phone and tap Trust; then `sudo systemctl restart usbmuxd`, then
`idevicepair pair`. If it still fails, check the version is ≥ 1.4.0 (§2) — 1.3.0
cannot pair with current iOS.

**`lsusb` prints nothing**
`usbutils` was missing (audit item 3, fixed in §2). Cosmetic — the runbook uses
`idevice_id`, not `lsusb`.

**fps sags after several minutes of running**
CPU governor (§7), or thermal limiting on a 15 W part in a warm boat tent. Check
`grep MHz /proc/cpuinfo` while the stream runs.
