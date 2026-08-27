# sudo commands needed to unblock the build (T0)

Run these on the target machine (`HOSTNAME`) from a terminal. They come from
`INSTALL.md` §2, §3, §6. Everything here needs an interactive password
(no passwordless sudo).

Then run `source ~/regatta/venv/bin/activate` + the §4 verify lines and report
back so the bench tests can be re-run inside the venv.

---

## 1. System packages (INSTALL.md §2)

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

Verify:
```bash
command -v iproxy idevice_id ffplay lsusb gcc sqlite3
dpkg -s libimobiledevice-utils | grep -E '^(Package|Version)'
ls /usr/include/linux/input-event-codes.h
python3 -c "import ensurepip; print('ensurepip ok')"
```

`libimobiledevice-utils` must be ≥ 1.4.0.

## 2. Input group for evdev trigger (INSTALL.md §3)

```bash
sudo usermod -aG input "$USER"
```

**After this you MUST log out and log back in** (or reboot) for the group to
take effect in the session. Verify after logging back in:
```bash
id -nG | tr ' ' '\n' | grep -qx input && echo "input group: OK" || echo "input group: MISSING"
```

## 3. Pin the environment (INSTALL.md §6)

Do this once §4 verifies clean:

```bash
sudo apt-mark hold python3 python3-minimal libpython3-stdlib
sudo apt-mark showhold

sudo systemctl disable --now unattended-upgrades.service
sudo systemctl disable --now apt-daily.timer apt-daily-upgrade.timer
```

## 4. Python environment (INSTALL.md §4) — does NOT need sudo

Only included here so the full flow is in one place. Run after §1:

```bash
python3 -m venv ~/regatta/venv
source ~/regatta/venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install PySide6-Essentials requests evdev pillow
```

Verify:
```bash
python -c "import PySide6; print('PySide6', PySide6.__version__)"
python -c "from importlib.metadata import version; print('evdev', version('evdev'))"
python -c "import evdev.ecodes as e; print('KEY_F13 =', e.KEY_F13, ' KEY_F14 =', e.KEY_F14)"
python -c "import requests, PIL; print('requests/pillow ok')"
```

---

When done, report back that these ran and that `environment-lock.txt` can be
created, or paste any error output. Then I'll re-run the bench tests (T3, T6,
T9) inside the venv and continue.
