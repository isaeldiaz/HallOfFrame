# Sheet 04 — trigger: identify the internal keyboard

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/trigger.py`, `tests/test_trigger.py` (append only).
**Read first:** `hallofframe/trigger.py`, `tests/test_trigger.py` (how `evdev` is faked without hardware).

### Goal
A function the UI can call to refuse arming when the scratchpad is on and the trigger device is the laptop's built-in keyboard.

### Spec
```python
INTERNAL_KEYBOARD_NAMES = ("AT Translated Set 2 keyboard",)
INTERNAL_KEYBOARD_PHYS_PREFIXES = ("isa0060/",)

def device_identity(device_path: str) -> tuple[str, str]:
    """(name, phys) of an evdev device; ("", "") when evdev is missing, the path is empty, or it cannot be opened. Closes the device. Never raises."""

def is_internal_keyboard(device_path: str) -> bool:
    """True when device_identity() matches INTERNAL_KEYBOARD_NAMES exactly or phys starts with one of INTERNAL_KEYBOARD_PHYS_PREFIXES."""
```
Open with `evdev.InputDevice(path)`, read `.name` and `.phys`, close in `finally`.
`comment: the T460s i8042 keyboard reports name "AT Translated Set 2 keyboard", phys isa0060/serio0/input0 (system-environment.md §2.8, verified 2026-10-09); external USB devices report usb-… phys`.

### Tests (append to `tests/test_trigger.py`)
`device_identity` imports `evdev` lazily (inside the function), so a test fakes the module:
```python
import sys, types
class _Dev:
    def __init__(self, path): self.name, self.phys = _NAME, _PHYS
    def close(self): pass
monkeypatch.setitem(sys.modules, "evdev", types.SimpleNamespace(InputDevice=_Dev))
```
For "no evdev": `monkeypatch.setitem(sys.modules, "evdev", None)` (import then raises `ImportError`).
| Test | Fake device | Expected |
|---|---|---|
| `test_internal_keyboard_by_name` | name `"AT Translated Set 2 keyboard"`, phys `"isa0060/serio0/input0"` | `True` |
| `test_internal_keyboard_by_phys_only` | name `"Other"`, phys `"isa0060/serio0/input0"` | `True` |
| `test_external_button` | name `"SayoDevice"`, phys `"usb-0000:00:14.0-1/input0"` | `False` |
| `test_no_evdev_is_false` | `evdev` import raises `ImportError` | `device_identity("/dev/input/event3") == ("", "")`; `is_internal_keyboard(...) is False` |
| `test_unopenable_is_false` | `InputDevice` raises `OSError` | `False` |
| `test_empty_path` | — | `("", "")`, `False` |

**Commit:** `feat(trigger): device_identity / is_internal_keyboard (§13.3 step 04)`
