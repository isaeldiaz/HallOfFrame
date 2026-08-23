# Target System Profile — Regatta Finish-Line Timer

**Audit date:** 2026-08-21
**Purpose:** Documents the actual hardware and software state of the laptop on which the
regatta finish-timer application (see `regatta-finish-timer-spec.md`) will be developed
and operated. Gaps against the spec's setup runbook (§9.1) are listed at the end.

---

## 1. Machine overview

| Property | Value |
|---|---|
| Hostname | `borodin` |
| Vendor / Model | Lenovo ThinkPad T460s (SKU `20F9`) |
| Chassis | Laptop |
| Firmware | BIOS N1CET73W, version 1.41 (dated 2018-12-07) |
| Power source | Mains (AC connected); dual battery design |

## 2. Hardware detail

### 2.1 CPU

| Property | Value |
|---|---|
| Model | Intel Core i7-6600U @ 2.60 GHz (Skylake, dual-core / 4 threads) |
| Max turbo | 3.4 GHz |
| Current scaling | ~67% (ondemand/schedutil governor active) |

Adequate for the workload: MJPEG parsing is byte-copy bound, JPEG decode only runs at
10 fps for preview, and H.264 is not used. The two cores are sufficient for ingest +
archive + GUI, but headroom is modest — avoid adding decode-heavy features.

### 2.2 Memory

| Property | Value |
|---|---|
| Total RAM | 7.5 GiB |
| In use (idle audit) | 2.5 GiB |
| Available | ~5.0 GiB |
| Swap | 4 GiB swapfile (`/swap.img`), swappiness 60 |

Comfortably above the ~60 MB ring buffer plus Qt overhead. Note for race day: the
archive disk budget (§6.6) matters more than RAM here.

### 2.3 Storage

| Property | Value |
|---|---|
| Device | Samsung MZNLF192 (192 GB SSD, non-rotational) |
| Root filesystem | `/dev/sda2`, ext-family, 174 GB total |
| Free space | **156 GB available (6% used)** |

**Corrected 2026-08-21 after review.** The original text here read "the spec's
pre-flight threshold of 20 GB free (§6.8) gives roughly 6–8 hours of continuous
1080p30 archiving". Both halves were wrong. The threshold is in **§6.6** (§6.8 is
`export.py`), and 6–8 hours is the headroom from *156 GB*, not from the 20 GB
floor:

| From | At 16 GB/h | At 27 GB/h |
|---|---|---|
| 20 GB (the pre-flight threshold) | **75 min** | **44 min** |
| 156 GB (actual free space) | 9.8 h | 5.8 h |

So a heat can pass the start-time check and run the disk dry 45 minutes later,
and a full day of continuous archiving consumes essentially the whole disk.
Spec v1.2 §6.6 raises `min_free_gb` to 60, adds continuous monitoring with
graceful degradation at 10 GB and 3 GB, and reserves a `fallocate` ballast so
that filling the archive cannot take SQLite and the desktop session down with
it — the data root shares the single root filesystem (§2.3 above).

### 2.4 Display and GPU

| Property | Value |
|---|---|
| GPU | Intel HD Graphics 520 (Skylake-U GT2, integrated) |
| Panel | Internal eDP-1, **1920×1080**, currently the only active output |
| External ports | DP-1, DP-2, HDMI-1, HDMI-2 (all disconnected) |

The 1080p panel is what the calibration routine films (§5.5): the millisecond counter
will be rendered on this screen and re-captured by the iPhone camera. High contrast and
full-screen rendering are straightforward at this resolution.

### 2.5 USB

| Property | Value |
|---|---|
| Controller | Intel Sunrise Point-LP xHCI (USB 3.0) |
| Bus activity observed | One 5 Gbps (USB 3.x) link, two 480 Mbps, three 12 Mbps devices |
| iPhone | **Not currently connected** |

The phone will attach through the xHCI controller; `usbmuxd` provides the transport.
Throughput of the usbmuxd tunnel (not the physical port) is the bottleneck per §10.2 and
must be measured with the real device before committing to 1080p30.

### 2.6 Batteries

Dual-battery configuration (BAT0 + BAT0/BAT1 present; one reports 23.54 Wh design
capacity). Currently on AC. Race-day operation should always be on mains — USB streaming
drains the phone battery fast (§9.3 step 10) and the laptop's own endurance under
continuous disk writes is limited on these aging cells.

### 2.7 Network

| Interface | State |
|---|---|
| `enp0s31f6` (Ethernet) | DOWN, no carrier |
| `wlp4s0` (Intel Wireless 8260) | UP (currently associated) |
| `lo` | loopback |

Spec N3 requires fully offline operation. Wi-Fi works today; on race day it should be
disconnected/radar-off so no NTP steps perturb the wall clock (harmless to timing, which
is monotonic-based, but keeps `t_wall` records clean).

### 2.8 Input devices

- 15 `/dev/input/event*` nodes present (internal keyboard, TrackPad, etc.).
- Nodes are `root:input`, mode `crw-rw----`.
- A USB footswitch/keypad will enumerate as an additional event device — the settings
  dialog (§6.4) will list them.

## 3. Operating system and session

