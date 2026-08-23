#!/usr/bin/env bash
# verify-env.sh — T0 gate (INSTALL.md §8). Every line must print OK.
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