| Property | Value |
|---|---|
| OS | **Ubuntu "Stonking Stingray" — development branch** (Xubuntu-flavoured: XFCE desktop) |
| Kernel | Linux 7.0.0-14-generic, x86_64 |
| Desktop | XFCE 4.20.4 |
| Display server | **X11** (`DISPLAY=:0`, Xorg 21.1) — matches the spec's preference (§11.3) |
| Session type env | `XDG_SESSION_TYPE=x11` ✓ |
| Boot | systemd, up ~2 h at audit time |

Two deviations from the spec's stated environment ("Xubuntu latest release", Python 3.12):

1. **This is a development-branch release, not a stable one.** Behavioural risk is low
   for this workload, but package versions may shift under you; pin what matters.
2. **System Python is 3.14.6, not 3.12.** The spec's target language line needs updating.
   *(Resolved: PySide6 6.11.2 ships a `cp310-abi3` wheel with
   `requires_python = ">=3.10,<3.15"` and an explicit 3.14 classifier, so 3.14 is
   supported by a single stable-ABI wheel. The `<3.15` ceiling is now tracked as a
   risk in spec §11 and `INSTALL.md` §6.)*

## 4. Software inventory (relevant to the project)

### 4.1 Already installed

| Package | Version | Role |
|---|---|---|
| `usbmuxd` | 1.1.1-7 | USB multiplexing daemon (service inactive until a device is attached — normal) |
| `libimobiledevice-1.0-6` | 1.4.0 | Core library |
| `libusbmuxd-2.0-7` | 2.1.1 | usbmux client library |
| `libevdev2` | 1.13.6 | evdev support library (C level) |
| Xorg server + input/video drivers | 21.1 | Display stack |
| Python `requests` | 2.32.5 (system site-packages) | HTTP/MJPEG client |
| Python `Pillow` | 12.1.1 (system site-packages) | Thumbnail generation |

### 4.2 Missing (required by the spec)

| Item | Spec reference | Needed for |
|---|---|---|
| `libimobiledevice-utils` (`iproxy`, `idevice_id`) | §6.1, §9.1 | Entire USB transport; smoke test |
| `ffmpeg` / `ffplay` | §9.1, Appendix B | Transport smoke test; RTSP fallback |
| `python3-pip` / `python3-venv` | §9.1 | Virtualenv creation (**neither pip nor ensurepip is present**) |
| Python `PySide6` | §7 | GUI |
| Python `evdev` | §6.4 | Precise trigger |
| `input` group membership for the operator user `<user>` | §6.4 | Reading `/dev/input/event*` |

### 4.3 Python environment

```
/usr/bin/python3  →  Python 3.14.6
pip               →  NOT AVAILABLE (no pip3, no `python3 -m pip`, no ensurepip)
venv              →  unusable until python3-venv is installed
~/regatta/venv    →  does not exist yet
```

System-wide `requests` and `Pillow` exist, but the venv (created without
`--system-site-packages`) will need its own copies per the spec's install list.

## 5. Permissions and privilege notes

- Operator user `<user>` is in: `adm, cdrom, sudo, dip, plugdev, users, lpadmin, lxd`.
- **Not in `input`** → evdev trigger will fail with permission denied until
  `sudo usermod -aG input <user>` plus logout/login (§6.4). The app's Qt-fallback warning
  path would engage if unaddressed.
- `sudo` requires a password (no passwordless sudo) — the one-time setup commands need
  interactive execution; they cannot be scripted silently.

## 6. Project directory state

```
<repo-path>/
    regatta-finish-timer-spec.md   # build specification (v1.0)
    system-environment.md          # this file
    new, session_log.md            # scratch/log files
```

No source code, no virtualenv, no config yet. Note the spec's data layout (§6.7) also
uses `~/regatta/` for runtime data (`regatta.db`, `races/`, `config.toml`) — decide
whether code and data share this directory or data moves elsewhere before scaffolding.

## 7. Readiness checklist (gap closure order)

Mirrors spec §12 step 1 (transport + smoke test gate):

```bash
sudo apt update
sudo apt install -y libimobiledevice-utils ffmpeg python3-pip python3-venv
sudo usermod -aG input "$USER"        # then log out and back in
python3 -m venv ~/regatta/venv
source ~/regatta/venv/bin/activate
pip install PySide6 requests evdev pillow
```

Then, with the iPhone connected by USB:

```bash
idevice_id -l                         # expect a UDID
iproxy 8081:8081 &
ffplay -fflags nobuffer -flags low_delay http://127.0.0.1:8081/live
```

**Open verification items specific to this machine:**

1. ~~PySide6 wheel availability for Python 3.14 inside the venv.~~ **Resolved** —
   `cp310-abi3` wheel covers 3.10–3.14. See `INSTALL.md` §4.
2. Actual sustained usbmuxd throughput with the real phone (decides 1080p30 vs 720p60, §10.2).
3. ~~`lsusb` printed no devices during this audit despite sysfs showing USB links.~~
   **Resolved** — `usbutils` is simply not installed; it is added to `INSTALL.md` §2.
   (Cosmetic either way: the runbook relies on `idevice_id`, not `lsusb`.)
4. Calibration legibility of the ms-counter on the 1080p panel from the tripod position.
